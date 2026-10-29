"""로컬 저장·조회 실제 파일 검증. Author: JunyoungJung · 2026-10-29."""

import contextlib
from concurrent.futures import ThreadPoolExecutor
import base64
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import test_accounts as cli
from account_catalog import Catalog
from credential_store import CredentialStore


SYNTHETIC_CREDENTIALS = {"username": '  synthetic 사용자\n"quote"  ',
                         "password": ' synthetic-only \n한글\\$()`value`\t '}
PROJECT = "example.invalid/team/sample"
SYNTHETIC_OTHER_REFERENCE = base64.b64encode(b"synthetic-mismatched-reference").decode("ascii")


class LocalAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.catalog = Catalog(self.folder / "accounts.json")
        self.store = CredentialStore(self.folder / "credentials.json")
        for patcher in [patch.object(cli, "Catalog", return_value=self.catalog),
                        patch.object(cli, "CredentialStore", return_value=self.store),
                        patch.object(cli, "project_key", return_value=PROJECT)]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, arguments, value=None):
        output = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(json.dumps(value))), contextlib.redirect_stdout(output):
            code = cli.main(arguments)
        result = json.loads(output.getvalue())
        if arguments[0] != "get":
            self.assertNotIn(SYNTHETIC_CREDENTIALS["username"], output.getvalue())
            self.assertNotIn(json.dumps(SYNTHETIC_CREDENTIALS["password"]), output.getvalue())
        return code, result

    def register(self, name="sample", *extra):
        return self.run_cli(["register", "--name", name, "--credentials-stdin", *extra], SYNTHETIC_CREDENTIALS)

    def test_register_get_update_without_native_tools_or_reentry(self):
        with patch("subprocess.run", side_effect=AssertionError("native process must not run")):
            code, result = self.register()
            self.assertEqual((code, result["status"]), (0, "registered"))
            self.assertEqual((result["account"]["environment"], result["account"]["role"]), ("default", "tester"))
            code, result = self.run_cli(["get", "sample"])
            self.assertEqual((code, result["status"]), (0, "found"))
            self.assertEqual({key: result[key] for key in SYNTHETIC_CREDENTIALS}, SYNTHETIC_CREDENTIALS)
            replacement = {"username": "synthetic-replacement", "password": "synthetic-replacement-only"}
            self.assertEqual(self.run_cli(["update", "sample", "--credentials-stdin"], replacement)[0], 0)
            self.assertEqual(self.store.read(self.catalog.list()[0]), replacement)
            self.assertEqual(self.run_cli(["check"]), (0, {"status": "ready", "count": 1}))
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.folder.stat().st_mode), 0o700)

    def test_existing_native_file_reads_without_migration_or_writes(self):
        locator = "c3ludGhldGljLW5hdGl2ZS1yZWZlcmVuY2U="
        self.store.path.write_text(json.dumps({"version": 1, "accounts": [
            {"id": "existing", "reference": locator, **SYNTHETIC_CREDENTIALS}]}))
        self.store.path.chmod(0o600)
        self.catalog.add({"id": "existing", "project": PROJECT, "environment": "production",
                          "role": "tester", "capabilities": [], "keychain_ref": locator})
        before = self.store.path.read_bytes()
        code, result = self.run_cli(["get", "existing"])
        self.assertEqual(code, 0)
        self.assertEqual(result["password"], SYNTHETIC_CREDENTIALS["password"])
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_registration_failure_rolls_back_and_preserves_existing_values(self):
        self.register()
        original = self.store.path.read_bytes()
        with patch.object(self.catalog, "add", side_effect=ValueError("synthetic failure")):
            self.assertEqual(self.register("second")[0], 1)
        self.assertEqual(json.loads(self.store.path.read_bytes()), json.loads(original))
        self.assertEqual(self.run_cli(["get", "sample"])[0], 0)

    def test_duplicate_alias_and_invalid_input_do_not_change_saved_accounts(self):
        self.register()
        original = self.store.path.read_bytes()
        self.assertEqual(self.register()[0], 1)
        for value in [{"username": "synthetic"}, {"username": "", "password": "synthetic-only"},
                      {**SYNTHETIC_CREDENTIALS, "extra": True}]:
            self.assertEqual(self.run_cli(["register", "--name", "second", "--credentials-stdin"], value)[0], 1)
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertEqual(len(self.catalog.list()), 1)

    def test_find_has_optional_filters_and_returns_binding_without_blocking(self):
        device = self.folder / "device.json"
        device.write_text(json.dumps({"platform": "android", "name": "Synthetic phone", "identifier": None}))
        self.register("bound", "--environment", "production", "--device-file", str(device))
        code, result = self.run_cli(["find"])
        self.assertEqual((code, result["status"]), (0, "selected"))
        self.assertEqual(result["candidates"][0]["id"], "bound")
        self.assertNotIn("keychain_ref", result["candidates"][0])
        self.assertEqual(self.run_cli(["find", "--environment", "staging"])[1]["status"], "not_found")
        self.assertEqual(self.run_cli(["find", "--platform", "ios"])[1]["status"], "not_found")
        self.register("second")
        self.assertEqual(self.run_cli(["find"])[1]["status"], "ambiguous")

    def test_copy_reuses_existing_account_without_simulator_preparation(self):
        self.register()
        code, result = self.run_cli(["register", "--name", "copy", "--from-account", "sample"])
        self.assertEqual((code, result["status"]), (0, "registered"))
        self.assertEqual(self.run_cli(["get", "copy"])[1]["password"], SYNTHETIC_CREDENTIALS["password"])
        self.assertEqual(self.run_cli(["check"])[1]["count"], 2)

    def test_missing_or_mismatched_reference_never_returns_another_account(self):
        self.register()
        code, result = self.run_cli(["get", "missing"])
        self.assertEqual(code, 1)
        self.assertIn("list", result["message"])
        record = {**self.catalog.list()[0], "keychain_ref": SYNTHETIC_OTHER_REFERENCE}
        with self.assertRaises(ValueError):
            self.store.read(record)

    def test_concurrent_registrations_preserve_each_account_and_shared_references(self):
        def save(index):
            record = {"id": f"sample-{index}", "project": PROJECT, "environment": "default",
                      "role": "tester", "capabilities": []}
            value = {**SYNTHETIC_CREDENTIALS, "username": f"synthetic-{index}"}
            with self.store.registration(record["id"], value) as locator:
                self.catalog.add({**record, "keychain_ref": locator})
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(save, range(12)))
        records = self.catalog.list()
        self.assertEqual(self.store.check(records), {"status": "ready", "count": 12})
        for record in records:
            index = record["id"].removeprefix("sample-")
            self.assertEqual(self.store.read(record)["username"], f"synthetic-{index}")


if __name__ == "__main__":
    unittest.main()
