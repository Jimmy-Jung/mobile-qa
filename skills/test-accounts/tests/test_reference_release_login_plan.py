"""등록한 프로필만 사용하며 Release 우회는 허용하지 않는다. Author: JunyoungJung · 2026-10-22."""

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import test_accounts as cli


class TrustedLoginProfileTests(unittest.TestCase):
    def test_cli_requires_explicit_profile_and_accepts_generic_scope_labels(self):
        arguments = ["login", "--project", "/synthetic/project", "--environment", "qa-stage", "--role", "admin", "--platform", "ios", "--device", "synthetic-phone"]
        with self.assertRaises(ValueError):
            cli.parser().parse_args(arguments)
        self.assertEqual(cli.parser().parse_args([*arguments, "--login-profile", "sample-qa"]).login_profile, "sample-qa")

    def test_cli_forwards_current_project_and_full_scope_without_secret_access(self):
        args = SimpleNamespace(project=Path("/synthetic/project"), environment="staging", platform="ios", device="synthetic-phone", app_id="org.example.sample", device_name=None, account=None, capability=[], role="tester", login_profile="sample-staging")
        catalog = Mock()
        catalog.find.return_value = {"candidates": [{"id": "synthetic-account"}]}
        with patch.object(cli, "project_key", return_value="example.test/team/sample"), patch.object(cli, "build_login_plan", return_value={}) as build, patch.object(cli, "Keychain") as keychain:
            keychain.return_value.login.return_value = {"status": "not_ready"}
            cli.login(args, catalog)
        build.assert_called_once_with("example.test/team/sample", "staging", "ios", "synthetic-phone", app_id="org.example.sample", role="tester", project_path=args.project, login_profile="sample-staging")

    def test_native_sources_have_no_implicit_launch_or_environment_selection(self):
        root = Path(__file__).resolve().parents[1]
        swift = (root / "ios/RunnerTests+AccountLogin.swift").read_text()
        java = (root / "android/SecureInput.java").read_text()
        self.assertNotIn("launchArguments", swift)
        self.assertNotIn("candidate.launch()", swift)
        self.assertNotIn("referenceRelease", swift)
        self.assertIn("target.isSelected", swift)
        self.assertIn('release_digest == secureAccountReleaseDigest', swift)
        self.assertIn('RELEASE_DIGEST.equals(text(object, "release_digest", 64))', java)
        environment = java.split("void ensureEnvironment", 1)[1].split("AccessibilityNodeInfo environmentNode", 1)[0]
        self.assertNotIn("performAction", environment)


if __name__ == "__main__":
    unittest.main()
