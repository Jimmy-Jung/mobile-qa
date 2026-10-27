// Created by JunyoungJung on 2026-10-22.

import Darwin
import Foundation
import Security

enum CredentialStoreFailure: Error, Equatable {
    case storageFailed
    case invalidCredentials
    case migrationRequiresApproval
    case selfTestFailed

    var code: String {
        switch self {
        case .storageFailed: "storage_failed"
        case .invalidCredentials: "invalid_credentials"
        case .migrationRequiresApproval: "migration_requires_approval"
        case .selfTestFailed: "self_test_failed"
        }
    }
}

struct StoredCredentials: Codable, Equatable, Sendable {
    let username: String
    let password: String

    var valid: Bool {
        !username.isEmpty && !password.isEmpty
            && username.utf8.count <= 16384 && password.utf8.count <= 16384
    }

    static func decode(_ data: Data) throws -> Self {
        let object = try CredentialJSON.object(data)
        guard Set(object.keys) == ["username", "password"],
              let username = object["username"] as? String, let password = object["password"] as? String else {
            throw CredentialStoreFailure.invalidCredentials
        }
        let credentials = Self(username: username, password: password)
        guard credentials.valid else { throw CredentialStoreFailure.invalidCredentials }
        return credentials
    }

    static func readFromPipe(_ descriptor: Int32) throws -> Self {
        var info = stat()
        guard descriptor >= 3, fstat(descriptor, &info) == 0, isatty(descriptor) == 0 else {
            throw CredentialStoreFailure.invalidCredentials
        }
        let kind = info.st_mode & mode_t(S_IFMT)
        guard kind == mode_t(S_IFIFO) || kind == mode_t(S_IFSOCK) else {
            throw CredentialStoreFailure.invalidCredentials
        }
        do {
            var pending = Data()
            let data = try SecureIO.line(descriptor, pending: &pending, limit: 256 * 1024,
                                         deadline: SecureIO.now + 30)
            return try decode(data)
        } catch {
            throw CredentialStoreFailure.invalidCredentials
        }
    }
}

struct CredentialLocator: Sendable {
    let id: String
    let reference: Data

    static func validID(_ id: String) -> Bool {
        !id.isEmpty && id.utf8.count <= 96 && id.utf8.allSatisfy {
            (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0)
                || $0 == 45 || $0 == 46 || $0 == 95
        }
    }

    static func reference(_ value: Any?) throws -> Data {
        guard let value = value as? String, value.utf8.count <= 8192,
              let data = Data(base64Encoded: value), !data.isEmpty, data.base64EncodedString() == value else {
            throw CredentialStoreFailure.invalidCredentials
        }
        return data
    }

    static func readBatch() throws -> [Self] {
        var pending = Data()
        let data = try SecureIO.line(STDIN_FILENO, pending: &pending, limit: 10 * 1024 * 1024,
                                     deadline: SecureIO.now + 30)
        let object = try CredentialJSON.object(data)
        guard Set(object.keys) == ["accounts"], let values = object["accounts"] as? [[String: Any]],
              values.count <= 1024 else { throw CredentialStoreFailure.invalidCredentials }
        let locators = try values.map { value in
            guard Set(value.keys) == ["id", "reference"], let id = value["id"] as? String,
                  validID(id) else { throw CredentialStoreFailure.invalidCredentials }
            return Self(id: id, reference: try reference(value["reference"]))
        }
        guard Set(locators.map(\.id)).count == locators.count,
              Set(locators.map(\.reference)).count == locators.count else {
            throw CredentialStoreFailure.invalidCredentials
        }
        return locators
    }
}

struct CredentialFileStore: Sendable {
    private static let maximumBytes = 40 * 1024 * 1024
    private let testDirectory: String?

    init() { testDirectory = nil }
    init(testDirectory: String) { self.testDirectory = testDirectory }

    private struct Entry: Codable {
        let id: String
        let reference: String
        let username: String
        let password: String

        var credentials: StoredCredentials { StoredCredentials(username: username, password: password) }

        init(_ locator: CredentialLocator, _ credentials: StoredCredentials) {
            id = locator.id
            reference = locator.reference.base64EncodedString()
            username = credentials.username
            password = credentials.password
        }
    }

