"""Simulator runner build trust contracts. Author: JunyoungJung · 2026-10-27."""

from contextlib import nullcontext
from pathlib import Path
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_helper as helper
import build_ios_input as ios
import build_android_input as android_input

DIGEST = "a" * 64


class SimulatorInputBuildTests(unittest.TestCase):
    def setUp(self):
        output = patch.object(ios, "verify_output", side_effect=lambda path: path)
        output.start()
        self.addCleanup(output.stop)
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "IOSSimulatorInput"
        self.developer = self.root / "Selected Developer"
        self.xcodebuild = self.developer / "usr/bin/xcodebuild"
        self.xcodebuild.parent.mkdir(parents=True)
        self.xcodebuild.write_text("synthetic-xcodebuild")
        self.xcodebuild.chmod(0o755)

    def build_fixture(self, simulator=True):
        directory = self.directory if simulator else self.root / "IOSInput"
        directory.mkdir()
        paths = {key: directory / relative for key, relative in {
            "derived_data": "DerivedData", "temporary": "tmp", "cache_root": "Cache",
            "module_cache": "DerivedData/ModuleCache.noindex", "cloned_packages": "DerivedData/SourcePackages",
            "package_cache": "SwiftPM/Cache",
        }.items()}
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)
        self.commands = []

        def run(command, **kwargs):
            self.commands.append(command)
            if command[0] == "/usr/bin/xcodebuild":
                sdk = "iphonesimulator" if simulator else "iphoneos"
                products = directory / "DerivedData/Build/Products"
                self.products_fixture(products)
                if getattr(self, "mutate_product", None):
                    self.mutate_product(products)
            return subprocess.CompletedProcess(command, 0)

        with patch.object(ios, "current_digest", return_value=DIGEST), \
             patch.object(ios, "package_identity", return_value={"version": "0.21.30"}), \
             patch.object(ios, "prepare", return_value=(directory / "Source/slot/runner/AgentDeviceRunner", {})), \
             patch.object(ios, "verify_output", side_effect=lambda path: path), \
             patch.object(ios.subprocess, "run", side_effect=run):
            ios._build(directory, paths, ios.signing_settings(None, simulator), {},
                       {"DEVELOPER_DIR": str(self.developer)}, simulator, simulator=simulator)
        return json.loads((directory / "build.json").read_text())

    def products_fixture(self, products):
        root = products / "Debug-iphonesimulator"
        for name, bundle_id, package in [
            ("AgentDeviceRunner.app", ios.SIMULATOR_APP_BUNDLE, "APPL"),
            ("AgentDeviceRunnerUITests-Runner.app", ios.SIMULATOR_TEST_BUNDLE + ".xctrunner", "APPL"),
            ("AgentDeviceRunnerUITests-Runner.app/PlugIns/AgentDeviceRunnerUITests.xctest", ios.SIMULATOR_TEST_BUNDLE, "BNDL"),
        ]:
            bundle = root / name
            bundle.mkdir(parents=True)
            executable = bundle.name.rsplit(".", 1)[0]
            (bundle / executable).write_text("synthetic-binary")
            (bundle / executable).chmod(0o755)
            (bundle / "Info.plist").write_bytes(plistlib.dumps({
                "CFBundleIdentifier": bundle_id, "CFBundleExecutable": executable,
                "CFBundlePackageType": package, "CFBundleSupportedPlatforms": ["iPhoneSimulator"],
                "DTPlatformName": "iphonesimulator", "DTSDKName": "iphonesimulator26.5",
            }))
        data = {"TestConfigurations": [{"TestTargets": [{
            "BlueprintName": "AgentDeviceRunnerUITests", "IsUITestBundle": True,
            "TestBundlePath": "__TESTHOST__/PlugIns/AgentDeviceRunnerUITests.xctest",
            "TestHostPath": "__TESTROOT__/Debug-iphonesimulator/AgentDeviceRunnerUITests-Runner.app",
            "TestHostBundleIdentifier": ios.SIMULATOR_TEST_BUNDLE + ".xctrunner",
            "UITargetAppPath": "__TESTROOT__/Debug-iphonesimulator/AgentDeviceRunner.app",
        }]}]}
        run = products / "AgentDeviceRunner_iphonesimulator26.5-arm64.xctestrun"
        run.write_bytes(plistlib.dumps(data))
        return run

    def test_simulator_build_is_unsigned_separate_and_records_exact_target(self):
        manifest = self.build_fixture()
        command = self.commands[0]
        self.assertEqual(command[command.index("-destination") + 1], "generic/platform=iOS Simulator")
        self.assertEqual(command[command.index("-sdk") + 1], "iphonesimulator")
        self.assertIn("CODE_SIGNING_ALLOWED=NO", command)
        self.assertIn("AGENT_DEVICE_IOS_RUNNER_APP_BUNDLE_ID=com.agenttestaccounts.simulator", command)
        self.assertIn("AGENT_DEVICE_IOS_RUNNER_TEST_BUNDLE_ID=com.agenttestaccounts.simulator.uitests", command)
        self.assertFalse(any(command[0] == "/usr/bin/codesign" for command in self.commands))
        self.assertEqual(manifest["target_kind"], "simulator")
        self.assertEqual(manifest["sdk"], "iphonesimulator")
        self.assertIs(manifest["signed"], False)
        self.assertIn("IOSSimulatorInput", manifest["runner_root"])
        self.assertIn("iphonesimulator", manifest["xctestrun"])
        data = plistlib.loads(Path(manifest["xctestrun"]).read_bytes())
        target = data["TestConfigurations"][0]["TestTargets"][0]
        self.assertEqual(target["SystemAttachmentLifetime"], "keepNever")
        self.assertEqual(target["UserAttachmentLifetime"], "keepNever")

    def test_build_rejects_wrong_actual_runner_id_before_writing_manifest(self):
        def mutate(products):
            path = products / "Debug-iphonesimulator/AgentDeviceRunnerUITests-Runner.app/Info.plist"
            data = plistlib.loads(path.read_bytes())
            path.write_bytes(plistlib.dumps({**data, "CFBundleIdentifier": "org.example.wrong.xctrunner"}))
        self.mutate_product = mutate
        with self.assertRaises(ValueError):
            self.build_fixture()
        self.assertFalse((self.directory / "build.json").exists())

    def test_simulator_entry_selects_its_fixed_output_without_a_signing_team(self):
        class Captured(Exception):
            pass
        captured = {}

        def run(command, **kwargs):
            if command[0] == "/usr/bin/pgrep":
                return subprocess.CompletedProcess(command, 1, stdout="")
            captured.update(command=command, environment=kwargs["env"])
            raise Captured()

        with patch.object(ios, "helper_directory", return_value=self.root / "NativeHelper"), \
             patch.object(ios, "developer_directory", return_value=self.developer), \
             patch.object(ios, "verify_release"), patch.object(ios, "verify_output", side_effect=lambda path: path), \
             patch.object(ios, "current_digest", return_value=DIGEST), \
             patch.object(ios, "package_identity", return_value={"version": "0.21.30"}), \
             patch.object(ios, "prepare", return_value=(self.directory / "Source/slot/runner/AgentDeviceRunner", {})), \
             patch.object(ios, "build_lock", return_value=nullcontext()), \
             patch.object(ios.subprocess, "run", side_effect=run):
            with self.assertRaises(Captured):
                ios.build(simulator=True)
        self.assertIn(str(self.directory / "DerivedData"), captured["command"])
        self.assertEqual(captured["environment"]["TMPDIR"], str(self.directory / "tmp") + "/")
        self.assertIn("DEVELOPMENT_TEAM=", captured["command"])
        self.assertFalse((self.root / "IOSInput").exists())

    def simulator_manifest(self):
        source = self.directory / "Source/Main.swift"
        source.parent.mkdir(parents=True)
        source.write_text("synthetic-source")
        products = self.directory / "DerivedData/Build/Products"
        xctestrun = self.products_fixture(products)
        manifest = {"schema_version": 1, "target_kind": "simulator", "sdk": "iphonesimulator",
            "signed": False, "release_digest": DIGEST, "developer_dir": str(self.developer),
            "xcodebuild_sha256": helper.sha256(self.xcodebuild), "agent_device_identity": {"version": "0.21.30"},
            "agent_device_version": "0.21.30", "port": 19087,
            "app_bundle_id": "com.agenttestaccounts.simulator", "test_bundle_id": "com.agenttestaccounts.simulator.uitests",
            "source_root": str(source.parent), "source_sha256": ios.source_hashes(source.parent),
            "runner_root": str(products), "runner_tree_sha256": ios.runner_tree_digest(products),
            "xctestrun": str(xctestrun), "build_script_sha256": helper.sha256(ios.SKILL_DIR / "scripts/build_ios_input.py"),
            "account_source_sha256": helper.sha256(ios.SKILL_DIR / "ios/RunnerTests+AccountLogin.swift")}
        (self.directory / "build.json").write_text(json.dumps(manifest))
        return manifest

    def pins(self, *, simulator_only=False):
        directory = self.root / "NativeHelper"
        directory.mkdir(exist_ok=True)
        settings = {"mount_point": str(self.root), "volume_uuid": "11111111-2222-4333-8444-555555555555"}
        with patch.object(helper, "verify_output", side_effect=lambda path: path), \
             patch.object(helper, "load_settings", return_value=settings), \
             patch.object(helper, "verify_storage", side_effect=lambda value: value), \
             patch.object(helper, "current_digest", return_value=DIGEST), \
             patch.object(helper.shutil, "which", return_value=None), \
             patch.object(android_input, "toolchain", return_value={"adb": self.root / "adb"}), \
             patch.object(ios, "package_identity", return_value={"version": "0.21.30"}):
            return helper.driver_pins(directory, simulator_only=simulator_only).read_text()

    def test_verified_simulator_manifest_emits_separate_optional_pins(self):
        self.simulator_manifest()
        pins = self.pins()
        self.assertIn("static let iosXCTestRunPath: String? = nil", pins)
        for name in ["iosSimulatorXCTestRunPath", "iosSimulatorRunnerRoot", "iosSimulatorRunnerTreeSHA256",
                     "iosSimulatorDeveloperDirectory", "iosSimulatorXcodeBuildSHA256"]:
            self.assertIn("static let " + name + ": String? = ", pins)
            self.assertNotIn("static let " + name + ": String? = nil", pins)

    def test_simulator_only_rejects_missing_input_manifest(self):
        with self.assertRaises(ValueError):
            self.pins(simulator_only=True)

    def test_simulator_product_identity_checks_actual_info_platform_and_executable(self):
        manifest = self.simulator_manifest()
        products, run = Path(manifest["runner_root"]), Path(manifest["xctestrun"])
        host = products / "Debug-iphonesimulator/AgentDeviceRunner.app"
        info = host / "Info.plist"
        original = plistlib.loads(info.read_bytes())
        for changes in [{"CFBundleIdentifier": "org.example.other"}, {"DTPlatformName": "iphoneos"},
                        {"CFBundleSupportedPlatforms": ["iPhoneOS"]}, {"DTSDKName": "iphoneos26.5"},
                        {"CFBundleExecutable": "../outside"}, {"CFBundlePackageType": "BNDL"}]:
            with self.subTest(changes=changes):
                info.write_bytes(plistlib.dumps({**original, **changes}))
                with self.assertRaises(ValueError):
                    ios.verify_simulator_products(products, run)
        info.write_bytes(plistlib.dumps(original))
        executable = host / original["CFBundleExecutable"]
        executable.chmod(0o644)
        with self.assertRaises(ValueError):
            ios.verify_simulator_products(products, run)
        executable.chmod(0o755)
        executable.rename(executable.with_suffix(".real"))
        executable.symlink_to(executable.with_suffix(".real"))
        with self.assertRaises(ValueError):
            ios.verify_simulator_products(products, run)

    def test_simulator_xctestrun_checks_exact_host_test_and_app_paths(self):
        manifest = self.simulator_manifest()
        products, run = Path(manifest["runner_root"]), Path(manifest["xctestrun"])
        original = plistlib.loads(run.read_bytes())
        target = original["TestConfigurations"][0]["TestTargets"][0]
        for changes in [{"TestHostBundleIdentifier": "org.example.other.xctrunner"},
                        {"TestBundlePath": "__TESTROOT__/outside.xctest"},
                        {"TestHostPath": "__TESTROOT__/Debug-iphoneos/AgentDeviceRunnerUITests-Runner.app"},
                        {"UITargetAppPath": "__TESTROOT__/Debug-iphonesimulator/Other.app"},
                        {"IsUITestBundle": False}, {"TestBundleIdentifier": "org.example.other"}]:
            with self.subTest(changes=changes):
                run.write_bytes(plistlib.dumps({"TestConfigurations": [{"TestTargets": [{**target, **changes}]}]}))
                with self.assertRaises(ValueError):
                    ios.verify_simulator_products(products, run)
        run.write_bytes(plistlib.dumps(original))
        run.rename(products / "unrelated-name.xctestrun")
        ios.verify_simulator_products(products, products / "unrelated-name.xctestrun")

    def test_simulator_pins_revalidate_actual_products_even_with_rehashed_manifest(self):
        manifest = self.simulator_manifest()
        products = Path(manifest["runner_root"])
        path = products / "Debug-iphonesimulator/AgentDeviceRunner.app/Info.plist"
        info = plistlib.loads(path.read_bytes())
        path.write_bytes(plistlib.dumps({**info, "CFBundleIdentifier": "org.example.other"}))
        manifest["runner_tree_sha256"] = ios.runner_tree_digest(products)
        (self.directory / "build.json").write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            self.pins()

    def test_simulator_target_rejects_swapped_unsigned_physical_and_changed_source(self):
        manifest = self.simulator_manifest()
        path = self.directory / "build.json"
        invalid = [{"target_kind": "physical"}, {"sdk": "iphoneos"}, {"signed": True}, {"schema_version": True},
                   {"agent_device_version": "0.21.31"},
                   {"app_bundle_id": "com.agenttestaccounts.runner"}, {"test_bundle_id": "com.agenttestaccounts.runner.uitests"},
                   {"release_digest": "b" * 64}, {"runner_tree_sha256": "b" * 64},
                   {"source_sha256": {}}, {"xcodebuild_sha256": "b" * 64}]
        for changes in invalid:
            with self.subTest(changes=changes):
                path.write_text(json.dumps({**manifest, **changes}))
                with self.assertRaises(ValueError):
                    self.pins()

    def test_simulator_only_pins_preserve_but_do_not_trust_other_stale_artifacts(self):
        self.simulator_manifest()
        physical = self.root / "IOSInput/build.json"
        android = self.root / "AndroidInput/secure-input.jar"
        physical.parent.mkdir()
        physical.write_text('{"release_digest":"stale"}')
        android.parent.mkdir()
        android.write_text("stale-jar")
        originals = (physical.read_bytes(), android.read_bytes())
        pins = self.pins(simulator_only=True)
        self.assertIn("static let iosXCTestRunPath: String? = nil", pins)
        self.assertIn('static let androidJARSHA256: String = ""', pins)
        self.assertNotIn("static let iosSimulatorXCTestRunPath: String? = nil", pins)
        self.assertEqual((physical.read_bytes(), android.read_bytes()), originals)
        with self.assertRaises((OSError, ValueError)):
            self.pins()


if __name__ == "__main__":
    unittest.main()
