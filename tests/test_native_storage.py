"""Native selected-volume contracts. Author: JunyoungJung · 2026-10-22."""

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


@unittest.skipUnless(NATIVE, "Opt in to isolated Swift tests with MOBILE_QA_NATIVE_TESTS=1")
class NativeStorageTests(unittest.TestCase):
    def test_actual_native_volume_checks_accept_selected_local_storage_and_reject_mismatches(self):
        native = (ROOT / "skills/test-accounts/native/SecureLogin.swift").read_text()
        validation = "    static func verifyStorage() throws {" + native.split("    static func verifyStorage() throws {", 1)[1].split("    private static func digest(", 1)[0]
        selftest = (ROOT / "skills/test-accounts/native/CredentialFileStoreSelfTest.swift").read_text()
        fixture_boundary = selftest.split("    static func run() throws {", 1)[1].split("        guard mkdir(", 1)[0]
        harness = r'''
import Foundation
import CoreFoundation
import Darwin
struct SecureLoginFailure: Error { let code: String }
enum CredentialStoreFailure: Error { case storageFailed }
enum SecureIO { static let now: TimeInterval = 10 }
enum DriverPins {
  static var workRoot = ""
  static var volumeMount = ""
  static let volumeUUID = "11111111-2222-4333-8444-555555555555"
}
final class LoginChild {
  static var metadata: [String: Any] = [:]
  static var devices: [String: String] = [:]
  static var timeout = false
  static var invalidDF = false
  static var active = 0
  static var calls: [[String]] = []
  let path: String
  let arguments: [String]
  var stopped = false
  init(path: String, arguments: [String], temporaryDirectory: String) throws {
    self.path = path; self.arguments = arguments
    Self.active += 1; Self.calls.append([path] + arguments)
    assert(temporaryDirectory == DriverPins.workRoot + "/tmp/")
  }
  func collect(deadline: TimeInterval) throws -> Data {
    assert(deadline == 15)
    if Self.timeout { throw SecureLoginFailure(code: "transport_timeout") }
    if path == "/bin/df" {
      assert(arguments.count == 2 && arguments[0] == "-P")
      if Self.invalidDF { return Data("invalid df response".utf8) }
      let device = Self.devices[arguments[1]] ?? "/dev/disk404s1"
      return Data(("Filesystem 512-blocks Used Available Capacity Mounted on\n" + device + " 100 20 80 20% Fixture\n").utf8)
    }
    assert(path == "/usr/sbin/diskutil")
    assert(arguments == ["info", "-plist", Self.devices[DriverPins.workRoot] ?? "/dev/disk404s1"])
    return try PropertyListSerialization.data(fromPropertyList: Self.metadata, format: .binary, options: 0)
  }
  func stop() { if !stopped { Self.active -= 1; stopped = true } }
}
enum SecureLogin {
''' + validation + r'''
}
func fixtureBoundary() throws -> URL {
''' + fixture_boundary + r'''
  return root
}
let base = URL(fileURLWithPath: "__FIXTURE_ROOT__", isDirectory: true)
let external = base.appendingPathComponent("External", isDirectory: true)
let externalWork = external.appendingPathComponent("work", isDirectory: true)
let dataMount = base.appendingPathComponent("Data", isDirectory: true)
let internalWork = dataMount.appendingPathComponent("work", isDirectory: true)
let userAlias = base.appendingPathComponent("Users/fixture/work", isDirectory: true)
for directory in [externalWork, internalWork, userAlias] {
  try! FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
}
func reset(_ work: URL = externalWork, mount: URL = external, internalVolume: Bool = false) {
  assert(LoginChild.active == 0)
  DriverPins.workRoot = work.path; DriverPins.volumeMount = mount.path
  LoginChild.calls = []; LoginChild.timeout = false; LoginChild.invalidDF = false
  LoginChild.devices = [work.path: "/dev/disk9s1", mount.path: "/dev/disk9s1"]
  LoginChild.metadata = ["MountPoint": mount.path, "DeviceNode": "/dev/disk9s1",
    "VolumeUUID": DriverPins.volumeUUID, "Internal": internalVolume, "WritableVolume": true]
}
func accepted() {
  do { try SecureLogin.verifyStorage() } catch { assertionFailure("Valid selected volume was rejected") }
  assert(LoginChild.active == 0)
}
func rejected(_ expected: String = "storage_not_ready") {
  do { try SecureLogin.verifyStorage(); assertionFailure("Invalid selected volume was accepted") }
  catch let failure as SecureLoginFailure { assert(failure.code == expected) }
  catch { assertionFailure("Unexpected storage error") }
  assert(LoginChild.active == 0)
}
reset(); accepted()
reset(internalWork, mount: dataMount, internalVolume: true); accepted()
reset(userAlias, mount: dataMount, internalVolume: true); accepted()
assert(!userAlias.path.hasPrefix(dataMount.path + "/"))
let fixture = try! fixtureBoundary()
assert(fixture.deletingLastPathComponent().path == userAlias.path)
assert(LoginChild.active == 0)
reset(); LoginChild.metadata["VolumeUUID"] = "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"; rejected()
reset(); LoginChild.metadata["DeviceNode"] = "/dev/disk8s1"; rejected()
reset(); LoginChild.devices[external.path] = "/dev/disk8s1"; rejected()
reset(); LoginChild.metadata["MountPoint"] = dataMount.path; rejected()
reset(); LoginChild.metadata["WritableVolume"] = false; rejected()
reset(); LoginChild.metadata["WritableVolume"] = 1; rejected()
reset(); LoginChild.devices[externalWork.path] = "server:/share"; rejected()
reset(); LoginChild.devices[externalWork.path] = "/dev/disk9s1\rInjected"; rejected()
reset(); LoginChild.invalidDF = true; rejected()
reset(); LoginChild.timeout = true; rejected("transport_timeout")
reset(); assert(chmod(externalWork.path, 0o500) == 0); rejected(); assert(chmod(externalWork.path, 0o700) == 0)
let link = base.appendingPathComponent("linked-work")
try! FileManager.default.createSymbolicLink(at: link, withDestinationURL: externalWork)
reset(link); rejected(); assert(LoginChild.calls.isEmpty)
let linkedMount = base.appendingPathComponent("linked-mount")
try! FileManager.default.createSymbolicLink(at: linkedMount, withDestinationURL: external)
reset(externalWork, mount: linkedMount); rejected(); assert(LoginChild.calls.isEmpty)
let parentLink = base.appendingPathComponent("linked-parent")
try! FileManager.default.createSymbolicLink(at: parentLink, withDestinationURL: external)
reset(parentLink.appendingPathComponent("work")); rejected(); assert(LoginChild.calls.isEmpty)
'''
        with tempfile.TemporaryDirectory(prefix="native-storage-") as folder:
            temporary = Path(folder)
            cache = Path(tempfile.gettempdir()) / "mobile-qa-storage-module-cache"
            cache.mkdir(exist_ok=True)
            source = temporary / "StorageCheck.swift"
            source.write_text(harness.replace('"__FIXTURE_ROOT__"', json.dumps(str(temporary), ensure_ascii=False)))
            binary = temporary / "check"
            environment = {**os.environ, "TMPDIR": str(temporary) + "/"}
            log_path = Path(tempfile.gettempdir()) / "native-storage-check.log"
            with log_path.open("w") as log:
                compiled = subprocess.run(["/usr/bin/xcrun", "swiftc", "-module-cache-path", str(cache), str(source), "-o", str(binary)], env=environment, stdout=log, stderr=log)
                self.assertEqual(compiled.returncode, 0, "Native storage regression compilation failed; inspect isolated compiler log")
                executed = subprocess.run([str(binary)], env=environment, stdout=log, stderr=log)
                self.assertEqual(executed.returncode, 0, "Native storage boundary assertions failed; inspect isolated test log")


if __name__ == "__main__":
    unittest.main()
