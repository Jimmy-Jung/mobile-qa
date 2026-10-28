"""범용 로그인 프로필의 입력 경계. Author: JunyoungJung · 2026-10-22."""

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from login_plan import validate_profile
from test_profiles import PROFILE

NAVIGATION = {
    "open_selector": {"identifier": "login.options"},
    "close_selector": {"label": "Close"},
    "open_gesture": "long_press", "select_if_needed": True,
}


class LoginPlanTests(unittest.TestCase):
    def test_unlabelled_login_fields_can_use_exact_semantic_types(self):
        for platform in ["ios", "android"]:
            profile = {**PROFILE, "platform": platform,
                       "username_selector": {"type": "textField"},
                       "password_selector": {"type": "secureTextField"},
                       "submit_selector": {"label": "Sign in"},
                       "success_selectors": [{"label": "Home"}]}
            with self.subTest(platform=platform):
                self.assertEqual(validate_profile(profile), profile)
            for key, value in [("username_selector", "secureTextField"), ("password_selector", "textField"),
                               ("submit_selector", "textField"), ("environment_selector", "secureTextField")]:
                with self.subTest(platform=platform, key=key), self.assertRaises(ValueError):
                    validate_profile({**profile, key: {"type": value}})
            with self.assertRaises(ValueError):
                validate_profile({**profile, "success_selectors": [{"type": "textField"}]})

    def test_generic_ios_profile_preserves_exact_selectors(self):
        self.assertEqual(validate_profile(copy.deepcopy(PROFILE)), PROFILE)

    def test_tab_button_label_is_only_an_ios_success_selector(self):
        selector = {"tab_button_label": "Home"}
        profile = {**PROFILE, "success_selectors": [selector]}
        self.assertEqual(validate_profile(copy.deepcopy(profile)), profile)
        for field in ["username_selector", "password_selector", "submit_selector", "environment_selector"]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_profile({**PROFILE, field: selector})
        for field in ["open_selector", "close_selector"]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_profile({**PROFILE, "environment_navigation": {**NAVIGATION, field: selector}})
        with self.assertRaises(ValueError):
            validate_profile({**profile, "platform": "android"})

    def test_tab_button_label_keeps_single_condition_and_string_safety_limits(self):
        for selector in [{"tab_button_label": ""}, {"tab_button_label": "Home\n"},
                         {"tab_button_label": "Home\u200b"}, {"tab_button_label": "한" * 86},
                         {"tab_button_label": 1}, {"tab_button_label": "Home", "label": "Home"}]:
            with self.subTest(selector=selector), self.assertRaises(ValueError):
                validate_profile({**PROFILE, "success_selectors": [selector]})

    def test_simulator_environment_navigation_preserves_exact_reviewed_contract(self):
        for gesture in ["tap", "long_press"]:
            for selection in [True, False]:
                profile = {**PROFILE, "environment_navigation": {
                    **NAVIGATION, "open_gesture": gesture, "select_if_needed": selection,
                }}
                self.assertEqual(validate_profile(copy.deepcopy(profile)), profile)

    def test_environment_navigation_rejects_commands_nonbooleans_and_selector_aliases(self):
        invalid = [
            None, [], {**NAVIGATION, "command": "synthetic-only"},
            {key: value for key, value in NAVIGATION.items() if key != "open_gesture"},
            {**NAVIGATION, "open_gesture": "double_tap"},
            {**NAVIGATION, "select_if_needed": 1},
            {**NAVIGATION, "select_if_needed": "true"},
            {**NAVIGATION, "open_selector": {"type": "textField"}},
            {**NAVIGATION, "close_selector": {"placeholder": ""}},
            {**NAVIGATION, "close_selector": NAVIGATION["open_selector"]},
        ]
        for key in ["username_selector", "password_selector", "submit_selector", "environment_selector"]:
            invalid.append({**NAVIGATION, "open_selector": PROFILE[key]})
        invalid.append({**NAVIGATION, "close_selector": PROFILE["success_selectors"][0]})
        for navigation in invalid:
            with self.subTest(navigation=navigation), self.assertRaises(ValueError):
                validate_profile({**PROFILE, "environment_navigation": navigation})
        with self.assertRaises(ValueError):
            validate_profile({**PROFILE, "platform": "android", "environment_navigation": NAVIGATION})
        with self.assertRaises(ValueError):
            validate_profile({key: value for key, value in {**PROFILE, "environment_navigation": NAVIGATION}.items()
                              if key != "environment_selector"})

    def test_null_environment_selector_declares_no_environment_control(self):
        for platform in ["ios", "android"]:
            profile = {**PROFILE, "platform": platform, "environment_selector": None,
                       "username_selector": {"label": "Username"}, "password_selector": {"label": "Password"},
                       "submit_selector": {"label": "Sign in"}, "success_selectors": [{"label": "Home"}]}
            with self.subTest(platform=platform):
                self.assertEqual(validate_profile(copy.deepcopy(profile)), profile)
        with self.assertRaises(ValueError):
            validate_profile({**PROFILE, "environment_selector": None, "environment_navigation": NAVIGATION})
        with self.assertRaises(ValueError):
            validate_profile({key: value for key, value in PROFILE.items() if key != "environment_selector"})
        for value in [{}, "Staging", []]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_profile({**PROFILE, "environment_selector": value})

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
