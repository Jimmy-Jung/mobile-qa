"""Metadata-only Simulator preflight. Author: JunyoungJung · 2026-10-27."""

import unittest

import test_account_cli as fixtures


class PreflightCLITests(unittest.TestCase):
    setUp = fixtures.AccountCLITests.setUp
    run_cli = fixtures.AccountCLITests.run_cli
    login_args = fixtures.AccountCLITests.login_args
    login_record = fixtures.AccountCLITests.login_record

    def preflight_args(self, *extra):
        return self.login_args("--device", self.copy_device["identifier"], "--preflight-only", *extra)

    def test_preflight_uses_only_explicit_metadata_mode_and_marks_its_observation(self):
        self.catalog.find.return_value = {"candidates": [self.login_record()]}
        for response in [{"status": "ready", "environment_verified": True},
                         {"status": "already_authenticated", "environment_verified": False}]:
            with self.subTest(status=response["status"]):
                self.keychain.login.return_value = response
                code, result = self.run_cli(self.preflight_args())
                self.assertEqual(code, 0)
                self.assertEqual(result, {**response, "account": "first", "preflight_only": True})
                self.assertIs(self.keychain.login.call_args.kwargs["preflight_only"], True)

    def test_preflight_ready_without_environment_proof_is_not_observation_success(self):
        self.catalog.find.return_value = {"candidates": [self.login_record()]}
        self.keychain.login.return_value = {"status": "ready", "environment_verified": False}
        code, result = self.run_cli(self.preflight_args())
        self.assertEqual((code, result["status"]), (1, "error"))

    def test_preflight_rejects_physical_or_android_target_before_catalog_access(self):
        for platform, device in [("ios", "00008101-0012345678901234"),
                                 ("android", self.copy_device["identifier"])]:
            with self.subTest(platform=platform):
                code, result = self.run_cli(self.preflight_args("--platform", platform, "--device", device))
                self.assertEqual((code, result["status"], result["preflight_only"]), (1, "not_ready", True))
        self.mocks[0].assert_not_called()
        self.mocks[1].assert_not_called()

    def test_preflight_cannot_report_authenticated_and_normal_login_cannot_report_ready(self):
        self.catalog.find.return_value = {"candidates": [self.login_record()]}
        cases = [(self.preflight_args(), status) for status in ["authenticated", "failed", "not_ready"]]
        for args, status in [*cases, (self.login_args(), "ready")]:
            with self.subTest(status=status):
                self.keychain.login.return_value = {"status": status, "environment_verified": True}
                code, result = self.run_cli(args)
                self.assertEqual((code, result["status"]), (1, "error"))


if __name__ == "__main__":
    unittest.main()
