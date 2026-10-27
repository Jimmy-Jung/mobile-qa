// Author: JunyoungJung · 2026-10-27.

import Darwin
import Foundation

/// Owns only one private test invocation and its diagnostic artifacts.
final class PrivateIOSRun {
    let path: String
    var runfile: String { path + "/Runner.xctestrun" }
    var resultBundle: String { path + "/Result.xcresult" }
    var derivedData: String { path + "/DerivedData" }
    private let parent: Int32
    private let directory: Int32
    private let name: String
    private let identity: stat

    init(root: String) throws {
        let url = URL(fileURLWithPath: root, isDirectory: true)
        guard url.standardizedFileURL.path == root, url.resolvingSymlinksInPath().path == root else {
            throw SecureLoginFailure(code: "runner_ownership_unavailable")
        }
        let parent = Darwin.open(root, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard parent >= 0 else { throw SecureLoginFailure(code: "runner_ownership_unavailable") }
        let name = "account-run-" + UUID().uuidString
        guard mkdirat(parent, name, 0o700) == 0 else {
            _ = close(parent)
            throw SecureLoginFailure(code: "runner_ownership_unavailable")
        }
        let directory = openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        var info = stat()
        guard directory >= 0, fstat(directory, &info) == 0, info.st_uid == getuid(),
              info.st_mode & S_IFMT == S_IFDIR, info.st_mode & 0o777 == 0o700 else {
            if directory >= 0 { _ = close(directory) }
            _ = close(parent)
            throw SecureLoginFailure(code: "runner_ownership_unavailable")
        }
        self.parent = parent
        self.directory = directory
        self.name = name
        self.path = root + "/" + name
        self.identity = info
    }

    deinit { _ = close(directory); _ = close(parent) }

    func requireFreshResult() throws {
        for name in ["Result.xcresult", "DerivedData"] {
            var info = stat()
            guard fstatat(directory, name, &info, AT_SYMLINK_NOFOLLOW) != 0, errno == ENOENT else {
                throw SecureLoginFailure(code: "runner_ownership_unavailable")
            }
        }
    }

    private func remove() throws {
        var named = stat()
        guard fstatat(parent, name, &named, AT_SYMLINK_NOFOLLOW) == 0,
              Self.sameDirectory(named, identity) else {
            throw SecureLoginFailure(code: "ios_artifact_cleanup_unconfirmed")
        }
        try Self.removeContents(directory)
        guard fstatat(parent, name, &named, AT_SYMLINK_NOFOLLOW) == 0,
              Self.sameDirectory(named, identity), unlinkat(parent, name, AT_REMOVEDIR) == 0 else {
            throw SecureLoginFailure(code: "ios_artifact_cleanup_unconfirmed")
        }
    }

    private static func sameDirectory(_ lhs: stat, _ rhs: stat) -> Bool {
        lhs.st_mode & S_IFMT == S_IFDIR && lhs.st_uid == getuid()
            && lhs.st_dev == rhs.st_dev && lhs.st_ino == rhs.st_ino
    }

    private static func removeContents(_ descriptor: Int32) throws {
        let copy = dup(descriptor)
        guard copy >= 0 else { throw SecureLoginFailure(code: "ios_artifact_cleanup_unconfirmed") }
        guard let iterator = fdopendir(copy) else {
            _ = close(copy)
            throw SecureLoginFailure(code: "ios_artifact_cleanup_unconfirmed")
        }
        defer { _ = closedir(iterator) }
        while true {
            errno = 0
            guard let entry = readdir(iterator) else {
                guard errno == 0 else { throw SecureLoginFailure(code: "ios_artifact_cleanup_unconfirmed") }
                return
            }
            let name = withUnsafePointer(to: &entry.pointee.d_name) { pointer in
                pointer.withMemoryRebound(to: CChar.self, capacity: Int(NAME_MAX) + 1) { String(validatingUTF8: $0) }
            }
            guard let name else { throw SecureLoginFailure(code: "ios_artifact_cleanup_unconfirmed") }
            if name == "." || name == ".." { continue }
            var before = stat()
            guard fstatat(descriptor, name, &before, AT_SYMLINK_NOFOLLOW) == 0, before.st_uid == getuid() else {
                throw SecureLoginFailure(code: "ios_artifact_cleanup_unconfirmed")
            }
            if before.st_mode & S_IFMT == S_IFDIR {
                let child = openat(descriptor, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
                guard child >= 0 else { throw SecureLoginFailure(code: "ios_artifact_cleanup_unconfirmed") }
                defer { _ = close(child) }
                var opened = stat()
                guard fstat(child, &opened) == 0, sameDirectory(opened, before) else {
                    throw SecureLoginFailure(code: "ios_artifact_cleanup_unconfirmed")
                }
                try removeContents(child)
                var after = stat()
                guard fstatat(descriptor, name, &after, AT_SYMLINK_NOFOLLOW) == 0,
                      sameDirectory(after, before), unlinkat(descriptor, name, AT_REMOVEDIR) == 0 else {
                    throw SecureLoginFailure(code: "ios_artifact_cleanup_unconfirmed")
                }
            } else if unlinkat(descriptor, name, 0) != 0 {
                throw SecureLoginFailure(code: "ios_artifact_cleanup_unconfirmed")
            }
        }
    }

    static func perform(root: String, _ body: (PrivateIOSRun) throws -> [String: Any]) throws -> [String: Any] {
        let artifacts = try PrivateIOSRun(root: root)
        var result: Result<[String: Any], Error> = .failure(SecureLoginFailure(code: "runner_ownership_unavailable"))
        func execute() {
            defer {
                do { try artifacts.remove() }
                catch {
                    result = .success(["status": "uncertain", "code": "ios_artifact_cleanup_unconfirmed",
                                       "cleanup": "unconfirmed", "environment_verified": false])
                }
            }
            do { result = .success(try body(artifacts)) }
            catch { result = .failure(error) }
        }
        execute()
        return try result.get()
    }
}
