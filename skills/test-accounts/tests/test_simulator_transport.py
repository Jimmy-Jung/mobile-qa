"""Simulator-only protected transport checks. Author: JunyoungJung · 2026-10-27."""

import json
import os
from pathlib import Path
import plistlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
NATIVE = sys.platform == "darwin" and shutil.which("xcrun") and os.environ.get("MOBILE_QA_NATIVE_TESTS") == "1"


@unittest.skipUnless(NATIVE, "Opt in to isolated Swift transport tests with MOBILE_QA_NATIVE_TESTS=1")
class SimulatorTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scratch = Path(os.environ["TMPDIR"])
        if not scratch.is_absolute() or not scratch.is_dir():
            raise RuntimeError("An existing absolute isolated TMPDIR is required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="simulator-transport-", dir=scratch)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.folder = Path(cls.temporary.name)
        cls.binary = cls.folder / "check"
        native = (ROOT / "native/SecureLogin.swift").read_text()
        io = "enum SecureIO {" + native.split("enum SecureIO {", 1)[1].split("final class LoginChild", 1)[0]
        prefix = r'''
import Foundation
import CoreFoundation
import Darwin
struct SecureLoginFailure: Error { let code: String }
enum DriverPins {
  static let workRoot = "__FIXTURE_ROOT__"
  static let releaseDigest = String(repeating: "0", count: 64)
}
final class LoginChild {
  static var terminated = false
  var isRunning: Bool { true }
  let arguments: [String]
  init(path: String, arguments: [String], temporaryDirectory: String,
       discardOutput: Bool = false, simulator: Bool = false) throws { self.arguments = arguments }
  func collect(deadline: TimeInterval, allowedExitCodes: Set<Int32> = [0]) throws -> Data {
    if arguments.contains("terminate") { Self.terminated = true; return Data() }
    if CommandLine.arguments[1] == "finish_owned", Self.terminated { return Data() }
    return Data(CommandLine.arguments[2].utf8)
  }
  func stop(grace: TimeInterval = 1) {}
}
'''.replace('"__FIXTURE_ROOT__"', json.dumps(str(cls.folder)))
        checks = r'''
do {
  switch CommandLine.arguments[1] {
  case "post":
    let syntheticListenerToken = "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"
    let result = try SimulatorConnection.post(body: Data("synthetic-payload".utf8), listenerToken: syntheticListenerToken,
                                               deadline: SecureIO.now + 0.5)
    assert(result == Data("fixture-response".utf8))
    print("accepted")
  case "classify":
    print(try SimulatorConnection.isSimulator(device: "11111111-2222-4333-8444-555555555555") ? "simulator" : "physical")
  case "physical":
    let simulator = try SimulatorConnection.isSimulator(device: "00008020-0012345678901234")
    assert(!simulator)
    print("physical")
  case "identity", "identity_change":
    let device = "11111111-2222-4333-8444-555555555555"
    let before = try SimulatorConnection.appIdentity(device: device, appID: "org.example.synthetic")
    if CommandLine.arguments[1] == "identity_change" {
      try Data("changed-synthetic-debug".utf8).write(to: URL(fileURLWithPath: before.bundle + "/Fixture.debug.dylib"))
    }
    let after = try SimulatorConnection.appIdentity(device: device, appID: "org.example.synthetic")
    print(before == after ? "same_binary" : "binary_changed")
  case "pid":
    print(try SimulatorConnection.appProcessID(device: "fixture", appID: "org.example.synthetic"))
  case "terminate":
    try SimulatorConnection.terminateRunner(device: "fixture", expectedPID: 77)
    print(LoginChild.terminated ? "terminated" : "skipped")
  case "finish_owned", "finish_unowned":
    let child = try LoginChild(path: "fixture", arguments: [], temporaryDirectory: DriverPins.workRoot)
    let owned: Int32? = CommandLine.arguments[1] == "finish_owned" ? 77 : nil
    let result = CleanupCheck.finish(["status": "authenticated", "environment_verified": true], device: "fixture", child: child, pid: owned)
    print(result["status"] as! String)
    print(LoginChild.terminated ? "terminated" : "preserved")
  case "profile":
    let profile = try JSONSerialization.jsonObject(with: Data(CommandLine.arguments[2].utf8)) as! [String: Any]
    print(LoginPlan.validProfile(profile) ? "valid" : "invalid")
  case "artifacts_success", "artifacts_failure", "artifacts_replace", "artifacts_existing_result":
    let root = CommandLine.arguments[2]
    let result = try PrivateIOSRun.perform(root: root) { artifacts in
      let attributes = try FileManager.default.attributesOfItem(atPath: artifacts.path)
      assert((attributes[.posixPermissions] as! NSNumber).intValue == 0o700)
      try artifacts.requireFreshResult()
      try Data("synthetic-run-metadata".utf8).write(to: URL(fileURLWithPath: artifacts.runfile))
      try FileManager.default.createDirectory(atPath: artifacts.resultBundle, withIntermediateDirectories: false)
      let logs = artifacts.derivedData + "/Logs/Test/Fixture.xcresult"
      try FileManager.default.createDirectory(atPath: logs, withIntermediateDirectories: true)
      try Data("synthetic-diagnostic".utf8).write(to: URL(fileURLWithPath: logs + "/record"))
      try FileManager.default.createSymbolicLink(atPath: artifacts.resultBundle + "/external", withDestinationPath: root + "/foreign")
      if CommandLine.arguments[1] == "artifacts_failure" { throw SecureLoginFailure(code: "fixture_failure") }
      if CommandLine.arguments[1] == "artifacts_existing_result" { try artifacts.requireFreshResult() }
      if CommandLine.arguments[1] == "artifacts_replace" {
        try FileManager.default.moveItem(atPath: artifacts.path, toPath: root + "/renamed-owned")
        try FileManager.default.createDirectory(atPath: artifacts.path, withIntermediateDirectories: false)
        try Data("foreign-marker".utf8).write(to: URL(fileURLWithPath: artifacts.path + "/foreign-marker"))
      }
      return ["status": "authenticated", "environment_verified": true]
    }
    print(result["status"] as! String)
    if let code = result["code"] as? String { print(code) }
  case "preflight", "normal_session", "physical_preflight":
    try SessionCheck.execute(preflightOnly: CommandLine.arguments[1] != "normal_session",
                             physical: CommandLine.arguments[1] != "preflight")
  case "preflight_request":
    _ = try PreflightReaderCheck.readPreflightPlan()
    print("plan_only")
  default: fatalError("Invalid fixture mode")
  }
} catch let failure as SecureLoginFailure { print(failure.code) }
catch { print("unexpected_failure") }
'''
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            cls.port = available.getsockname()[1]
        simulator = (ROOT / "native/SimulatorConnection.swift").read_text()
        simulator = simulator.replace("static let port: UInt16 = 19087", f"static let port: UInt16 = {cls.port}")
        finish = "    private static func finishSimulator(" + native.split("    private static func finishSimulator(", 1)[1].split("    private static func runnerInventory(", 1)[0]
        finish = finish.replace("SecureIO.now + 3", "SecureIO.now + 0.05")
        cleanup = "enum CleanupCheck {\n" + finish + r'''
  static func finish(_ result: [String: Any], device: String, child: LoginChild, pid: Int32?) -> [String: Any] {
    finishSimulator(result, device: device, child: child, trustedRunnerPID: pid)
  }
}
'''
        session = "    private static func iosSession(" + native.split("    private static func iosSession(", 1)[1].split("    private static func finishIOS(", 1)[0]
        session = session.replace("LoginPlan", "SessionPlan")
        session_check = r'''
struct SessionPlan {
  let device = "11111111-2222-4333-8444-555555555555"
  let appID = "org.example.synthetic"
  let timeout = 1000
}
enum SessionCheck {
  static var actions: [String] = []
  private static func iosRequest(_ plan: SessionPlan, deviceID: Int?, nonce: String, listenerToken: String,
                                 action: String, secret: Data?, deadline: TimeInterval) throws -> [String: Any] {
    actions.append(action)
    if action == "preflight" {
      assert(secret == nil)
      return try JSONSerialization.jsonObject(with: Data(CommandLine.arguments[2].utf8)) as! [String: Any]
    }
    return ["status": "authenticated", "environment_verified": true]
  }
''' + session + r'''
  static func execute(preflightOnly: Bool, physical: Bool) throws {
    var reads = 0
    let child = try LoginChild(path: "fixture", arguments: [], temporaryDirectory: DriverPins.workRoot)
    let result = try iosSession(SessionPlan(), deviceID: physical ? 123 : nil, nonce: "fixture", listenerToken: "fixture",
      child: child, credentials: {
        reads += 1
        if preflightOnly { throw SecureLoginFailure(code: "forbidden_credential_read") }
        return Data("synthetic-only".utf8)
      }, preflightOnly: preflightOnly)
    print(result["status"] as! String)
    print(reads)
    print(actions.joined(separator: ","))
  }
}
'''
        source = cls.folder / "TransportChecks.swift"
        plan = "struct LoginPlan {" + native.split("struct LoginPlan {", 1)[1].split("enum SecureIO", 1)[0]
        reader = "    static func readPreflightPlan()" + native.split("    static func readPreflightPlan()", 1)[1].split("\n}\n\nstruct LoginPlan", 1)[0]
        reader = reader.replace("LoginPlan", "PreflightFixturePlan")
        reader_check = r'''
struct PreflightFixturePlan {
  init(_ metadata: [String: Any]) throws {
    guard Set(metadata.keys) == ["synthetic"] else { throw SecureLoginFailure(code: "invalid_login_plan") }
  }
}
enum PreflightReaderCheck {
''' + reader + "\n}\n"
        source.write_text(prefix + plan + io + (ROOT / "native/USBMux.swift").read_text() + simulator
                          + (ROOT / "native/PrivateIOSRun.swift").read_text() + cleanup + session_check + reader_check + checks)
        cache = scratch / "mobile-qa-simulator-transport-module-cache"
        cache.mkdir(exist_ok=True)
        with (scratch / "simulator-transport-compile.log").open("w") as log:
            compiled = subprocess.run(["/usr/bin/xcrun", "swiftc", "-module-cache-path", str(cache),
                                       str(source), "-o", str(cls.binary)], stdout=log, stderr=log)
        if compiled.returncode:
            raise AssertionError("Simulator transport fixture compilation failed; inspect isolated compiler log")

    def run_fixture(self, mode, value="", input_data=None):
        result = subprocess.run([str(self.binary), mode, value], input=input_data, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, "Simulator fixture failed without a safe outcome")
        self.assertEqual(result.stderr, "")
        return result.stdout.strip()

    def exchange(self, response):
        received = []
        failure = []
        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", self.port))
        server.listen()
        server.settimeout(3)

        def serve():
            try:
                with server:
                    connection, address = server.accept()
                    self.assertEqual(address[0], "127.0.0.1")
                    with connection:
                        connection.settimeout(2)
                        data = b""
                        while b"\r\n\r\n" not in data:
                            data += connection.recv(4096)
                        header, body = data.split(b"\r\n\r\n", 1)
                        length = int(next(line for line in header.split(b"\r\n") if line.startswith(b"Content-Length:")).split(b":")[1])
                        while len(body) < length:
                            body += connection.recv(length - len(body))
                        received.append((header, body))
                        if response is None:
                            connection.settimeout(1)
                            try:
                                connection.recv(1)
                            except socket.timeout:
                                pass
                        else:
                            connection.sendall(response)
            except Exception as error:
                failure.append(type(error).__name__)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        outcome = self.run_fixture("post")
        thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failure, [])
        self.assertEqual(len(received), 1)
        header, body = received[0]
        self.assertEqual(body, b"synthetic-payload")
        self.assertIn(b"x-agent-device-runner-token: AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE", header)
        return outcome

    def test_loopback_request_requires_authenticated_response(self):
        response = b"HTTP/1.1 200 OK\r\nContent-Length: 16\r\nX-Agent-Device-Runner-Auth: required\r\n\r\nfixture-response"
        self.assertEqual(self.exchange(response), "accepted")

    def test_missing_authentication_and_invalid_framing_are_rejected(self):
        cases = [
            (b"HTTP/1.1 200 OK\r\nContent-Length: 16\r\n\r\nfixture-response", "transport_not_trusted"),
            (b"HTTP/1.1 200 OK\r\nContent-Length: 16\r\nTransfer-Encoding: chunked\r\nX-Agent-Device-Runner-Auth: required\r\n\r\nfixture-response", "transport_response_invalid"),
            (b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nX-Agent-Device-Runner-Auth: required\r\n\r\nfixture-response", "transport_response_invalid"),
        ]
        for response, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.exchange(response), expected)

    def test_read_timeout_returns_safe_code(self):
        self.assertEqual(self.exchange(None), "transport_timeout")

    def test_unavailable_listener_does_not_fall_back_to_usb(self):
        self.assertEqual(self.run_fixture("post"), "ios_runner_unavailable")

    def test_booted_uuid_is_required_and_physical_serial_is_preserved(self):
        device = {"udid": "11111111-2222-4333-8444-555555555555", "isAvailable": True, "state": "Booted"}
        self.assertEqual(self.run_fixture("classify", json.dumps({"devices": {"runtime": [device]}})), "simulator")
        self.assertEqual(self.run_fixture("physical"), "physical")
        for changed in [{**device, "state": "Shutdown"}, {**device, "isAvailable": False}]:
            self.assertEqual(self.run_fixture("classify", json.dumps({"devices": {"runtime": [changed]}})), "simulator_not_booted")
        for invalid in [{"devices": {}}, {"devices": {"runtime": [device, device]}}, {"devices": []}]:
            self.assertEqual(self.run_fixture("classify", json.dumps(invalid)), "ios_target_unverified")

    def app_fixture(self):
        folder = tempfile.TemporaryDirectory(prefix="identity-", dir=self.folder)
        self.addCleanup(folder.cleanup)
        bundle = Path(folder.name) / "Fixture.app"
        bundle.mkdir()
        (bundle / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "org.example.synthetic", "CFBundleExecutable": "Fixture",
        }))
        (bundle / "Fixture").write_bytes(b"synthetic-executable")
        (bundle / "Fixture.debug.dylib").write_bytes(b"synthetic-debug")
        return bundle

    def test_session_binary_snapshot_includes_main_debug_dylib_and_info(self):
        bundle = self.app_fixture()
        self.assertEqual(self.run_fixture("identity", str(bundle) + "\n"), "same_binary")
        self.assertEqual(self.run_fixture("identity_change", str(bundle) + "\n"), "binary_changed")
        (bundle / "Fixture.debug.dylib").unlink()
        self.assertEqual(self.run_fixture("identity", str(bundle) + "\n"), "same_binary")

    def test_bundle_identity_rejects_wrong_id_path_and_nonregular_or_linked_files(self):
        for change in ["id", "traversal", "main_directory", "main_symlink", "debug_symlink", "info_symlink", "bundle_symlink", "large_info"]:
            with self.subTest(change=change):
                bundle = self.app_fixture()
                selected = bundle
                if change in {"id", "traversal"}:
                    info = {"CFBundleIdentifier": "other.app" if change == "id" else "org.example.synthetic",
                            "CFBundleExecutable": "../Fixture" if change == "traversal" else "Fixture"}
                    (bundle / "Info.plist").write_bytes(plistlib.dumps(info))
                elif change == "main_directory":
                    (bundle / "Fixture").unlink()
                    (bundle / "Fixture").mkdir()
                elif change.endswith("_symlink"):
                    name = {"main_symlink": "Fixture", "debug_symlink": "Fixture.debug.dylib", "info_symlink": "Info.plist"}.get(change)
                    if name:
                        original = bundle / name
                        moved = bundle / (name + ".original")
                        original.rename(moved)
                        original.symlink_to(moved)
                    else:
                        selected = bundle.parent / "Alias.app"
                        selected.symlink_to(bundle, target_is_directory=True)
                else:
                    (bundle / "Info.plist").write_bytes(b"x" * 1048577)
                self.assertEqual(self.run_fixture("identity", str(selected) + "\n"), "driver_not_trusted")

    def test_exact_bundle_pid_and_owned_runner_cleanup(self):
        listing = "41\t0\tUIKitApplication:org.example.synthetic[base][rb-legacy]\n42\t0\tUIKitApplication:org.example.synthetic.dev[dev][rb-legacy]\n"
        self.assertEqual(self.run_fixture("pid", listing), "41")
        own = "77\t0\tUIKitApplication:com.agenttestaccounts.simulator.uitests.xctrunner[runner][rb-legacy]\n"
        other = own.replace("77\t", "78\t")
        self.assertEqual(self.run_fixture("terminate", own), "terminated")
        self.assertEqual(self.run_fixture("terminate", other), "runner_ownership_unavailable")
        self.assertEqual(self.run_fixture("finish_owned", own), "authenticated\nterminated")
        self.assertEqual(self.run_fixture("finish_unowned", own), "uncertain\npreserved")

    def test_navigation_profile_keeps_strict_selectors_and_boolean_contract(self):
        base = {
            "schema_version": 1, "name": "synthetic", "platform": "ios", "app_id": "org.example.synthetic",
            "environment": "staging", "role": "tester", "timeout_ms": 10000,
            "username_selector": {"identifier": "username"}, "password_selector": {"identifier": "password"},
            "submit_selector": {"identifier": "submit"}, "environment_selector": {"identifier": "staging"},
            "success_selectors": [{"identifier": "home"}],
        }
        navigation = {"open_selector": {"identifier": "options"}, "close_selector": {"label": "Close"},
                      "open_gesture": "long_press", "select_if_needed": True}
        self.assertEqual(self.run_fixture("profile", json.dumps(base)), "valid")
        self.assertEqual(self.run_fixture("profile", json.dumps({**base, "environment_navigation": navigation})), "valid")
        cases = [
            {**navigation, "select_if_needed": 1}, {**navigation, "open_gesture": "script"},
            {**navigation, "open_selector": {"type": "textField"}},
            {**navigation, "close_selector": base["username_selector"]},
            {**navigation, "close_selector": navigation["open_selector"]},
            {**navigation, "unknown": "value"}, None,
        ]
        for invalid in cases:
            self.assertEqual(self.run_fixture("profile", json.dumps({**base, "environment_navigation": invalid})), "invalid")
        self.assertEqual(self.run_fixture("profile", json.dumps({**base, "platform": "android", "environment_navigation": navigation})), "invalid")

    def test_private_result_and_derived_diagnostics_are_removed_on_return_and_throw(self):
        for mode, expected in [("artifacts_success", "authenticated"), ("artifacts_failure", "fixture_failure"),
                               ("artifacts_existing_result", "runner_ownership_unavailable")]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix="artifact-root-", dir=self.folder) as temporary:
                root = Path(temporary)
                foreign = root / "foreign"
                foreign.mkdir()
                (foreign / "keep").write_text("synthetic-foreign-file")
                self.assertEqual(self.run_fixture(mode, str(root)), expected)
                self.assertEqual(sorted(path.name for path in root.iterdir()), ["foreign"])
                self.assertEqual((foreign / "keep").read_text(), "synthetic-foreign-file")

    def test_replaced_runtime_directory_is_preserved_and_cleanup_is_unconfirmed(self):
        with tempfile.TemporaryDirectory(prefix="artifact-root-", dir=self.folder) as temporary:
            root = Path(temporary)
            (root / "foreign").mkdir()
            self.assertEqual(self.run_fixture("artifacts_replace", str(root)), "uncertain\nios_artifact_cleanup_unconfirmed")
            replacement = next(path for path in root.iterdir() if path.name.startswith("account-run-"))
            self.assertEqual((replacement / "foreign-marker").read_text(), "foreign-marker")
            self.assertTrue((root / "renamed-owned/Runner.xctestrun").is_file())

    def test_preflight_never_reads_credentials_or_sends_login(self):
        for result in [{"status": "ready", "environment_verified": True},
                       {"status": "already_authenticated", "environment_verified": False},
                       {"status": "error", "code": "LOGIN_VIEW_NOT_READY", "environment_verified": False}]:
            with self.subTest(status=result["status"]):
                self.assertEqual(self.run_fixture("preflight", json.dumps(result)), result["status"] + "\n0\npreflight")
        prepared = json.dumps({"status": "ready", "environment_verified": True})
        self.assertEqual(self.run_fixture("physical_preflight", prepared), "invalid_login_plan")
        self.assertEqual(self.run_fixture("normal_session", prepared), "authenticated\n1\npreflight,login")

    def test_preflight_wire_request_has_only_a_plan_and_rejects_account_references(self):
        plan = {"synthetic": True}
        self.assertEqual(self.run_fixture("preflight_request", input_data=json.dumps({"plan": plan}) + "\n"), "plan_only")
        for request in [{"plan": plan, "id": "synthetic"}, {"plan": plan, "reference": "fixture"},
                        {"id": "synthetic", "reference": "fixture", "plan": plan}, {"plan": None}, []]:
            self.assertEqual(self.run_fixture("preflight_request", input_data=json.dumps(request) + "\n"), "invalid_login_request")

    def test_tab_button_label_is_only_an_ios_success_selector(self):
        base = {
            "schema_version": 1, "name": "synthetic", "platform": "ios", "app_id": "org.example.synthetic",
            "environment": "staging", "role": "tester", "timeout_ms": 10000,
            "username_selector": {"label": "Username"}, "password_selector": {"label": "Password"},
            "submit_selector": {"label": "Submit"}, "environment_selector": {"label": "Staging"},
            "success_selectors": [{"tab_button_label": "Library"}],
        }
        self.assertEqual(self.run_fixture("profile", json.dumps(base)), "valid")
        self.assertEqual(self.run_fixture("profile", json.dumps({**base, "platform": "android"})), "invalid")
        for role in ["username_selector", "password_selector", "submit_selector", "environment_selector"]:
            self.assertEqual(self.run_fixture("profile", json.dumps({**base, role: {"tab_button_label": "Other"}})), "invalid")
        for role in ["open_selector", "close_selector"]:
            navigation = {"open_selector": {"label": "Options"}, "close_selector": {"label": "Close"},
                          "open_gesture": "tap", "select_if_needed": False}
            navigation[role] = {"tab_button_label": "Other"}
            self.assertEqual(self.run_fixture("profile", json.dumps({**base, "environment_navigation": navigation})), "invalid")
        for label in ["", "line\nbreak", "zero\u200bwidth", "한" * 86]:
            self.assertEqual(self.run_fixture("profile", json.dumps({**base, "success_selectors": [{"tab_button_label": label}]})), "invalid")


if __name__ == "__main__":
    unittest.main()
