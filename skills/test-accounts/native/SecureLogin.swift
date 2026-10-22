// Created by JunyoungJung on 2026-10-20.

import CryptoKit
import CoreFoundation
import Darwin
import Foundation

struct SecureLoginFailure: Error {
    let code: String
}

struct SecureLoginRequest {
    let id: String
    let reference: Data
    let plan: LoginPlan

    static func read() throws -> Self {
        var pending = Data()
        let bytes = try SecureIO.line(STDIN_FILENO, pending: &pending, limit: 65536, deadline: SecureIO.now + 30)
        let parsed: Any
        do { parsed = try JSONSerialization.jsonObject(with: bytes) } catch {
            throw SecureLoginFailure(code: "invalid_login_request")
        }
        guard let object = parsed as? [String: Any],
              Set(object.keys) == ["id", "reference", "plan"],
              let id = object["id"] as? String, LoginPlan.token(id, maximum: 96),
              let encoded = object["reference"] as? String, encoded.utf8.count <= 8192,
              let reference = Data(base64Encoded: encoded), !reference.isEmpty,
              reference.base64EncodedString() == encoded,
              let metadata = object["plan"] as? [String: Any] else {
            throw SecureLoginFailure(code: "invalid_login_request")
        }
        return Self(id: id, reference: reference, plan: try LoginPlan(metadata))
    }
}

struct LoginPlan {
    let platform: String
    let device: String
    let appID: String
    let environment: String
    let timeout: Int
    let metadata: [String: Any]

    static let profileKeys: Set<String> = ["schema_version", "name", "platform", "app_id", "environment", "role",
        "username_selector", "password_selector", "submit_selector", "environment_selector", "success_selectors", "timeout_ms"]

    init(_ object: [String: Any]) throws {
        let keys = Self.profileKeys.subtracting(["schema_version", "name"]).union(["device", "project_key", "account_project", "trust"])
        guard Set(object.keys) == keys,
              let device = object["device"] as? String, Self.token(device, maximum: 256, colon: true),
              let project = object["project_key"] as? String,
              project.range(of: #"^[a-z0-9][a-z0-9-]{0,48}-[a-f0-9]{12}\z"#, options: .regularExpression) != nil,
              let accountProject = object["account_project"] as? String, !accountProject.isEmpty,
              accountProject.utf8.count <= 2048,
              let trust = object["trust"] as? [String: String], Set(trust.keys) == ["path", "profile_sha256", "release_digest"],
              trust["release_digest"] == DriverPins.releaseDigest,
              let path = trust["path"], let expectedHash = trust["profile_sha256"],
              expectedHash.range(of: #"^[a-f0-9]{64}\z"#, options: .regularExpression) != nil else {
            throw SecureLoginFailure(code: "invalid_login_plan")
        }
        let contents = try Self.privateProfile(path, project: project)
        let actualHash = SHA256.hash(data: contents).map { String(format: "%02x", $0) }.joined()
        guard actualHash == expectedHash,
              let wrapper = try JSONSerialization.jsonObject(with: contents) as? [String: Any],
              Set(wrapper.keys) == ["schema_version", "project_key", "account_project", "release_digest", "profile"],
              Self.integer(wrapper["schema_version"]) == 1,
              wrapper["project_key"] as? String == project, wrapper["account_project"] as? String == accountProject,
              wrapper["release_digest"] as? String == DriverPins.releaseDigest,
              let profile = wrapper["profile"] as? [String: Any], Self.validProfile(profile),
              let name = profile["name"] as? String,
              path == NSHomeDirectory() + "/Library/Application Support/mobile-device-qa/projects/" + project + "/profiles/" + name + ".json",
              NSDictionary(dictionary: profile.filter { !["schema_version", "name"].contains($0.key) })
                .isEqual(to: object.filter { !["device", "project_key", "account_project", "trust"].contains($0.key) }),
              let platform = profile["platform"] as? String, let appID = profile["app_id"] as? String,
              let environment = profile["environment"] as? String, let timeout = Self.integer(profile["timeout_ms"]) else {
            throw SecureLoginFailure(code: "invalid_login_plan")
        }
        self.platform = platform
        self.device = device
        self.appID = appID
        self.environment = environment
        self.timeout = timeout
        self.metadata = profile.filter { !["schema_version", "name", "platform", "environment", "role"].contains($0.key) }
            .merging(["release_digest": DriverPins.releaseDigest]) { _, new in new }
    }

    private static func privateProfile(_ path: String, project: String) throws -> Data {
        let root = NSHomeDirectory() + "/Library/Application Support/mobile-device-qa/projects/" + project
        let url = URL(fileURLWithPath: path).standardizedFileURL
        guard path.hasPrefix(root + "/profiles/"), url.path == path,
              url.resolvingSymlinksInPath().path == path else { throw SecureLoginFailure(code: "invalid_login_plan") }
        for folder in [root, root + "/profiles"] {
            var info = stat()
            guard lstat(folder, &info) == 0, info.st_mode & S_IFMT == S_IFDIR,
                  info.st_uid == getuid(), info.st_mode & 0o777 == 0o700 else {
                throw SecureLoginFailure(code: "invalid_login_plan")
            }
        }
        let descriptor = Darwin.open(path, O_RDONLY | O_NOFOLLOW)
        guard descriptor >= 0 else { throw SecureLoginFailure(code: "invalid_login_plan") }
        let file = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? file.close() }
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_uid == getuid(), info.st_mode & 0o777 == 0o600, info.st_nlink == 1, info.st_size <= 32768,
              let contents = try file.read(upToCount: 32769), contents.count <= 32768 else {
            throw SecureLoginFailure(code: "invalid_login_plan")
        }
        return contents
    }

