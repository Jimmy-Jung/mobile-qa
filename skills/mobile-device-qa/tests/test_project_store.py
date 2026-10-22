"""프로젝트 저장소 격리 검증. Author: JunyoungJung · 2026-10-22."""

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/project_store.py"
SPEC = importlib.util.spec_from_file_location("project_store", SCRIPT)
store = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(store)


class ProjectStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "checkout"
        self.project.mkdir()
        self.data = self.root / "qa-data/projects"

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", "-C", str(cwd or self.project), *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def init_repo(self, remote=None):
        self.git("init", "-q")
        if remote:
            self.git("remote", "add", "origin", remote)

    def test_ssh_and_https_share_identity_without_credentials(self):
        expected = store.normalize_remote("git@github.com:Team/App.git", self.project)
        actual = store.normalize_remote("https://name:example@github.com/Team/App.git?x=1#part", self.project)
        self.assertEqual(expected, actual)
        self.assertNotIn("example", actual[0])
        self.assertNotIn("name@", actual[0])
        self.assertNotIn("?", actual[0])

    def test_ssh_url_and_scp_style_share_identity(self):
        self.assertEqual(
            store.normalize_remote("ssh://git@github.com:22/team/app.git", self.project),
            store.normalize_remote("git@github.com:team/app.git", self.project),
        )

    def test_github_path_case_does_not_split_project(self):
        self.assertEqual(
            store.normalize_remote("git@github.com:Team/App.git", self.project),
            store.normalize_remote("https://github.com/team/app.git", self.project),
        )

    def test_ipv6_scp_origin_is_independent_of_checkout_path(self):
        origin = "git@[2001:db8::1]:team/app.git"
        self.assertEqual(store.normalize_remote(origin, self.project),
                         store.normalize_remote(origin, self.root / "second"))
        self.assertEqual(
            store.normalize_remote("git@[2001:db8::1]:/team/app.git", self.project),
            store.normalize_remote("ssh://git@[2001:db8::1]/team/app.git", self.project),
        )

    def test_host_port_and_repository_are_separate(self):
        origins = ["https://a.test/team/app.git", "https://b.test/team/app.git",
                   "https://a.test/other/app.git", "ssh://git@a.test:2222/team/app.git"]
        identities = {store.normalize_remote(origin, self.project)[0] for origin in origins}
        self.assertEqual(len(identities), len(origins))

    def test_distinct_ssh_users_do_not_share_store(self):
        first = store.normalize_remote("alice@host.test:repo.git", self.project)
        second = store.normalize_remote("bob@host.test:repo.git", self.project)
        self.assertNotEqual(first[0], second[0])
        self.assertNotIn("alice", first[0])
        self.assertNotEqual(first, store.normalize_remote("ssh://alice@host.test/repo.git", self.project))
        self.assertEqual(
            store.normalize_remote("alice@host.test:/repo.git", self.project),
            store.normalize_remote("ssh://alice@host.test/repo.git", self.project),
        )

    def test_implicit_ssh_user_does_not_alias_explicit_git_user(self):
        self.assertNotEqual(
            store.normalize_remote("ssh://host.test/repo.git", self.project),
            store.normalize_remote("ssh://git@host.test/repo.git", self.project),
        )

    def test_ipv6_address_and_port_are_distinct(self):
        self.assertNotEqual(
            store.normalize_remote("ssh://git@[2001:db8::1]:2222/repo.git", self.project),
            store.normalize_remote("ssh://git@[2001:db8::1:2222]/repo.git", self.project),
        )

    def test_local_remote_forms_share_identity(self):
        remote = self.root / "upstream.git"
        self.assertEqual(
            store.normalize_remote(str(remote), self.project),
            store.normalize_remote(remote.as_uri(), self.project),
        )

    def test_lookup_does_not_create_storage(self):
        result = store.locate(self.project, self.data)
        self.assertFalse(result["exists"])
        self.assertFalse(self.data.exists())

    def test_init_creates_only_project_directory(self):
        result = store.locate(self.project, self.data, create=True)
        folder = Path(result["store_dir"])
        self.assertTrue(folder.is_dir())
        self.assertEqual(list(folder.iterdir()), [])
        self.assertEqual(store.locate(self.project, self.data), result)

    def test_clones_with_same_origin_share_store(self):
        self.init_repo("git@github.com:Team/App.git")
        second = self.root / "different-checkout-name"
        second.mkdir()
        self.git("init", "-q", cwd=second)
        self.git("remote", "add", "origin", "https://github.com/Team/App.git", cwd=second)
        first_result = store.locate(self.project, self.data)
        second_result = store.locate(second, self.data)
        self.assertEqual(first_result["store_dir"], second_result["store_dir"])

    def test_worktree_without_origin_shares_store(self):
        self.init_repo()
        self.git("-c", "user.name=QA Fixture", "-c", "user.email=qa@example.invalid",
                 "-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "--no-gpg-sign", "-qm", "fixture")
        second = self.root / "worktree"
        self.git("worktree", "add", "--detach", "-q", str(second))
        self.assertEqual(store.locate(self.project, self.data)["store_dir"],
                         store.locate(second, self.data)["store_dir"])

    def test_subdirectory_and_symlink_share_project_identity(self):
        self.init_repo("https://host.test/team/app.git")
        child = self.project / "Sources"
        child.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(self.project, target_is_directory=True)
        expected = store.locate(self.project, self.data)["project_key"]
        self.assertEqual(store.locate(child, self.data)["project_key"], expected)
        self.assertEqual(store.locate(alias, self.data)["project_key"], expected)

    def test_relative_origin_keeps_identity_from_subdirectory(self):
        self.init_repo("../upstream.git")
        child = self.project / "Sources"
        child.mkdir()
        self.assertEqual(store.locate(child, self.data)["project_key"],
                         store.locate(self.project, self.data)["project_key"])

    def test_git_environment_cannot_redirect_project(self):
        self.init_repo("https://host.test/team/app.git")
        other = self.root / "other"
        other.mkdir()
        self.git("init", "-q", cwd=other)
        self.git("remote", "add", "origin", "https://host.test/team/other.git", cwd=other)
        expected = store.locate(self.project, self.data)
        injected = {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other),
                    "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "remote.origin.url",
                    "GIT_CONFIG_VALUE_0": "https://host.test/injected/repo.git"}
        with patch.dict(os.environ, injected):
            self.assertEqual(store.locate(self.project, self.data), expected)

    def test_distinct_non_git_projects_do_not_share_store(self):
        second = self.root / "other/checkout"
        second.mkdir(parents=True)
        self.assertNotEqual(store.locate(self.project, self.data)["project_key"],
                            store.locate(second, self.data)["project_key"])

    def test_project_symlink_cannot_redirect_storage(self):
        result = store.locate(self.project, self.data)
        self.data.mkdir(parents=True)
        outside = self.root / "outside"
        outside.mkdir()
        Path(result["store_dir"]).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            store.locate(self.project, self.data, create=True)
        self.assertEqual(list(outside.iterdir()), [])

    def test_missing_project_is_rejected(self):
        with self.assertRaises(ValueError):
            store.locate(self.root / "missing", self.data)
        self.assertFalse(self.data.exists())

    def test_public_or_unwritable_store_is_rejected_without_repair(self):
        folder = Path(store.locate(self.project, self.data, create=True)["store_dir"])
        try:
            for mode in (0o755, 0o500):
                with self.subTest(mode=mode):
                    folder.chmod(mode)
                    with self.assertRaises(ValueError):
                        store.locate(self.project, self.data, create=True)
                    self.assertEqual(folder.stat().st_mode & 0o777, mode)
        finally:
            folder.chmod(0o700)

    def test_other_owner_store_is_rejected(self):
        store.locate(self.project, self.data, create=True)
        uid = os.getuid()
        with patch.object(store.os, "getuid", return_value=uid + 1):
            with self.assertRaises(ValueError):
                store.locate(self.project, self.data)

    def test_cli_lookup_has_no_writes_or_remote_credentials(self):
        self.init_repo("https://name:example@host.test/team/app.git")
        home = self.root / "isolated-home"
        home.mkdir()
        result = subprocess.run(
            [os.sys.executable, "-B", str(SCRIPT), "--project", str(self.project)],
            env={**os.environ, "HOME": str(home)}, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("example", result.stdout)
        self.assertNotIn("name@", result.stdout)
        self.assertFalse((home / "Library").exists())

    def test_invalid_remote_is_reported_without_raw_value(self):
        self.init_repo("https://name:example@host.test:invalid/team/app.git")
        result = subprocess.run(
            [os.sys.executable, "-B", str(SCRIPT), "--project", str(self.project)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("example", result.stdout + result.stderr)

    def test_control_characters_in_remote_are_rejected(self):
        with self.assertRaises(ValueError):
            store.normalize_remote("https://host.test/team/app\n.git", self.project)


if __name__ == "__main__":
    unittest.main()
