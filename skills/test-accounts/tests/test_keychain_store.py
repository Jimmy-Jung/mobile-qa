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
        self.assertNotIn("pass_fds", launch.call_args.kwargs)
        process.communicate.assert_called_once_with(input="commit\n")

    def test_copy_registration_passes_only_source_identity_and_commits_after_catalog(self):
        process = self.process()
        with patch.object(store.subprocess, "Popen", return_value=process) as launch:
            with store.Keychain().registration("new-sample", "metadata", copy_from=("source-sample", REF)) as persistent_ref:
                self.assertEqual(persistent_ref, REF)
                process.communicate.assert_not_called()
        self.assertEqual(launch.call_args.args[0], [
            "/native/keychain-helper", "create-copy", "--id", "new-sample", "--context", "metadata",
            "--source-id", "source-sample", "--source-ref", REF,
        ])
        self.assertNotIn("pass_fds", launch.call_args.kwargs)
        process.communicate.assert_called_once_with(input="commit\n")

    def test_copy_registration_preserves_transaction_rollback_and_cancellation(self):
        process = self.process(final="rolled_back")
        with patch.object(store.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "catalog failed"):
                with store.Keychain().registration("new-sample", "metadata", copy_from=("source-sample", REF)):
                    raise RuntimeError("catalog failed")
        process.communicate.assert_called_once_with(input="rollback\n")
        process = self.process(initial='{"status":"cancelled"}\n')
        with patch.object(store.subprocess, "Popen", return_value=process):
            with self.assertRaises(store.Cancelled):
                with store.Keychain().registration("new-sample", "metadata", copy_from=("source-sample", REF)):
                    self.fail("cancelled copy reached caller")

    def test_copy_registration_rejects_bad_source_and_mixed_pipe_before_launch(self):
        for source in [("new-sample", REF), ("source-sample", "not-a-reference"), ("invalid!", REF),
                       ["source-sample", REF], ("source-sample",), ("source-sample", REF, "extra")]:
            with self.subTest(source=type(source).__name__), patch.object(store.subprocess, "Popen") as launch:
                with self.assertRaises(store.KeychainError):
                    with store.Keychain().registration("new-sample", "metadata", copy_from=source):
                        self.fail("invalid copy reached caller")
                launch.assert_not_called()
        with patch.object(store.subprocess, "Popen") as launch:
            with self.assertRaises(store.KeychainError):
                with store.Keychain().registration("new-sample", "metadata", copy_from=("source-sample", REF), credentials_fd=55):
                    self.fail("mixed input reached caller")
            launch.assert_not_called()

    def test_pipe_registration_passes_only_duplicated_descriptor_and_closes_it(self):
        process = self.process()
        with patch.object(store.os, "isatty", return_value=False), \
             patch.object(store.os, "dup", return_value=56) as duplicate, \
             patch.object(store.os, "close") as close, \
             patch.object(store.subprocess, "Popen", return_value=process) as launch:
            with store.Keychain().registration("sample", "metadata", credentials_fd=55):
                close.assert_called_once_with(56)
            duplicate.assert_called_once_with(55)
        self.assertEqual(launch.call_args.args[0], [
            "/native/keychain-helper", "create-stdin", "--id", "sample", "--context", "metadata", "--input-fd", "56",
        ])
        self.assertEqual(launch.call_args.kwargs["pass_fds"], (56,))
        self.assertIs(launch.call_args.kwargs["stdin"], subprocess.PIPE)
        self.assertIs(launch.call_args.kwargs["stderr"], subprocess.DEVNULL)
        process.communicate.assert_called_once_with(input="commit\n")

    def test_pipe_registration_closes_descriptor_when_helper_cannot_start(self):
        with patch.object(store.os, "isatty", return_value=False), \
             patch.object(store.os, "dup", return_value=56), \
             patch.object(store.os, "close") as close, \
             patch.object(store.subprocess, "Popen", side_effect=OSError("password-sentinel")):
            with self.assertRaises(store.KeychainError) as failure:
                with store.Keychain().registration("sample", "metadata", credentials_fd=55):
                    self.fail("failed launch reached registration caller")
            close.assert_called_once_with(56)
        self.assertNotIn("password-sentinel", str(failure.exception))

    def test_pipe_registration_rejects_interactive_or_unavailable_input(self):
        for kind in ["tty", "invalid-fd", "dup-failed"]:
            with self.subTest(kind=kind), \
                 patch.object(store.os, "isatty", return_value=kind == "tty"), \
                 patch.object(store.os, "dup", side_effect=OSError("password-sentinel")) as duplicate, \
                 patch.object(store.os, "close") as close, patch.object(store.subprocess, "Popen") as launch:
                with self.assertRaises(store.KeychainError) as failure:
                    with store.Keychain().registration("sample", "metadata", credentials_fd=0 if kind == "invalid-fd" else 55):
                        self.fail("invalid input reached registration caller")
                self.assertNotIn("password-sentinel", str(failure.exception))
                launch.assert_not_called()
                close.assert_not_called()
                if kind != "dup-failed":
                    duplicate.assert_not_called()

    def test_pipe_registration_keeps_rollback_on_caller_failure(self):
        process = self.process(final="rolled_back")
        with patch.object(store.os, "isatty", return_value=False), \
             patch.object(store.os, "dup", return_value=55), patch.object(store.os, "close"), \
             patch.object(store.subprocess, "Popen", return_value=process):
            with self.assertRaises(RuntimeError):
                with store.Keychain().registration("sample", "metadata", credentials_fd=55):
                    raise RuntimeError("catalog failed")
        process.communicate.assert_called_once_with(input="rollback\n")

    def test_real_pipe_forwarding_keeps_commit_control_separate_from_credentials(self):
        # 공개 합성 값만 사용하며 실제 계정 저장소나 로그인 도구에는 접근하지 않는다.
        synthetic_credentials = {"username": " user-sentinel 한글\n", "password": " password-sentinel $()`\\\"\n "}
        for decision in ["commit", "rollback"]:
            with self.subTest(decision=decision), tempfile.TemporaryDirectory(prefix="credential-pipe-") as folder:
                helper = Path(folder) / "synthetic-helper"
                helper.write_text(
                    "#!" + sys.executable + "\n"
                    "import json, os, stat, sys\n"
                    "descriptor = int(sys.argv[sys.argv.index('--input-fd') + 1])\n"
                    "assert descriptor >= 3 and stat.S_ISFIFO(os.fstat(descriptor).st_mode)\n"
                    "with os.fdopen(descriptor, 'r', encoding='utf-8') as source:\n"
                    "    credentials = json.loads(source.readline())\n"
                    "assert credentials == " + repr(synthetic_credentials) + "\n"
                    "print(json.dumps({'status': 'created', 'persistent_ref': '" + REF + "'}), flush=True)\n"
                    "decision = sys.stdin.readline()\n"
                    "assert decision in ('commit\\n', 'rollback\\n')\n"
                    "print(json.dumps({'status': 'committed' if decision == 'commit\\n' else 'rolled_back'}))\n",
                    encoding="utf-8",
                )
                helper.chmod(0o700)
                read_fd, write_fd = os.pipe()
                try:
                    os.write(write_fd, (json.dumps(synthetic_credentials, ensure_ascii=False) + "\n").encode())
                finally:
                    os.close(write_fd)
                os.set_inheritable(read_fd, False)
                try:
                    with patch.object(store, "verified_helper", return_value=helper):
                        if decision == "commit":
                            with store.Keychain().registration("sample", "metadata", credentials_fd=read_fd) as persistent_ref:
                                self.assertEqual(persistent_ref, REF)
                        else:
                            with self.assertRaisesRegex(RuntimeError, "catalog failed"):
                                with store.Keychain().registration("sample", "metadata", credentials_fd=read_fd):
                                    raise RuntimeError("catalog failed")
                finally:
                    os.close(read_fd)

    def test_input_isolation_only_gives_native_helper_the_credential_pipe(self):
        source = (
            "import os, stat, subprocess, sys\n"
            "from keychain_store import isolated_credentials_input\n"
            "with isolated_credentials_input() as descriptor:\n"
            "    assert descriptor >= 3 and not os.get_inheritable(descriptor)\n"
            "    assert stat.S_ISFIFO(os.fstat(descriptor).st_mode)\n"
            "    metadata = subprocess.run([sys.executable, '-c', 'import sys; assert sys.stdin.buffer.read() == bytes()'], close_fds=False, check=True)\n"
            "    helper = subprocess.run([sys.executable, '-c', 'import os,sys; assert os.read(int(sys.argv[1]), 64) == b\"synthetic-pipe-sentinel\\\\n\"', str(descriptor)], pass_fds=(descriptor,), check=True)\n"
            "assert stat.S_ISFIFO(os.fstat(0).st_mode)\n"
            "try:\n"
            "    os.fstat(descriptor)\n"
            "    raise AssertionError('descriptor retained')\n"
            "except OSError:\n"
            "    pass\n"
        )
        environment = {**os.environ, "PYTHONPATH": str(Path(store.__file__).parent)}
        result = subprocess.run([sys.executable, "-B", "-c", source], input=b"synthetic-pipe-sentinel\n",
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, timeout=10)
        self.assertEqual(result.returncode, 0, "isolated input subprocess failed")
        self.assertEqual((result.stdout, result.stderr), (b"", b""))

    def test_input_isolation_rejects_regular_file_or_tty_before_duplication(self):
        for mode, interactive in [(0o100600, False), (0o020600, True)]:
            with self.subTest(mode=mode), patch.object(store.os, "fstat", return_value=Mock(st_mode=mode)), \
                 patch.object(store.os, "isatty", return_value=interactive), patch.object(store.os, "dup") as duplicate, \
                 patch.object(store.os, "dup2") as replace:
                with self.assertRaises(store.KeychainError):
                    with store.isolated_credentials_input():
                        self.fail("invalid standard input reached caller")
                duplicate.assert_not_called()
                replace.assert_not_called()

    def test_input_isolation_restores_stdin_and_closes_own_descriptors_on_failure(self):
        with patch.object(store.os, "fstat", return_value=Mock(st_mode=0o010600)), \
             patch.object(store.os, "isatty", return_value=False), patch.object(store.os, "dup", return_value=55), \
             patch.object(store.os, "get_inheritable", return_value=True), patch.object(store.os, "open", return_value=56), \
             patch.object(store.os, "dup2") as replace, patch.object(store.os, "close") as close:
            with self.assertRaisesRegex(RuntimeError, "metadata failed"):
                with store.isolated_credentials_input() as descriptor:
                    self.assertEqual(descriptor, 55)
                    raise RuntimeError("metadata failed")
        self.assertEqual(replace.call_args_list[0].args, (56, 0))
        self.assertEqual(replace.call_args_list[-1].args, (55, 0))
        self.assertEqual({call.args[0] for call in close.call_args_list}, {55, 56})

    def test_input_isolation_closes_pipe_when_devnull_or_replacement_fails(self):
        for failure in ["open", "replace"]:
            with self.subTest(failure=failure), \
                 patch.object(store.os, "fstat", return_value=Mock(st_mode=0o010600)), \
                 patch.object(store.os, "isatty", return_value=False), patch.object(store.os, "dup", return_value=55), \
                 patch.object(store.os, "get_inheritable", return_value=True), \
                 patch.object(store.os, "open", return_value=56) as open_null, \
                 patch.object(store.os, "dup2") as replace, patch.object(store.os, "close") as close:
                if failure == "open":
                    open_null.side_effect = OSError("password-sentinel")
                else:
                    replace.side_effect = [OSError("password-sentinel"), None]
                with self.assertRaises(store.KeychainError) as result:
                    with store.isolated_credentials_input():
                        self.fail("failed isolation reached caller")
                self.assertNotIn("password-sentinel", str(result.exception))
                self.assertEqual({call.args[0] for call in close.call_args_list},
                                 {55} if failure == "open" else {55, 56})

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

    def test_preflight_passes_only_plan_without_accessing_account_locator(self):
        plan = {"platform": "ios", "device": "11111111-2222-3333-4444-AAAAAAAAAAAA"}
        response = {"status": "ready", "environment_verified": True}
        with patch.object(store.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(response))) as run:
            self.assertEqual(store.Keychain().login({}, plan, preflight_only=True), response)
        self.assertEqual(run.call_args.args[0], ["/native/keychain-helper", "login-preflight"])
        self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"plan": plan})
        self.assertNotIn("reference", run.call_args.kwargs["input"])
        self.assertNotIn("id", json.loads(run.call_args.kwargs["input"]))

    def test_preflight_rejects_other_targets_and_intrusive_success_states(self):
        for plan in [{"platform": "android", "device": "11111111-2222-3333-4444-AAAAAAAAAAAA"},
                     {"platform": "ios", "device": "physical-device"}]:
            with self.subTest(platform=plan["platform"]), patch.object(store.subprocess, "run") as run:
                with self.assertRaises(store.KeychainError):
                    store.Keychain().login({}, plan, preflight_only=True)
                run.assert_not_called()
        response = {"status": "authenticated", "environment_verified": True}
        with patch.object(store.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(response))):
            with self.assertRaises(store.KeychainError):
                store.Keychain().login({}, {"platform": "ios", "device": "11111111-2222-3333-4444-AAAAAAAAAAAA"}, preflight_only=True)

    def test_preflight_rejects_ready_or_existing_session_without_exact_environment_semantics(self):
        plan = {"platform": "ios", "device": "11111111-2222-3333-4444-AAAAAAAAAAAA"}
        for response in [{"status": "ready", "environment_verified": False}, {"status": "ready"},
                         {"status": "already_authenticated", "environment_verified": True},
                         {"status": "already_authenticated"}, {"status": "failed"}, {"status": "not_ready"}]:
            with self.subTest(response=response), patch.object(store.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(response))):
                with self.assertRaises(store.KeychainError):
                    store.Keychain().login({}, plan, preflight_only=True)

    def test_login_accepts_only_safe_specific_environment_diagnostics(self):
        for reason in ["FIELDS_NOT_READY", "NOT_UNIQUE", "NOT_BUTTON", "NOT_ENABLED",
                       "NOT_HITTABLE", "NOT_SELECTED", "ALIASES_LOGIN_FIELD"]:
            with self.subTest(reason=reason):
                response = {"status": "error", "code": "ENVIRONMENT_" + reason, "environment_verified": False}
                with patch.object(store.subprocess, "run", return_value=Mock(returncode=1, stdout=json.dumps(response))) as run:
                    self.assertEqual(store.Keychain().login({"id": "sample", "keychain_ref": REF}, {"platform": "ios"}), response)
                run.assert_called_once()

    def test_login_accepts_fixed_ui_failure_reasons_without_exposing_values(self):
        for code in ["APP_NOT_FOREGROUND", "LOGIN_VIEW_STILL_PRESENT", "SUCCESS_MARKER_MISSING",
                     "SUCCESS_MARKER_NOT_UNIQUE_OR_HITTABLE"]:
            with self.subTest(code=code):
                response = {"status": "failed", "code": code, "environment_verified": False,
                            "cleanup": "unavailable"}
                with patch.object(store.subprocess, "run", return_value=Mock(returncode=1, stdout=json.dumps(response))) as run:
                    self.assertEqual(store.Keychain().login({"id": "sample", "keychain_ref": REF}, {"platform": "ios"}), response)
                run.assert_called_once()

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
