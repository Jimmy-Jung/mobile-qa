"""네이티브 계정 저장소 경계 검증. Author: JunyoungJung · 2026-10-20."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import keychain_store as store

REF = "b3BhcXVlLXJlZmVyZW5jZQ=="


class NativeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.verified = patch.object(store, "verified_helper", return_value=Path("/native/keychain-helper"))
        self.verified.start()
        self.addCleanup(self.verified.stop)

    def process(self, initial=None, final="committed"):
        process = Mock(returncode=None)
        process.stdout = io.StringIO(initial if initial is not None else json.dumps({"status": "created", "persistent_ref": REF}) + "\n")
        process.poll.side_effect = lambda: process.returncode
        def communicate(**kwargs):
            process.returncode = 0
            return json.dumps({"status": final}), ""
        process.communicate.side_effect = communicate
        return process

    def test_registration_commits_only_after_caller_finishes(self):
        process = self.process()
        with patch.object(store.subprocess, "Popen", return_value=process) as launch:
            with store.Keychain().registration("sample", "project / staging / student") as persistent_ref:
                self.assertEqual(persistent_ref, REF)
                process.communicate.assert_not_called()
        self.assertEqual(launch.call_args.args[0], ["/native/keychain-helper", "create", "--id", "sample", "--context", "project / staging / student"])
        process.communicate.assert_called_once_with(input="commit\n")

    def test_caller_failure_cancels_only_its_open_registration(self):
        process = self.process(final="rolled_back")
        with patch.object(store.subprocess, "Popen", return_value=process):
            with self.assertRaises(RuntimeError):
                with store.Keychain().registration("sample", "metadata"):
                    raise RuntimeError("catalog failed")
        process.communicate.assert_called_once_with(input="rollback\n")
        self.assertNotIn(REF, repr(process.communicate.call_args))

    def test_update_binds_id_and_persistent_reference(self):
        with patch.object(store.subprocess, "run", return_value=Mock(returncode=0, stdout='{"status":"updated"}')) as run:
            store.Keychain().update("sample", REF, "metadata")
        self.assertEqual(run.call_args.args[0][1:], ["update", "--id", "sample", "--ref", REF, "--context", "metadata"])

    def test_check_passes_only_alias_and_opaque_reference_via_stdin(self):
        records = [{"id": "sample", "keychain_ref": REF, "unrelated": "secret-sentinel"}]
        response = {"status": "ready", "count": 1}
        with patch.object(store.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(response))) as run:
            self.assertEqual(store.Keychain().check(records), response)
        self.assertEqual(run.call_args.args[0], ["/native/keychain-helper", "check"])
        self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"accounts": [{"id": "sample", "reference": REF}]})
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertNotIn("secret-sentinel", repr(run.call_args))

    def test_migration_requires_explicit_interaction_flag(self):
        records = [{"id": "sample", "keychain_ref": REF}]
        for allow_interaction, arguments in [(False, ["migrate-keychain"]), (True, ["migrate-keychain", "--allow-interaction"])]:
            with self.subTest(allow_interaction=allow_interaction):
                response = {"status": "migrated", "count": 1}
                with patch.object(store.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(response))) as run:
                    self.assertEqual(store.Keychain().migrate_keychain(records, allow_interaction=allow_interaction), response)
                self.assertEqual(run.call_args.args[0][1:], arguments)
                self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"accounts": [{"id": "sample", "reference": REF}]})

    def test_storage_summary_rejects_malformed_or_secret_bearing_responses(self):
        responses = [
            'secret-sentinel', '[]', '{"status":"ready","count":1,"count":0}',
            {"status": "ready", "count": True}, {"status": "ready", "count": -1},
            {"status": "ready", "count": 0}, {"status": "ready", "count": 2},
            {"status": "ready", "count": "1"}, {"status": "ready", "count": 1.0},
            {"status": "ready", "count": 1, "password": "secret-sentinel"},
            {"status": "ready", "count": 1, "code": "secret-sentinel"},
            {"status": "error", "code": "secret-sentinel"},
            {"status": "error", "code": "storage_failed", "message": "secret-sentinel"},
        ]
        for command, status in [("check", "ready"), ("migrate_keychain", "migrated")]:
            for response in responses:
                if isinstance(response, dict) and response.get("status") == "ready":
                    response = {**response, "status": status}
                with self.subTest(command=command, response_type=type(response).__name__):
                    raw = response if isinstance(response, str) else json.dumps(response)
                    with patch.object(store.subprocess, "run", return_value=Mock(returncode=0, stdout=raw)):
                        with self.assertRaises(store.KeychainError) as error:
                            getattr(store.Keychain(), command)([{"id": "sample", "keychain_ref": REF}])
                    self.assertNotIn("secret-sentinel", str(error.exception))

    def test_storage_summary_rejects_success_with_failed_exit(self):
        with patch.object(store.subprocess, "run", return_value=Mock(returncode=1, stdout='{"status":"ready","count":1}')):
            with self.assertRaises(store.KeychainError):
                store.Keychain().check([{"id": "sample", "keychain_ref": REF}])

    def test_storage_summary_preserves_only_allowlisted_failure_code_without_retry(self):
        for command, code in [("check", "storage_failed"), ("migrate_keychain", "migration_requires_approval")]:
            with self.subTest(command=command):
                with patch.object(store.subprocess, "run", return_value=Mock(returncode=1, stdout=json.dumps({"status": "error", "code": code}))) as run:
                    with self.assertRaises(store.KeychainError) as error:
                        getattr(store.Keychain(), command)([{"id": "sample", "keychain_ref": REF}])
                self.assertEqual(error.exception.code, code)
                self.assertEqual(run.call_count, 1)

    def test_empty_storage_summary_is_valid_and_invalid_reference_does_not_launch(self):
        with patch.object(store.subprocess, "run", return_value=Mock(returncode=0, stdout='{"status":"ready","count":0}')) as run:
            self.assertEqual(store.Keychain().check([]), {"status": "ready", "count": 0})
            self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"accounts": []})
        for command in ["check", "migrate_keychain"]:
            with patch.object(store.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    getattr(store.Keychain(), command)([{"id": "sample", "keychain_ref": "not-base64"}])
            run.assert_not_called()

    def test_storage_timeout_and_os_errors_never_return_raw_output(self):
        failures = [OSError("secret-sentinel"), subprocess.TimeoutExpired("synthetic-helper", 180, output="secret-sentinel")]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch.object(store.subprocess, "run", side_effect=failure) as run:
                    with self.assertRaises(store.KeychainError) as error:
                        store.Keychain().check([])
                self.assertNotIn("secret-sentinel", str(error.exception))
                self.assertEqual(run.call_count, 1)

    def test_cancellation_is_not_success(self):
        process = self.process(initial='{"status":"cancelled"}\n', final="rolled_back")
        with patch.object(store.subprocess, "Popen", return_value=process):
            with self.assertRaises(store.Cancelled):
                with store.Keychain().registration("sample", "metadata"):
                    self.fail("cancelled input reached caller")

    def test_malformed_or_unexpected_output_is_never_echoed(self):
        for output in ["password-sentinel", '{"status":"failed","message":"password-sentinel"}', '{"status":"created","persistent_ref":"invalid","password":"password-sentinel"}', '{"status":"created","persistent_ref":"invalid","persistent_ref":"' + REF + '"}']:
            process = self.process(initial=output, final="rolled_back")
            with self.subTest(output_length=len(output)):
                with patch.object(store.subprocess, "Popen", return_value=process):
                    with self.assertRaises(store.KeychainError) as error:
                        with store.Keychain().registration("sample", "metadata"):
                            self.fail("invalid native output reached caller")
                    self.assertNotIn("password-sentinel", str(error.exception))

    def test_invalid_reference_does_not_launch_helper(self):
        with patch.object(store.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                store.Keychain().update("sample", "not-base64", "metadata")
        run.assert_not_called()

    def test_login_passes_only_metadata_via_stdin(self):
        record = {"id": "sample", "keychain_ref": REF}
        plan = {"platform": "android", "device": "synthetic-device"}
        response = {"status": "authenticated", "environment_verified": True}
        with patch.object(store.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(response))) as run:
            self.assertEqual(store.Keychain().login(record, plan), response)
        self.assertEqual(run.call_args.args[0], ["/native/keychain-helper", "login"])
        self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"id": "sample", "reference": REF, "plan": plan})

    def test_login_keeps_finite_platform_specific_wait(self):
        for platform, timeout in [("ios", 360), ("android", 180)]:
            with self.subTest(platform=platform):
                with patch.object(store.subprocess, "run", return_value=Mock(returncode=0, stdout='{"status":"not_ready"}')) as run:
                    store.Keychain().login({"id": "sample", "keychain_ref": REF}, {"platform": platform})
                self.assertEqual(run.call_args.kwargs["timeout"], timeout)

    def test_login_timeout_preserves_uncertainty_without_retry_or_raw_output(self):
        expired = subprocess.TimeoutExpired("synthetic-helper", 360, output="secret-sentinel", stderr="secret-sentinel")
        with patch.object(store.subprocess, "run", side_effect=expired) as run:
            result = store.Keychain().login({"id": "sample", "keychain_ref": REF}, {"platform": "ios"})
        self.assertEqual(result, {"status": "uncertain", "code": "timed_out", "cleanup": "unconfirmed",
                                  "environment_verified": False})
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("secret-sentinel", repr(result))

    def test_login_drops_unexpected_or_secret_bearing_response(self):
        for response in [
            {"status": "authenticated", "password": "secret-sentinel"},
            {"status": "error", "message": "secret-sentinel"},
            {"status": "error", "code": "secret-sentinel"},
            {"status": "authenticated", "environment_verified": "secret-sentinel"},
            {"status": "already_authenticated", "environment_verified": True},
        ]:
            with self.subTest(keys=list(response)):
                with patch.object(store.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(response))):
                    with self.assertRaises(store.KeychainError) as failure:
                        store.Keychain().login({"id": "sample", "keychain_ref": REF}, {})
                self.assertNotIn("secret-sentinel", str(failure.exception))

    def test_login_rejects_duplicate_fields(self):
        for output in [
            '{"status":"error","code":"secret-sentinel","code":"storage_failed"}',
            '{"status":"authenticated","environment_verified":false,"environment_verified":true}',
        ]:
            with patch.object(store.subprocess, "run", return_value=Mock(returncode=0, stdout=output)):
                with self.assertRaises(store.KeychainError) as failure:
                    store.Keychain().login({"id": "sample", "keychain_ref": REF}, {})
            self.assertNotIn("secret-sentinel", str(failure.exception))

    def test_device_outcomes_preserve_fixed_success_and_failure_codes(self):
        for response in [
            {"status": "authenticated", "code": "SUCCESS_OBSERVED", "environment_verified": True},
            {"status": "already_authenticated", "code": "SESSION_REUSED", "environment_verified": False},
            {"status": "failed", "code": "authentication_unconfirmed", "cleanup": "cleared"},
            {"status": "uncertain", "code": "ios_runner_cleanup_unconfirmed"},
            {"status": "error", "code": "storage_failed"},
            {"status": "error", "code": "invalid_credentials"},
        ]:
            with patch.object(store.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(response))):
                self.assertEqual(store.Keychain().login({"id": "sample", "keychain_ref": REF}, {}), response)

    def test_unconfigured_storage_is_rejected_before_native_execution(self):
        with patch.object(store, "verify_release"), patch.object(store, "helper_directory", side_effect=ValueError("not configured")), \
             patch.object(store.subprocess, "run") as run:
            with self.assertRaises(store.KeychainError):
                self.verified.stop()
                try:
                    store.verified_helper()
                finally:
                    self.verified.start()
        run.assert_not_called()


class HelperIdentityTests(unittest.TestCase):
    def test_changed_source_binary_or_signature_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="helper-identity-") as folder:
            root = Path(folder)
            (root / "native").mkdir()
            source = root / "native" / "KeychainHelper.swift"
            source.write_text("synthetic-source")
            executable = root / "keychain-helper"
            executable.write_text("synthetic-executable")
            (root / "DriverPins.swift").write_text("synthetic-pins")
            with patch.object(store, "SKILL_DIR", root), patch.object(store, "helper_directory", return_value=root), \
                 patch.object(store, "verify_release"), patch.object(store, "current_digest", return_value="a" * 64), \
                 patch.object(store, "verify_output", side_effect=lambda path: path):
                manifest = store.helper_manifest(executable)
                (root / "build.json").write_text(json.dumps(manifest))
                with patch.object(store.subprocess, "run", return_value=Mock(returncode=0)):
                    self.assertEqual(store.verified_helper(), executable)
                    source.write_text("changed-source")
                    with self.assertRaises(store.KeychainError):
                        store.verified_helper()
                    source.write_text("synthetic-source")
                    added = root / "native" / "SecureLogin.swift"
                    added.write_text("another-source")
                    with self.assertRaises(store.KeychainError):
                        store.verified_helper()
                    added.unlink()
                    executable.write_text("changed-executable")
                    with self.assertRaises(store.KeychainError):
                        store.verified_helper()
                    executable.write_text("synthetic-executable")
                with patch.object(store.subprocess, "run", return_value=Mock(returncode=1)):
                    with self.assertRaises(store.KeychainError):
                        store.verified_helper()


@unittest.skipUnless(sys.platform == "darwin" and os.environ.get("MOBILE_QA_NATIVE_TESTS") == "1", "Opt in with MOBILE_QA_NATIVE_TESTS=1 after isolated helper build")
class NativeCredentialStoreIntegrationTests(unittest.TestCase):
    def test_native_local_store_roundtrip_and_storage_safety(self):
        helper = store.verified_helper()
        result = subprocess.run(
            [str(helper), "self-test"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, check=False, timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"status": "passed"})

    def test_no_public_reference_deletion_command(self):
        helper = store.verified_helper()
        result = subprocess.run([str(helper), "rollback", "--ref", REF], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"status": "error", "code": "invalid_arguments"})



if __name__ == "__main__":
    unittest.main()
