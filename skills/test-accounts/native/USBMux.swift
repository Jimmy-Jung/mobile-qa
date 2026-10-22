// Created by JunyoungJung on 2026-10-20.

import Darwin
import Foundation

enum USBMux {
    private static let port: UInt16 = 19087

    static func deviceID(serial: String, deadline: TimeInterval) throws -> Int {
        let descriptor = try socket(deadline: deadline)
        defer { _ = close(descriptor) }
        let response = try message(descriptor, values: ["MessageType": "ListDevices"], deadline: deadline)
        guard let devices = response["DeviceList"] as? [[String: Any]] else {
            throw SecureLoginFailure(code: "usb_device_unavailable")
        }
        let matches = devices.compactMap { entry -> Int? in
            guard let properties = entry["Properties"] as? [String: Any],
                  let rawSerial = properties["SerialNumber"] as? String,
                  properties["ConnectionType"] as? String == "USB",
                  let identifier = (properties["DeviceID"] ?? entry["DeviceID"]) as? Int,
                  identifier > 0 else { return nil }
            let normalized = rawSerial.utf8.count == 24
                ? String(rawSerial.prefix(8)) + "-" + String(rawSerial.dropFirst(8)) : rawSerial
            guard normalized == serial,
                  (entry["DeviceID"] as? Int).map({ $0 == identifier }) ?? true else { return nil }
            return identifier
        }
        guard matches.count == 1, let identifier = matches.first else {
            throw SecureLoginFailure(code: "usb_device_unavailable")
        }
        return identifier
    }

    static func runnerPresent(deviceID: Int, deadline: TimeInterval) throws -> Bool {
        let descriptor = try socket(deadline: deadline)
        defer { _ = close(descriptor) }
        let response = try message(descriptor, values: ["MessageType": "Connect", "DeviceID": deviceID,
            "PortNumber": Int(port.bigEndian)], deadline: deadline)
        guard let number = response["Number"] as? Int else {
            throw SecureLoginFailure(code: "usb_protocol_invalid")
        }
        if number == 0 { return true }
        guard number == 3 else { throw SecureLoginFailure(code: "usb_device_unavailable") }
        return false
    }

    static func commandRequest(body: Data, listenerToken: String) throws -> Data {
        guard body.count <= 262144 else { throw SecureLoginFailure(code: "invalid_credentials_input") }
        guard let token = UUID(uuidString: listenerToken), token.uuidString == listenerToken else {
            throw SecureLoginFailure(code: "transport_not_trusted")
        }
        let header = "POST /command HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nx-agent-device-runner-token: \(listenerToken)\r\nContent-Length: \(body.count)\r\nConnection: close\r\n\r\n"
        return Data(header.utf8) + body
    }

    static func post(deviceID: Int, body: Data, listenerToken: String, deadline: TimeInterval) throws -> Data {
        let packet = try commandRequest(body: body, listenerToken: listenerToken)
        let descriptor = try socket(deadline: deadline)
        defer { _ = close(descriptor) }
        let response = try message(descriptor, values: ["MessageType": "Connect", "DeviceID": deviceID,
            "PortNumber": Int(port.bigEndian)], deadline: deadline)
        guard response["Number"] as? Int == 0 else { throw SecureLoginFailure(code: "ios_runner_unavailable") }
        try SecureIO.write(packet, to: descriptor, deadline: deadline)
        var incoming = Data()
        let separator = Data([13, 10, 13, 10])
        while incoming.range(of: separator) == nil {
            incoming.append(try readSome(descriptor, maximum: 4096, deadline: deadline))
            guard incoming.count <= 16384 else { throw SecureLoginFailure(code: "transport_response_invalid") }
        }
        guard let boundary = incoming.range(of: separator),
              let headers = String(data: incoming[..<boundary.lowerBound], encoding: .ascii) else {
            throw SecureLoginFailure(code: "transport_response_invalid")
        }
        let length = try responseBodyLength(headers)
        var result = Data(incoming[boundary.upperBound...])
        guard result.count <= length else { throw SecureLoginFailure(code: "transport_response_invalid") }
        while result.count < length {
            result.append(try readSome(descriptor, maximum: min(4096, length - result.count), deadline: deadline))
        }
        return result
    }

