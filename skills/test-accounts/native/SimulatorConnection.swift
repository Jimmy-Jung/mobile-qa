// Author: JunyoungJung · 2026-10-27.

import CoreFoundation
import CryptoKit
import Darwin
import Foundation

/// The local Simulator runner is reached only through authenticated loopback IPC.
enum SimulatorConnection {
    static let port: UInt16 = 19087
    static let runnerBundle = "com.agenttestaccounts.simulator.uitests.xctrunner"

    struct AppIdentity: Equatable {
        let bundle: String
        let files: [String: String]
    }

    static func appIdentity(device: String, appID: String) throws -> AppIdentity {
        let child = try LoginChild(path: "/usr/bin/xcrun", arguments: ["simctl", "get_app_container", device, appID, "app"],
            temporaryDirectory: DriverPins.workRoot + "/tmp/", simulator: true)
        defer { child.stop() }
        let bytes = try child.collect(deadline: SecureIO.now + 5)
        guard let result = String(data: bytes, encoding: .utf8), result.hasSuffix("\n") else {
            throw SecureLoginFailure(code: "driver_not_trusted")
        }
        let path = String(result.dropLast())
        let bundle = URL(fileURLWithPath: path, isDirectory: true)
        guard path.hasPrefix("/"), bundle.standardizedFileURL.path == path,
              bundle.resolvingSymlinksInPath().path == path, !path.contains("\n"), !path.contains("\r") else {
            throw SecureLoginFailure(code: "driver_not_trusted")
        }
        let infoURL = bundle.appendingPathComponent("Info.plist")
        let infoFile = try checkedFile(infoURL, maximum: 1048576)
        defer { try? infoFile.close() }
        guard let infoData = try infoFile.read(upToCount: 1048577), infoData.count <= 1048576,
              let plist = try? PropertyListSerialization.propertyList(from: infoData, format: nil) as? [String: Any],
              plist["CFBundleIdentifier"] as? String == appID,
              let executable = plist["CFBundleExecutable"] as? String,
              executable.range(of: #"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,199}\z"#, options: .regularExpression) != nil else {
            throw SecureLoginFailure(code: "driver_not_trusted")
        }
        let infoHash = SHA256.hash(data: infoData).map { String(format: "%02x", $0) }.joined()
        var files = ["Info.plist": infoHash, executable: try fileDigest(bundle.appendingPathComponent(executable))]
        let debugName = executable + ".debug.dylib"
        let debug = bundle.appendingPathComponent(debugName)
        var attributes = stat()
        if lstat(debug.path, &attributes) == 0 {
            files[debugName] = try fileDigest(debug)
        } else if errno != ENOENT {
            throw SecureLoginFailure(code: "driver_not_trusted")
        }
        return AppIdentity(bundle: path, files: files)
    }

