"""로컬 로그인 프로필의 신뢰·범위 계약. Author: JunyoungJung · 2026-10-22."""

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import login_plan as profiles

DIGEST = "a" * 64
PROFILE = {
    "schema_version": 1, "name": "sample-staging", "platform": "ios",
    "app_id": "org.example.sample", "environment": "staging", "role": "tester",
    "username_selector": {"identifier": "login.username"},
    "password_selector": {"identifier": "login.password"},
    "submit_selector": {"identifier": "login.submit"},
    "environment_selector": {"label": "Staging"},
    "success_selectors": [{"identifier": "home.ready"}], "timeout_ms": 15000,
}


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="login-profiles-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "checkout"
        self.project.mkdir()
        self.store = self.root / "library" / "sample-123456789abc"
        self.source = self.root / "source.json"
        self.source.write_text(json.dumps(PROFILE))
        self.patches = [
            patch.object(profiles, "profile_context", return_value=("sample-123456789abc", "example.test/team/sample", self.store / "profiles")),
            patch.object(profiles, "current_digest", return_value=DIGEST),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def register(self):
        return profiles.register_profile(self.project, self.source)

    def load(self, **changes):
        arguments = dict(project_path=self.project, project="example.test/team/sample", environment="staging", platform="ios", device="synthetic-phone", app_id="org.example.sample", role="tester", login_profile="sample-staging")
        arguments.update(changes)
        return profiles.build_login_plan(**arguments)

    def test_registration_and_load_are_bound_to_scope_and_release(self):
        result = self.register()
        path = self.store / "profiles/sample-staging.json"
        self.assertEqual(result["status"], "profile_registered")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(path.parent).st_mode & 0o777, 0o700)
        plan = self.load()
        self.assertEqual(plan["environment"], "staging")
        self.assertEqual(plan["trust"]["release_digest"], DIGEST)
        self.assertEqual(plan["trust"]["path"], str(path))
        self.assertEqual(plan["account_project"], "example.test/team/sample")
        self.assertNotIn("username", plan)

    def test_navigation_profile_loads_only_for_an_ios_simulator_identifier(self):
        navigation = {
            "open_selector": {"identifier": "login.options"},
            "close_selector": {"label": "Close"},
            "open_gesture": "long_press", "select_if_needed": True,
        }
        self.source.write_text(json.dumps({**PROFILE, "environment_navigation": navigation}))
        self.register()
        plan = self.load(device="11111111-2222-4333-8444-555555555555")
        self.assertEqual(plan["environment_navigation"], navigation)
        self.assertEqual(plan["environment_selector"], PROFILE["environment_selector"])
        with self.assertRaises(ValueError) as failure:
            self.load(device="synthetic-physical-phone")
        self.assertEqual(getattr(failure.exception, "code", None), "profile_scope_mismatch")

    def test_tab_button_success_selector_survives_trusted_registration_without_widening(self):
        selector = {"tab_button_label": "Home"}
        self.source.write_text(json.dumps({**PROFILE, "success_selectors": [selector]}))
        self.register()
        self.assertEqual(self.load()["success_selectors"], [selector])

    def test_unregistered_and_changed_scope_fail_before_native_access(self):
        with self.assertRaises(ValueError):
            self.load()
        self.register()
        for changes in [dict(environment="production"), dict(role="admin"), dict(platform="android"), dict(app_id="org.example.other"), dict(project="example.test/other/app"), dict(login_profile="other")]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load(**changes)

    def test_profile_failures_have_safe_actionable_codes(self):
        with self.assertRaises(ValueError) as failure:
            self.load()
        self.assertEqual(getattr(failure.exception, "code", None), "profile_not_found")
        self.register()
        with patch.object(profiles, "current_digest", return_value="b" * 64), self.assertRaises(ValueError) as failure:
            self.load()
        self.assertEqual(getattr(failure.exception, "code", None), "profile_release_mismatch")
        with self.assertRaises(ValueError) as failure:
            self.load(environment="production")
        self.assertEqual(getattr(failure.exception, "code", None), "profile_scope_mismatch")
        (self.store / "profiles/sample-staging.json").chmod(0o644)
        with self.assertRaises(ValueError) as failure:
            self.load()
        self.assertEqual(getattr(failure.exception, "code", None), "profile_untrusted")

    def test_damaged_stored_profile_reports_recoverable_error_without_contents(self):
        self.register()
        path = self.store / "profiles/sample-staging.json"
        wrapper = json.loads(path.read_text())
        for contents in ["password-sentinel", json.dumps({"unexpected": "password-sentinel"}),
                         json.dumps({**wrapper, "profile": {**PROFILE, "username_selector": {"unknown": "password-sentinel"}}})]:
            with self.subTest(length=len(contents)):
                path.write_text(contents)
                with self.assertRaises(ValueError) as failure:
                    self.load()
                self.assertEqual(getattr(failure.exception, "code", None), "profile_untrusted")
                self.assertNotIn("password-sentinel", str(failure.exception))

    def test_mixed_release_and_untrusted_file_are_rejected(self):
        self.register()
        path = self.store / "profiles/sample-staging.json"
        with patch.object(profiles, "current_digest", return_value="b" * 64), self.assertRaises(ValueError):
            self.load()
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.load()
        path.chmod(0o600)
        original = path.read_bytes()
        path.unlink()
        target = self.root / "redirect.json"
        target.write_bytes(original)
        target.chmod(0o600)
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.load()

    def test_profile_is_a_fixed_login_contract_not_commands(self):
        invalid = [
            {**PROFILE, "shell": "password-sentinel"},
            {**PROFILE, "launch_arguments": ["--server"]},
            {**PROFILE, "retry": 2},
            {**PROFILE, "environment_selector": None},
            {**PROFILE, "submit_selector": PROFILE["username_selector"]},
            {**PROFILE, "success_selectors": [PROFILE["password_selector"]]},
            {**PROFILE, "success_selectors": [PROFILE["success_selectors"][0]] * 2},
            {**PROFILE, "username_selector": {"identifier": "a", "label": "b"}},
            {**PROFILE, "name": "../../outside"},
            {**PROFILE, "timeout_ms": True},
            {**PROFILE, "role": "bad\npassword-sentinel"},
            {**PROFILE, "platform": "android", "username_selector": {"identifier": "other.app:id/user"}},
        ]
        for profile in invalid:
            with self.subTest(keys=list(profile)):
                self.source.write_text(json.dumps(profile))
                with self.assertRaises(ValueError) as failure:
                    self.register()
                self.assertNotIn("password-sentinel", str(failure.exception))

    def test_no_implicit_profile_or_mutable_return(self):
        self.register()
        with self.assertRaises(ValueError):
            self.load(login_profile=None)
        first = self.load()
        first["success_selectors"].clear()
        self.assertEqual(self.load()["success_selectors"], PROFILE["success_selectors"])

    def test_registration_does_not_follow_store_symlink_or_public_permissions(self):
        self.store.mkdir(parents=True, mode=0o700)
        target = self.root / "target"
        target.mkdir(mode=0o700)
        (self.store / "profiles").symlink_to(target)
        with self.assertRaises(ValueError):
            self.register()
        (self.store / "profiles").unlink()
        (self.store / "profiles").mkdir(mode=0o755)
        with self.assertRaises(ValueError):
            self.register()

    def test_hardlinked_profile_and_tampered_binding_are_rejected(self):
        self.register()
        path = self.store / "profiles/sample-staging.json"
        wrapper = json.loads(path.read_text())
        path.write_text(json.dumps({**wrapper, "account_project": "example.test/other/app"}))
        with self.assertRaises(ValueError):
            self.load()
        path.write_text(json.dumps(wrapper))
        os.link(path, self.root / "second-link.json")
        with self.assertRaises(ValueError):
            self.load()

    def test_real_locator_binds_existing_account_key_and_qa_key_without_creating_on_read(self):
        self.patches[0].stop()
        fake_home = self.root / "home"
        fake_home.mkdir()
        with patch.object(Path, "home", return_value=fake_home):
            qa_key, account_key, folder = profiles.profile_context(self.project)
            self.assertEqual(account_key, "path:" + str(self.project.resolve()))
            self.assertEqual(folder, fake_home / "Library/Application Support/mobile-device-qa/projects" / qa_key / "profiles")
            self.assertFalse(folder.exists())
            profiles.register_profile(self.project, self.source)
            loaded = profiles.build_login_plan(account_key, "staging", "ios", "synthetic-phone", project_path=self.project, role="tester", login_profile="sample-staging")
            self.assertEqual(loaded["project_key"], qa_key)

    def test_registration_rejects_non_json_oversize_and_duplicates(self):
        for contents in ["{", " " * 32769, '{"name":"one","name":"two"}']:
            self.source.write_text(contents)
            with self.assertRaises(ValueError):
                self.register()


if __name__ == "__main__":
    unittest.main()
