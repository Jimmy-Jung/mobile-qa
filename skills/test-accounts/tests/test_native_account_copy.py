"""Native account-copy transaction contracts. Author: JunyoungJung · 2026-10-27."""

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
class NativeAccountCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        requested = os.environ.get("TMPDIR")
        if not requested or not Path(requested).is_absolute():
            raise RuntimeError("Native account-copy tests require an explicit absolute TMPDIR")
        work = Path(requested).resolve(strict=True)
        if not work.is_dir() or not os.access(work, os.W_OK):
            raise RuntimeError("The isolated native test TMPDIR is unavailable")
        cls.temporary = tempfile.TemporaryDirectory(prefix="account-copy-native-", dir=work)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.folder = Path(cls.temporary.name)
        cls.env = {**os.environ, "TMPDIR": str(cls.folder) + "/"}
        store = (ROOT / "native/CredentialFileStore.swift").read_text()
        declarations = store.split("enum CredentialStoreFailure:", 1)[1].split("struct CredentialFileStore:", 1)[0]
        parser = "private enum CredentialJSON {" + store.split("private enum CredentialJSON {", 1)[1]
        stubs = r'''
import Foundation
import Darwin
struct SecureLoginFailure: Error { let code: String }
enum SecureIO {
    static let now: TimeInterval = 1
    static func line(_ descriptor: Int32, pending: inout Data, limit: Int, deadline: TimeInterval) throws -> Data {
        throw CredentialStoreFailure.invalidCredentials
    }
}
''' + "enum CredentialStoreFailure:" + declarations + parser + r'''
let syntheticFixturePassword = " synthetic-only-password\t$()` "
let credentialFixture = StoredCredentials(username: " synthetic user 漢字\n ", password: syntheticFixturePassword)
struct CredentialFileStore {
    func read(id: String, reference: Data) throws -> StoredCredentials {
        guard id == "source-fixture", reference == Data([1, 2, 3]) else {
            throw CredentialStoreFailure.invalidCredentials
        }
        return credentialFixture
    }
    func create(id: String, credentials: StoredCredentials) throws -> Data {
        precondition(id == "copy-fixture")
        precondition(Array(credentials.username.utf8) == Array(credentialFixture.username.utf8))
        precondition(Array(credentials.password.utf8) == Array(credentialFixture.password.utf8))
        return Data([4, 5, 6])
    }
    func removeCreated(id: String, reference: Data) throws {
        precondition(id == "copy-fixture" && reference == Data([4, 5, 6]))
        try FileHandle.standardOutput.write(contentsOf: Data("rollback_observed\n".utf8))
    }
    func update(id: String, reference: Data, credentials: StoredCredentials) throws {}
    func check(_ locators: [CredentialLocator]) throws -> Int { locators.count }
    func migrate(_ locators: [CredentialLocator], read: (CredentialLocator) throws -> StoredCredentials) throws -> Int { 0 }
}
enum CredentialFileStoreSelfTest { static func run() throws {} }
struct SecureLoginRequest {
    let id = "source-fixture"
    let reference = Data([1, 2, 3])
    let plan = 0
    static func read() throws -> Self { Self() }
    static func readPreflightPlan() throws -> Int { 0 }
}
enum SecureLogin {
    static func safetySelfTest() throws {}
    static func run(_ plan: Int, preflightOnly: Bool = false, credentials: () throws -> Data) throws -> [String: Any] { [:] }
}
'''
        harness = cls.folder / "CopyHarness.swift"
        helper = cls.folder / "KeychainHelper.swift"
        harness.write_text(stubs)
        helper.write_text((ROOT / "native/KeychainHelper.swift").read_text())
        cls.binary = cls.folder / "helper"
        cache = work / "account-copy-native-ModuleCache"
        cache.mkdir(exist_ok=True)
        with (work / "account-copy-native-compile.log").open("w") as log:
            result = subprocess.run([
                "/usr/bin/xcrun", "swiftc", "-parse-as-library", "-module-cache-path", str(cache),
                str(harness), str(helper), "-o", str(cls.binary),
            ], env=cls.env, stdout=log, stderr=log)
        if result.returncode:
            raise RuntimeError("Native account-copy harness compilation failed; inspect the isolated compiler log")

    def run_copy(self, *, control=b"commit\n", changes=()):
        arguments = [
            "create-copy", "--id", "copy-fixture", "--context", "synthetic-project / staging / learner",
            "--source-id", "source-fixture", "--source-ref", "AQID", *changes,
        ]
        result = subprocess.run([str(self.binary), *arguments], input=control,
                                env=self.env, capture_output=True, timeout=5)
        self.assertEqual(result.stderr, b"", "Copy diagnostics must contain no raw credential values")
        for value in [b"synthetic user", b"synthetic-only-password", b'"username"', b'"password"']:
            self.assertNotIn(value, result.stdout + result.stderr)
        return result

    def test_copy_reads_matching_source_and_commits_only_the_new_item(self):
        result = self.run_copy()
        self.assertEqual(result.returncode, 0)
        self.assertEqual([json.loads(line) for line in result.stdout.splitlines()], [
            {"status": "created", "persistent_ref": "BAUG"}, {"status": "committed"},
        ])

    def test_copy_rollback_or_control_eof_only_removes_the_new_item(self):
        for control in [b"rollback\n", b""]:
            with self.subTest(control=control):
                result = self.run_copy(control=control)
                self.assertEqual(result.returncode, 0)
                lines = result.stdout.splitlines()
                self.assertEqual(json.loads(lines[0]), {"status": "created", "persistent_ref": "BAUG"})
                self.assertEqual(lines[1], b"rollback_observed")
                self.assertEqual(json.loads(lines[2]), {"status": "rolled_back"})

    def test_copy_rejects_wrong_source_alias_reference_and_duplicate_options(self):
        for old, new in [("source-fixture", "another-fixture"), ("AQID", "BwgJ")]:
            with self.subTest(value=new):
                arguments = [
                    "create-copy", "--id", "copy-fixture", "--context", "synthetic context",
                    "--source-id", "source-fixture", "--source-ref", "AQID",
                ]
                arguments[arguments.index(old)] = new
                result = subprocess.run([str(self.binary), *arguments], input=b"commit\n",
                                        env=self.env, capture_output=True, timeout=5)
                self.assertEqual((result.returncode, json.loads(result.stdout)),
                                 (1, {"status": "error", "code": "invalid_credentials"}))
        result = self.run_copy(changes=["--source-id", "source-fixture"])
        self.assertEqual((result.returncode, json.loads(result.stdout)),
                         (1, {"status": "error", "code": "invalid_arguments"}))


if __name__ == "__main__":
    unittest.main()
