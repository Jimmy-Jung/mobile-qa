"""Selected local storage contracts. Author: JunyoungJung · 2026-10-22."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import call, patch

from mobile_qa import storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.mount = self.base / "External"
        self.mount.mkdir()
        self.selected = self.mount / "QA"
        self.selected.mkdir()
        self.config = self.base / "settings" / "config.json"
        self.project = self.base / "Example App"
        self.project.mkdir()
        self.device = "/dev/disk9s1"
        self.aliases = []
        self.nested_volumes = []
        self.volume = {"MountPoint": str(self.mount), "DeviceNode": self.device,
                       "VolumeUUID": "11111111-2222-3333-4444-555555555555",
                       "Internal": False, "WritableVolume": True}
        self.disk = patch.object(storage.subprocess, "run", side_effect=self.disk_result).start()
        self.addCleanup(patch.stopall)

    def disk_result(self, command, **kwargs):
        if command[:2] == ["/bin/df", "-P"]:
            path = Path(command[2])
            on_volume = any(path.is_relative_to(root) for root in (self.mount, *self.aliases))
            nested = any(path.is_relative_to(root) for root in self.nested_volumes)
            device = self.device if on_volume and not nested else "/dev/disk0s1"
            output = f"Filesystem 512-blocks Used Available Capacity Mounted on\n{device} 100 20 80 20% {self.mount}\n"
            return subprocess.CompletedProcess(command, 0, output.encode(), b"")
        self.assertEqual(command[:3], ["/usr/sbin/diskutil", "info", "-plist"])
        self.assertEqual(command[3], self.device)
        return subprocess.CompletedProcess(command, 0, plistlib.dumps(self.volume), b"")

    def configure(self):
        return storage.configure_storage(self.selected, self.config)

    def test_configure_roundtrip_private_permissions(self):
        settings = self.configure()
        self.assertEqual(settings["storage_dir"], str(self.selected))
        self.assertEqual(storage.load_settings(self.config), settings)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.config.parent.stat().st_mode), 0o700)

    def test_subfolder_selection_queries_its_mounted_volume(self):
        selected = self.selected / "Build Storage"
        selected.mkdir()
        settings = storage.configure_storage(selected, self.config)
        self.assertEqual(self.disk.call_args.args[0],
                         ["/bin/df", "-P", str(self.mount)])
        self.assertIn(call(["/usr/sbin/diskutil", "info", "-plist", self.device],
                           capture_output=True, timeout=15), self.disk.call_args_list)
        self.assertEqual(settings["storage_dir"], str(selected))
        self.assertEqual(settings["mount_point"], str(self.mount))

    def test_missing_settings_is_readonly(self):
        with self.assertRaisesRegex(storage.StorageError, "setup"):
            storage.load_settings(self.config)
        self.assertFalse(self.config.parent.exists())
        self.disk.assert_not_called()

    def test_config_environment_is_used_without_changing_default(self):
        with patch.dict(os.environ, {"MOBILE_QA_CONFIG": str(self.config)}):
            self.assertEqual(storage._config_path(None), self.config)
            storage.configure_storage(self.selected)
            self.assertEqual(storage.load_settings()["storage_dir"], str(self.selected))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(storage._config_path(None), Path.home() / "Library/Application Support/mobile-qa/config.json")

    def test_explicit_config_takes_precedence_over_environment(self):
        with patch.dict(os.environ, {"MOBILE_QA_CONFIG": "relative/ignored.json"}):
            self.configure()
            self.assertEqual(storage.load_settings(self.config)["storage_dir"], str(self.selected))

    def test_environment_config_keeps_path_validation(self):
        alias = self.base / "config-alias"
        alias.symlink_to(self.base, target_is_directory=True)
        for value in ("relative/config.json", str(alias / "config.json")):
            with self.subTest(value=value), patch.dict(os.environ, {"MOBILE_QA_CONFIG": value}):
                with self.assertRaises(storage.StorageError):
                    storage.load_settings()

    def test_cancel_keeps_existing_settings(self):
        self.configure()
        original = self.config.read_bytes()
        with patch.object(storage, "_choose_folder", side_effect=storage.StorageError("cancelled")):
            with self.assertRaises(storage.StorageError):
                storage.configure_storage(config_path=self.config)
        self.assertEqual(self.config.read_bytes(), original)

    def test_native_dialog_has_no_interpolated_path(self):
        result = subprocess.CompletedProcess([], 0, b"/chosen/folder/\n", b"")
        with patch.object(storage.sys, "platform", "darwin"), patch.object(storage.subprocess, "run", return_value=result) as run:
            self.assertEqual(storage._choose_folder(), Path("/chosen/folder"))
        self.assertEqual(run.call_args.args[0][0], "/usr/bin/osascript")

    def test_headless_requires_explicit_folder(self):
        with patch.object(storage.sys, "platform", "linux"):
            with self.assertRaisesRegex(storage.StorageError, "storage-dir"):
                storage._choose_folder()

    def test_invalid_config_path_is_rejected_before_dialog(self):
        with patch.object(storage, "_choose_folder") as choose:
            with self.assertRaises(storage.StorageError):
                storage.configure_storage(config_path="relative/config.json")
        choose.assert_not_called()

    def test_dialog_cancel_does_not_expose_error_output(self):
        result = subprocess.CompletedProcess([], 1, b"", b"PRIVATE (-128)")
        with patch.object(storage.sys, "platform", "darwin"), patch.object(storage.subprocess, "run", return_value=result):
            with self.assertRaises(storage.StorageError) as error:
                storage._choose_folder()
        self.assertNotIn("PRIVATE", str(error.exception))

    def test_selected_folder_must_exist(self):
        with self.assertRaises(storage.StorageError):
            storage.configure_storage(self.mount / "missing", self.config)
        self.assertFalse(self.config.exists())

    def test_internal_volume_is_supported_without_changing_config_format(self):
        self.volume["Internal"] = True
        settings = self.configure()
        self.assertEqual(set(settings), {"version", "storage_dir", "volume_uuid", "mount_point"})
        self.assertEqual(storage.verify_storage(storage.load_settings(self.config)), settings)
        self.assertEqual(storage.verify_output(self.selected / "reports/result.md", self.config),
                         self.selected / "reports/result.md")

    def test_first_setup_can_choose_internal_storage_then_reuse_it(self):
        self.volume["Internal"] = True
        spec = importlib.util.spec_from_file_location("mobile_qa_setup_internal", Path(__file__).parents[1] / "scripts/setup.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(storage, "_choose_folder", return_value=self.selected) as choose, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(["--config", str(self.config)]), 0)
            self.assertEqual(module.main(["--config", str(self.config)]), 0)
        choose.assert_called_once()

    def test_readonly_volume_rejected(self):
        self.volume["WritableVolume"] = False
        with self.assertRaises(storage.StorageError):
            self.configure()

    def test_unmounted_folder_cannot_be_configured(self):
        self.disk.side_effect = None
        self.disk.return_value = subprocess.CompletedProcess([], 1, b"", b"PRIVATE")
        with self.assertRaises(storage.StorageError):
            self.configure()
        self.assertFalse(self.config.exists())

    def test_apfs_user_folder_can_be_outside_actual_mount_path(self):
        self.volume["Internal"] = True
        selected = self.base / "Users" / "QA Storage"
        selected.mkdir(parents=True)
        self.aliases = [selected]
        settings = storage.configure_storage(selected, self.config)
        self.assertFalse(selected.is_relative_to(self.mount))
        self.assertEqual(settings["storage_dir"], str(selected))
        self.assertEqual(settings["mount_point"], str(self.mount))
        self.assertEqual(storage.verify_storage(storage.load_settings(self.config)), settings)
        self.assertEqual(storage.verify_output(selected / "reports/result.md", self.config),
                         selected / "reports/result.md")

    def test_invalid_df_device_or_output_is_rejected_without_leaking_contents(self):
        for output in (b"PRIVATE", b"header\nserver:/PRIVATE 100 20 80 20% /volume\n",
                       b"header\n/dev/disk9s1 100 20 80 20% /volume\nextra\n"):
            with self.subTest(output=output):
                self.disk.side_effect = None
                self.disk.return_value = subprocess.CompletedProcess([], 0, output, b"PRIVATE")
                with self.assertRaises(storage.StorageError) as error:
                    self.configure()
                self.assertNotIn("PRIVATE", str(error.exception))
                self.assertFalse(self.config.exists())

    def test_diskutil_device_identity_must_match_the_selected_folder(self):
        self.volume["DeviceNode"] = "/dev/disk8s1"
        with self.assertRaises(storage.StorageError):
            self.configure()

    def test_volume_mount_verification_does_not_rely_on_ismount(self):
        with patch.object(storage.os.path, "ismount", return_value=False):
            self.assertEqual(self.configure()["volume_uuid"], self.volume["VolumeUUID"])

    def test_nested_volume_is_rejected_even_when_stat_devices_look_equal(self):
        self.configure()
        nested = self.selected / "Other Volume"
        nested.mkdir()
        self.nested_volumes = [nested]
        with self.assertRaises(storage.StorageError):
            storage.verify_output(nested / "result.md", self.config)
        self.assertEqual(list(nested.iterdir()), [])

    def test_configured_volume_changes_are_rejected_without_rewriting_settings(self):
        self.configure()
        original = self.config.read_bytes()
        self.volume["VolumeUUID"] = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
        with self.assertRaises(storage.StorageError):
            storage.verify_output(self.selected / "result.md", self.config)
        self.assertEqual(self.config.read_bytes(), original)

    def test_missing_df_fails_closed(self):
        self.disk.side_effect = FileNotFoundError("PRIVATE")
        with self.assertRaises(storage.StorageError) as error:
            self.configure()
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertFalse(self.config.exists())

    def test_mounted_volume_path_cannot_be_a_symlink(self):
        alias = self.base / "mount-alias"
        alias.symlink_to(self.mount, target_is_directory=True)
        self.volume["MountPoint"] = str(alias)
        with self.assertRaises(storage.StorageError):
            self.configure()

    def test_volume_without_uuid_rejected(self):
        del self.volume["VolumeUUID"]
        with self.assertRaises(storage.StorageError):
            self.configure()

    def test_diskutil_failure_redacted(self):
        self.disk.side_effect = lambda command, **kwargs: (
            self.disk_result(command, **kwargs) if command[0] == "/bin/df"
            else subprocess.CompletedProcess(command, 1, b"PRIVATE", b"PRIVATE"))
        with self.assertRaises(storage.StorageError) as error:
            self.configure()
        self.assertNotIn("PRIVATE", str(error.exception))

    def test_unavailable_diskutil_fails_closed(self):
        def run(command, **kwargs):
            if command[0] == "/bin/df":
                return self.disk_result(command, **kwargs)
            raise FileNotFoundError("PRIVATE")
        self.disk.side_effect = run
        with self.assertRaises(storage.StorageError) as error:
            self.configure()
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertFalse(self.config.exists())

    def test_invalid_plist_rejected(self):
        self.disk.side_effect = lambda command, **kwargs: (
            self.disk_result(command, **kwargs) if command[0] == "/bin/df"
            else subprocess.CompletedProcess(command, 0, b"invalid", b""))
        with self.assertRaises(storage.StorageError):
            self.configure()

    def test_uuid_mismatch_does_not_create_workspace(self):
        self.configure()
        self.volume["VolumeUUID"] = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
        with self.assertRaises(storage.StorageError):
            storage.workspace_directory(self.project, config_path=self.config, create=True)
        self.assertFalse((self.selected / "AgentBuilds").exists())

    def test_mountpoint_mismatch_rejected(self):
        settings = self.configure()
        self.volume["MountPoint"] = str(self.base)
        with self.assertRaises(storage.StorageError):
            storage.verify_storage(settings)

    def test_workspace_default_readonly_and_stable_physical_key(self):
        self.configure()
        alias = self.base / "project-alias"
        alias.symlink_to(self.project, target_is_directory=True)
        directory = storage.workspace_directory(self.project, config_path=self.config)
        self.assertEqual(directory, storage.workspace_directory(alias, config_path=self.config))
        self.assertRegex(directory.name, r"^Example App-[a-f0-9]{12}$")
        self.assertFalse(directory.exists())

    def test_workspace_creation_and_category(self):
        self.configure()
        directory = storage.workspace_directory(self.project, category="logs", config_path=self.config, create=True)
        self.assertTrue(directory.is_dir())
        self.assertTrue(directory.is_relative_to(self.selected / "AgentBuilds"))
        self.assertEqual(directory.name, "logs")

    def test_invalid_categories_rejected(self):
        self.configure()
        for category in ("..", "../outside", "/absolute", "a/b", "", "a\\b"):
            with self.subTest(category=category), self.assertRaises(storage.StorageError):
                storage.workspace_directory(self.project, category=category, config_path=self.config)

    def test_output_verification_is_readonly(self):
        self.configure()
        output = self.selected / "reports" / "capture.png"
        self.assertEqual(storage.verify_output(output, self.config), output)
        self.assertFalse(output.parent.exists())
        with self.assertRaises(storage.StorageError):
            storage.verify_output(self.base / "outside", self.config)

    def test_output_symlink_escape_rejected(self):
        self.configure()
        (self.selected / "escape").symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(storage.StorageError):
            storage.verify_output(self.selected / "escape" / "capture.png", self.config)

    def test_workspace_symlink_escape_rejected(self):
        self.configure()
        (self.selected / "AgentBuilds").symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(storage.StorageError):
            storage.workspace_directory(self.project, config_path=self.config, create=True)

    def test_nested_mount_cannot_receive_outputs_or_workspaces(self):
        self.configure()
        nested = self.selected / "AgentBuilds"
        nested.mkdir()
        original_stat = Path.stat

        def different_device(path, *args, **kwargs):
            info = original_stat(path, *args, **kwargs)
            if path == nested:
                fields = list(info)
                fields[2] += 1
                return os.stat_result(fields)
            return info

        with patch.object(Path, "stat", different_device):
            with self.assertRaises(storage.StorageError):
                storage.verify_output(nested / "future" / "capture.png", self.config)
            with self.assertRaises(storage.StorageError):
                storage.workspace_directory(self.project, config_path=self.config, create=True)
        self.assertEqual(list(nested.iterdir()), [])

    def test_duplicate_json_is_rejected_without_contents(self):
        self.configure()
        self.config.write_text('{"version":1,"version":"PRIVATE"}')
        with self.assertRaises(storage.StorageError) as error:
            storage.load_settings(self.config)
        self.assertNotIn("PRIVATE", str(error.exception))

    def test_invalid_configuration_shapes_are_rejected(self):
        self.configure()
        valid = storage.load_settings(self.config)
        for value in ([], {**valid, "version": True}, {**valid, "volume_uuid": "PRIVATE"},
                      {**valid, "mount_point": "relative/mount"}, {**valid, "extra": "PRIVATE"}):
            with self.subTest(value=value):
                self.config.write_text(json.dumps(value))
                with self.assertRaises(storage.StorageError) as error:
                    storage.load_settings(self.config)
                self.assertNotIn("PRIVATE", str(error.exception))

    def test_config_owner_must_match(self):
        self.configure()
        with patch.object(storage.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaises(storage.StorageError):
                storage.load_settings(self.config)

    def test_insecure_or_symlink_settings_rejected(self):
        self.configure()
        self.config.chmod(0o644)
        with self.assertRaises(storage.StorageError):
            storage.load_settings(self.config)
        self.config.chmod(0o600)
        alias = self.config.parent / "alias.json"
        alias.symlink_to(self.config)
        with self.assertRaises(storage.StorageError):
            storage.load_settings(alias)

    def test_insecure_config_directory_rejected_without_chmod(self):
        self.config.parent.mkdir(mode=0o755)
        with self.assertRaises(storage.StorageError):
            self.configure()
        self.assertEqual(stat.S_IMODE(self.config.parent.stat().st_mode), 0o755)

    def test_atomic_write_failure_preserves_previous_settings(self):
        self.configure()
        original = self.config.read_bytes()
        with patch.object(storage.os, "replace", side_effect=OSError("PRIVATE")):
            with self.assertRaises(storage.StorageError):
                self.configure()
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(list(self.config.parent.iterdir()), [self.config])

    def test_setup_cli_first_use_then_reuses_without_dialog(self):
        spec = importlib.util.spec_from_file_location("mobile_qa_setup", Path(__file__).parents[1] / "scripts/setup.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(storage, "_choose_folder", return_value=self.selected) as choose, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(["--config", str(self.config)]), 0)
            self.assertEqual(module.main(["--config", str(self.config)]), 0)
        choose.assert_called_once()

    def test_setup_cli_explicit_reconfigure(self):
        self.configure()
        other = self.mount / "Other QA"
        other.mkdir()
        spec = importlib.util.spec_from_file_location("mobile_qa_setup", Path(__file__).parents[1] / "scripts/setup.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(["--config", str(self.config), "--storage-dir", str(other)]), 0)
        self.assertEqual(storage.load_settings(self.config)["storage_dir"], str(other))

    def test_setup_cli_reuses_environment_config(self):
        self.configure()
        spec = importlib.util.spec_from_file_location("mobile_qa_setup", Path(__file__).parents[1] / "scripts/setup.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.dict(os.environ, {"MOBILE_QA_CONFIG": str(self.config)}), patch.object(storage, "_choose_folder") as choose:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.main([]), 0)
        choose.assert_not_called()


if __name__ == "__main__":
    unittest.main()
