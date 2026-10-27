"""Inherited credential input contracts. Author: JunyoungJung · 2026-10-27."""

import base64
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
NATIVE = sys.platform == "darwin" and shutil.which("xcrun") and os.environ.get("MOBILE_QA_NATIVE_TESTS") == "1"
FIXTURE = {
    "username": "  사용자 e\u0301\t\n'\"$`\\ 이름  ",
    "password": "  비밀🙂\n\r\t !@#$%^&*()'\"`\\ e\u0301  ",
}


def section(source, start, end):
    return start + source.split(start, 1)[1].split(end, 1)[0]


@unittest.skipUnless(NATIVE, "Opt in to isolated Swift tests with MOBILE_QA_NATIVE_TESTS=1")
class NativeCredentialInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        requested = os.environ.get("TMPDIR")
        if not requested or not Path(requested).is_absolute():
            raise RuntimeError("Native tests require an explicit absolute TMPDIR for isolated output")
        cls.work = Path(requested).resolve(strict=True)
        if not cls.work.is_dir() or not os.access(cls.work, os.W_OK):
            raise RuntimeError("The isolated native test TMPDIR is unavailable")
        cls.cache = cls.work / "chat-credential-native-ModuleCache"
        outputs = [cls.cache]
        outputs += [cls.work / ("chat-credential-native-" + name + "-compile.log")
                    for name in ["reader", "helper", "production"]]
        if any(not path.resolve().is_relative_to(cls.work) for path in outputs):
            raise RuntimeError("Native test output must stay in the caller-supplied TMPDIR")
        cls.temporary = tempfile.TemporaryDirectory(prefix="chat-credential-native-", dir=cls.work)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.folder = Path(cls.temporary.name)
        cls.cache.mkdir(exist_ok=True)
        cls.env = {**os.environ, "TMPDIR": str(cls.work) + "/"}

        store = (ROOT / "native/CredentialFileStore.swift").read_text()
        login = (ROOT / "native/SecureLogin.swift").read_text()
        credentials = section(store, "enum CredentialStoreFailure:", "struct CredentialLocator:")
        parser = "private enum CredentialJSON {" + store.split("private enum CredentialJSON {", 1)[1]
        io = section(login, "enum SecureIO {", "final class LoginChild")
        cls.common = ("import Foundation\nimport Darwin\n"
                      "struct SecureLoginFailure: Error { let code: String }\n"
                      + credentials + parser + io)
        fixture = json.dumps(FIXTURE, ensure_ascii=False).encode()
        cls.expected = """
let fixture = try! StoredCredentials.decode(Data(base64Encoded: "%s")!)
func verifyFixture(_ value: StoredCredentials) {
    precondition(Array(value.username.utf8) == Array(fixture.username.utf8))
    precondition(Array(value.password.utf8) == Array(fixture.password.utf8))
}
""" % base64.b64encode(fixture).decode()
        checks = cls.expected + """
let descriptor = Int32(CommandLine.arguments[1])!
do {
    let value = try StoredCredentials.readFromPipe(descriptor)
    verifyFixture(value)
    print("accepted")
} catch {
    print("rejected")
    exit(1)
}
"""
        cls.reader = cls._compile("reader", cls.common + checks)

        locator = section(store, "struct CredentialLocator:", "struct CredentialFileStore:")
        stubs = cls.expected + """
struct CredentialFileStore {
    func create(id: String, credentials: StoredCredentials) throws -> Data {
        precondition(id == "synthetic-fixture")
        verifyFixture(credentials)
        return Data([1, 2, 3])
    }
    func removeCreated(id: String, reference: Data) throws {
        precondition(id == "synthetic-fixture" && reference == Data([1, 2, 3]))
        try FileHandle.standardOutput.write(contentsOf: Data("rollback_observed\\n".utf8))
    }
    func read(id: String, reference: Data) throws -> StoredCredentials {
        precondition(CommandLine.arguments.dropFirst().first != "login-preflight")
        return fixture
    }
    func update(id: String, reference: Data, credentials: StoredCredentials) throws {}
    func check(_ locators: [CredentialLocator]) throws -> Int { locators.count }
    func migrate(_ locators: [CredentialLocator], read: (CredentialLocator) throws -> StoredCredentials) throws -> Int { 0 }
}
enum CredentialFileStoreSelfTest { static func run() throws {} }
struct SecureLoginRequest {
    let id = "synthetic-fixture"
    let reference = Data([1, 2, 3])
    let plan = 0
    static func read() throws -> Self { Self() }
    static func readPreflightPlan() throws -> Int { 0 }
}
enum SecureLogin {
    static func safetySelfTest() throws {}
    static func run(_ plan: Int, preflightOnly: Bool = false, credentials: () throws -> Data) throws -> [String: Any] {
        preflightOnly ? ["status": "ready", "environment_verified": true] : [:]
    }
}
"""
        helper_source = cls.folder / "KeychainHelper.swift"
        helper_source.write_text((ROOT / "native/KeychainHelper.swift").read_text())
        cls.helper = cls._compile("helper", cls.common + locator + stubs, helper_source)

    @classmethod
    def _compile(cls, name, source, helper=None):
        source_path = cls.folder / (name + ".swift")
        source_path.write_text(source)
        binary = cls.folder / name
        arguments = ["/usr/bin/xcrun", "swiftc", "-module-cache-path", str(cls.cache)]
        if helper:
            arguments += ["-parse-as-library", str(source_path), str(helper)]
        else:
            arguments.append(str(source_path))
        arguments += ["-o", str(binary)]
        with (cls.work / ("chat-credential-native-" + name + "-compile.log")).open("w") as log:
            result = subprocess.run(arguments, env=cls.env, stdout=log, stderr=log)
        if result.returncode:
            raise RuntimeError("Native credential harness compilation failed; inspect its external compiler log")
        return binary

    def _run(self, binary, descriptor, arguments=(), pass_fds=(), control=b""):
        result = subprocess.run([str(binary), *arguments, str(descriptor)], input=control,
                                pass_fds=pass_fds, capture_output=True, env=self.env, timeout=5)
        private_values = [value.encode() for value in FIXTURE.values()]
        private_values += [json.dumps(value, ensure_ascii=ascii).encode() for value in FIXTURE.values()
                           for ascii in [False, True]]
        private_values += [b'"username"', b'"password"']
        self.assertTrue(all(value not in result.stdout + result.stderr for value in private_values),
                        "Native credential output must not contain credential fields")
        self.assertEqual(result.stderr, b"", "Native credential diagnostics must be value-free")
        return result

    def test_preflight_helper_command_never_reads_stored_credentials(self):
        result = subprocess.run([str(self.helper), "login-preflight"], input=b"", capture_output=True,
                                env=self.env, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"status": "ready", "environment_verified": True})
        self.assertEqual(result.stderr, b"")

    def _pipe(self, payload, helper=False, control=b"commit\n", metadata=None):
        read_fd, write_fd = os.pipe()
        writer_errors = []

        def write():
            try:
                with os.fdopen(write_fd, "wb") as stream:
                    stream.write(payload)
            except BrokenPipeError:
                pass
            except Exception as error:
                writer_errors.append(type(error).__name__)

        writer = threading.Thread(target=write)
        writer.start()
        try:
            arguments = metadata if metadata is not None else ["create-stdin", "--id", "synthetic-fixture",
                                                              "--context", "Synthetic account", "--input-fd"]
            result = self._run(self.helper if helper else self.reader, read_fd,
                               arguments=arguments if helper else (), pass_fds=(read_fd,), control=control)
        finally:
            os.close(read_fd)
            writer.join(timeout=5)
        self.assertFalse(writer.is_alive(), "Synthetic credential writer did not finish")
        self.assertFalse(writer_errors, "Synthetic credential writer failed")
        return result

    def test_pipe_preserves_exact_utf8_and_control_characters(self):
        result = self._pipe(json.dumps(FIXTURE, ensure_ascii=False).encode() + b"\n")
        self.assertEqual((result.returncode, result.stdout), (0, b"accepted\n"))

    def test_socket_is_supported(self):
        left, right = socket.socketpair()
        with left, right:
            right.sendall(json.dumps(FIXTURE, ensure_ascii=False).encode() + b"\n")
            result = self._run(self.reader, left.fileno(), pass_fds=(left.fileno(),))
        self.assertEqual((result.returncode, result.stdout), (0, b"accepted\n"))

    def test_transport_limit_accepts_exact_boundary_and_rejects_oversized_valid_json(self):
        encoded = json.dumps(FIXTURE, ensure_ascii=False).encode()
        for size, expected in [(262144, (0, b"accepted\n")), (262145, (1, b"rejected\n"))]:
            with self.subTest(size=size):
                result = self._pipe(b" " * (size - len(encoded)) + encoded + b"\n")
                self.assertEqual((result.returncode, result.stdout), expected)

    def test_invalid_payloads_are_rejected_without_values(self):
        invalid = [b"", b"{", b"{}\n", b"[]\n", b'{"username":"x","password":"x"',
                   b'{"username":"x","password":"x","username":"y"}\n',
                   b'{"username":"x","password":"x","user\\u006eame":"y"}\n',
                   b'{"username":"x","password":false}\n',
                   b'{"username":"x","password":1}\n', b'{"username":null,"password":"x"}\n',
                   b'{"username":"","password":"x"}\n', b'{"username":"x","password":""}\n',
                   b'{"username":"x","password":"x","extra":"x"}\n',
                   json.dumps({"username": "x", "password": "x" * 16385}).encode() + b"\n",
                   json.dumps({"username": "🙂" * 4097, "password": "x"}).encode() + b"\n",
                   b" " * 262145 + b"\n"]
        for index, payload in enumerate(invalid):
            with self.subTest(case=index):
                result = self._pipe(payload)
                self.assertEqual((result.returncode, result.stdout), (1, b"rejected\n"))

    def test_standard_closed_regular_file_and_tty_descriptors_are_rejected(self):
        for descriptor in [-1, 0, 1, 2, 2147483647]:
            with self.subTest(descriptor=descriptor):
                result = self._run(self.reader, descriptor)
                self.assertEqual((result.returncode, result.stdout), (1, b"rejected\n"))
        with tempfile.TemporaryFile(dir=self.folder) as stream:
            stream.write(json.dumps(FIXTURE).encode() + b"\n")
            stream.seek(0)
            result = self._run(self.reader, stream.fileno(), pass_fds=(stream.fileno(),))
            self.assertEqual((result.returncode, result.stdout), (1, b"rejected\n"))
        master, slave = os.openpty()
        try:
            result = self._run(self.reader, slave, pass_fds=(slave,))
            self.assertEqual((result.returncode, result.stdout), (1, b"rejected\n"))
        finally:
            os.close(master)
            os.close(slave)

    def test_create_stdin_preserves_transaction_commit_and_rollback(self):
        payload = json.dumps(FIXTURE, ensure_ascii=False).encode() + b"\n"
        for control, status in [(b"commit\n", "committed"), (b"rollback\n", "rolled_back"), (b"", "rolled_back")]:
            with self.subTest(status=status, eof=not control):
                result = self._pipe(payload, helper=True, control=control)
                lines = result.stdout.splitlines()
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(lines[0]), {"status": "created", "persistent_ref": "AQID"})
                if status == "rolled_back":
                    self.assertEqual(lines[1], b"rollback_observed")
                self.assertEqual(json.loads(lines[-1]), {"status": status})

    def test_create_stdin_validates_metadata_before_waiting_for_credentials(self):
        invalid = [
            ["create-stdin", "--id", "invalid alias", "--context", "Synthetic", "--input-fd"],
            ["create-stdin", "--id", "synthetic-fixture", "--context", "", "--input-fd"],
            ["create-stdin", "--id", "synthetic-fixture", "--context", "Synthetic\n", "--input-fd"],
            ["create-stdin", "--id", "synthetic-fixture", "--id", "duplicate", "--input-fd"],
        ]
        read_fd, write_fd = os.pipe()
        try:
            for arguments in invalid:
                result = self._run(self.helper, read_fd, arguments=arguments, pass_fds=(read_fd,))
                self.assertEqual((result.returncode, json.loads(result.stdout)),
                                 (1, {"status": "error", "code": "invalid_arguments"}))
        finally:
            os.close(read_fd)
            os.close(write_fd)
        arguments = ["create-stdin", "--id", "synthetic-fixture", "--context", "Synthetic", "--input-fd"]
        for invalid_fd in ["0", "2", "-1", "03", "+3", "text", "2147483648"]:
            result = self._run(self.helper, invalid_fd, arguments=arguments)
            self.assertEqual((result.returncode, json.loads(result.stdout)),
                             (1, {"status": "error", "code": "invalid_arguments"}))

    def test_production_native_sources_compile_together_without_running(self):
        values = {"releaseDigest": "0" * 64, "volumeMount": str(self.folder / "synthetic-volume"),
                  "volumeUUID": "00000000-0000-4000-8000-000000000000", "workRoot": str(self.folder),
                  "adbPath": str(self.folder / "synthetic-unused-adb"), "adbSHA256": "0" * 64,
                  "androidJARPath": str(self.folder / "synthetic-unused.jar"), "androidJARSHA256": "0" * 64,
                  "iosXCTestRunPath": None, "iosRunnerRoot": None, "iosRunnerTreeSHA256": None,
                  "iosDeveloperDirectory": None, "iosXcodeBuildSHA256": None,
                  "iosSimulatorXCTestRunPath": None, "iosSimulatorRunnerRoot": None,
                  "iosSimulatorRunnerTreeSHA256": None, "iosSimulatorDeveloperDirectory": None,
                  "iosSimulatorXcodeBuildSHA256": None}
        lines = ["// Synthetic compile-only identities; no driver execution or credential access.", "enum DriverPins {"]
        for key, value in values.items():
            lines.append("static let " + key + ": String" + ("?" if key.startswith("ios") else "")
                         + " = " + ("nil" if value is None else json.dumps(value)))
        pins = self.folder / "DriverPins.swift"
        pins.write_text("\n".join(lines + ["}", ""]))
        arguments = ["/usr/bin/xcrun", "swiftc", "-parse-as-library", "-O", "-target",
                     platform.machine() + "-apple-macos13.0", "-module-cache-path", str(self.cache)]
        arguments += [str(path) for path in sorted((ROOT / "native").glob("*.swift"))]
        arguments += [str(pins), "-o", str(self.folder / "compile-only-helper")]
        with (self.work / "chat-credential-native-production-compile.log").open("w") as log:
            result = subprocess.run(arguments, env=self.env, stdout=log, stderr=log, timeout=180)
        self.assertEqual(result.returncode, 0, "Production native compilation failed; inspect its external compiler log")


if __name__ == "__main__":
    unittest.main()