    private struct Document: Encodable {
        let version = 1
        let accounts: [Entry]
    }

    func read(id: String, reference: Data) throws -> StoredCredentials {
        try withLock { directory in
            let entries = try load(directory)
            guard let entry = entries.first(where: { $0.id == id && $0.reference == reference.base64EncodedString() }) else {
                throw CredentialStoreFailure.invalidCredentials
            }
            return entry.credentials
        }
    }

    func check(_ locators: [CredentialLocator]) throws -> Int {
        try withLock { directory in
            let entries = try load(directory)
            guard entries.count == locators.count, locators.allSatisfy({ locator in entries.contains {
                $0.id == locator.id && $0.reference == locator.reference.base64EncodedString()
            } }) else { throw CredentialStoreFailure.invalidCredentials }
            return locators.count
        }
    }

    func create(id: String, credentials: StoredCredentials) throws -> Data {
        guard CredentialLocator.validID(id), credentials.valid else { throw CredentialStoreFailure.invalidCredentials }
        return try withLock { directory in
            let entries = try load(directory)
            guard entries.count < 1024, !entries.contains(where: { $0.id == id }) else {
                throw CredentialStoreFailure.storageFailed
            }
            var bytes = [UInt8](repeating: 0, count: 32)
            guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
                throw CredentialStoreFailure.storageFailed
            }
            let reference = Data(bytes)
            guard !entries.contains(where: { $0.reference == reference.base64EncodedString() }) else {
                throw CredentialStoreFailure.storageFailed
            }
            try save(entries + [Entry(CredentialLocator(id: id, reference: reference), credentials)], directory)
            return reference
        }
    }

    func update(id: String, reference: Data, credentials: StoredCredentials) throws {
        guard credentials.valid else { throw CredentialStoreFailure.invalidCredentials }
        try withLock { directory in
            let entries = try load(directory)
            guard entries.contains(where: { $0.id == id && $0.reference == reference.base64EncodedString() }) else {
                throw CredentialStoreFailure.invalidCredentials
            }
            try save(entries.map { $0.id == id ? Entry(CredentialLocator(id: id, reference: reference), credentials) : $0 }, directory)
        }
    }

    func removeCreated(id: String, reference: Data) throws {
        try withLock { directory in
            let entries = try load(directory)
            let remaining = entries.filter { !($0.id == id && $0.reference == reference.base64EncodedString()) }
            if remaining.count != entries.count { try save(remaining, directory) }
        }
    }

    func migrate(_ locators: [CredentialLocator], readLegacy: (CredentialLocator) throws -> StoredCredentials) throws -> Int {
        try withLock { directory in
            let entries = try load(directory)
            guard locators.allSatisfy({ locator in !entries.contains {
                ($0.id == locator.id || $0.reference == locator.reference.base64EncodedString())
                    && !($0.id == locator.id && $0.reference == locator.reference.base64EncodedString())
            } }) else { throw CredentialStoreFailure.storageFailed }
            let missing = locators.filter { locator in !entries.contains { $0.id == locator.id } }
            guard entries.count + missing.count <= 1024 else { throw CredentialStoreFailure.storageFailed }
            let additions = try missing.map { locator in
                let credentials = try readLegacy(locator)
                guard credentials.valid else { throw CredentialStoreFailure.invalidCredentials }
                return Entry(locator, credentials)
            }
            if !additions.isEmpty { try save(entries + additions, directory) }
            return locators.count
        }
    }

    private func openDirectory() throws -> Int32 {
        if let testDirectory {
            let descriptor = try openAbsoluteDirectory(testDirectory)
            do { try validate(descriptor, directory: true) } catch { close(descriptor); throw error }
            return descriptor
        }
        guard getuid() == geteuid(), let entry = getpwuid(getuid()), let home = entry.pointee.pw_dir else {
            throw CredentialStoreFailure.storageFailed
        }
        let homeFD = try openAbsoluteDirectory(String(cString: home))
        defer { close(homeFD) }
        var homeInfo = stat()
        guard fstat(homeFD, &homeInfo) == 0, homeInfo.st_uid == getuid(), homeInfo.st_mode & 0o022 == 0 else {
            throw CredentialStoreFailure.storageFailed
        }
        let config = try openChildDirectory(homeFD, ".config", exactMode: false)
        defer { close(config) }
        return try openChildDirectory(config, "agent-test-accounts", exactMode: true)
    }

    private func openAbsoluteDirectory(_ path: String) throws -> Int32 {
        guard path.hasPrefix("/") else { throw CredentialStoreFailure.storageFailed }
        var descriptor = Darwin.open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC)
        guard descriptor >= 0 else { throw CredentialStoreFailure.storageFailed }
        for component in path.split(separator: "/") {
            guard component != ".", component != ".." else { close(descriptor); throw CredentialStoreFailure.storageFailed }
            let next = openat(descriptor, String(component), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            close(descriptor)
            guard next >= 0 else { throw CredentialStoreFailure.storageFailed }
            descriptor = next
        }
        return descriptor
    }

    private func openChildDirectory(_ parent: Int32, _ name: String, exactMode: Bool) throws -> Int32 {
        guard mkdirat(parent, name, 0o700) == 0 || errno == EEXIST else {
            throw CredentialStoreFailure.storageFailed
        }
        let descriptor = openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard descriptor >= 0 else { throw CredentialStoreFailure.storageFailed }
        do { try validate(descriptor, directory: true, exactMode: exactMode) }
        catch { close(descriptor); throw error }
        return descriptor
    }

    private func withLock<T>(_ operation: (Int32) throws -> T) throws -> T {
        let directory = try openDirectory()
        defer { close(directory) }
        let lock = openat(directory, "credentials.lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK, 0o600)
        guard lock >= 0 else { throw CredentialStoreFailure.storageFailed }
        defer { close(lock) }
        try validate(lock, directory: false)
        while flock(lock, LOCK_EX) != 0 {
            guard errno == EINTR else { throw CredentialStoreFailure.storageFailed }
        }
        defer { _ = flock(lock, LOCK_UN) }
        try validate(directory, directory: true)
        try validate(lock, directory: false)
        try sameFile(lock, directory, "credentials.lock")
        return try operation(directory)
    }

    private func validate(_ descriptor: Int32, directory: Bool, exactMode: Bool = true) throws {
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_uid == getuid(),
              info.st_mode & S_IFMT == (directory ? S_IFDIR : S_IFREG),
              (exactMode ? (info.st_mode & 0o7777 == (directory ? 0o700 : 0o600)) : (info.st_mode & 0o022 == 0)),
              directory || (info.st_nlink == 1 && info.st_size >= 0 && info.st_size <= Self.maximumBytes) else {
            throw CredentialStoreFailure.storageFailed
        }
        guard let acl = acl_get_fd_np(descriptor, ACL_TYPE_EXTENDED) else {
            guard errno == ENOENT else { throw CredentialStoreFailure.storageFailed }
            return
        }
        defer { _ = acl_free(UnsafeMutableRawPointer(acl)) }
        var entry: acl_entry_t?
        guard acl_get_entry(acl, Int32(ACL_FIRST_ENTRY.rawValue), &entry) == -1, errno == EINVAL || errno == ENOENT else {
            throw CredentialStoreFailure.storageFailed
        }
    }

    private func sameFile(_ descriptor: Int32, _ directory: Int32, _ name: String) throws {
        var opened = stat()
        var named = stat()
        guard fstat(descriptor, &opened) == 0,
              fstatat(directory, name, &named, AT_SYMLINK_NOFOLLOW) == 0,
              opened.st_dev == named.st_dev, opened.st_ino == named.st_ino else {
            throw CredentialStoreFailure.storageFailed
        }
    }

    private func load(_ directory: Int32) throws -> [Entry] {
        let descriptor = openat(directory, "credentials.json", O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK)
        if descriptor < 0 && errno == ENOENT { return [] }
        guard descriptor >= 0 else { throw CredentialStoreFailure.storageFailed }
        defer { close(descriptor) }
        try validate(descriptor, directory: false)
        try sameFile(descriptor, directory, "credentials.json")
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 65536)
        while true {
            let count = Darwin.read(descriptor, &buffer, buffer.count)
            if count < 0 && errno == EINTR { continue }
            guard count >= 0, data.count + count <= Self.maximumBytes else { throw CredentialStoreFailure.storageFailed }
            if count == 0 { break }
            data.append(contentsOf: buffer.prefix(count))
        }
        try validate(descriptor, directory: false)
        try sameFile(descriptor, directory, "credentials.json")
        do {
            let object = try CredentialJSON.object(data)
            guard Set(object.keys) == ["version", "accounts"], let version = object["version"] as? NSNumber,
                  ["i", "q"].contains(String(cString: version.objCType)), version == 1,
                  let values = object["accounts"] as? [[String: Any]], values.count <= 1024 else {
                throw CredentialStoreFailure.storageFailed
            }
            let entries = try values.map { value in
                guard Set(value.keys) == ["id", "reference", "username", "password"],
                      let id = value["id"] as? String, CredentialLocator.validID(id),
                      let username = value["username"] as? String, let password = value["password"] as? String else {
                    throw CredentialStoreFailure.storageFailed
                }
                let credentials = StoredCredentials(username: username, password: password)
                guard credentials.valid else { throw CredentialStoreFailure.storageFailed }
                return Entry(CredentialLocator(id: id, reference: try CredentialLocator.reference(value["reference"])), credentials)
            }
            guard Set(entries.map(\.id)).count == entries.count, Set(entries.map(\.reference)).count == entries.count else {
                throw CredentialStoreFailure.storageFailed
            }
            return entries
        } catch { throw CredentialStoreFailure.storageFailed }
    }

    private func save(_ entries: [Entry], _ directory: Int32) throws {
        let data = try JSONEncoder().encode(Document(accounts: entries))
        guard data.count <= Self.maximumBytes else { throw CredentialStoreFailure.storageFailed }
        let name = ".credentials-" + UUID().uuidString + ".tmp"
        let descriptor = openat(directory, name, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard descriptor >= 0 else { throw CredentialStoreFailure.storageFailed }
        defer { close(descriptor); _ = unlinkat(directory, name, 0) }
        try validate(descriptor, directory: false)
        try data.withUnsafeBytes { bytes in
            var offset = 0
            while offset < bytes.count {
                let count = Darwin.write(descriptor, bytes.baseAddress!.advanced(by: offset), bytes.count - offset)
                if count < 0 && errno == EINTR { continue }
                guard count > 0 else { throw CredentialStoreFailure.storageFailed }
                offset += count
            }
        }
        guard fsync(descriptor) == 0 else { throw CredentialStoreFailure.storageFailed }
        try validate(descriptor, directory: false)
        try sameFile(descriptor, directory, name)
        guard renameat(directory, name, directory, "credentials.json") == 0, fsync(directory) == 0 else {
            throw CredentialStoreFailure.storageFailed
        }
        try sameFile(descriptor, directory, "credentials.json")
    }
}

private enum CredentialJSON {
    static func object(_ data: Data) throws -> [String: Any] {
        do {
            guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw CredentialStoreFailure.invalidCredentials
            }
            // Foundation accepts duplicate object keys; reject them before any credential mutation.
            var stack: [Set<String>] = []
            let bytes = [UInt8](data)
            var index = 0
            while index < bytes.count {
                if bytes[index] == 123 { stack.append([]) }
                if bytes[index] == 125 { _ = stack.popLast() }
                if bytes[index] == 34 {
                    let start = index
                    index += 1
                    while index < bytes.count && bytes[index] != 34 {
                        if bytes[index] == 92 { index += 1 }
                        index += 1
                    }
                    var next = index + 1
                    while next < bytes.count && [9, 10, 13, 32].contains(bytes[next]) { next += 1 }
                    if next < bytes.count && bytes[next] == 58 {
                        let value = try JSONSerialization.jsonObject(with: Data(bytes[start...index]), options: .fragmentsAllowed)
                        guard let key = value as? String, let last = stack.indices.last,
                              stack[last].insert(key).inserted else { throw CredentialStoreFailure.invalidCredentials }
                    }
                }
                index += 1
            }
            return object
        } catch { throw CredentialStoreFailure.invalidCredentials }
    }
}
