"""Authenticated listener transport contracts. Author: JunyoungJung · 2026-10-22."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NATIVE = sys.platform == "darwin" and shutil.which("xcrun") and os.environ.get("MOBILE_QA_NATIVE_TESTS") == "1"


def section(source, start, end):
    return start + source.split(start, 1)[1].split(end, 1)[0]


class ListenerProtocolTests(unittest.TestCase):
    def test_private_token_is_independent_and_sendable_completion_is_preserved(self):
        native = (ROOT / "native/SecureLogin.swift").read_text()
        runner = (ROOT / "ios/RunnerTests+AccountLogin.swift").read_text()
        self.assertTrue("let listenerToken = UUID().uuidString" in native, "Fresh independent listener token is required")
        self.assertTrue("temporaryTestRun(source: source, nonce: nonce, listenerToken: listenerToken)" in native, "Listener token must enter the private runfile")
        self.assertTrue('environment["AGENT_DEVICE_RUNNER_TOKEN"] = listenerToken' in native, "Listener token must enter the runner environment")
        session = section(native, "    private static func iosSession(", "    private static func finishIOS(")
        self.assertEqual(session.count("listenerToken: listenerToken"), 2)
        request = section(native, "    private static func iosRequest(", "    private static func openResponse(")
        self.assertTrue("listenerToken: listenerToken" in request)
        self.assertTrue("AES.GCM.seal" in request)
        self.assertTrue("completion: @escaping @Sendable" in runner)
        self.assertTrue("X-Agent-Device-Runner-Auth: required" in runner)
        arguments = section(native, '        let child = try LoginChild(path: "/usr/bin/xcodebuild"', "        do {")
        self.assertNotIn("listenerToken", arguments)
        self.assertNotIn("nonce", arguments)

    @unittest.skipUnless(NATIVE, "Opt in to isolated Swift tests with MOBILE_QA_NATIVE_TESTS=1")
    def test_http_command_header_and_authenticated_response(self):
        mux = (ROOT / "native/USBMux.swift").read_text()
        request = section(mux, "    static func commandRequest(", "    static func post(")
        response = section(mux, "    static func responseBodyLength(", "    private static func socket(")
        checks = r'''
let fixtureToken = "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"
let body = Data([0, 255, 65, 66])
let request = try! USBMux.commandRequest(body: body, listenerToken: fixtureToken)
let boundary = request.range(of: Data([13, 10, 13, 10]))!
let headers = String(decoding: request[..<boundary.lowerBound], as: UTF8.self)
assert(headers.hasPrefix("POST /command HTTP/1.1\r\n"))
assert(headers.components(separatedBy: "\r\n").filter { $0.hasPrefix("x-agent-device-runner-token:") } == ["x-agent-device-runner-token: " + fixtureToken])
assert(headers.contains("Content-Length: 4"))
assert(Data(request[boundary.upperBound...]) == body)
for invalid in ["", "short", fixtureToken + "\r\nInjected: value", fixtureToken.lowercased(), String(repeating: "a", count: 64)] {
  var rejected = false
  do { _ = try USBMux.commandRequest(body: body, listenerToken: invalid) } catch { rejected = true }
  assert(rejected)
}
var oversizedRejected = false
do { _ = try USBMux.commandRequest(body: Data(repeating: 0, count: 262145), listenerToken: fixtureToken) } catch { oversizedRejected = true }
assert(oversizedRejected)
let valid = "HTTP/1.1 200 OK\r\nContent-Length: 5\r\nX-Agent-Device-Runner-Auth: required"
assert(try! USBMux.responseBodyLength(valid) == 5)
for invalid in [
  "HTTP/1.1 200 OK\r\nContent-Length: 5",
  valid.replacingOccurrences(of: "required", with: "optional"),
  valid + "\r\nx-agent-device-runner-auth: required",
  valid + "\r\nContent-Length: 5",
  valid + "\r\nTransfer-Encoding: chunked",
  valid.replacingOccurrences(of: "required", with: "required, required")
] {
  var rejected = false
  do { _ = try USBMux.responseBodyLength(invalid) } catch { rejected = true }
  assert(rejected)
}
'''
        self._swift("enum USBMux {\n" + request + response + "\n}\n" + checks)

    @unittest.skipUnless(NATIVE, "Opt in to isolated Swift tests with MOBILE_QA_NATIVE_TESTS=1")
    def test_listener_token_is_written_only_to_private_runfile(self):
        native = (ROOT / "native/SecureLogin.swift").read_text()
        prepare = section(native, "    private static func temporaryTestRun(", "    private static func iosRequest(")
        io = section(native, "enum SecureIO {", "final class LoginChild")
        checks = r'''
let nonce = "11111111-2222-4333-8444-555555555555"
let listenerToken = "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"
let original: [String: Any] = ["Tests": ["TestBundlePath": "__TESTROOT__/Fixture.xctest", "EnvironmentVariables": ["EXISTING": "retained"]]]
let source = DriverPins.workRoot + "/fixture.xctestrun"
let contents = try! PropertyListSerialization.data(fromPropertyList: original, format: .binary, options: 0)
try! contents.write(to: URL(fileURLWithPath: source))
let prepared = try! SecureLogin.prepare(source: source, nonce: nonce, listenerToken: listenerToken)
assert(try! Data(contentsOf: URL(fileURLWithPath: source)) == contents)
let info = try! FileManager.default.attributesOfItem(atPath: prepared)
assert((info[.posixPermissions] as! NSNumber).intValue == 0o600)
let plist = try! PropertyListSerialization.propertyList(from: Data(contentsOf: URL(fileURLWithPath: prepared)), format: nil) as! [String: Any]
let environment = (plist["Tests"] as! [String: Any])["EnvironmentVariables"] as! [String: String]
assert(environment["EXISTING"] == "retained")
assert(environment["AGENT_TEST_ACCOUNTS_NONCE"] == nonce)
assert(environment["AGENT_DEVICE_RUNNER_TOKEN"] == listenerToken)
assert(nonce != listenerToken && listenerToken.utf8.count >= 32)
assert(!prepared.contains(listenerToken) && !prepared.contains(nonce))
'''
        wrapper = "enum SecureLogin {\n" + prepare + "\nstatic func prepare(source: String, nonce: String, listenerToken: String) throws -> String { try temporaryTestRun(source: source, nonce: nonce, listenerToken: listenerToken) }\n}\n"
        self._swift(io + wrapper + checks)

    def _swift(self, source):
        with tempfile.TemporaryDirectory(prefix="listener-protocol-") as folder:
            temporary = Path(folder)
            cache = Path(tempfile.gettempdir()) / "mobile-qa-listener-module-cache"
            cache.mkdir(exist_ok=True)
            prefix = "import Foundation\nimport Darwin\nstruct SecureLoginFailure: Error { let code: String }\nenum DriverPins { static let workRoot = " + json.dumps(str(temporary), ensure_ascii=False) + " }\n"
            swift, binary = temporary / "ListenerChecks.swift", temporary / "check"
            swift.write_text(prefix + source)
            env = {**os.environ, "TMPDIR": str(temporary) + "/"}
            log_path = Path(tempfile.gettempdir()) / (self._testMethodName + ".log")
            with log_path.open("w") as log:
                compiled = subprocess.run(["/usr/bin/xcrun", "swiftc", "-module-cache-path", str(cache), str(swift), "-o", str(binary)], env=env, stdout=log, stderr=log)
                self.assertEqual(compiled.returncode, 0, "Listener protocol compilation failed; inspect isolated compiler log")
                executed = subprocess.run([str(binary)], env=env, stdout=log, stderr=log)
                self.assertEqual(executed.returncode, 0, "Listener protocol assertions failed; inspect isolated test log")


if __name__ == "__main__":
    unittest.main()
