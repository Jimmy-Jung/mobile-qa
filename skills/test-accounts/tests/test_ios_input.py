"""iOS 전용 runner 준비 계약. Author: JunyoungJung · 2026-10-22."""

from contextlib import nullcontext
from pathlib import Path
import sys
import json
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_ios_input as ios

NATIVE = sys.platform == "darwin" and shutil.which("xcrun") and os.environ.get("MOBILE_QA_NATIVE_TESTS") == "1"
DIGEST = "a" * 64
PLAN = {"app_id": "org.example.fixture", "username_selector": {"identifier": "username"},
        "password_selector": {"identifier": "password"}, "submit_selector": {"identifier": "submit"},
        "success_selectors": [{"identifier": "home"}], "environment_selector": {"label": "Staging"},
        "timeout_ms": 1000, "release_digest": DIGEST}


class IOSInputBuildTests(unittest.TestCase):
    @unittest.skipUnless(NATIVE, "Opt in to isolated Swift tests with MOBILE_QA_NATIVE_TESTS=1")
    def test_native_xcode_children_use_only_verified_pinned_developer_directory(self):
        source = (ios.SKILL_DIR / "native/SecureLogin.swift").read_text()
        environment = "    static func childEnvironment(" + source.split("    static func childEnvironment(", 1)[1].split("    var isRunning:", 1)[0]
        self._check_swift(r'''
import Darwin
struct SecureLoginFailure: Error { let code: String }
private enum DriverPins {
  nonisolated(unsafe) static var iosDeveloperDirectory: String?
  nonisolated(unsafe) static var iosXcodeBuildSHA256: String?
}
private enum LoginChild {
''' + environment + r'''
}
let temporary = "__FIXTURE_HOME__"
let developer = temporary + "/Chosen Developer"
let tool = developer + "/usr/bin/xcodebuild"
try! FileManager.default.createDirectory(atPath: developer + "/usr/bin", withIntermediateDirectories: true)
let contents = Data("synthetic-xcodebuild".utf8)
try! contents.write(to: URL(fileURLWithPath: tool))
try! FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: tool)
setenv("DEVELOPER_DIR", "/ignored-selection", 1)
setenv("JAVA_HOME", "/ignored-jdk", 1)
let plain = try! LoginChild.childEnvironment(path: "/usr/sbin/diskutil", temporaryDirectory: temporary)
assert(Set(plain.keys) == ["PATH", "HOME", "TMPDIR"])
do {
  _ = try LoginChild.childEnvironment(path: "/usr/bin/xcrun", temporaryDirectory: temporary)
  assertionFailure("unprepared iOS selection accepted")
} catch {}
DriverPins.iosDeveloperDirectory = developer
DriverPins.iosXcodeBuildSHA256 = SHA256.hash(data: contents).map { String(format: "%02x", $0) }.joined()
for path in ["/usr/bin/xcrun", "/usr/bin/xcodebuild"] {
  let values = try! LoginChild.childEnvironment(path: path, temporaryDirectory: temporary)
  assert(values["DEVELOPER_DIR"] == developer)
  assert(Set(values.keys) == ["PATH", "HOME", "TMPDIR", "DEVELOPER_DIR"])
}
try! Data("changed-xcodebuild".utf8).write(to: URL(fileURLWithPath: tool))
do {
  _ = try LoginChild.childEnvironment(path: "/usr/bin/xcodebuild", temporaryDirectory: temporary)
  assertionFailure("changed Xcode tool accepted")
} catch {}
''')

    def test_source_lock_rejects_same_version_patch_added_deleted_and_symlink_files(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder) / "package"
            apple = package / "dist/apple"
            runner = apple / "runner/AgentDeviceRunner/Main.swift"
            snapshot = apple / "snapshot-presentation/Package.swift"
            for path in (runner, snapshot):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("official-source")
            (package / "package.json").write_text(json.dumps({"name": "agent-device", "version": "0.21.30"}))
            lock = Path(folder) / "source-lock.json"
            files = {str(path.relative_to(apple)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() for path in (runner, snapshot)}
            lock.write_text(json.dumps({"schema_version": 1, "version": "0.21.30",
                "dist_integrity": "sha512-" + "A" * 86 + "==",
                "tarball": "https://registry.npmjs.org/agent-device/-/agent-device-0.21.30.tgz", "files": files}))
            with patch.object(ios, "SOURCE_LOCK", lock):
                self.assertEqual(ios.package_identity(package)["source_lock_sha256"], hashlib.sha256(lock.read_bytes()).hexdigest())
                runner.write_text("same-version-modified-source")
                with self.assertRaises(ValueError):
                    ios.package_identity(package)

                runner.write_text("official-source")
                extra = runner.parent / "Extra.swift"
                extra.write_text("unreviewed-source")
                with self.assertRaises(ValueError):
                    ios.package_identity(package)
                extra.unlink()
                snapshot.unlink()
                with self.assertRaises(ValueError):
                    ios.package_identity(package)
                snapshot.symlink_to(runner)
                with self.assertRaises(ValueError):
                    ios.package_identity(package)

    def test_build_paths_are_fixed_without_creating_before_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "not-created/IOSInput"
            with patch.object(ios, "verify_output", side_effect=lambda path: path) as verify:
                first = ios.selected_build_paths(directory)
                self.assertEqual(first, ios.selected_build_paths(directory))
            self.assertFalse(directory.exists())
            self.assertEqual(first["module_cache"], directory / "DerivedData/ModuleCache.noindex")
            self.assertEqual(first["package_cache"], directory.parent / "SwiftPM/Cache")
            self.assertIn(directory / "build.json", [item.args[0] for item in verify.call_args_list])

    def test_build_paths_stop_before_any_creation_when_storage_is_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "not-created/IOSInput"
            with patch.object(ios, "verify_output", side_effect=ValueError("unmounted")):
                with self.assertRaises(ValueError):
                    ios.selected_build_paths(directory)
            self.assertFalse(directory.exists())

    def test_build_passes_selected_external_cache_settings_to_xcode_and_children(self):
        class Captured(Exception):
            pass
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "IOSInput"
            developer = Path(folder) / "Selected Developer"
            tool = developer / "usr/bin/xcodebuild"
            tool.parent.mkdir(parents=True)
            tool.write_text("synthetic-xcodebuild")
            captured = {}
            def run(command, **kwargs):
                if command[0] == "/usr/bin/pgrep":
                    return subprocess.CompletedProcess(command, 1, stdout="")
                if command[0] == "/usr/bin/xcodebuild":
                    captured.update(command=command, environment=kwargs["env"], kwargs=kwargs)
                    raise Captured()
                self.fail("Unexpected external process in synthetic build")
            with patch.object(ios, "helper_directory", return_value=directory.parent / "NativeHelper"), \
                 patch.object(ios, "developer_directory", return_value=developer), \
                 patch.object(ios, "verify_release"), patch.object(ios, "verify_output", side_effect=lambda path: path), \
                 patch.object(ios, "current_digest", return_value=DIGEST), \
                 patch.object(ios, "package_identity", return_value={"version": "0.21.30"}), \
                 patch.object(ios, "build_lock", return_value=nullcontext()), \
                 patch.object(ios, "prepare", return_value=(directory / "Source/runner", {})), \
                 patch.object(ios.subprocess, "run", side_effect=run):
                with self.assertRaises(Captured):
                    ios.build(team_id="ABCDEFGHIJ")
            command, environment = captured["command"], captured["environment"]
            self.assertEqual(environment["DEVELOPER_DIR"], str(developer))
            for option, expected in [("-derivedDataPath", directory / "DerivedData"),
                                     ("-clonedSourcePackagesDirPath", directory / "DerivedData/SourcePackages"),
                                     ("-packageCachePath", directory.parent / "SwiftPM/Cache")]:
                self.assertEqual(command[command.index(option) + 1], str(expected))
            for name, expected in [("CACHE_ROOT", directory / "Cache"),
                                   ("CLANG_MODULE_CACHE_PATH", directory / "DerivedData/ModuleCache.noindex"),
                                   ("SWIFT_MODULE_CACHE_PATH", directory / "DerivedData/ModuleCache.noindex")]:
                self.assertIn(name + "=" + str(expected), command)
                self.assertEqual(environment[name], str(expected))
            self.assertEqual(environment["TMPDIR"], str(directory / "tmp") + "/")
            self.assertIs(captured["kwargs"]["stdout"], captured["kwargs"]["stderr"])

    def test_prepare_pins_release_without_editing_installed_package(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package, output, skill = root / "package", root / "output", root / "skill"
            tests = package / "dist/apple/runner/AgentDeviceRunner/AgentDeviceRunnerUITests"
            tests.mkdir(parents=True)
            (package / "dist/apple/snapshot-presentation").mkdir()
            transport = '  private func handleRequestBody(\n let command = try JSONDecoder().decode(Command.self, from: body)\n  func inlineResponse(for command: Command) -> Response? {'
            (tests / "RunnerTests+Transport.swift").write_text(transport)
            (tests / "RunnerTests.swift").write_text('  override func record(_ issue: XCTIssue) {\n')
            (skill / "ios").mkdir(parents=True)
            original = 'let release = "__MOBILE_QA_RELEASE_DIGEST__"'
            (skill / "ios/RunnerTests+AccountLogin.swift").write_text(original)
            with patch.object(ios, "agent_device_package", return_value=package), \
                 patch.object(ios, "package_identity", return_value={"version": "0.21.30"}), \
                 patch.object(ios, "current_digest", return_value=DIGEST), \
                 patch.object(ios, "verify_output", side_effect=lambda path: path), patch.object(ios, "SKILL_DIR", skill):
                runner, hashes = ios.prepare(output)
            self.assertIn(DIGEST, (runner / "AgentDeviceRunnerUITests/RunnerTests+AccountLogin.swift").read_text())
            self.assertEqual((tests / "RunnerTests+Transport.swift").read_text(), transport)
            self.assertEqual((skill / "ios/RunnerTests+AccountLogin.swift").read_text(), original)
            self.assertTrue(hashes)

    def test_transport_rejects_generic_commands_before_decode(self):
        source = '  private func handleRequestBody(\n let command = try JSONDecoder().decode(Command.self, from: body)\n  func inlineResponse(for command: Command) -> Response? {'
        result = ios.patch_transport(source)
        self.assertIn("handleSecureAccountRequest", result)
        self.assertIn("@Sendable", result)
        self.assertNotIn("decode(Command.self", result)
        self.assertIn("secureAccountUnavailableResponse()", result)
        with self.assertRaises(ValueError):
            ios.patch_transport("unknown upstream transport")

    def test_issue_hook_precedes_raw_issue_description(self):
        source = '  override func record(_ issue: XCTIssue) {\n let description = issue.compactDescription\n'
        result = ios.patch_issue_recording(source)
        self.assertLess(result.index("recordSecureAccountIssue"), result.index("issue.compactDescription"))
        with self.assertRaises(ValueError):
            ios.patch_issue_recording("unknown upstream issue hook")

    def test_builds_only_conflict_when_output_is_shared(self):
        directory = Path("/synthetic/external/IOSInput")
        ios.verify_separate_build_output(directory, ["xcodebuild -xctestrun /other/Product.xctestrun"])
        with self.assertRaises(ValueError):
            ios.verify_separate_build_output(directory, ["xcodebuild -derivedDataPath " + str(directory / "DerivedData")])
        with patch.object(ios.subprocess, "run", return_value=subprocess.CompletedProcess([], 2, stdout="")):
            with self.assertRaises(ValueError):
                ios.verify_separate_builds(directory)

    def test_signing_values_are_bounded(self):
        self.assertIn("DEVELOPMENT_TEAM=ABCDEFGHIJ", ios.signing_settings("ABCDEFGHIJ", False))
        self.assertIn("CODE_SIGNING_ALLOWED=NO", ios.signing_settings(None, True))
        for value in ("bad value", None):
            with self.assertRaises(ValueError):
                ios.signing_settings(value, False)

    def test_nonce_mode_has_no_general_input_fallback(self):
        source = (ios.SKILL_DIR / "ios/RunnerTests+AccountLogin.swift").read_text()
        self.assertIn("RunnerSynthesizedTextEntry.replaceText", source)
        for forbidden in (".typeText(", "executeAccepted", "executeTypeCommand", "NSLog"):
            self.assertNotIn(forbidden, source)
        self.assertIn("AGENT_TEST_ACCOUNTS_NONCE", source)
        self.assertIn("recordSecureAccountIssue", source)

    @unittest.skipUnless(NATIVE, "Opt in to isolated Swift tests with MOBILE_QA_NATIVE_TESTS=1")
    def test_swift_plan_validation_and_one_shot_request(self):
        plan = json.dumps(PLAN, ensure_ascii=False)
        source = 'let validData = Data(#"' + plan + '"#.utf8)\n'
        source += 'private let valid = try! JSONDecoder().decode(SecureAccountPlan.self, from: validData)\nassert(valid.valid)\n'
        invalid = [dict(PLAN, app_id="/wrong"), dict(PLAN, timeout_ms=0), dict(PLAN, success_selectors=[]),
                   dict(PLAN, username_selector={}), dict(PLAN, release_digest="b" * 64),
                   dict(PLAN, submit_selector=PLAN["password_selector"]),
                   dict(PLAN, username_selector={"identifier": "u", "label": "ambiguous"})]
        for value in invalid:
            source += 'assert(!(try! JSONDecoder().decode(SecureAccountPlan.self, from: Data(#"' + json.dumps(value) + '"#.utf8))).valid)\n'
        source += r'''
let plan = try! JSONSerialization.jsonObject(with: validData) as! [String: Any]
func request(_ changes: [String: Any] = [:], action: String = "preflight") -> Data {
  var object: [String: Any] = ["secureAccountProtocol": "agent-test-accounts-v1",
    "nonce": "11111111-2222-3333-4444-555555555555", "action": action, "plan": plan]
  if action == "login" { object["username"] = "synthetic-user"; object["password"] = "synthetic-password" }
  return try! JSONSerialization.data(withJSONObject: object.merging(changes) { _, new in new })
}
assert(SecureAccountRequest.decode(request()) != nil)
assert(SecureAccountRequest.decode(request(action: "login")) != nil)
for action in ["tap", "retry", "shell"] { assert(SecureAccountRequest.decode(request(action: action)) == nil) }
assert(SecureAccountRequest.decode(request(["extra": "untrusted"])) == nil)
'''
        self._check_swift(source)

    @unittest.skipUnless(NATIVE, "Opt in to isolated Swift tests with MOBILE_QA_NATIVE_TESTS=1")
    def test_authenticated_envelope_rejects_tamper_and_cleanup_remains_uncertain(self):
        self._check_swift(r'''
let nonce = "11111111-2222-3333-4444-555555555555"
let clear = Data("synthetic credential payload".utf8)
let key = SymmetricKey(data: SHA256.hash(data: Data(nonce.utf8)))
let sealed = try! AES.GCM.seal(clear, using: key).combined!
func envelope(_ data: Data) -> Data {
  try! JSONSerialization.data(withJSONObject: ["secureAccountProtocol": "agent-test-accounts-v1",
    "sealed_request": data.base64EncodedString()])
}
let request = envelope(sealed)
assert(SecureAccountEnvelope.openRequest(request, nonce: nonce) == clear)
assert(!String(decoding: request, as: UTF8.self).contains("synthetic credential"))
var tampered = sealed; tampered[tampered.index(before: tampered.endIndex)] ^= 1
assert(SecureAccountEnvelope.openRequest(envelope(tampered), nonce: nonce) == nil)
assert(SecureAccountEnvelope.openRequest(request, nonce: "wrong nonce") == nil)
let fakeReady = Data(#"{"protocol":"agent-test-accounts-v1","status":"ready"}"#.utf8)
assert(SecureAccountEnvelope.openRequest(fakeReady, nonce: nonce) == nil)
let response = SecureAccountEnvelope.sealResponse(fakeReady, nonce: nonce)!
let object = try! JSONSerialization.jsonObject(with: response) as! [String: String]
assert(Set(object.keys) == Set(["protocol", "sealed_response"]))
let box = try! AES.GCM.SealedBox(combined: Data(base64Encoded: object["sealed_response"]!)!)
assert(try! AES.GCM.open(box, using: key) == fakeReady)
private let operation = SecureAccountOperation()
assert(operation.unconfirmedCleanup == nil && operation.beginInput())
operation.cancel()
assert(operation.unconfirmedCleanup == .unconfirmed && !operation.canContinue && !operation.beginInput())
''')

    @unittest.skipUnless(NATIVE, "Opt in to isolated Swift tests with MOBILE_QA_NATIVE_TESTS=1")
    def test_actual_field_safety_and_failure_clear(self):
        runner = (ios.SKILL_DIR / "ios/RunnerTests+AccountLogin.swift").read_text()
        fields = "  private func secureAccountFields(" + runner.split("  private func secureAccountFields(", 1)[1].split("  private func performSecureAccountRequest(", 1)[0]
        clear = "  private func secureAccountClear(" + runner.split("  private func secureAccountClear(", 1)[1].split("  private func secureAccountReplace(", 1)[0]
        self._check_swift(r'''
private final class XCUIElement {
  enum ElementType { case textField, textView, secureTextField, button, other }
  let name: String
  var elementType: ElementType
  var isEnabled = true; var isHittable = true
  init(_ name: String, _ type: ElementType) { self.name = name; elementType = type }
}
private final class XCUIApplication {
  enum State { case runningForeground, notRunning }
  var state = State.runningForeground
  var elements: [String: [XCUIElement]] = [:]
  var clears: [String] = []; var failingField: String?
}
private enum RunnerObjCExceptionCatcher {
  static var failure: String?
  static func catchException(_ work: () -> Void) -> String? { work(); return failure }
}
private final class RunnerTests {
  func secureAccountMatches(_ selector: SecureAccountSelector, in app: XCUIApplication) -> [XCUIElement] {
    app.elements[selector.fields.values.first!] ?? []
  }
  func secureAccountReplace(_ text: String, in field: XCUIElement, app: XCUIApplication) -> Bool {
    assert(text.isEmpty); app.clears.append(field.name); return app.failingField != field.name
  }
''' + fields + clear + '\n  func runChecks() {\n    let plan = try! JSONDecoder().decode(SecureAccountPlan.self, from: Data(#"' + json.dumps(PLAN) + '"#.utf8))\n' + r'''
    let app = XCUIApplication()
    let username = XCUIElement("username", .textField)
    let password = XCUIElement("password", .secureTextField)
    let submit = XCUIElement("submit", .button)
    app.elements = ["username": [username], "password": [password], "submit": [submit]]
    assert(secureAccountFields(plan, app: app) != nil)
    app.elements["submit"] = [submit, submit]
    assert(secureAccountFields(plan, app: app) == nil)
    app.elements["submit"] = [submit]; password.elementType = .textField
    assert(secureAccountClear(plan, app: app) == .unavailable && app.clears.isEmpty)
    password.elementType = .secureTextField; submit.isEnabled = false
    assert(secureAccountFields(plan, app: app) == nil)
    assert(secureAccountClear(plan, app: app) == .cleared && app.clears == ["username", "password"])
    app.clears = []; app.failingField = "username"
    assert(secureAccountClear(plan, app: app) == .failed && app.clears == ["username", "password"])
    RunnerObjCExceptionCatcher.failure = "synthetic-exception"
    assert(secureAccountClear(plan, app: app) == .unconfirmed)
    RunnerObjCExceptionCatcher.failure = nil; app.state = .notRunning; app.clears = []
    assert(secureAccountClear(plan, app: app) == .unavailable && app.clears.isEmpty)
  }
}
RunnerTests().runChecks()
''')

    @unittest.skipUnless(NATIVE, "Opt in to isolated Swift tests with MOBILE_QA_NATIVE_TESTS=1")
    def test_native_session_reads_secret_only_after_verified_ready(self):
        native = (ios.SKILL_DIR / "native/SecureLogin.swift").read_text()
        session = "    private static func iosSession(" + native.split("    private static func iosSession(", 1)[1].split("    private static func finishIOS(", 1)[0]
        self._check_swift(r'''
struct SecureLoginFailure: Error { let code: String }
struct LoginPlan { let device = "synthetic-phone"; let appID = "org.example.fixture"; let timeout = 1000 }
private enum SecureIO {
  static var tick: TimeInterval = 0
  static var now: TimeInterval { tick += 10; return tick }
}
private enum DriverPins { static let workRoot = "/synthetic-unused" }
private final class LoginChild {
  var isRunning = true
  init() {}
  init(path: String, arguments: [String], temporaryDirectory: String, discardOutput: Bool = false) throws {}
  func stop() {}
  func collect(deadline: TimeInterval) throws -> Data { Data() }
}
private enum SecureLogin {
  static var preflight: [String: Any] = [:]
  static var outcome: [String: Any] = [:]
  static var actions: [String] = []
  static var stopAfterPreflight = false
  static var child = LoginChild()
  private static func iosRequest(_ plan: LoginPlan, deviceID: Int, nonce: String, listenerToken: String, action: String,
                                secret: Data?, deadline: TimeInterval) throws -> [String: Any] {
    assert(listenerToken == "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
    actions.append(action)
    if action == "preflight" {
      assert(secret == nil)
      if stopAfterPreflight { child.isRunning = false }
      return preflight
    }
    assert(secret == Data("synthetic-secret".utf8)); return outcome
  }
''' + session + r'''
  static func runChecks() {
    let plan = LoginPlan()
    for response: [String: Any] in [
      ["status": "error", "code": "LOGIN_VIEW_NOT_READY"],
      ["status": "already_authenticated", "environment_verified": false],
      ["status": "ready", "environment_verified": false],
      ["status": "ready", "environment_verified": "true"],
    ] {
      preflight = response; actions = []; child = LoginChild(); SecureIO.tick = 0
      var reads = 0
      _ = try! iosSession(plan, deviceID: 1, nonce: "synthetic-nonce", listenerToken: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", child: child) {
        reads += 1; return Data("synthetic-secret".utf8)
      }
      assert(reads == 0 && actions == ["preflight"])
    }
    preflight = ["status": "ready", "environment_verified": true]
    outcome = ["status": "authenticated", "environment_verified": true]
    actions = []; child = LoginChild(); SecureIO.tick = 0
    var reads = 0
    _ = try! iosSession(plan, deviceID: 1, nonce: "synthetic-nonce", listenerToken: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", child: child) {
      reads += 1; return Data("synthetic-secret".utf8)
    }
    assert(reads == 1 && actions == ["preflight", "login"])
    stopAfterPreflight = true; actions = []; child = LoginChild(); SecureIO.tick = 0; reads = 0
    do {
      _ = try iosSession(plan, deviceID: 1, nonce: "synthetic-nonce", listenerToken: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", child: child) {
        reads += 1; return Data("synthetic-secret".utf8)
      }
      assertionFailure("stopped runner must reject input")
    } catch { assert(reads == 0 && actions == ["preflight"]) }
  }
}
SecureLogin.runChecks()
''')

    @unittest.skipUnless(NATIVE, "Opt in to isolated Swift tests with MOBILE_QA_NATIVE_TESTS=1")
    def test_native_profile_trust_validates_actual_file_scope_hash_and_permissions(self):
        native = (ios.SKILL_DIR / "native/SecureLogin.swift").read_text()
        plan_source = "struct LoginPlan {" + native.split("struct LoginPlan {", 1)[1].split("enum SecureIO {", 1)[0]
        plan_source = plan_source.replace("NSHomeDirectory()", "fixtureHome")
        profile = {**PLAN, "schema_version": 1, "name": "sample-staging", "platform": "ios",
                   "environment": "staging", "role": "tester"}
        del profile["release_digest"]
        self._check_swift('''
import Darwin
import CoreFoundation
private let fixtureHome = "__FIXTURE_HOME__"
struct SecureLoginFailure: Error { let code: String }
private enum DriverPins { static let releaseDigest = "''' + DIGEST + '" }\n' + plan_source + '\nprivate let profile = try! JSONSerialization.jsonObject(with: Data(#"' + json.dumps(profile) + '"#.utf8)) as! [String: Any]\n' + r'''
let project = "sample-123456789abc"
let accountProject = "example.test/team/sample"
let directory = fixtureHome + "/Library/Application Support/mobile-device-qa/projects/" + project + "/profiles"
let path = directory + "/sample-staging.json"
let files = FileManager.default
try! files.createDirectory(atPath: directory, withIntermediateDirectories: true,
                           attributes: [.posixPermissions: 0o700])
try! files.setAttributes([.posixPermissions: 0o700], ofItemAtPath: String(directory.dropLast("/profiles".count)))
let wrapper: [String: Any] = ["schema_version": 1, "project_key": project, "account_project": accountProject,
  "release_digest": DriverPins.releaseDigest, "profile": profile]
let contents = try! JSONSerialization.data(withJSONObject: wrapper, options: [.sortedKeys])
try! contents.write(to: URL(fileURLWithPath: path))
try! files.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path)
let hash = SHA256.hash(data: contents).map { String(format: "%02x", $0) }.joined()
let trust = ["path": path, "profile_sha256": hash, "release_digest": DriverPins.releaseDigest]
let metadata = profile.filter { !["schema_version", "name"].contains($0.key) }.merging([
  "device": "synthetic-phone", "project_key": project, "account_project": accountProject, "trust": trust
]) { _, new in new }
private let valid = try! LoginPlan(metadata)
assert(valid.appID == "org.example.fixture" && valid.environment == "staging")
assert(valid.metadata["trust"] == nil && valid.metadata["role"] == nil)
func rejects(_ changes: [String: Any] = [:]) {
  do { _ = try LoginPlan(metadata.merging(changes) { _, new in new }); assertionFailure("untrusted plan accepted") }
  catch {}
}
for changes: [String: Any] in [
  ["app_id": "org.example.other"], ["environment": "production"], ["role": "admin"],
  ["project_key": "other-" + String(repeating: "a", count: 12)], ["account_project": "example.test/other/app"],
  ["trust": trust.merging(["profile_sha256": String(repeating: "b", count: 64)]) { _, new in new }],
  ["trust": trust.merging(["release_digest": String(repeating: "b", count: 64)]) { _, new in new }],
  ["shell": "untrusted"],
] { rejects(changes) }
try! files.setAttributes([.posixPermissions: 0o644], ofItemAtPath: path); rejects()
try! files.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path)
try! files.setAttributes([.posixPermissions: 0o755], ofItemAtPath: directory); rejects()
try! files.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory)
try! files.moveItem(atPath: path, toPath: path + ".target")
try! files.createSymbolicLink(atPath: path, withDestinationPath: path + ".target"); rejects()
try! files.removeItem(atPath: path)
try! files.moveItem(atPath: path + ".target", toPath: path)
var altered = contents; altered.append(0x20)
try! altered.write(to: URL(fileURLWithPath: path)); rejects()
''')

    @unittest.skipUnless(NATIVE, "Opt in to isolated Swift tests with MOBILE_QA_NATIVE_TESTS=1")
    def test_preflight_rejects_visible_success_marker_and_aliased_environment(self):
        runner = (ios.SKILL_DIR / "ios/RunnerTests+AccountLogin.swift").read_text()
        observations = "  private func secureAccountHasPostLoginMarker(" + runner.split("  private func secureAccountHasPostLoginMarker(", 1)[1].split("  private func performSecureAccountRequest(", 1)[0]
        preflight = runner.split("    let plan = request.plan", 1)[1].split('    guard let username = request.username', 1)[0]
        preflight = "    let plan = request.plan" + preflight + '    return .error("INPUT_UNAVAILABLE")\n'
        self._check_swift(r'''
private final class XCUIElement: NSObject {
  enum ElementType { case textField, textView, secureTextField, button, other }
  var elementType: ElementType
  var identifier: String; var label: String; var placeholderValue: String?
  var isEnabled = true; var isHittable = true; var isSelected = true; var exists = true
  init(_ identifier: String, _ type: ElementType, label: String = "") {
    self.identifier = identifier; self.label = label; self.elementType = type
  }
}
private final class XCUIApplication {
  enum State { case runningForeground, notRunning }
  static var elements: [String: [XCUIElement]] = [:]
  var state = State.runningForeground
  init(bundleIdentifier: String) {}
}
private final class RunnerTests {
  func secureAccountMatches(_ selector: SecureAccountSelector, in app: XCUIApplication) -> [XCUIElement] {
    XCUIApplication.elements[selector.fields.values.first!] ?? []
  }
''' + observations + r'''
  func preflight(_ request: SecureAccountRequest) -> SecureAccountResult {
    let operation = SecureAccountOperation()
''' + preflight + r'''
  }
}
let metadata: [String: Any] = ["app_id": "org.example.fixture", "username_selector": ["identifier": "username"],
  "password_selector": ["identifier": "password"], "submit_selector": ["identifier": "submit"],
  "environment_selector": ["label": "Staging"], "success_selectors": [["identifier": "home"]],
  "timeout_ms": 1000, "release_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
let data = try! JSONSerialization.data(withJSONObject: ["secureAccountProtocol": "agent-test-accounts-v1",
  "nonce": "11111111-2222-3333-4444-555555555555", "action": "preflight", "plan": metadata])
private let request = SecureAccountRequest.decode(data)!
private let username = XCUIElement("username", .textField)
private let password = XCUIElement("password", .secureTextField)
private let submit = XCUIElement("submit", .button, label: "Sign in")
private let environment = XCUIElement("environment", .button, label: "Staging")
private let home = XCUIElement("home", .other)
private let runner = RunnerTests()
XCUIApplication.elements = ["username": [username], "password": [password], "submit": [submit], "Staging": [environment]]
assert(runner.preflight(request).status == "ready")
XCUIApplication.elements["home"] = [home]
assert(runner.preflight(request).status == "error")
home.isHittable = false
assert(runner.preflight(request).status == "error")
home.isHittable = true
XCUIApplication.elements["home"] = [home, XCUIElement("another-home", .other)]
assert(runner.preflight(request).status == "error")
XCUIApplication.elements = ["home": [home]]
private let reused = runner.preflight(request)
assert(reused.status == "already_authenticated" && !reused.environmentVerified)
XCUIApplication.elements = ["username": [username], "password": [password], "submit": [submit], "Staging": [submit]]
submit.label = "Staging"
assert(runner.preflight(request).code == "ENVIRONMENT_NOT_VERIFIED")
XCUIApplication.elements["Staging"] = [environment]
submit.label = "Sign in"
assert(runner.preflight(request).status == "ready")
''')

    def _check_swift(self, checks):
        directory = ios.helper_directory().parent / "SwiftChecks"
        ios.verify_output(directory)
        directory.mkdir(parents=True, exist_ok=True)
        source = (ios.SKILL_DIR / "ios/RunnerTests+AccountLogin.swift").read_text()
        source = source.split("extension RunnerTests {", 1)[0].replace("import XCTest\n", "")
        source = source.replace("__MOBILE_QA_RELEASE_DIGEST__", DIGEST) + checks
        with tempfile.TemporaryDirectory(dir=directory) as folder:
            temporary = Path(folder)
            swift, binary = temporary / "ProtocolCheck.swift", temporary / "check"
            swift.write_text(source.replace('"__FIXTURE_HOME__"', json.dumps(str(temporary / "fake-home"), ensure_ascii=False)))
            cache = directory / "ModuleCache"
            ios.verify_output(cache)
            cache.mkdir(exist_ok=True)
            env = {**os.environ, "TMPDIR": str(temporary) + "/"}
            with (directory / (self._testMethodName + ".log")).open("w") as log:
                result = subprocess.run(["/usr/bin/xcrun", "swiftc", "-module-cache-path", str(cache), str(swift), "-o", str(binary)],
                                        env=env, stdout=log, stderr=log)
                self.assertEqual(result.returncode, 0, "합성 프로토콜 검사 컴파일 실패")
                result = subprocess.run([str(binary)], env=env, stdout=log, stderr=log)
                self.assertEqual(result.returncode, 0, "합성 프로토콜 검사 실패")


if __name__ == "__main__":
    unittest.main()
