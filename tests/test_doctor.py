"""Doctor is read-only. Author: JunyoungJung · 2026-10-22."""

from contextlib import redirect_stdout, redirect_stderr
import importlib.util
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('doctor_command', Path(__file__).resolve().parents[1] / 'scripts/doctor.py')
doctor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(doctor)


class DoctorTests(unittest.TestCase):
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
