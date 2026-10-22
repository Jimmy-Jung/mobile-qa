"""계정 조회·등록의 비밀값 경계. Author: JunyoungJung · 2026-10-20."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import test_accounts as cli

REF = "b3BhcXVlLXJlZmVyZW5jZQ=="
RECORD = {"id": "sample-student", "project": "example.test/team/app", "environment": "staging", "role": "student", "capabilities": [], "keychain_ref": REF, "related_projects": [], "related_environments": [], "download_device": None}


class AccountCLITests(unittest.TestCase):
    def setUp(self):
        self.catalog = Mock()
        self.catalog.list.return_value = []
        self.keychain = Mock()
        self.registration = MagicMock()
        self.registration.__enter__.return_value = REF
        self.keychain.registration.return_value = self.registration
        self.patches = [
            patch.object(cli, "Catalog", return_value=self.catalog),
            patch.object(cli, "Keychain", return_value=self.keychain),
            patch.object(cli, "project_key", return_value="example.test/team/app"),
            patch.object(cli, "dialog", side_effect=["staging", "student", "sample-student"]),
            patch.object(cli, "build_login_plan", side_effect=lambda project, environment, platform, device, **options: {"device": device, "environment": environment, "app_id": options["app_id"] or "org.example.sample", "environment_selector": {"label": "Staging"}}),
        ]
        self.mocks = [item.start() for item in self.patches]
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])

    def run_cli(self, args):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = cli.main(args)
        text = output.getvalue() + error.getvalue()
        for hidden in ["user-sentinel", "password-sentinel", REF, "keychain_ref", "private-device-sentinel"]:
            self.assertNotIn(hidden, text)
        return code, json.loads(output.getvalue())

    def register_args(self):
        return ["register", "--name", "sample-student", "--environment", "staging", "--role", "student"]

    def test_registration_uses_native_dialog_and_stores_only_reference(self):
        code, result = self.run_cli(self.register_args())
        self.assertEqual((code, result["status"]), (0, "registered"))
        self.keychain.registration.assert_called_once_with("sample-student", cli.context(RECORD))
        self.catalog.add.assert_called_once_with(RECORD)
        self.mocks[3].assert_not_called()

    def test_missing_metadata_prompts_only_for_metadata(self):
        code, _ = self.run_cli(["register"])
        self.assertEqual(code, 0)
        self.assertEqual(self.mocks[3].call_count, 3)
        self.catalog.add.assert_called_once_with(RECORD)

    def test_find_selects_metadata_without_native_access(self):
        self.catalog.find.return_value = {"status": "selected", "candidates": [RECORD]}
        code, result = self.run_cli(["find", "--environment", "staging", "--role", "student", "--capability", "download"])
        self.assertEqual((code, result["status"]), (0, "selected"))
        self.mocks[1].assert_not_called()
        self.mocks[3].assert_not_called()
        self.catalog.find.assert_called_once_with("example.test/team/app", "staging", "student", ["download"], device=None, platform=None)

    def test_shared_projects_environments_and_download_device_registration(self):
        with tempfile.TemporaryDirectory(prefix="account-cli-") as folder:
            path = Path(folder) / "device.json"
            device = {"platform": "android", "name": "Test phone", "identifier": "private-device-sentinel"}
            path.write_text(json.dumps(device))
            self.mocks[2].side_effect = ["example.test/team/app", "example.test/team/android", "example.test/team/flutter"]
            args = self.register_args() + ["--related-project", "/android", "--related-project", "/flutter", "--related-environment", "development", "--download-device-file", str(path)]
            code, result = self.run_cli(args)
        self.assertEqual(code, 0)
        saved = self.catalog.add.call_args.args[0]
        self.assertEqual(saved["related_projects"], ["example.test/team/android", "example.test/team/flutter"])
        self.assertEqual(saved["related_environments"], ["development"])
        self.assertEqual(saved["download_device"], device)
        prompt = self.keychain.registration.call_args.args[1]
        for project in [saved["project"], *saved["related_projects"]]:
            self.assertIn(project, prompt)
        self.assertEqual(result["account"]["download_device"], {"platform": "android", "name": "Test phone", "identifier_saved": True})

    def test_download_target_is_forwarded_and_identifier_not_returned(self):
        record = {**RECORD, "download_device": {"platform": "android", "name": "Test phone", "identifier": "private-device-sentinel"}}
        self.catalog.find.return_value = {"status": "selected", "candidates": [record]}
        code, _ = self.run_cli(["find", "--environment", "staging", "--role", "student", "--capability", "download", "--device", "Test phone", "--platform", "android"])
        self.assertEqual(code, 0)
        self.catalog.find.assert_called_once_with("example.test/team/app", "staging", "student", ["download"], device="Test phone", platform="android")

    def test_list_does_not_print_native_reference(self):
        self.catalog.list.return_value = [RECORD]
        code, result = self.run_cli(["list"])
        self.assertEqual(code, 0)
        self.assertEqual(result["accounts"], [cli.public_record(RECORD)])
        self.mocks[1].assert_not_called()

    def test_check_reads_catalog_and_prints_only_ready_count(self):
        self.catalog.list.return_value = [RECORD]
        self.keychain.check.return_value = {"status": "ready", "count": 1}
        code, result = self.run_cli(["check"])
        self.assertEqual((code, result), (0, {"status": "ready", "count": 1}))
        self.catalog.list.assert_called_once_with()
        self.keychain.check.assert_called_once_with([RECORD])
        self.catalog.add.assert_not_called()
        self.keychain.migrate_keychain.assert_not_called()

    def test_migration_preserves_catalog_and_requires_explicit_interaction_flag(self):
        self.catalog.list.return_value = [RECORD]
        self.keychain.migrate_keychain.return_value = {"status": "migrated", "count": 1}
        for arguments, allow_interaction in [(["migrate-keychain"], False), (["migrate-keychain", "--allow-interaction"], True)]:
            with self.subTest(allow_interaction=allow_interaction):
                code, result = self.run_cli(arguments)
                self.assertEqual((code, result), (0, {"status": "migrated", "count": 1}))
                self.keychain.migrate_keychain.assert_called_with([RECORD], allow_interaction=allow_interaction)
        self.catalog.add.assert_not_called()
        self.mocks[3].assert_not_called()

    def test_storage_commands_reject_malformed_or_secret_bearing_summary(self):
        self.catalog.list.return_value = [RECORD]
        for command, method, status in [("check", self.keychain.check, "ready"), ("migrate-keychain", self.keychain.migrate_keychain, "migrated")]:
            for response in [{"status": status, "count": True}, {"status": status, "count": 0}, {"status": status, "count": 1, "password": "password-sentinel"}, {"status": "password-sentinel", "count": 1}]:
                with self.subTest(command=command, fields=list(response)):
                    method.return_value = response
                    code, result = self.run_cli([command])
                    self.assertEqual((code, result["status"]), (1, "error"))

    def test_migration_approval_requirement_is_safe_and_does_not_retry(self):
        self.catalog.list.return_value = [RECORD]
        self.keychain.migrate_keychain.side_effect = cli.KeychainError("password-sentinel", code="migration_requires_approval")
        code, result = self.run_cli(["migrate-keychain"])
        self.assertEqual((code, result["status"], result["code"]), (1, "error", "migration_requires_approval"))
        self.keychain.migrate_keychain.assert_called_once_with([RECORD], allow_interaction=False)
        self.keychain.login.assert_not_called()
        self.catalog.add.assert_not_called()

    def test_storage_commands_do_not_allow_secret_or_reference_arguments(self):
        for command in ["check", "migrate-keychain"]:
            for option in ["--password", "--username", "--reference", "--export"]:
                with self.subTest(command=command, option=option):
                    code, result = self.run_cli([command, option, "password-sentinel"])
                    self.assertEqual((code, result["status"]), (1, "error"))
        self.mocks[0].assert_not_called()
        self.mocks[1].assert_not_called()

    def test_check_never_allows_keychain_interaction(self):
        code, result = self.run_cli(["check", "--allow-interaction"])
        self.assertEqual((code, result["status"]), (1, "error"))
        self.mocks[1].assert_not_called()

    def test_unknown_password_argument_is_not_echoed(self):
        code, result = self.run_cli(["list", "--password", "password-sentinel"])
        self.assertEqual((code, result["status"]), (1, "error"))
        self.mocks[0].assert_not_called()

    def test_cancelled_native_input_does_not_write_catalog(self):
        self.registration.__enter__.side_effect = cli.Cancelled()
        code, result = self.run_cli(self.register_args())
        self.assertEqual((code, result["status"]), (2, "cancelled"))
        self.catalog.add.assert_not_called()

    def test_duplicate_alias_does_not_open_native_dialog(self):
        self.catalog.list.return_value = [RECORD]
        code, _ = self.run_cli(self.register_args())
        self.assertEqual(code, 1)
        self.mocks[1].assert_not_called()

    def test_catalog_failure_aborts_unconfirmed_registration(self):
        self.catalog.add.side_effect = OSError("password-sentinel")
        code, _ = self.run_cli(self.register_args())
        self.assertEqual(code, 1)
        self.assertIs(self.registration.__exit__.call_args.args[0], OSError)

    def test_keychain_failure_does_not_write_catalog_or_rollback(self):
        self.registration.__enter__.side_effect = cli.KeychainError("password-sentinel")
        code, _ = self.run_cli(self.register_args())
        self.assertEqual(code, 1)
        self.catalog.add.assert_not_called()

    def test_update_uses_exact_reference_and_preserves_metadata(self):
        self.catalog.list.return_value = [RECORD]
        code, result = self.run_cli(["update", "sample-student"])
        self.assertEqual((code, result["status"]), (0, "updated"))
        self.keychain.update.assert_called_once_with("sample-student", REF, cli.context(RECORD))
        self.catalog.add.assert_not_called()

    def test_missing_update_alias_never_opens_native_dialog(self):
        code, _ = self.run_cli(["update", "missing"])
        self.assertEqual(code, 1)
        self.mocks[1].assert_not_called()

    def test_register_profile_uses_explicit_file_without_opening_account_store(self):
        with patch.object(cli, "register_profile", return_value={"status": "profile_registered", "name": "sample"}) as register:
            code, result = self.run_cli(["register-profile", "--project", "/project", "--file", "/reviewed.json"])
        self.assertEqual((code, result["status"]), (0, "profile_registered"))
        register.assert_called_once_with(Path("/project"), Path("/reviewed.json"))
        self.mocks[0].assert_not_called()
        self.mocks[1].assert_not_called()

    def login_args(self, *extra):
        return ["login", "--project", "/project", "--environment", "production", "--role", "learner", "--platform", "ios", "--device", "00008101-0012345678901234", "--login-profile", "sample", *extra]

    def login_record(self, identifier="first", **changes):
        self.mocks[2].return_value = "example.test/team/ios-app"
        return {**RECORD, "id": identifier, "project": self.mocks[2].return_value, "environment": "production", "role": "learner", **changes}

    def test_login_scopes_account_before_accessing_keychain(self):
        record = self.login_record()
        self.catalog.find.return_value = {"status": "selected", "candidates": [record]}
        self.keychain.login.return_value = {"status": "authenticated", "environment_verified": True}
        code, result = self.run_cli(self.login_args())
        self.assertEqual((code, result), (0, {"status": "authenticated", "environment_verified": True, "account": "first"}))
        self.catalog.find.assert_called_once_with(record["project"], "production", "learner", [], device="00008101-0012345678901234", platform="ios")
        selected, plan = self.keychain.login.call_args.args
        self.assertEqual(selected, record)
        self.assertEqual(plan["device"], "00008101-0012345678901234")
        self.assertEqual(plan["environment"], "production")

    def test_login_ambiguity_and_missing_scoped_alias_never_access_keychain(self):
        records = [self.login_record("first"), self.login_record("second")]
        self.catalog.find.return_value = {"status": "ambiguous", "candidates": records}
        code, result = self.run_cli(self.login_args())
        self.assertEqual((code, result), (1, {"status": "ambiguous"}))
        code, result = self.run_cli(self.login_args("--account", "outside-scope"))
        self.assertEqual((code, result), (1, {"status": "not_found"}))
        self.mocks[1].assert_not_called()

    def test_login_resolves_alias_only_inside_scoped_candidates(self):
        records = [self.login_record("first"), self.login_record("second")]
        self.catalog.find.return_value = {"status": "ambiguous", "candidates": records}
        self.keychain.login.return_value = {"status": "already_authenticated", "environment_verified": False}
        code, result = self.run_cli(self.login_args("--account", "second"))
        self.assertEqual((code, result), (1, {"status": "already_authenticated", "account": "second", "environment_verified": False}))
        self.assertEqual(self.keychain.login.call_args.args[0]["id"], "second")

    def test_login_prefers_account_registered_for_exact_physical_device(self):
        device = {"platform": "ios", "name": "Test phone", "identifier": "00008101-0012345678901234"}
        records = [self.login_record("other", download_device={**device, "identifier": "other-physical-device"}), self.login_record("matching", download_device=device)]
        self.catalog.find.return_value = {"status": "ambiguous", "candidates": records}
        self.keychain.login.return_value = {"status": "authenticated", "environment_verified": True}
        code, result = self.run_cli(self.login_args("--device-name", "Test phone"))
        self.assertEqual((code, result), (0, {"status": "authenticated", "account": "matching", "environment_verified": True}))
        self.assertEqual(self.keychain.login.call_args.args[0]["id"], "matching")
        self.catalog.find.assert_called_once_with(records[0]["project"], "production", "learner", [], device="Test phone", platform="ios")

    def test_download_login_rejects_name_match_with_conflicting_physical_identifier(self):
        record = self.login_record(download_device={"platform": "ios", "name": "Test phone", "identifier": "other-physical-device"})
        self.catalog.find.return_value = {"status": "selected", "candidates": [record]}
        code, result = self.run_cli(self.login_args("--capability", "download", "--device-name", "Test phone"))
        self.assertEqual((code, result), (1, {"status": "not_found"}))
        self.mocks[1].assert_not_called()

    def test_download_login_uses_explicit_device_name_when_identifier_is_unknown(self):
        record = self.login_record(download_device={"platform": "ios", "name": "Test phone", "identifier": None})
        self.catalog.find.return_value = {"status": "selected", "candidates": [record]}
        self.keychain.login.return_value = {"status": "authenticated", "environment_verified": True}
        code, result = self.run_cli(self.login_args("--capability", "download", "--device-name", "Test phone"))
        self.assertEqual((code, result), (0, {"status": "authenticated", "account": "first", "environment_verified": True}))
        self.assertEqual(self.keychain.login.call_args.args[1]["device"], "00008101-0012345678901234")

    def test_android_login_preserves_development_scope_and_known_app_override(self):
        record = self.login_record(environment="development")
        self.mocks[2].return_value = "example.test/team/android-app"
        record = {**record, "project": self.mocks[2].return_value}
        self.catalog.find.return_value = {"status": "selected", "candidates": [record]}
        self.keychain.login.return_value = {"status": "authenticated", "environment_verified": True}
        code, _ = self.run_cli(["login", "--project", "/android", "--environment", "development", "--role", "learner", "--platform", "android", "--device", "R39TEST123", "--app-id", "org.example.sample", "--login-profile", "sample"])
        self.assertEqual(code, 0)
        self.catalog.find.assert_called_once_with(record["project"], "development", "learner", [], device="R39TEST123", platform="android")
        plan = self.keychain.login.call_args.args[1]
        self.assertEqual(plan["app_id"], "org.example.sample")
        self.assertEqual(plan["environment_selector"], {"label": "Staging"})

    def test_login_rejects_untrusted_profile_before_native_access(self):
        self.mocks[4].side_effect = ValueError("password-sentinel")
        self.catalog.find.return_value = {"status": "selected", "candidates": [RECORD]}
        code, _ = self.run_cli(self.login_args())
        self.assertEqual(code, 1)
        self.mocks[2].return_value = "example.test/team/ios-app"
        code, _ = self.run_cli(self.login_args("--app-id", "com.test;password-sentinel"))
        self.assertEqual(code, 1)
        self.mocks[1].assert_not_called()

    def test_login_prints_only_safe_status_fields_not_raw_native_response(self):
        self.catalog.find.return_value = {"status": "selected", "candidates": [self.login_record()]}
        self.keychain.login.return_value = {"status": "failed", "raw_response": "password-sentinel"}
        code, result = self.run_cli(self.login_args())
        self.assertEqual((code, result), (1, {"status": "failed", "account": "first"}))

    def test_login_unknown_native_status_is_a_safe_error(self):
        self.catalog.find.return_value = {"status": "selected", "candidates": [self.login_record()]}
        self.keychain.login.return_value = {"status": "password-sentinel"}
        code, result = self.run_cli(self.login_args())
        self.assertEqual((code, result["status"]), (1, "error"))

    def test_login_preserves_validated_failure_cleanup(self):
        self.catalog.find.return_value = {"status": "selected", "candidates": [self.login_record()]}
        self.keychain.login.return_value = {"status": "failed", "code": "SUCCESS_NOT_OBSERVED", "cleanup": "unconfirmed"}
        code, result = self.run_cli(self.login_args())
        self.assertEqual(code, 1)
        self.assertEqual(result["cleanup"], "unconfirmed")
        self.assertEqual(result["code"], "SUCCESS_NOT_OBSERVED")

    def test_login_preserves_unverified_environment_when_reusing_session(self):
        self.catalog.find.return_value = {"status": "selected", "candidates": [self.login_record()]}
        self.keychain.login.return_value = {"status": "already_authenticated", "environment_verified": False}
        code, result = self.run_cli(self.login_args())
        self.assertEqual((code, result), (1, {"status": "already_authenticated", "account": "first", "environment_verified": False}))
        self.keychain.login.assert_called_once()
        self.keychain.registration.assert_not_called()
        self.keychain.update.assert_not_called()
        self.mocks[3].assert_not_called()

    def test_login_requires_explicit_verified_environment_for_successful_exit(self):
        self.catalog.find.return_value = {"status": "selected", "candidates": [self.login_record()]}
        for status in ["authenticated", "already_authenticated"]:
            for verification in [{}, {"environment_verified": False}, {"environment_verified": True}]:
                with self.subTest(status=status, verification=verification):
                    self.keychain.login.reset_mock()
                    self.keychain.login.return_value = {"status": status, **verification}
                    code, result = self.run_cli(self.login_args())
                    if status == "already_authenticated" and verification.get("environment_verified") is True:
                        self.assertEqual((code, result["status"]), (1, "error"))
                    else:
                        expected_code = 0 if status == "authenticated" and verification.get("environment_verified") is True else 1
                        self.assertEqual((code, result), (expected_code, {"status": status, "account": "first", **verification}))
                    self.keychain.login.assert_called_once()
        self.keychain.registration.assert_not_called()
        self.keychain.update.assert_not_called()
        self.mocks[3].assert_not_called()

    def test_login_explicit_empty_alias_is_not_automatic_selection(self):
        self.catalog.find.return_value = {"status": "selected", "candidates": [self.login_record()]}
        code, result = self.run_cli(self.login_args("--account", ""))
        self.assertEqual((code, result["status"]), (1, "error"))
        self.mocks[1].assert_not_called()

    def test_login_alias_cannot_override_matching_registered_device(self):
        device = {"platform": "ios", "name": "Test phone", "identifier": "00008101-0012345678901234"}
        records = [self.login_record("other", download_device={**device, "identifier": "other-physical-device"}), self.login_record("matching", download_device=device)]
        self.catalog.find.return_value = {"status": "ambiguous", "candidates": records}
        code, result = self.run_cli(self.login_args("--account", "other"))
        self.assertEqual((code, result), (1, {"status": "not_found"}))
        self.mocks[1].assert_not_called()

    def test_login_native_non_success_statuses_are_nonzero_and_redacted(self):
        self.catalog.find.return_value = {"status": "selected", "candidates": [self.login_record()]}
        for status in ["not_ready", "failed", "uncertain", "error"]:
            with self.subTest(status=status):
                self.keychain.login.return_value = {"status": status, "raw_response": "password-sentinel"}
                code, result = self.run_cli(self.login_args())
                self.assertEqual((code, result), (1, {"status": status, "account": "first"}))


if __name__ == "__main__":
    unittest.main()
