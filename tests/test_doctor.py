"""Doctor is read-only. Author: JunyoungJung · 2026-10-22."""

from contextlib import redirect_stdout, redirect_stderr
import importlib.util
from io import StringIO
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('doctor_command', Path(__file__).resolve().parents[1] / 'scripts/doctor.py')
doctor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(doctor)
XCRUN = '/usr/bin/xcrun'
SIMCTL = '/synthetic/simctl'


class DoctorTests(unittest.TestCase):
    def test_simulator_mode_is_ready_without_agent_device_signing_or_account_helper(self):
        stream = StringIO()
        with patch.object(doctor, 'verify_release', return_value={'version': '0.2.2'}), \
             patch.object(doctor.sys, 'platform', 'darwin'), \
             patch.object(doctor.importlib.metadata, 'version', return_value='synthetic'), \
             patch.object(doctor.shutil, 'which', return_value=None), \
             patch.object(doctor.Path, 'is_file', autospec=True, side_effect=lambda path: str(path) in {XCRUN, SIMCTL}), \
             patch.object(doctor.os, 'access', side_effect=lambda path, mode: str(path) in {XCRUN, SIMCTL}), \
             patch.object(doctor.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, SIMCTL + '\n', '')) as run, \
             patch.object(doctor, 'package_identity') as package, \
             patch.object(doctor, 'java_home') as java, \
             patch.object(doctor, 'android_sdk') as android, \
             patch.object(doctor, 'helper_readiness') as helper, \
             patch.object(doctor, 'load_settings', return_value={}), \
             patch.object(doctor, 'verify_storage', return_value={'storage_dir': '/synthetic/storage'}), \
             patch.object(doctor, 'workspace_directory', return_value=Path('/synthetic/work')) as workspace, \
             redirect_stdout(stream):
            self.assertEqual(doctor.main(['--simulator', '--project-dir', '/synthetic/app']), 0)
        result = json.loads(stream.getvalue())
        self.assertEqual(result['target'], 'simulator')
        self.assertEqual(result['scope'], 'simulator_capture_and_pixel_comparison')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['features'], {'pixel_comparison': True, 'simulator_capture': True})
        self.assertEqual(result['not_checked'], ['ui_control', 'authentication', 'app_execution'])
        self.assertNotIn('account_helper', result)
        run.assert_called_once_with([XCRUN, '--find', 'simctl'], capture_output=True, text=True, check=False, timeout=10)
        workspace.assert_called_once_with(Path('/synthetic/app'), create=False)
        for unrelated in [package, java, android, helper]:
            unrelated.assert_not_called()

    def test_simulator_tool_detection_rejects_failed_or_invalid_resolution(self):
        for output in ['', 'relative/simctl', '/missing/synthetic/simctl', '/', '/first\n/second', '/invalid\0path']:
            with self.subTest(output=output), \
                 patch.object(doctor.sys, 'platform', 'darwin'), \
                 patch.object(doctor.shutil, 'which', return_value=XCRUN), \
                 patch.object(doctor.Path, 'is_file', autospec=True, side_effect=lambda path: str(path) in {XCRUN, SIMCTL}), \
                 patch.object(doctor.os, 'access', side_effect=lambda path, mode: str(path) in {XCRUN, SIMCTL}), \
                 patch.object(doctor.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output, '')):
                self.assertIsNone(doctor.detect_tools(simulator=True)['simctl'])
        outcomes = [subprocess.CompletedProcess([], 1, SIMCTL, 'not available'),
                    subprocess.TimeoutExpired('xcrun', 10), OSError('not available')]
        for outcome in outcomes:
            options = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
            with self.subTest(outcome=type(outcome).__name__), \
                 patch.object(doctor.sys, 'platform', 'darwin'), \
                 patch.object(doctor.shutil, 'which', return_value=XCRUN), \
                 patch.object(doctor.Path, 'is_file', autospec=True, side_effect=lambda path: str(path) in {XCRUN, SIMCTL}), \
                 patch.object(doctor.os, 'access', side_effect=lambda path, mode: str(path) in {XCRUN, SIMCTL}), \
                 patch.object(doctor.subprocess, 'run', **options):
                self.assertIsNone(doctor.detect_tools(simulator=True)['simctl'])

    def test_simulator_tool_detection_requires_macos_xcrun_and_an_executable(self):
        for platform, file_exists, executable in [('linux', True, True), ('darwin', False, True), ('darwin', True, False)]:
            with self.subTest(platform=platform, file_exists=file_exists, executable=executable), \
                 patch.object(doctor.sys, 'platform', platform), \
                 patch.object(doctor.shutil, 'which', return_value='/project/xcrun'), \
                 patch.object(doctor.Path, 'is_file', return_value=file_exists), \
                 patch.object(doctor.os, 'access', return_value=executable), \
                 patch.object(doctor.subprocess, 'run') as run:
                self.assertIsNone(doctor.detect_tools(simulator=True)['simctl'])
            run.assert_not_called()
        with patch.object(doctor.sys, 'platform', 'darwin'), \
             patch.object(doctor.shutil, 'which', return_value=XCRUN), \
             patch.object(doctor.Path, 'is_file', autospec=True, side_effect=lambda path: str(path) in {XCRUN, SIMCTL}), \
             patch.object(doctor.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, SIMCTL, '')), \
             patch.object(doctor.os, 'access', side_effect=lambda path, mode: str(path) == XCRUN):
            self.assertIsNone(doctor.detect_tools(simulator=True)['simctl'])

    def test_simulator_tool_detection_ignores_relative_and_project_xcrun_shims(self):
        for shim in ['project/bin/xcrun', '/project/bin/xcrun']:
            with self.subTest(shim=shim), \
                 patch.object(doctor.sys, 'platform', 'darwin'), \
                 patch.object(doctor.shutil, 'which', return_value=shim) as which, \
                 patch.object(doctor.Path, 'is_file', autospec=True, side_effect=lambda path: str(path) in {XCRUN, SIMCTL}), \
                 patch.object(doctor.os, 'access', side_effect=lambda path, mode: str(path) in {XCRUN, SIMCTL}), \
                 patch.object(doctor.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, SIMCTL, '')) as run:
                tools = doctor.detect_tools(simulator=True)
            self.assertEqual((tools['xcrun'], tools['simctl']), (XCRUN, SIMCTL))
            which.assert_not_called()
            run.assert_called_once_with([XCRUN, '--find', 'simctl'], capture_output=True, text=True, check=False, timeout=10)

    def test_simulator_mode_reports_missing_dependencies_without_account_checks(self):
        for tools in [
            {'macos': True, 'Pillow': 'synthetic', 'numpy': 'synthetic'},
            {'macos': False, 'simctl': '/synthetic/simctl', 'Pillow': 'synthetic', 'numpy': 'synthetic'},
            {'macos': True, 'simctl': '/synthetic/simctl', 'Pillow': 'synthetic'},
        ]:
            stream = StringIO()
            with self.subTest(tools=tools), \
                 patch.object(doctor, 'verify_release', return_value={'version': '0.2.2'}), \
                 patch.object(doctor, 'detect_tools', return_value=tools), \
                 patch.object(doctor, 'load_settings', return_value={}), \
                 patch.object(doctor, 'verify_storage', return_value={'storage_dir': '/synthetic/storage'}), \
                 patch.object(doctor, 'helper_readiness') as helper, redirect_stdout(stream):
                self.assertEqual(doctor.main(['--simulator']), 1)
            self.assertEqual(json.loads(stream.getvalue())['status'], 'needs_dependencies')
            helper.assert_not_called()

    def test_simulator_mode_keeps_storage_failures_and_does_not_prepare_it(self):
        stream = StringIO()
        with patch.object(doctor, 'verify_release', return_value={'version': '0.2.2'}), \
             patch.object(doctor, 'detect_tools', return_value={'macos': True, 'simctl': '/synthetic/simctl', 'Pillow': 'synthetic', 'numpy': 'synthetic'}), \
             patch.object(doctor, 'load_settings', side_effect=ValueError('run setup first')), \
             patch.object(doctor, 'helper_readiness') as helper, redirect_stdout(stream):
            self.assertEqual(doctor.main(['--simulator']), 1)
        result = json.loads(stream.getvalue())
        self.assertEqual((result['target'], result['status'], result['storage']['status']), ('simulator', 'needs_setup', 'needs_setup'))
        helper.assert_not_called()

    def test_default_mode_still_requires_agent_device(self):
        for agent_device, expected in [(None, 1), ('/synthetic/agent-device', 0)]:
            stream = StringIO()
            with self.subTest(agent_device=agent_device), \
                 patch.object(doctor, 'verify_release', return_value={'version': '0.2.2'}), \
                 patch.object(doctor, 'detect_tools', return_value={'macos': True, 'agent-device': agent_device, 'Pillow': 'synthetic', 'numpy': 'synthetic'}) as tools, \
                 patch.object(doctor, 'load_settings', return_value={}), \
                 patch.object(doctor, 'verify_storage', return_value={'storage_dir': '/synthetic/storage'}), \
                 patch.object(doctor, 'helper_readiness', return_value={'verified': False}) as helper, redirect_stdout(stream):
                self.assertEqual(doctor.main([]), expected)
            tools.assert_called_once_with()
            helper.assert_called_once_with()
            result = json.loads(stream.getvalue())
            self.assertNotIn('target', result)
            self.assertEqual(result['features']['device_qa'], bool(agent_device))

    def test_runner_source_readiness_uses_builder_validation_without_claiming_cli_trust(self):
        identity = {'path': '/synthetic/runner-package', 'version': '0.21.30'}
        with patch.object(doctor, 'package_identity', return_value=identity), \
             patch.object(doctor, 'java_home', side_effect=ValueError('no JDK')), \
             patch.object(doctor, 'android_sdk', side_effect=ValueError('no SDK')), \
             patch.object(doctor.shutil, 'which', return_value=None):
            tools = doctor.detect_tools()
        self.assertEqual(tools['agent_device_source'], {'verified': True, 'package': identity['path'], 'version': identity['version']})
        self.assertIsNone(tools['agent-device'])
        self.assertIsNone(tools['agent_device_version'])
        self.assertIsNone(tools['java'])

    def test_invalid_explicit_runner_source_is_reported_without_cli_execution(self):
        with patch.object(doctor, 'package_identity', side_effect=ValueError('source differs')), \
             patch.object(doctor, 'java_home', side_effect=ValueError('no JDK')), \
             patch.object(doctor, 'android_sdk', side_effect=ValueError('no SDK')):
            tools = doctor.detect_tools()
        self.assertEqual(tools['agent_device_source'], {'verified': False, 'reason': 'source differs'})
        self.assertIsNone(tools['agent_device_version'])

    def test_release_only_does_not_touch_storage_or_tools(self):
        stream = StringIO()
        with patch.object(doctor, 'verify_release', return_value={'version':'0.1.0'}), \
             patch.object(doctor, 'load_settings') as storage, \
             patch.object(doctor, 'detect_tools') as tools, redirect_stdout(stream):
            self.assertEqual(doctor.main(['--release-only']), 0)
        storage.assert_not_called()
        tools.assert_not_called()
        self.assertIn('verified', stream.getvalue())

    def test_missing_configuration_reports_setup_without_creating_it(self):
        with patch.object(doctor, 'verify_release', return_value={'version':'0.1.0'}), \
             patch.object(doctor, 'load_settings', side_effect=ValueError('run setup first')), \
             patch.object(doctor, 'detect_tools', return_value={}), redirect_stdout(StringIO()):
            self.assertEqual(doctor.main([]), 1)

    def test_invalid_inventory_stops_before_reading_settings(self):
        with patch.object(doctor, 'verify_release', side_effect=ValueError('mixed release')), \
             patch.object(doctor, 'load_settings') as settings, redirect_stderr(StringIO()):
            self.assertEqual(doctor.main([]), 1)
        settings.assert_not_called()


if __name__ == '__main__':
    unittest.main()