    static func integer(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              String(cString: number.objCType) != "d", String(cString: number.objCType) != "f" else { return nil }
        return number.intValue
    }

    static func validProfile(_ profile: [String: Any]) -> Bool {
        guard Set(profile.keys) == profileKeys, integer(profile["schema_version"]) == 1,
              let platform = profile["platform"] as? String, ["ios", "android"].contains(platform),
              let app = profile["app_id"] as? String, app.utf8.count <= 200,
              app.range(of: #"^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z][A-Za-z0-9_-]*)+\z"#, options: .regularExpression) != nil,
              (platform != "android" || !app.contains("-")),
              ["name", "environment", "role"].allSatisfy({ label(profile[$0] as? String) }),
              let timeout = integer(profile["timeout_ms"]), (1000...15000).contains(timeout),
              let success = profile["success_selectors"] as? [[String: String]], (1...8).contains(success.count) else { return false }
        var selectors: [[String: String]] = []
        for key in ["username_selector", "password_selector", "submit_selector", "environment_selector"] {
            guard let selector = profile[key] as? [String: String] else { return false }
            selectors.append(selector)
        }
        selectors += success
        for (index, selector) in selectors.enumerated() {
            guard selector.count == 1, let (key, value) = selector.first,
                  (platform == "ios" ? ["identifier", "label", "placeholder"] : ["identifier", "label"]).contains(key),
                  !value.isEmpty, value.utf8.count <= 256,
                  !value.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
                  !selectors.prefix(index).contains(selector) else { return false }
            if platform == "android", key == "identifier" {
                let prefix = app + ":id/"
                guard value.hasPrefix(prefix), String(value.dropFirst(prefix.count)).range(of: #"^[A-Za-z_][A-Za-z0-9_]*\z"#, options: .regularExpression) != nil else { return false }
            }
        }
        return true
    }

    static func label(_ value: String?) -> Bool {
        guard let value else { return false }
        return value.range(of: #"^[a-z0-9][a-z0-9-]{0,63}\z"#, options: .regularExpression) != nil
    }

    static func token(_ value: String, maximum: Int, colon: Bool = false) -> Bool {
        !value.isEmpty && value.utf8.count <= maximum && value.utf8.allSatisfy {
            (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0)
                || [UInt8(45), 46, 95].contains($0) || (colon && $0 == 58)
        }
    }
}

enum SecureIO {
    static var now: TimeInterval { ProcessInfo.processInfo.systemUptime }

    static func ready(_ descriptor: Int32, event: Int16, deadline: TimeInterval) throws {
        while now < deadline {
            var item = pollfd(fd: descriptor, events: event, revents: 0)
            let result = poll(&item, 1, Int32(min(100, max(1, (deadline - now) * 1000))))
            if result < 0 && errno == EINTR { continue }
            guard result >= 0 else { throw SecureLoginFailure(code: "transport_failed") }
            if result > 0 {
                guard item.revents & Int16(POLLNVAL) == 0 else {
                    throw SecureLoginFailure(code: "transport_failed")
                }
                return
            }
        }
        throw SecureLoginFailure(code: "transport_timeout")
    }

    static func line(_ descriptor: Int32, pending: inout Data, limit: Int, deadline: TimeInterval) throws -> Data {
        while true {
            if let newline = pending.firstIndex(of: 10) {
                let result = pending.prefix(upTo: newline)
                guard result.count <= limit else { throw SecureLoginFailure(code: "transport_response_invalid") }
                pending.removeSubrange(...newline)
                return Data(result)
            }
            guard pending.count <= limit else { throw SecureLoginFailure(code: "transport_response_invalid") }
            try ready(descriptor, event: Int16(POLLIN), deadline: deadline)
            var bytes = [UInt8](repeating: 0, count: min(4096, limit + 1 - pending.count))
            let count = Darwin.read(descriptor, &bytes, bytes.count)
            if count < 0 && errno == EINTR { continue }
            guard count > 0 else { throw SecureLoginFailure(code: "transport_closed") }
            pending.append(contentsOf: bytes.prefix(count))
        }
    }

    static func write(_ data: Data, to descriptor: Int32, deadline: TimeInterval) throws {
        _ = fcntl(descriptor, F_SETNOSIGPIPE, 1)
        let flags = fcntl(descriptor, F_GETFL)
        guard flags >= 0, fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) == 0 else {
            throw SecureLoginFailure(code: "transport_failed")
        }
        var offset = 0
        while offset < data.count {
            try ready(descriptor, event: Int16(POLLOUT), deadline: deadline)
            let count = data.withUnsafeBytes { Darwin.write(descriptor, $0.baseAddress!.advanced(by: offset), data.count - offset) }
            if count < 0 && [EINTR, EAGAIN].contains(errno) { continue }
            guard count > 0 else { throw SecureLoginFailure(code: "transport_failed") }
            offset += count
        }
    }

    static func jsonLine(_ object: [String: Any], to descriptor: Int32, deadline: TimeInterval) throws {
        var bytes = try JSONSerialization.data(withJSONObject: object)
        bytes.append(10)
        try write(bytes, to: descriptor, deadline: deadline)
    }
}

final class LoginChild {
    private let process = Process()
    private let input = Pipe()
    private let output = Pipe()
    private var pending = Data()

    init(path: String, arguments: [String], temporaryDirectory: String, discardOutput: Bool = false) throws {
        process.executableURL = URL(fileURLWithPath: path)
        process.arguments = arguments
        process.environment = ["PATH": "/usr/bin:/bin", "HOME": NSHomeDirectory(), "TMPDIR": temporaryDirectory]
        process.standardInput = input
        process.standardOutput = discardOutput ? FileHandle.nullDevice : output
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { throw SecureLoginFailure(code: "transport_start_failed") }
        try input.fileHandleForReading.close()
        try output.fileHandleForWriting.close()
    }

    var isRunning: Bool { process.isRunning }
    var exitStatus: Int32 { process.terminationStatus }

    func send(_ data: Data, deadline: TimeInterval) throws {
        guard data.count <= 262144 else { throw SecureLoginFailure(code: "invalid_credentials_input") }
        try SecureIO.write(data + Data([10]), to: input.fileHandleForWriting.fileDescriptor, deadline: deadline)
    }

    func receive(deadline: TimeInterval) throws -> [String: Any] {
        let bytes = try SecureIO.line(output.fileHandleForReading.fileDescriptor, pending: &pending,
                                      limit: 65536, deadline: deadline)
        guard let object = try JSONSerialization.jsonObject(with: bytes) as? [String: Any] else {
            throw SecureLoginFailure(code: "transport_response_invalid")
        }
        return object
    }

    func collect(deadline: TimeInterval, allowedExitCodes: Set<Int32> = [0]) throws -> Data {
        var result = Data()
        let descriptor = output.fileHandleForReading.fileDescriptor
        while true {
            try SecureIO.ready(descriptor, event: Int16(POLLIN), deadline: deadline)
            var bytes = [UInt8](repeating: 0, count: 4096)
            let count = Darwin.read(descriptor, &bytes, bytes.count)
            if count < 0 && errno == EINTR { continue }
            guard count >= 0 else { throw SecureLoginFailure(code: "transport_failed") }
            if count == 0 { break }
            result.append(contentsOf: bytes.prefix(count))
            guard result.count <= 65536 else { throw SecureLoginFailure(code: "transport_response_invalid") }
        }
        while process.isRunning && SecureIO.now < deadline { usleep(10000) }
        guard !process.isRunning, allowedExitCodes.contains(process.terminationStatus) else {
            throw SecureLoginFailure(code: "transport_failed")
        }
        return result
    }

    func stop(grace: TimeInterval = 1) {
        try? input.fileHandleForWriting.close()
        if process.isRunning {
            process.terminate()
            let deadline = SecureIO.now + grace
            while process.isRunning && SecureIO.now < deadline { usleep(10000) }
            if process.isRunning { _ = kill(process.processIdentifier, SIGKILL) }
        }
        try? output.fileHandleForReading.close()
    }
}

enum SecureLogin {
    private static var remoteJAR: String {
        "/data/local/tmp/agent-test-accounts-secure-input-\(DriverPins.androidJARSHA256).jar"
    }
    private static let androidCodes: Set<String> = ["internal_error", "authentication_unconfirmed", "automation_unavailable",
        "environment_not_unique", "environment_selection_required", "environment_unverifiable", "input_closed",
        "input_timeout", "input_too_large", "invalid_arguments", "invalid_credentials_input", "invalid_plan",
        "login_elements_not_unique", "password_input_failed", "screen_unavailable", "self_check_failed", "submit_failed",
        "tree_limit", "unsafe_login_fields", "unsafe_submit", "unsupported_sdk", "username_input_failed", "wrong_app"]

    static func run(_ plan: LoginPlan, credentials: () throws -> Data) throws -> [String: Any] {
        do {
            try verifyStorage()
            if plan.platform == "android" { return try android(plan, credentials: credentials) }
            return try ios(plan, credentials: credentials)
        } catch let error as SecureLoginFailure {
            throw error
        } catch {
            throw SecureLoginFailure(code: "secure_login_failed")
        }
    }

    static func verifyStorage() throws {
        let deadline = SecureIO.now + 5
        guard storageDirectory(DriverPins.workRoot, writable: true),
              storageDirectory(DriverPins.volumeMount, writable: false) else {
            throw SecureLoginFailure(code: "storage_not_ready")
        }
        let device = try storageDevice(DriverPins.workRoot, deadline: deadline)
        let child = try LoginChild(path: "/usr/sbin/diskutil", arguments: ["info", "-plist", device],
                                   temporaryDirectory: DriverPins.workRoot + "/tmp/")
        defer { child.stop() }
        let bytes = try child.collect(deadline: deadline)
        guard let info = try PropertyListSerialization.propertyList(from: bytes, format: nil) as? [String: Any],
              info["DeviceNode"] as? String == device,
              info["MountPoint"] as? String == DriverPins.volumeMount,
              let volumeUUID = info["VolumeUUID"] as? String,
              UUID(uuidString: volumeUUID)?.uuidString == DriverPins.volumeUUID,
              let writable = info["WritableVolume"] as? NSNumber,
              CFGetTypeID(writable) == CFBooleanGetTypeID(), writable.boolValue,
              try storageDevice(DriverPins.volumeMount, deadline: deadline) == device else {
            throw SecureLoginFailure(code: "storage_not_ready")
        }
    }

    private static func storageDirectory(_ path: String, writable: Bool) -> Bool {
        let url = URL(fileURLWithPath: path, isDirectory: true).standardizedFileURL
        guard path.hasPrefix("/"), url.path == path, url.resolvingSymlinksInPath().path == path,
              !writable || access(path, W_OK | X_OK) == 0 else { return false }
        var component = url
        while true {
            var info = stat()
            guard lstat(component.path, &info) == 0, info.st_mode & S_IFMT == S_IFDIR else { return false }
            if component.path == "/" { return true }
            component.deleteLastPathComponent()
        }
    }

    private static func storageDevice(_ path: String, deadline: TimeInterval) throws -> String {
        let child = try LoginChild(path: "/bin/df", arguments: ["-P", path],
                                   temporaryDirectory: DriverPins.workRoot + "/tmp/")
        defer { child.stop() }
        let bytes = try child.collect(deadline: deadline)
        guard let output = String(data: bytes, encoding: .utf8) else {
            throw SecureLoginFailure(code: "storage_not_ready")
        }
        var lines = output.split(omittingEmptySubsequences: false, whereSeparator: { $0.isNewline })
        if lines.last == "" { lines.removeLast() }
        guard lines.count == 2 else { throw SecureLoginFailure(code: "storage_not_ready") }
        let fields = lines[1].split(whereSeparator: { $0.isWhitespace })
        guard fields.count >= 6 else { throw SecureLoginFailure(code: "storage_not_ready") }
        let device = String(fields[0])
        guard device.range(of: #"^/dev/disk[0-9]+(?:s[0-9]+)*\z"#, options: .regularExpression) != nil else {
            throw SecureLoginFailure(code: "storage_not_ready")
        }
        return device
    }

    private static func digest(_ path: String) throws -> String {
        let url = URL(fileURLWithPath: path)
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true else {
            throw SecureLoginFailure(code: "driver_not_trusted")
        }
        let file = try FileHandle(forReadingFrom: url)
        defer { try? file.close() }
        var hash = SHA256()
        while let bytes = try file.read(upToCount: 1048576), !bytes.isEmpty { hash.update(data: bytes) }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func pin(_ path: String, expected: String) throws {
        guard expected.count == 64, try digest(path) == expected else {
            throw SecureLoginFailure(code: "driver_not_trusted")
        }
    }

    private static func adb(_ arguments: [String], deadline: TimeInterval) throws -> Data {
        try pin(DriverPins.adbPath, expected: DriverPins.adbSHA256)
        let child = try LoginChild(path: DriverPins.adbPath, arguments: arguments,
                                   temporaryDirectory: DriverPins.workRoot + "/tmp/")
        defer { child.stop() }
        return try child.collect(deadline: deadline)
    }

    private static func android(_ plan: LoginPlan, credentials: () throws -> Data) throws -> [String: Any] {
        let deviceKey = SHA256.hash(data: Data(plan.device.utf8)).map { String(format: "%02x", $0) }.joined()
        let lock = Darwin.open(DriverPins.workRoot + "/android-" + deviceKey + ".lock",
                               O_RDWR | O_CREAT | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard lock >= 0 else { throw SecureLoginFailure(code: "runner_ownership_unavailable") }
        defer { _ = close(lock) }
        guard flock(lock, LOCK_EX | LOCK_NB) == 0 else { throw SecureLoginFailure(code: "runner_busy") }
        defer { _ = flock(lock, LOCK_UN) }
        try pin(DriverPins.androidJARPath, expected: DriverPins.androidJARSHA256)
        let deadline = SecureIO.now + 45
        _ = try adb(["-s", plan.device, "push", DriverPins.androidJARPath, remoteJAR], deadline: deadline)
        let hashBytes = try adb(["-s", plan.device, "shell", "-T", "sha256sum", remoteJAR], deadline: deadline)
        guard let remoteHash = String(data: hashBytes, encoding: .utf8)?.split(whereSeparator: { $0.isWhitespace }).first,
              remoteHash == Substring(DriverPins.androidJARSHA256) else {
            throw SecureLoginFailure(code: "driver_not_trusted")
        }
        try pin(DriverPins.adbPath, expected: DriverPins.adbSHA256)
        let child = try LoginChild(path: DriverPins.adbPath,
            arguments: ["-s", plan.device, "shell", "-T", "CLASSPATH=\(remoteJAR) app_process / com.agenttestaccounts.SecureInput"],
            temporaryDirectory: DriverPins.workRoot + "/tmp/")
        defer { child.stop() }
        try child.send(JSONSerialization.data(withJSONObject: plan.metadata), deadline: deadline)
        let prepared = try androidOutcome(child.receive(deadline: deadline))
        if prepared["status"] as? String == "already_authenticated" { return prepared }
        guard prepared["status"] as? String == "ready", prepared["environment_verified"] as? Bool == true else {
            return prepared
        }
        let secret = try credentials()
        do {
            try child.send(secret, deadline: SecureIO.now + 5)
            let outcome = try androidOutcome(child.receive(deadline: SecureIO.now + max(15, Double(plan.timeout) / 1000 + 5)))
            guard outcome["status"] as? String != "ready" else {
                throw SecureLoginFailure(code: "transport_response_invalid")
            }
            return outcome
        } catch {
            return ["status": "uncertain", "code": "transport_failed", "cleanup": "unconfirmed",
                    "environment_verified": false]
        }
    }

    private static func androidOutcome(_ object: [String: Any]) throws -> [String: Any] {
        let allowedKeys: Set<String> = ["status", "code", "environment_verified", "cleanup"]
        let statuses = ["ready", "already_authenticated", "authenticated", "not_ready", "failed", "uncertain", "error"]
        guard Set(object.keys).isSubset(of: allowedKeys),
              let status = object["status"] as? String, statuses.contains(status) else {
            throw SecureLoginFailure(code: "transport_response_invalid")
        }
        if let code = object["code"] { guard let value = code as? String, androidCodes.contains(value) else {
            throw SecureLoginFailure(code: "transport_response_invalid")
        } }
        if let cleanup = object["cleanup"] { guard let value = cleanup as? String,
            ["cleared", "failed", "unavailable", "unconfirmed"].contains(value) else {
            throw SecureLoginFailure(code: "transport_response_invalid")
        } }
        if ["ready", "authenticated"].contains(status), object["environment_verified"] as? Bool != true {
            throw SecureLoginFailure(code: "transport_response_invalid")
        }
        var result: [String: Any] = ["status": status]
        if let code = object["code"] { result["code"] = code }
        if let cleanup = object["cleanup"] { result["cleanup"] = cleanup }
        if let environment = object["environment_verified"] {
            guard let verified = environment as? Bool else {
                throw SecureLoginFailure(code: "transport_response_invalid")
            }
            result["environment_verified"] = verified
        }
        if status == "already_authenticated" { result["environment_verified"] = false }
        return result
    }

    private static func ios(_ plan: LoginPlan, credentials: () throws -> Data) throws -> [String: Any] {
        guard let source = DriverPins.iosXCTestRunPath, let root = DriverPins.iosRunnerRoot,
              let expected = DriverPins.iosRunnerTreeSHA256 else {
            throw SecureLoginFailure(code: "ios_not_prepared")
        }
        try verifyTree(root, expected: expected)
        guard source.hasPrefix(root + "/") else { throw SecureLoginFailure(code: "driver_not_trusted") }
        let lock = Darwin.open(DriverPins.workRoot + "/iOSRunner.lock", O_RDWR | O_CREAT | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard lock >= 0 else { throw SecureLoginFailure(code: "runner_ownership_unavailable") }
        defer { _ = close(lock) }
        guard flock(lock, LOCK_EX | LOCK_NB) == 0 else { throw SecureLoginFailure(code: "runner_busy") }
        defer { _ = flock(lock, LOCK_UN) }
        let muxID = try USBMux.deviceID(serial: plan.device, deadline: SecureIO.now + 5)
        guard try !USBMux.runnerPresent(deviceID: muxID, deadline: SecureIO.now + 2) else {
            throw SecureLoginFailure(code: "runner_busy")
        }
        try runnerInventory(device: plan.device)
        let nonce = UUID().uuidString
        let listenerToken = UUID().uuidString
        let privateRun = try temporaryTestRun(source: source, nonce: nonce, listenerToken: listenerToken)
        defer { try? FileManager.default.removeItem(atPath: privateRun) }
        let child = try LoginChild(path: "/usr/bin/xcodebuild", arguments: ["test-without-building", "-xctestrun", privateRun,
            "-destination", "id=\(plan.device)", "-quiet", "-derivedDataPath", DriverPins.workRoot + "/IOSRuntime",
            "-resultBundlePath", privateRun + ".xcresult"],
            temporaryDirectory: DriverPins.workRoot + "/tmp/", discardOutput: true)
        do {
            let outcome = try iosSession(plan, deviceID: muxID, nonce: nonce, listenerToken: listenerToken, child: child, credentials: credentials)
            return finishIOS(outcome, deviceID: muxID, child: child)
        } catch {
            let code = (error as? SecureLoginFailure)?.code ?? "secure_login_failed"
            return finishIOS(["status": "error", "code": code], deviceID: muxID, child: child)
        }
    }

    private static func iosSession(_ plan: LoginPlan, deviceID: Int, nonce: String, listenerToken: String, child: LoginChild,
                                    credentials: () throws -> Data) throws -> [String: Any] {
        let startupDeadline = SecureIO.now + 120
        var prepared: [String: Any]?
        var activated = false
        while SecureIO.now < startupDeadline && child.isRunning {
            do {
                prepared = try iosRequest(plan, deviceID: deviceID, nonce: nonce, listenerToken: listenerToken, action: "preflight", secret: nil,
                                          deadline: min(startupDeadline, SecureIO.now + 5))
                if prepared?["code"] as? String == "APP_NOT_FOREGROUND", !activated {
                    activated = true
                    let activation = try LoginChild(path: "/usr/bin/xcrun", arguments: ["devicectl", "device", "process",
                        "launch", "--device", plan.device, plan.appID], temporaryDirectory: DriverPins.workRoot + "/tmp/",
                        discardOutput: true)
                    defer { activation.stop() }
                    _ = try activation.collect(deadline: min(startupDeadline, SecureIO.now + 15))
                    continue
                }
                if prepared?["code"] as? String == "APP_NOT_FOREGROUND", activated {
                    usleep(100000)
                    continue
                }
                break
            } catch let failure as SecureLoginFailure where failure.code == "transport_not_trusted" {
                throw failure
            } catch { usleep(100000) }
        }
        guard let prepared else { throw SecureLoginFailure(code: "ios_runner_unavailable") }
        if prepared["status"] as? String == "already_authenticated" { return prepared }
        guard prepared["status"] as? String == "ready", prepared["environment_verified"] as? Bool == true else {
            return prepared
        }
        guard child.isRunning else { throw SecureLoginFailure(code: "ios_runner_unavailable") }
        let secret = try credentials()
        do {
            return try iosRequest(plan, deviceID: deviceID, nonce: nonce, listenerToken: listenerToken, action: "login", secret: secret,
                                  deadline: SecureIO.now + Double(plan.timeout) / 1000 + 20)
        } catch {
            return ["status": "uncertain", "code": "transport_failed", "cleanup": "unconfirmed",
                    "environment_verified": false]
        }
    }

    private static func finishIOS(_ outcome: [String: Any], deviceID: Int, child: LoginChild) -> [String: Any] {
        child.stop(grace: 5)
        let deadline = SecureIO.now + 3
        while SecureIO.now < deadline {
            do {
                if try !USBMux.runnerPresent(deviceID: deviceID, deadline: min(deadline, SecureIO.now + 1)) {
                    return outcome
                }
            } catch { break }
            usleep(100000)
        }
        var result: [String: Any] = ["status": "uncertain", "code": "ios_runner_cleanup_unconfirmed",
                                    "environment_verified": outcome["environment_verified"] as? Bool ?? false]
        if let cleanup = outcome["cleanup"] { result["cleanup"] = cleanup }
        return result
    }

    private static func runnerInventory(device: String) throws {
        let inventory = try LoginChild(path: "/usr/bin/pgrep", arguments: ["-lx", "xcodebuild"],
            temporaryDirectory: DriverPins.workRoot + "/tmp/")
        defer { inventory.stop() }
        let listing = try inventory.collect(deadline: SecureIO.now + 5, allowedExitCodes: [0, 1])
        guard let processes = String(data: listing, encoding: .utf8) else {
            throw SecureLoginFailure(code: "runner_inventory_unavailable")
        }
        for line in processes.split(separator: "\n") {
            guard let first = line.split(whereSeparator: { $0.isWhitespace }).first, Int32(first) != nil else {
                throw SecureLoginFailure(code: "runner_inventory_unavailable")
            }
            let inspect = try LoginChild(path: "/bin/ps", arguments: ["-p", String(first), "-o", "command="],
                temporaryDirectory: DriverPins.workRoot + "/tmp/")
            defer { inspect.stop() }
            let bytes = try inspect.collect(deadline: SecureIO.now + 5, allowedExitCodes: [0, 1])
            guard let command = String(data: bytes, encoding: .utf8) else {
                throw SecureLoginFailure(code: "runner_inventory_unavailable")
            }
            if command.contains(device) || command.contains(DriverPins.workRoot + "/IOSRuntime") {
                throw SecureLoginFailure(code: "runner_busy")
            }
        }
        let path = DriverPins.workRoot + "/processes-" + UUID().uuidString + ".json"
        defer { try? FileManager.default.removeItem(atPath: path) }
        let child = try LoginChild(path: "/usr/bin/xcrun", arguments: ["devicectl", "device", "info", "processes",
            "--device", device, "--json-output", path], temporaryDirectory: DriverPins.workRoot + "/tmp/",
            discardOutput: true)
        defer { child.stop() }
        _ = try child.collect(deadline: SecureIO.now + 15)
        let attributes = try FileManager.default.attributesOfItem(atPath: path)
        guard let size = attributes[.size] as? NSNumber, size.intValue <= 4194304 else {
            throw SecureLoginFailure(code: "runner_inventory_unavailable")
        }
        let root = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path)))
        func hasRunner(_ value: Any) -> Bool {
            if let string = value as? String {
                let name = string.lowercased()
                return ["agentdevicerunner", "xctrunner", "xctest", "uitests"].contains { name.contains($0) }
            }
            if let array = value as? [Any] { return array.contains(where: hasRunner) }
            if let object = value as? [String: Any] { return object.values.contains(where: hasRunner) }
            return false
        }
        guard let object = root as? [String: Any], object["result"] is [String: Any] else {
            throw SecureLoginFailure(code: "runner_inventory_unavailable")
        }
        guard !hasRunner(object["result"]!) else { throw SecureLoginFailure(code: "runner_busy") }
    }

    private static func verifyTree(_ root: String, expected: String) throws {
        let base = URL(fileURLWithPath: root).standardizedFileURL
        guard let iterator = FileManager.default.enumerator(at: base, includingPropertiesForKeys:
            [.isRegularFileKey, .isSymbolicLinkKey], options: []) else {
            throw SecureLoginFailure(code: "driver_not_trusted")
        }
        var files: [(String, String)] = []
        for case let file as URL in iterator {
            let values = try file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
            guard values.isSymbolicLink != true else { throw SecureLoginFailure(code: "driver_not_trusted") }
            if values.isRegularFile == true {
                guard file.path.hasPrefix(base.path + "/") else { throw SecureLoginFailure(code: "driver_not_trusted") }
                files.append((String(file.path.dropFirst(base.path.count + 1)), try digest(file.path)))
            }
        }
        files.sort { Array($0.0.utf8).lexicographicallyPrecedes(Array($1.0.utf8)) }
        let summary = files.map { $0.0 + "\0" + $0.1 + "\n" }.joined()
        let actual = SHA256.hash(data: Data(summary.utf8)).map { String(format: "%02x", $0) }.joined()
        guard !files.isEmpty, actual == expected else { throw SecureLoginFailure(code: "driver_not_trusted") }
    }

    private static func temporaryTestRun(source: String, nonce: String, listenerToken: String) throws -> String {
        let data = try Data(contentsOf: URL(fileURLWithPath: source))
        let original = try PropertyListSerialization.propertyList(from: data, format: nil)
        let testRoot = URL(fileURLWithPath: source).deletingLastPathComponent().path
        func patch(_ value: Any) -> Any {
            if let string = value as? String { return string.replacingOccurrences(of: "__TESTROOT__", with: testRoot) }
            if let array = value as? [Any] { return array.map(patch) }
            guard var dictionary = value as? [String: Any] else { return value }
            dictionary = dictionary.mapValues(patch)
            if dictionary["TestBundlePath"] != nil {
                var environment = dictionary["EnvironmentVariables"] as? [String: String] ?? [:]
                environment["AGENT_TEST_ACCOUNTS_NONCE"] = nonce
                environment["AGENT_DEVICE_RUNNER_TOKEN"] = listenerToken
                environment["AGENT_DEVICE_RUNNER_PORT"] = "19087"
                dictionary["EnvironmentVariables"] = environment
            }
            return dictionary
        }
        let modified = try PropertyListSerialization.data(fromPropertyList: patch(original), format: .binary, options: 0)
        let path = DriverPins.workRoot + "/runner-" + UUID().uuidString + ".xctestrun"
        let descriptor = Darwin.open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else { throw SecureLoginFailure(code: "runner_ownership_unavailable") }
        defer { _ = close(descriptor) }
        try SecureIO.write(modified, to: descriptor, deadline: SecureIO.now + 5)
        return path
    }

    private static func iosRequest(_ plan: LoginPlan, deviceID: Int, nonce: String, listenerToken: String, action: String,
                                   secret: Data?, deadline: TimeInterval) throws -> [String: Any] {
        var request: [String: Any] = ["secureAccountProtocol": "agent-test-accounts-v1", "nonce": nonce,
                                     "action": action, "plan": plan.metadata]
        if let secret {
            guard let fields = try JSONSerialization.jsonObject(with: secret) as? [String: String],
                  Set(fields.keys) == ["username", "password"] else {
                throw SecureLoginFailure(code: "invalid_credentials_input")
            }
            request["username"] = fields["username"]
            request["password"] = fields["password"]
        }
        let key = SymmetricKey(data: SHA256.hash(data: Data(nonce.utf8)))
        let plaintext = try JSONSerialization.data(withJSONObject: request)
        guard let combined = try AES.GCM.seal(plaintext, using: key).combined else {
            throw SecureLoginFailure(code: "transport_not_trusted")
        }
        let envelope: [String: String] = ["secureAccountProtocol": "agent-test-accounts-v1",
                                        "sealed_request": combined.base64EncodedString()]
        let response = try USBMux.post(deviceID: deviceID, body: JSONSerialization.data(withJSONObject: envelope),
                                       listenerToken: listenerToken, deadline: deadline)
        let clearResponse = try openResponse(response, key: key)
        guard let object = try JSONSerialization.jsonObject(with: clearResponse) as? [String: Any],
              Set(object.keys).isSubset(of: ["protocol", "status", "code", "environment_verified", "cleanup"]),
              object["protocol"] as? String == "agent-test-accounts-v1",
              let status = object["status"] as? String,
              ["ready", "already_authenticated", "authenticated", "login_failed_or_unverified", "error"].contains(status) else {
            throw SecureLoginFailure(code: "transport_response_invalid")
        }
        guard (action == "preflight" ? ["ready", "already_authenticated", "error"] :
            ["authenticated", "login_failed_or_unverified", "error"]).contains(status) else {
            throw SecureLoginFailure(code: "transport_response_invalid")
        }
        var safe: [String: Any] = ["status": status == "login_failed_or_unverified" ? "failed" : status]
        if let verified = object["environment_verified"] as? Bool { safe["environment_verified"] = verified }
        if status == "already_authenticated" { safe["environment_verified"] = false }
        if ["ready", "authenticated"].contains(status), safe["environment_verified"] as? Bool != true {
            throw SecureLoginFailure(code: "transport_response_invalid")
        }
        if let code = object["code"] as? String {
            guard iosCodes.contains(code) else { throw SecureLoginFailure(code: "transport_response_invalid") }
            safe["code"] = code
        }
        if let cleanup = object["cleanup"] as? String {
            guard ["cleared", "failed", "unavailable", "unconfirmed"].contains(cleanup) else {
                throw SecureLoginFailure(code: "transport_response_invalid")
            }
            safe["cleanup"] = cleanup
        }
        return safe
    }

    private static func openResponse(_ bytes: Data, key: SymmetricKey) throws -> Data {
        do {
            guard let envelope = try JSONSerialization.jsonObject(with: bytes) as? [String: String],
                  Set(envelope.keys) == ["protocol", "sealed_response"],
                  envelope["protocol"] == "agent-test-accounts-v1",
                  let encoded = envelope["sealed_response"], let combined = Data(base64Encoded: encoded),
                  combined.base64EncodedString() == encoded else {
                throw SecureLoginFailure(code: "transport_not_trusted")
            }
            let box = try AES.GCM.SealedBox(combined: combined)
            return try AES.GCM.open(box, using: key)
        } catch {
            throw SecureLoginFailure(code: "transport_not_trusted")
        }
    }

    private static let iosCodes: Set<String> = ["LOGIN_VIEW_READY", "SESSION_REUSED", "SUCCESS_OBSERVED", "SUCCESS_NOT_OBSERVED",
        "NO_AUTHORIZED_PROTOCOL", "INVALID_CREDENTIALS", "RUNNER_BUSY", "UI_FAILURE", "APP_NOT_FOREGROUND",
        "LOGIN_VIEW_NOT_READY", "ENVIRONMENT_NOT_VERIFIED", "INPUT_UNAVAILABLE"]

    static func safetySelfTest() throws {
        let metadata: [String: Any] = ["schema_version": 1, "name": "synthetic", "platform": "android",
            "app_id": "org.example.sample", "environment": "staging", "role": "tester", "timeout_ms": 10000,
            "username_selector": ["label": "Username"], "password_selector": ["label": "Password"],
            "submit_selector": ["label": "Sign in"], "success_selectors": [["label": "Home"]],
            "environment_selector": ["label": "Staging"]]
        guard LoginPlan.validProfile(metadata) else { throw SecureLoginFailure(code: "login_safety_self_test_failed") }
        for changed in [metadata.merging(["launch_arguments": ["--server"]]) { _, new in new },
                        metadata.merging(["server_url": "http://invalid.invalid"]) { _, new in new },
                        metadata.merging(["username_selector": ["label": "Password"]]) { _, new in new },
                        metadata.merging(["environment_selector": ["label": "Sign in"]]) { _, new in new }] {
            guard !LoginPlan.validProfile(changed) else { throw SecureLoginFailure(code: "login_safety_self_test_failed") }
        }
        var rejected = false
        do { _ = try androidOutcome(["status": "failed", "code": "untrusted_raw_response"]) } catch { rejected = true }
        guard rejected else { throw SecureLoginFailure(code: "login_safety_self_test_failed") }
        let key = SymmetricKey(size: .bits256)
        let fixture = Data("synthetic-response".utf8)
        guard var encrypted = try AES.GCM.seal(fixture, using: key).combined else {
            throw SecureLoginFailure(code: "login_safety_self_test_failed")
        }
        let valid = try JSONSerialization.data(withJSONObject: ["protocol": "agent-test-accounts-v1",
                                                                "sealed_response": encrypted.base64EncodedString()])
        guard try openResponse(valid, key: key) == fixture else {
            throw SecureLoginFailure(code: "login_safety_self_test_failed")
        }
        encrypted[encrypted.count - 1] ^= 1
        let tampered = try JSONSerialization.data(withJSONObject: ["protocol": "agent-test-accounts-v1",
                                                                   "sealed_response": encrypted.base64EncodedString()])
        rejected = false
        do { _ = try openResponse(tampered, key: key) } catch { rejected = true }
        guard rejected else { throw SecureLoginFailure(code: "login_safety_self_test_failed") }
    }
}
