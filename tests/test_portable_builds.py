"""Portable build and release boundaries. Author: JunyoungJung · 2026-10-22."""

import importlib
from contextlib import nullcontext, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import plistlib
import subprocess
import zipfile
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "skills/test-accounts/scripts"))
import build_helper as helper
import build_android_input as android
import build_ios_input as ios
import keychain_store as store


class PortableBuildTests(unittest.TestCase):
    def test_helper_build_records_signed_binary_and_release_without_raw_output(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "NativeHelper"
            calls = []
            def pins(path):
                result = path / "DriverPins.swift"
                result.write_text("synthetic-pins")
                return result
            def run(command, **kwargs):
                calls.append((command, kwargs))
                if command[0] == "/usr/bin/xcrun":
                    Path(command[-1]).write_text("synthetic-binary")
                return subprocess.CompletedProcess(command, 0)
            with patch.object(helper, "verify_release"), patch.object(helper, "helper_directory", return_value=directory), \
                 patch.object(helper, "verify_output", side_effect=lambda path: path), \
                 patch.object(helper, "build_lock", return_value=nullcontext()), patch.object(helper, "driver_pins", side_effect=pins), \
                 patch.object(helper, "current_digest", return_value="a" * 64), \
                 patch.object(store, "current_digest", return_value="a" * 64), \
                 patch.object(helper.subprocess, "run", side_effect=run), redirect_stdout(io.StringIO()):
                helper.build()
                saved = json.loads((directory / "build.json").read_text())
                (directory / "build.json").unlink()
                with patch.object(helper, "current_digest", side_effect=["a" * 64, "b" * 64]):
                    with self.assertRaises(ValueError):
                        helper.build()
                self.assertFalse((directory / "build.json").exists())
                (directory / "build.json").write_text(json.dumps(saved))
            manifest = json.loads((directory / "build.json").read_text())
            self.assertEqual(manifest["release_digest"], "a" * 64)
            self.assertEqual(manifest["driver_pins_sha256"], store.sha256(directory / "DriverPins.swift"))
            self.assertEqual(calls[-1][0][:3], ["/usr/bin/codesign", "--verify", "--strict"])
            self.assertTrue(all(kwargs["stdout"] is kwargs["stderr"] for _, kwargs in calls))
            self.assertEqual(calls[0][1]["env"]["TMPDIR"], str(directory / "tmp") + "/")

    def test_android_build_preserves_original_source_and_records_exact_artifact(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory, skill = root / "AndroidInput", root / "skill"
            source = skill / "android/SecureInput.java"
            source.parent.mkdir(parents=True)
            source.write_text('class SecureInput { String release = "__MOBILE_QA_RELEASE_DIGEST__"; }')
            tools = {}
            for name in ("javac", "java", "adb", "d8_jar", "android_jar"):
                tools[name] = root / name
                tools[name].write_text("synthetic-tool")
            def run(command, **kwargs):
                self.assertIs(kwargs["stdout"], kwargs["stderr"])
                if command[0] == str(tools["javac"]):
                    target = Path(command[command.index("-d") + 1]) / "SecureInput.class"
                    target.write_bytes(b"synthetic-class")
                    self.assertIn("a" * 64, Path(command[-1]).read_text())
                else:
                    target = Path(command[command.index("--output") + 1]) / "classes.dex"
                    target.write_bytes(b"synthetic-dex")
                return subprocess.CompletedProcess(command, 0)
            with patch.object(android, "verify_release"), patch.object(android, "build_directory", return_value=directory), \
                 patch.object(android, "toolchain", return_value=tools), patch.object(android, "SKILL_DIR", skill), \
                 patch.object(android, "current_digest", return_value="a" * 64), \
                 patch.object(android, "verify_output_path", side_effect=lambda path: path), \
                 patch.object(android, "build_lock", return_value=nullcontext()), \
                 patch.object(android.subprocess, "run", side_effect=run), redirect_stdout(io.StringIO()):
                android.build()
                saved = json.loads((directory / "build.json").read_text())
                (directory / "build.json").unlink()
                with patch.object(android, "current_digest", side_effect=["a" * 64, "b" * 64]):
                    with self.assertRaises(ValueError):
                        android.build()
                self.assertFalse((directory / "build.json").exists())
                (directory / "build.json").write_text(json.dumps(saved))
            artifact = directory / "secure-input.jar"
            with zipfile.ZipFile(artifact) as archive:
                self.assertEqual(archive.read("classes.dex"), b"synthetic-dex")
            self.assertIn("__MOBILE_QA_RELEASE_DIGEST__", source.read_text())
            manifest = json.loads((directory / "build.json").read_text())
            self.assertEqual(manifest["jar_sha256"], android.sha256(artifact))
            self.assertEqual(set(manifest["tools"]), set(tools))

    def test_ios_build_pins_signed_products_and_disables_attachments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = root / "IOSInput"
            products = directory / "DerivedData/Build/Products"
            runner = directory / "Source/slot/runner/AgentDeviceRunner"
            def run(command, **kwargs):
                if command[0] == "/usr/bin/pgrep":
                    return subprocess.CompletedProcess(command, 1, stdout="")
                if command[0] == "xcodebuild":
                    for name in ("Runner.app", "Tests.app"):
                        (products / "Debug-iphoneos" / name).mkdir(parents=True)
                    (products / "runner-iphoneos.xctestrun").write_bytes(plistlib.dumps({
                        "Tests": {"TestBundlePath": "fixture", "SystemAttachmentLifetime": "keepAlways"}}))
                return subprocess.CompletedProcess(command, 0)
            with patch.object(ios, "verify_release"), patch.object(ios, "helper_directory", return_value=root / "NativeHelper"), \
                 patch.object(ios, "verify_output", side_effect=lambda path: path), \
                 patch.object(ios, "build_lock", return_value=nullcontext()), \
                 patch.object(ios, "prepare", return_value=(runner, {"fixture": "b" * 64})), \
                 patch.object(ios, "package_identity", return_value={"version": "0.21.30"}), \
                 patch.object(ios, "current_digest", return_value="a" * 64), \
                 patch.object(ios.subprocess, "run", side_effect=run), redirect_stdout(io.StringIO()):
                ios.build(team_id="ABCDEFGHIJ")
            manifest = json.loads((directory / "build.json").read_text())
            self.assertIs(manifest["signed"], True)
            self.assertEqual(manifest["release_digest"], "a" * 64)
            self.assertEqual(manifest["runner_tree_sha256"], ios.runner_tree_digest(products))
            settings = plistlib.loads(Path(manifest["xctestrun"]).read_bytes())["Tests"]
            self.assertEqual(settings["SystemAttachmentLifetime"], "keepNever")
            self.assertEqual(settings["UserAttachmentLifetime"], "keepNever")

    def test_helper_location_uses_verified_selected_storage_without_creation(self):
        with patch.object(store, "workspace_directory", return_value=Path("/selected/NativeHelper")) as location:
            self.assertEqual(store.helper_directory(), Path("/selected/NativeHelper"))
        location.assert_called_once_with(store.SKILL_DIR, category="NativeHelper", create=False)

    def test_missing_storage_does_not_start_compiler_or_create_outputs(self):
        with patch.object(helper, "verify_release"), patch.object(helper, "current_digest", return_value="a" * 64), \
             patch.object(helper, "helper_directory", side_effect=ValueError("not configured")), \
             patch.object(helper.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                helper.build()
        run.assert_not_called()

    def test_agent_device_is_discovered_from_resolved_cli_and_pinned(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder) / "agent-device"
            binary = package / "bin/agent-device.mjs"
            binary.parent.mkdir(parents=True)
            binary.write_text("synthetic")
            metadata = {"name": "agent-device", "version": "0.21.30", "bin": {"agent-device": "bin/agent-device.mjs"}}
            (package / "package.json").write_text(json.dumps(metadata))
            with patch.object(ios.shutil, "which", return_value=str(binary)):
                self.assertEqual(ios.agent_device_package(), package)
                for version in ("0.21.0", "99.0.0"):
                    (package / "package.json").write_text(json.dumps({**metadata, "version": version}))
                    with self.subTest(version=version), self.assertRaises(ValueError):
                        ios.agent_device_package()
            with patch.object(ios.shutil, "which", return_value=None):
                with self.assertRaises(ValueError):
                    ios.agent_device_package()

    def test_helper_manifest_binds_release_and_tool_pins(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / "keychain-helper"
            executable.write_text("synthetic-binary")
            pins = root / "DriverPins.swift"
            pins.write_text("synthetic-pins")
            with patch.object(store, "current_digest", return_value="a" * 64), \
                 patch.object(store, "source_digest", return_value="b" * 64):
                recorded = store.helper_manifest(executable)
            self.assertEqual(recorded["release_digest"], "a" * 64)
            self.assertEqual(recorded["driver_pins_sha256"], store.sha256(pins))
            with patch.object(store, "helper_directory", return_value=root), \
                 patch.object(store, "verify_output", side_effect=lambda path: path), \
                 patch.object(store, "verify_release"), \
                 patch.object(store, "current_digest", return_value="c" * 64), \
                 patch.object(store, "source_digest", return_value="b" * 64), \
                 patch.object(store.subprocess, "run") as run:
                (root / "build.json").write_text(json.dumps(recorded))
                with self.assertRaises(store.KeychainError):
                    store.verified_helper()
                run.assert_not_called()

    def test_external_paths_validate_all_outputs_without_creating_them(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "not-created/IOSInput"
            seen = []
            with patch.object(ios, "verify_output", side_effect=lambda path: seen.append(Path(path)) or Path(path)):
                paths = ios.external_build_paths(root)
            self.assertFalse(root.exists())
            self.assertIn(root / "build.log", seen)
            self.assertEqual(paths["package_cache"], root.parent / "SwiftPM/Cache")

    def test_android_toolchain_uses_configured_sdk_and_jdk(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sdk, java_home = root / "sdk", root / "jdk"
            for relative in ("platforms/android-36/android.jar", "build-tools/36.0.0/lib/d8.jar", "platform-tools/adb"):
                path = sdk / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic")
                path.chmod(0o755)
            for name in ("java", "javac"):
                path = java_home / "bin" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic")
                path.chmod(0o755)
            with patch.dict(android.os.environ, {"ANDROID_HOME": str(sdk), "JAVA_HOME": str(java_home)}, clear=True):
                tools = android.toolchain()
            self.assertEqual(tools["adb"], (sdk / "platform-tools/adb").resolve())
            self.assertEqual(tools["java"], (java_home / "bin/java").resolve())
            self.assertEqual(tools["android_jar"], (sdk / "platforms/android-36/android.jar").resolve())
            with patch.dict(android.os.environ, {"ANDROID_HOME": str(sdk), "ANDROID_SDK_ROOT": str(root / "different")}, clear=True):
                with self.assertRaises(ValueError):
                    android.android_sdk()

    def test_build_sources_contain_no_personal_machine_paths(self):
        for filename in ("build_helper.py", "build_ios_input.py", "build_android_input.py", "keychain_store.py"):
            source = (store.SKILL_DIR / "scripts" / filename).read_text()
            self.assertNotRegex(source, r"/Users/[A-Za-z0-9_.-]+/")
            for value in (".nvm/versions", "AXBUILD"):
                self.assertNotIn(value, source, filename)

    def test_build_lock_rejects_concurrent_output_and_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with patch.object(store, "verify_output", side_effect=lambda path: path):
                with store.build_lock(directory):
                    with self.assertRaises(ValueError):
                        with store.build_lock(directory):
                            self.fail("shared output lock accepted")
                (directory / "build.lock").unlink()
                target = directory / "target"
                target.write_text("preserved")
                (directory / "build.lock").symlink_to(target)
                with self.assertRaises(OSError):
                    with store.build_lock(directory):
                        self.fail("symlink lock accepted")
                self.assertEqual(target.read_text(), "preserved")

    def test_driver_pins_preserve_unicode_paths_and_reject_mixed_runner_release(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "선택 폴더/NativeHelper"
            directory.mkdir(parents=True)
            settings = {"mount_point": str(Path(folder)), "volume_uuid": "11111111-2222-3333-4444-555555555555"}
            with patch.object(helper, "load_settings", return_value=settings), \
                 patch.object(helper, "verify_storage", return_value=settings), \
                 patch.object(helper, "verify_output", side_effect=lambda path: path), \
                 patch.object(helper, "current_digest", return_value="a" * 64), \
                 patch.object(helper.shutil, "which", return_value=None):
                pins = helper.driver_pins(directory).read_text()
                self.assertIn("선택 폴더", pins)
                self.assertIn('static let releaseDigest: String = "' + "a" * 64, pins)
                ios_root = directory.parent / "IOSInput"
                ios_root.mkdir()
                (ios_root / "build.json").write_text(json.dumps({
                    "signed": True, "release_digest": "b" * 64,
                    "runner_root": str(ios_root / "Products"), "source_root": str(ios_root / "Source"),
                    "xctestrun": str(ios_root / "Products/runner.xctestrun"),
                }))
                with self.assertRaises(ValueError):
                    helper.driver_pins(directory)


if __name__ == "__main__":
    unittest.main()
