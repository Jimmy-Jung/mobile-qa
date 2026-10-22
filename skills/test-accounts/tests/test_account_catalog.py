"""Project account metadata tests. Author: JunyoungJung. Date: 2026-10-20."""

from __future__ import annotations

import base64

import concurrent.futures
import json
import multiprocessing
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from account_catalog import Catalog, project_key


PROJECT = "github.com/example/player"
RELATED_PROJECT = "github.com/example/android-player"
DEVICE = {"platform": "ios", "name": "Synthetic iPhone", "identifier": "synthetic-ios-device"}


def record(identifier="learner", **changes):
    return {
        "id": identifier,
        "project": PROJECT,
        "environment": "staging",
        "role": "learner",
        "capabilities": ["download", "playback"],
        "keychain_ref": base64.b64encode(b"opaque-reference").decode("ascii"),
        "related_projects": [],
        "related_environments": [],
        "download_device": None,
        **changes,
    }


def add_accounts(path, worker):
    catalog = Catalog(Path(path))
    for index in range(8):
        catalog.add(record(f"worker-{worker}-{index}"))


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="account-catalog-")
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / "private" / "accounts.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_creates_private_storage_and_empty_list(self):
        self.assertEqual(Catalog(self.path).list(), [])
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.with_name("accounts.json.lock").stat().st_mode), 0o600)
        self.assertEqual(json.loads(self.path.read_text()), [])

    def test_add_list_filter_and_copies(self):
        catalog = Catalog(self.path)
        original = record(capabilities=["playback", "download"])
        catalog.add(original)
        original["capabilities"].append("billing")
        catalog.add(record("other", project="gitlab.example.com/other/player"))
        selected = catalog.list(PROJECT)
        self.assertEqual(selected, [record()])
        selected[0]["capabilities"].append("billing")
        self.assertEqual(catalog.list(PROJECT), [record()])
        self.assertEqual(len(catalog.list()), 2)

    def test_exact_match_and_capability_subset(self):
        catalog = Catalog(self.path)
        catalog.add(record())
        self.assertEqual(catalog.find(PROJECT, "staging", "learner", ["playback"]), {
            "status": "selected", "candidates": [record()],
        })
        for project, environment, role, capabilities in [
            ("github.com/example/other", "staging", "learner", []),
            (PROJECT, "production", "learner", []),
            (PROJECT, "staging", "admin", []),
            (PROJECT, "staging", "learner", ["billing"]),
        ]:
            with self.subTest(environment=environment, role=role):
                self.assertEqual(catalog.find(project, environment, role, capabilities), {
                    "status": "not_found", "candidates": [],
                })

    def test_legacy_records_receive_defaults_without_rewriting_storage(self):
        Catalog(self.path)
        legacy = record()
        del legacy["related_projects"], legacy["related_environments"], legacy["download_device"]
        self.path.write_text(json.dumps([legacy]))
        before = self.path.read_bytes()
        catalog = Catalog(self.path)
        self.assertEqual(catalog.list(), [record()])
        self.assertEqual(self.path.read_bytes(), before)

    def test_related_projects_match_exactly_without_creating_another_account(self):
        catalog = Catalog(self.path)
        shared = record(related_projects=[RELATED_PROJECT], download_device=DEVICE.copy())
        catalog.add(shared)
        self.assertEqual(catalog.list(PROJECT), [shared])
        self.assertEqual(catalog.list(RELATED_PROJECT), [shared])
        self.assertEqual(catalog.find(RELATED_PROJECT, "staging", "learner", ["playback"]), {
            "status": "selected", "candidates": [shared],
        })
        self.assertEqual(catalog.list(RELATED_PROJECT + "-other"), [])
        shared["related_projects"].clear()
        shared["download_device"]["name"] = "Changed after registration"
        selected = catalog.list(RELATED_PROJECT)
        selected[0]["related_projects"].clear()
        selected[0]["download_device"]["name"] = "Changed after reading"
        self.assertEqual(catalog.list(RELATED_PROJECT), [
            record(related_projects=[RELATED_PROJECT], download_device=DEVICE),
        ])
        self.assertEqual(len(catalog.list()), 1)

    def test_related_environments_share_one_account_and_match_exactly(self):
        catalog = Catalog(self.path)
        shared = record(
            environment="production", related_environments=["development"],
            related_projects=[RELATED_PROJECT], download_device=DEVICE,
        )
        catalog.add(shared)
        for environment in ["production", "development"]:
            for project in [PROJECT, RELATED_PROJECT]:
                with self.subTest(environment=environment, project=project):
                    self.assertEqual(catalog.find(project, environment, "learner", ["download"], device=DEVICE["name"], platform="ios"), {
                        "status": "selected", "candidates": [shared],
                    })
        for environment in ["staging", "development-other"]:
            self.assertEqual(catalog.find(PROJECT, environment, "learner", ["playback"]), {
                "status": "not_found", "candidates": [],
            })
        shared["related_environments"].clear()
        selected = catalog.list()
        selected[0]["related_environments"].clear()
        self.assertEqual(catalog.list(), [record(
            environment="production", related_environments=["development"],
            related_projects=[RELATED_PROJECT], download_device=DEVICE,
        )])

    def test_download_requires_explicit_device_and_platform_without_candidates(self):
        catalog = Catalog(self.path)
        for registered in [False, True]:
            if registered:
                catalog.add(record(download_device=DEVICE))
            for selection in [{}, {"device": DEVICE["name"]}, {"platform": "ios"}]:
                with self.subTest(registered=registered, provided=tuple(selection)):
                    self.assertEqual(catalog.find(PROJECT, "staging", "learner", ["download"], **selection), {
                        "status": "needs_device", "candidates": [],
                    })

    def test_download_matches_only_the_registered_device_name_or_identifier(self):
        catalog = Catalog(self.path)
        shared = record(related_projects=[RELATED_PROJECT], download_device=DEVICE)
        catalog.add(shared)
        catalog.add(record("unregistered-device"))
        catalog.add(record("other-environment", environment="production", download_device=DEVICE))
        catalog.add(record("other-project", project="github.com/example/other", download_device=DEVICE))
        catalog.add(record("other-role", role="admin", download_device=DEVICE))
        catalog.add(record("no-download", capabilities=["playback"], download_device=DEVICE))
        for project in [PROJECT, RELATED_PROJECT]:
            for device in [DEVICE["name"], DEVICE["identifier"]]:
                with self.subTest(project=project, device_kind="name" if device == DEVICE["name"] else "identifier"):
                    self.assertEqual(catalog.find(project, "staging", "learner", ["download"], device=device, platform="ios"), {
                        "status": "selected", "candidates": [shared],
                    })
        for device in ["Synthetic", DEVICE["name"].lower(), "other-device"]:
            with self.subTest(device_kind="unmatched"):
                self.assertEqual(catalog.find(PROJECT, "staging", "learner", ["download"], device=device, platform="ios"), {
                    "status": "not_found", "candidates": [],
                })
        self.assertEqual(catalog.find(RELATED_PROJECT + "-other", "staging", "learner", ["download"], device=DEVICE["name"], platform="ios"), {
            "status": "not_found", "candidates": [],
        })

    def test_download_can_match_android_device_without_an_identifier(self):
        catalog = Catalog(self.path)
        registered = record(download_device={"platform": "android", "name": "Synthetic Android", "identifier": None})
        catalog.add(registered)
        self.assertEqual(catalog.find(PROJECT, "staging", "learner", ["download"], device="Synthetic Android", platform="android"), {
            "status": "selected", "candidates": [registered],
        })

    def test_download_ambiguity_never_selects_first_registered_account(self):
        catalog = Catalog(self.path)
        catalog.add(record("first", download_device=DEVICE))
        catalog.add(record("second", download_device=DEVICE))
        result = catalog.find(PROJECT, "staging", "learner", ["download"], device=DEVICE["name"], platform="ios")
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual([item["id"] for item in result["candidates"]], ["first", "second"])

    def test_playback_selection_does_not_require_or_filter_by_device(self):
        catalog = Catalog(self.path)
        catalog.add(record())
        self.assertEqual(catalog.find(PROJECT, "staging", "learner", ["playback"], device="unregistered-device", platform="android"), {
            "status": "selected", "candidates": [record()],
        })

    def test_download_separates_platforms_when_device_names_are_identical(self):
        catalog = Catalog(self.path)
        ios = record("ios-account", download_device={**DEVICE, "name": "Synthetic shared name"})
        android = record("android-account", download_device={
            "platform": "android", "name": "Synthetic shared name", "identifier": "synthetic-android-device",
        })
        catalog.add(ios)
        self.assertEqual(catalog.find(PROJECT, "staging", "learner", ["download"], device="Synthetic shared name", platform="android"), {
            "status": "not_found", "candidates": [],
        })
        catalog.add(android)
        for platform, expected in [("ios", ios), ("android", android)]:
            with self.subTest(platform=platform):
                self.assertEqual(catalog.find(PROJECT, "staging", "learner", ["download"], device="Synthetic shared name", platform=platform), {
                    "status": "selected", "candidates": [expected],
                })
                other_identifier = android["download_device"]["identifier"] if platform == "ios" else ios["download_device"]["identifier"]
                self.assertEqual(catalog.find(PROJECT, "staging", "learner", ["download"], device=other_identifier, platform=platform), {
                    "status": "not_found", "candidates": [],
                })

    def test_ambiguity_never_selects_first_candidate(self):
        catalog = Catalog(self.path)
        catalog.add(record("first"))
        catalog.add(record("second"))
        result = catalog.find(PROJECT, "staging", "learner", [])
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual([item["id"] for item in result["candidates"]], ["first", "second"])

    def test_duplicate_id_is_global_and_preserves_data(self):
        catalog = Catalog(self.path)
        catalog.add(record())
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            catalog.add(record(project="github.com/example/another"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_rejects_unknown_credentials_and_invalid_fields_without_echoing_values(self):
        catalog = Catalog(self.path)
        sensitive_password_fixture = "sensitive-password"
        invalid_records = [
            record(username="sensitive-account"),
            record(password=sensitive_password_fixture),
            record(credentials={"token": "sensitive-token"}),
            record(extra="sensitive-extra"),
            record(id="Uppercase"),
            record(id="a" * 65),
            record(environment="stage_ing"),
            record(role=""),
            record(keychain_ref="not-base64"),
            record(keychain_ref=""),
            record(keychain_ref=123),
            record(capabilities=["playback", "playback"]),
            record(capabilities="playback"),
            record(capabilities=[123]),
            record(project="https://user:sensitive-password@host/repo"),
            {key: value for key, value in record().items() if key != "role"},
        ]
        for invalid in invalid_records:
            with self.subTest(keys=tuple(invalid)):
                with self.assertRaises(ValueError) as error:
                    catalog.add(invalid)
                self.assertNotIn("sensitive-", str(error.exception))
        self.assertEqual(catalog.list(), [])

    def test_rejects_invalid_selection_inputs(self):
        catalog = Catalog(self.path)
        for arguments in [
            ("", "staging", "learner", []),
            (None, "staging", "learner", []),
            (PROJECT, "", "learner", []),
            (PROJECT, "staging", "Learner", []),
            (PROJECT, "staging", "learner", ["playback", "playback"]),
        ]:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    catalog.find(*arguments)

    def test_rejects_invalid_related_projects_without_saving_values(self):
        catalog = Catalog(self.path)
        for projects in [
            None, "github.com/example/other", {}, [None], [123],
            ["https://sensitive-account:sensitive-password@host/repo"],
            ["GitHub.com/example/other"], ["github.com/example/other.git"],
            [RELATED_PROJECT, RELATED_PROJECT], [PROJECT],
        ]:
            with self.subTest(value_type=type(projects).__name__):
                with self.assertRaises(ValueError) as error:
                    catalog.add(record(related_projects=projects))
                self.assertNotIn("sensitive-", str(error.exception))
        self.assertEqual(catalog.list(), [])

    def test_rejects_invalid_or_duplicate_related_environments(self):
        catalog = Catalog(self.path)
        for environments in [
            None, "development", {}, [None], [123], [""], ["Development"],
            ["development_other"], ["development", "development"], ["staging"],
        ]:
            with self.subTest(value_type=type(environments).__name__):
                with self.assertRaises(ValueError):
                    catalog.add(record(related_environments=environments))
        self.assertEqual(catalog.list(), [])

    def test_rejects_invalid_registered_devices_without_saving_values(self):
        catalog = Catalog(self.path)
        devices = [False, [], {}, {"platform": "ios", "name": "Synthetic"}]
        devices.extend({**DEVICE, "platform": value} for value in [None, "IOS", "macos"])
        devices.extend({**DEVICE, "name": value} for value in [None, 123, "", "  ", "a" * 129, "bad\nname", "bad\x7fname", "bad\x85name"])
        devices.extend({**DEVICE, "identifier": value} for value in [123, "", " ", "a" * 257, "bad\ridentifier", "bad\x7fidentifier"])
        devices.extend({**DEVICE, field: "sensitive-value"} for field in ["username", "password", "token", "extra"])
        for device in devices:
            with self.subTest(value_type=type(device).__name__):
                with self.assertRaises(ValueError) as error:
                    catalog.add(record(download_device=device))
                self.assertNotIn("sensitive-", str(error.exception))
        self.assertEqual(catalog.list(), [])

    def test_device_text_length_boundaries_are_accepted(self):
        catalog = Catalog(self.path)
        device = {"platform": "ios", "name": "n" * 128, "identifier": "i" * 256}
        registered = record(download_device=device)
        catalog.add(registered)
        for value in [device["name"], device["identifier"]]:
            self.assertEqual(catalog.find(PROJECT, "staging", "learner", ["download"], device=value, platform="ios"), {
                "status": "selected", "candidates": [registered],
            })

    def test_rejects_invalid_device_selection_inputs_without_echoing_values(self):
        catalog = Catalog(self.path)
        for device in [False, 123, {}, [], "", "  ", "a" * 257, "sensitive-device\n", "sensitive-device\x7f", "sensitive-device\x85"]:
            with self.subTest(value_type=type(device).__name__):
                with self.assertRaises(ValueError) as error:
                    catalog.find(PROJECT, "staging", "learner", ["download"], device=device, platform="ios")
                self.assertNotIn("sensitive-", str(error.exception))

    def test_rejects_invalid_platform_selection_for_all_capabilities(self):
        catalog = Catalog(self.path)
        for platform in [False, 123, {}, [], "", "IOS", "macos", "sensitive-platform\n"]:
            for capabilities in [["download"], ["playback"]]:
                with self.subTest(value_type=type(platform).__name__, capabilities=capabilities):
                    with self.assertRaises(ValueError) as error:
                        catalog.find(PROJECT, "staging", "learner", capabilities, device=DEVICE["name"], platform=platform)
                    self.assertNotIn("sensitive-", str(error.exception))

    def test_rejects_malformed_json_schema_and_duplicate_ids_without_repair(self):
        Catalog(self.path)
        for contents in [
            '{"sensitive-password":',
            '{"accounts": []}',
            '[{"id": "sensitive-account"}]',
            json.dumps([record(), record()]),
            '[{"id":"first","id":"second"}]',
        ]:
            with self.subTest(contents_length=len(contents)):
                self.path.write_text(contents)
                with self.assertRaises(ValueError) as error:
                    Catalog(self.path)
                self.assertNotIn("sensitive-", str(error.exception))
                self.assertEqual(self.path.read_text(), contents)

    def test_rejects_non_utf8_json_without_echoing_bytes(self):
        Catalog(self.path)
        self.path.write_bytes(b"\xffsensitive-password")
        with self.assertRaises(ValueError) as error:
            Catalog(self.path)
        self.assertNotIn("sensitive", str(error.exception))

    def test_rejects_permissive_directory_file_and_lock_without_chmod(self):
        Catalog(self.path)
        for target, unsafe_mode, safe_mode in [
            (self.path.parent, 0o755, 0o700),
            (self.path, 0o644, 0o600),
            (self.path.with_name("accounts.json.lock"), 0o644, 0o600),
        ]:
            with self.subTest(kind=target.name):
                target.chmod(unsafe_mode)
                try:
                    with self.assertRaises(ValueError):
                        Catalog(self.path)
                    self.assertEqual(stat.S_IMODE(target.stat().st_mode), unsafe_mode)
                finally:
                    target.chmod(safe_mode)

    def test_rejects_symlink_directory_file_and_lock_without_touching_target(self):
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        secret = outside / "secret.json"
        secret.write_text("[]")
        secret.chmod(0o600)
        self.path.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            Catalog(self.path)
        self.path.parent.unlink()
        self.path.parent.mkdir(mode=0o700)
        self.path.symlink_to(secret)
        with self.assertRaises(ValueError):
            Catalog(self.path)
        self.path.unlink()
        self.path.with_name("accounts.json.lock").unlink()
        self.path.with_name("accounts.json.lock").symlink_to(secret)
        with self.assertRaises(ValueError):
            Catalog(self.path)
        self.assertEqual(secret.read_text(), "[]")

    def test_rejects_hardlinked_catalog(self):
        Catalog(self.path)
        os.link(self.path, self.root / "alias.json")
        with self.assertRaises(ValueError):
            Catalog(self.path)

    def test_rejects_foreign_owner(self):
        catalog = Catalog(self.path)
        with patch("account_catalog.os.geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(ValueError):
                catalog.list()

    def test_revalidates_storage_before_each_operation(self):
        catalog = Catalog(self.path)
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            catalog.add(record())
        self.assertEqual(json.loads(self.path.read_text()), [])

    def test_atomic_replace_preserves_existing_catalog_on_failure(self):
        catalog = Catalog(self.path)
        catalog.add(record())
        before = self.path.read_bytes()
        with patch("account_catalog.os.replace", side_effect=OSError("sensitive-path")):
            with self.assertRaises(ValueError) as error:
                catalog.add(record("second"))
        self.assertNotIn("sensitive", str(error.exception))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(item.name for item in self.path.parent.iterdir()), [
            "accounts.json", "accounts.json.lock",
        ])

    def test_concurrent_registrations_do_not_overwrite_each_other(self):
        context = multiprocessing.get_context("fork")
        with concurrent.futures.ProcessPoolExecutor(max_workers=5, mp_context=context) as executor:
            futures = [executor.submit(add_accounts, str(self.path), worker) for worker in range(5)]
            for future in futures:
                future.result(timeout=20)
        accounts = Catalog(self.path).list()
        self.assertEqual(len(accounts), 40)
        self.assertEqual(len({item["id"] for item in accounts}), 40)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_post_commit_close_failure_does_not_cancel_saved_registration(self):
        catalog = Catalog(self.path)
        real_replace, real_close = os.replace, os.close
        for failing_close in [1, 2, 3]:
            state = {"replaced": False, "closes": 0}

            def replace(*args, **kwargs):
                real_replace(*args, **kwargs)
                state["replaced"] = True

            def close(descriptor):
                real_close(descriptor)
                if state["replaced"]:
                    state["closes"] += 1
                    if state["closes"] == failing_close:
                        raise OSError("synthetic post-commit close failure")

            with self.subTest(cleanup=failing_close):
                with patch("account_catalog.os.replace", side_effect=replace), patch("account_catalog.os.close", side_effect=close):
                    catalog.add(record(f"committed-{failing_close}"))
                self.assertIn(record(f"committed-{failing_close}"), catalog.list())


class ProjectKeyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="account-project-")
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def test_project_identity_ignores_injected_git_environment(self):
        injected = {"GIT_DIR": "/untrusted/repository", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "remote.origin.url", "GIT_CONFIG_VALUE_0": "https://other.test/team/app.git", "GIT_OPTIONAL_LOCKS": "1", "SAFE_CONTEXT": "preserved"}
        with patch.dict(os.environ, injected), patch("account_catalog.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "https://github.com/example/player.git", "")) as run:
            self.assertEqual(project_key(self.root), PROJECT)
        environment = run.call_args.kwargs["env"]
        self.assertEqual({key: value for key, value in environment.items() if key.startswith("GIT_")}, {"GIT_OPTIONAL_LOCKS": "0"})
        self.assertEqual(environment["SAFE_CONTEXT"], "preserved")

    def test_global_origin_cannot_select_another_projects_account_scope(self):
        fake_home = self.root / "home"
        fake_home.mkdir()
        (fake_home / ".gitconfig").write_text('[remote "origin"]\n    url = https://example.test/global/other.git\n')
        checkout = self.root / "checkout"
        checkout.mkdir()
        environment = {**{key: value for key, value in os.environ.items() if not key.startswith("GIT_")}, "HOME": str(fake_home), "XDG_CONFIG_HOME": str(fake_home / "xdg")}
        initialized = subprocess.run(["git", "-C", str(checkout), "init", "--quiet"], env=environment, capture_output=True)
        self.assertEqual(initialized.returncode, 0)
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(project_key(checkout), "path:" + str(checkout))

    def test_remote_normalization_never_exposes_credentials(self):
        for remote in [
            "https://user:sensitive-token@GitHub.COM/example/player.git?token=sensitive-query#fragment",
            "ssh://git@github.com/example/player.git",
            "git@github.com:example/player.git",
            "git://github.com/example/player.git/",
        ]:
            with self.subTest(scheme=remote.split(":", 1)[0]):
                with patch("account_catalog.subprocess.run", return_value=subprocess.CompletedProcess([], 0, remote, "")) as run:
                    self.assertEqual(project_key(self.root), PROJECT)
                    self.assertTrue(run.call_args.kwargs["capture_output"])
                    self.assertNotIn("sensitive", str(run.call_args))

    def test_no_origin_uses_physical_checkout_path(self):
        alias = self.root / "alias"
        real = self.root / "checkout"
        real.mkdir()
        alias.symlink_to(real, target_is_directory=True)
        with patch("account_catalog.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "")):
            self.assertEqual(project_key(alias), f"path:{real}")

    def test_malformed_origin_is_rejected_without_leaking_remote(self):
        with patch("account_catalog.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "https://sensitive-token@/repo", "")):
            with self.assertRaises(ValueError) as error:
                project_key(self.root)
        self.assertNotIn("sensitive", str(error.exception))

    def test_origin_command_failure_is_redacted(self):
        with patch("account_catalog.subprocess.run", side_effect=OSError("sensitive-path")):
            with self.assertRaises(ValueError) as error:
                project_key(self.root)
        self.assertNotIn("sensitive", str(error.exception))


if __name__ == "__main__":
    unittest.main()