    private static func fileDigest(_ url: URL) throws -> String {
        let file = try checkedFile(url, maximum: 536870912)
        defer { try? file.close() }
        var digest = SHA256()
        var total = 0
        while let bytes = try file.read(upToCount: 1048576), !bytes.isEmpty {
            total += bytes.count
            guard total <= 536870912 else { throw SecureLoginFailure(code: "driver_not_trusted") }
            digest.update(data: bytes)
        }
        return digest.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func checkedFile(_ url: URL, maximum: Int64) throws -> FileHandle {
        guard url.resolvingSymlinksInPath().path == url.path else { throw SecureLoginFailure(code: "driver_not_trusted") }
        let descriptor = Darwin.open(url.path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK)
        guard descriptor >= 0 else { throw SecureLoginFailure(code: "driver_not_trusted") }
        let file = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_uid == getuid(), info.st_size >= 0, info.st_size <= maximum else {
            try? file.close()
            throw SecureLoginFailure(code: "driver_not_trusted")
        }
        return file
    }

    static func isSimulator(device: String) throws -> Bool {
        guard UUID(uuidString: device) != nil else { return false }
        let child = try LoginChild(path: "/usr/bin/xcrun", arguments: ["simctl", "list", "devices", "--json"],
            temporaryDirectory: DriverPins.workRoot + "/tmp/", simulator: true)
        defer { child.stop() }
        let bytes = try child.collect(deadline: SecureIO.now + 10)
        guard let root = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
              let inventory = root["devices"] as? [String: [[String: Any]]] else {
            throw SecureLoginFailure(code: "ios_target_unverified")
        }
        let matches = inventory.values.flatMap { $0 }.filter {
            ($0["udid"] as? String)?.caseInsensitiveCompare(device) == .orderedSame
        }
        guard matches.count == 1, let target = matches.first,
              let available = target["isAvailable"] as? NSNumber,
              CFGetTypeID(available) == CFBooleanGetTypeID(), let state = target["state"] as? String else {
            throw SecureLoginFailure(code: "ios_target_unverified")
        }
        guard available.boolValue, state == "Booted" else {
            throw SecureLoginFailure(code: "simulator_not_booted")
        }
        return true
    }

    static func appProcessID(device: String, appID: String) throws -> Int32 {
        guard let identifier = try processID(device: device, appID: appID) else {
            throw SecureLoginFailure(code: "app_instance_changed")
        }
        return identifier
    }

    static func processID(device: String, appID: String) throws -> Int32? {
        let listing = try processes(device: device)
        let prefix = "UIKitApplication:" + appID + "["
        let identifiers = listing.split(separator: "\n").compactMap { line -> Int32? in
            let fields = line.split(whereSeparator: { $0.isWhitespace })
            guard fields.count == 3, fields[2].hasPrefix(prefix),
                  let identifier = Int32(fields[0]), identifier > 0 else { return nil }
            return identifier
        }
        guard identifiers.count <= 1 else {
            throw SecureLoginFailure(code: "runner_inventory_unavailable")
        }
        return identifiers.first
    }

    static func runnerPresent(device: String) throws -> Bool {
        try processes(device: device).split(separator: "\n").contains { line in
            let fields = line.split(whereSeparator: { $0.isWhitespace })
            guard fields.count == 3, let identifier = Int32(fields[0]), identifier > 0 else { return false }
            let label = fields[2].lowercased()
            return ["agentdevicerunner", "xctrunner", "xctest", "uitests"].contains { label.contains($0) }
        }
    }

    private static func processes(device: String) throws -> String {
        let child = try LoginChild(path: "/usr/bin/xcrun", arguments: ["simctl", "spawn", device, "launchctl", "list"],
            temporaryDirectory: DriverPins.workRoot + "/tmp/", simulator: true)
        defer { child.stop() }
        let bytes = try child.collect(deadline: SecureIO.now + 5)
        guard let listing = String(data: bytes, encoding: .utf8) else {
            throw SecureLoginFailure(code: "runner_inventory_unavailable")
        }
        return listing
    }

    static func listenerPresent(deadline: TimeInterval) throws -> Bool {
        do {
            let descriptor = try socket(deadline: deadline)
            _ = close(descriptor)
            return true
        } catch let failure as SecureLoginFailure where failure.code == "ios_runner_unavailable" {
            return false
        }
    }

    static func post(body: Data, listenerToken: String, deadline: TimeInterval) throws -> Data {
        let packet = try USBMux.commandRequest(body: body, listenerToken: listenerToken)
        let descriptor = try socket(deadline: deadline)
        defer { _ = close(descriptor) }
        try SecureIO.write(packet, to: descriptor, deadline: deadline)
        return try USBMux.readResponse(descriptor, deadline: deadline)
    }

    static func terminateRunner(device: String, expectedPID: Int32) throws {
        guard let current = try processID(device: device, appID: runnerBundle) else { return }
        guard current == expectedPID else { throw SecureLoginFailure(code: "runner_ownership_unavailable") }
        let child = try LoginChild(path: "/usr/bin/xcrun", arguments: ["simctl", "terminate", device, runnerBundle],
            temporaryDirectory: DriverPins.workRoot + "/tmp/", discardOutput: true, simulator: true)
        defer { child.stop() }
        _ = try child.collect(deadline: SecureIO.now + 5, allowedExitCodes: [0, 3])
    }

    private static func socket(deadline: TimeInterval) throws -> Int32 {
        let descriptor = Darwin.socket(AF_INET, SOCK_STREAM, 0)
        guard descriptor >= 0 else { throw SecureLoginFailure(code: "transport_failed") }
        do {
            var preventSignal: Int32 = 1
            guard setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &preventSignal,
                             socklen_t(MemoryLayout.size(ofValue: preventSignal))) == 0,
                  fcntl(descriptor, F_SETFL, O_NONBLOCK) == 0,
                  fcntl(descriptor, F_SETFD, FD_CLOEXEC) == 0 else {
                throw SecureLoginFailure(code: "transport_failed")
            }
            var address = sockaddr_in()
            address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
            address.sin_family = sa_family_t(AF_INET)
            address.sin_port = port.bigEndian
            address.sin_addr = in_addr(s_addr: UInt32(INADDR_LOOPBACK).bigEndian)
            let result = withUnsafePointer(to: &address) { pointer in
                pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    connect(descriptor, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
            guard result == 0 || errno == EINPROGRESS else {
                throw SecureLoginFailure(code: errno == ECONNREFUSED ? "ios_runner_unavailable" : "transport_failed")
            }
            if result != 0 {
                try SecureIO.ready(descriptor, event: Int16(POLLOUT), deadline: deadline)
                var connectionError: Int32 = 0
                var size = socklen_t(MemoryLayout.size(ofValue: connectionError))
                guard getsockopt(descriptor, SOL_SOCKET, SO_ERROR, &connectionError, &size) == 0 else {
                    throw SecureLoginFailure(code: "transport_failed")
                }
                guard connectionError == 0 else {
                    throw SecureLoginFailure(code: connectionError == ECONNREFUSED ? "ios_runner_unavailable" : "transport_failed")
                }
            }
            return descriptor
        } catch {
            _ = close(descriptor)
            throw error
        }
    }
}
