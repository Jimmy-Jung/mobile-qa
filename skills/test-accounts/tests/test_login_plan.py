"""범용 로그인 프로필의 입력 경계. Author: JunyoungJung · 2026-10-22."""

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from login_plan import validate_profile
from test_profiles import PROFILE


class LoginPlanTests(unittest.TestCase):
    def test_generic_ios_profile_preserves_exact_selectors(self):
        self.assertEqual(validate_profile(copy.deepcopy(PROFILE)), PROFILE)

    def test_android_identifier_requires_same_app_resource(self):
        profile = {**PROFILE, "platform": "android", "username_selector": {"identifier": "org.example.sample:id/username"}, "password_selector": {"identifier": "org.example.sample:id/password"}, "submit_selector": {"identifier": "org.example.sample:id/submit"}, "success_selectors": [{"identifier": "org.example.sample:id/home"}]}
        self.assertEqual(validate_profile(profile), profile)
        for selector in [{"identifier": "other.app:id/user"}, {"placeholder": "Username"}, {"identifier": "org.example.sample:id/../user"}]:
            with self.subTest(selector=selector), self.assertRaises(ValueError):
                validate_profile({**profile, "username_selector": selector})

    def test_generic_staging_and_admin_labels_do_not_require_builtin_roles(self):
        for environment, role in [("staging", "admin"), ("qa-v2", "operator"), ("production", "tester")]:
            self.assertEqual(validate_profile({**PROFILE, "environment": environment, "role": role})["role"], role)

    def test_selector_control_characters_and_byte_limits_are_rejected(self):
        for value in ["name\npassword-sentinel", "name\u200b", "한" * 86, ""]:
            with self.subTest(length=len(value)), self.assertRaises(ValueError) as failure:
                validate_profile({**PROFILE, "username_selector": {"label": value}})
            self.assertNotIn("password-sentinel", str(failure.exception))

    def test_schema_rejects_missing_unknown_noninteger_and_ambiguous_conditions(self):
        invalid = [
            {key: value for key, value in PROFILE.items() if key != "environment_selector"},
            {**PROFILE, "timeout_ms": 15000.0}, {**PROFILE, "schema_version": True},
            {**PROFILE, "app_id": "invalid;password-sentinel"},
            {**PROFILE, "success_selectors": []},
            {**PROFILE, "success_selectors": [PROFILE["environment_selector"]]},
        ]
        for profile in invalid:
            with self.subTest(keys=list(profile)), self.assertRaises(ValueError):
                validate_profile(profile)


if __name__ == "__main__":
    unittest.main()