    static func responseBodyLength(_ headers: String) throws -> Int {
        let lines = headers.components(separatedBy: "\r\n")
        guard let status = lines.first, status.hasPrefix("HTTP/1.1 200 ") || status.hasPrefix("HTTP/1.0 200 ") else {
            throw SecureLoginFailure(code: "transport_response_invalid")
        }
        var length: Int?
        var authenticated: Bool?
        for line in lines.dropFirst() {
            let pair = line.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
            guard pair.count == 2 else { throw SecureLoginFailure(code: "transport_response_invalid") }
            let name = pair[0].lowercased()
            let value = pair[1].trimmingCharacters(in: .whitespaces)
            guard name != "transfer-encoding" else { throw SecureLoginFailure(code: "transport_response_invalid") }
            if name == "content-length" {
                guard length == nil, let count = Int(value), (1...65536).contains(count) else {
                    throw SecureLoginFailure(code: "transport_response_invalid")
                }
                length = count
            }
            if name == "x-agent-device-runner-auth" {
                guard authenticated == nil, value == "required" else {
                    throw SecureLoginFailure(code: "transport_not_trusted")
                }
                authenticated = true
            }
        }
        guard authenticated == true else { throw SecureLoginFailure(code: "transport_not_trusted") }
        guard let length else { throw SecureLoginFailure(code: "transport_response_invalid") }
        return length
    }

    private static func socket(deadline: TimeInterval) throws -> Int32 {
        let descriptor = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard descriptor >= 0 else { throw SecureLoginFailure(code: "usb_unavailable") }
        do {
            var preventSignal: Int32 = 1
            guard setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &preventSignal,
                             socklen_t(MemoryLayout.size(ofValue: preventSignal))) == 0,
                  fcntl(descriptor, F_SETFL, O_NONBLOCK) == 0 else {
                throw SecureLoginFailure(code: "usb_unavailable")
            }
            var address = sockaddr_un()
            address.sun_family = sa_family_t(AF_UNIX)
            address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
            let path = Array("/var/run/usbmuxd".utf8) + [0]
            withUnsafeMutableBytes(of: &address.sun_path) { $0.copyBytes(from: path) }
            let result = withUnsafePointer(to: &address) { pointer in
                pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    connect(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            guard result == 0 || errno == EINPROGRESS else { throw SecureLoginFailure(code: "usb_unavailable") }
            if result != 0 {
                try SecureIO.ready(descriptor, event: Int16(POLLOUT), deadline: deadline)
                var error: Int32 = 0
                var size = socklen_t(MemoryLayout.size(ofValue: error))
                guard getsockopt(descriptor, SOL_SOCKET, SO_ERROR, &error, &size) == 0, error == 0 else {
                    throw SecureLoginFailure(code: "usb_unavailable")
                }
            }
            return descriptor
        } catch {
            _ = close(descriptor)
            throw error
        }
    }

    private static func message(_ descriptor: Int32, values: [String: Any], deadline: TimeInterval) throws -> [String: Any] {
        let metadata = values.merging(["ClientVersionString": "agent-test-accounts-v1", "ProgName": "keychain-helper",
                                      "kLibUSBMuxVersion": 3]) { current, _ in current }
        let body = try PropertyListSerialization.data(fromPropertyList: metadata, format: .xml, options: 0)
        var packet = Data()
        for value in [UInt32(body.count + 16), 1, 8, 1] {
            var little = value.littleEndian
            withUnsafeBytes(of: &little) { packet.append(contentsOf: $0) }
        }
        packet.append(body)
        try SecureIO.write(packet, to: descriptor, deadline: deadline)
        let header = try exact(descriptor, count: 16, deadline: deadline)
        let fields = stride(from: 0, to: 16, by: 4).map { index -> UInt32 in
            header[index..<(index + 4)].enumerated().reduce(0) { $0 | (UInt32($1.element) << ($1.offset * 8)) }
        }
        guard fields[0] > 16, fields[0] <= 1048576, fields[1] == 1, fields[2] == 8, fields[3] == 1 else {
            throw SecureLoginFailure(code: "usb_protocol_invalid")
        }
        let bytes = try exact(descriptor, count: Int(fields[0]) - 16, deadline: deadline)
        guard let response = try PropertyListSerialization.propertyList(from: bytes, format: nil) as? [String: Any] else {
            throw SecureLoginFailure(code: "usb_protocol_invalid")
        }
        return response
    }

    private static func exact(_ descriptor: Int32, count: Int, deadline: TimeInterval) throws -> Data {
        var data = Data()
        while data.count < count {
            data.append(try readSome(descriptor, maximum: min(4096, count - data.count), deadline: deadline))
        }
        return data
    }

    private static func readSome(_ descriptor: Int32, maximum: Int, deadline: TimeInterval) throws -> Data {
        while true {
            try SecureIO.ready(descriptor, event: Int16(POLLIN), deadline: deadline)
            var buffer = [UInt8](repeating: 0, count: maximum)
            let count = Darwin.read(descriptor, &buffer, buffer.count)
            if count < 0 && [EINTR, EAGAIN].contains(errno) { continue }
            guard count > 0 else { throw SecureLoginFailure(code: "transport_closed") }
            return Data(buffer.prefix(count))
        }
    }
}
