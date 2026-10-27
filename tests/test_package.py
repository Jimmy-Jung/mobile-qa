"""Release archive behavior. Author: JunyoungJung · 2026-10-22."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from mobile_qa import release

SPEC = importlib.util.spec_from_file_location('package_command', Path(__file__).resolve().parents[1] / 'scripts/package.py')
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'source'
        self.root.mkdir()
        (self.root / '.claude-plugin').mkdir()
        for name in ['plugin.json', '.claude-plugin/plugin.json']:
            (self.root / name).write_text(json.dumps({'name':'mobile-qa', 'version':'0.3.0'}))
        release.write_inventory(self.root)
        self.output = Path(self.temp.name) / 'output'

    def test_archive_only_contains_verified_public_files_and_checksum(self):
        with patch.object(package, 'verify_output', side_effect=lambda p: Path(p)):
            result = package.create_archive(self.output, self.root)
        with zipfile.ZipFile(result['archive']) as archive:
            self.assertEqual(set(archive.namelist()), {
                'mobile-qa/plugin.json', 'mobile-qa/.claude-plugin/plugin.json', 'mobile-qa/release.json'})
        self.assertEqual(len(result['sha256']), 64)
        self.assertTrue(Path(result['checksum']).is_file())

    def test_a_file_changed_during_packaging_is_rejected(self):
        original = Path.read_bytes
        reads = 0
        def changing_read(path):
            nonlocal reads
            if path == self.root / 'plugin.json':
                reads += 1
                if reads >= 3:
                    return b'changed after verification'
            return original(path)
        with patch.object(package, 'verify_output', side_effect=lambda p: Path(p)), \
             patch.object(Path, 'read_bytes', changing_read):
            with self.assertRaises(ValueError):
                package.create_archive(self.output, self.root)
        self.assertFalse(list(self.output.glob('*.zip')))

    def test_duplicate_fields_in_inventory_snapshot_are_rejected(self):
        original = Path.read_bytes
        payload = (self.root / 'release.json').read_bytes().replace(
            b'"schema_version": 1,', b'"schema_version": 1, "schema_version": 1,')
        def snapshot_read(path):
            return payload if path == self.root / 'release.json' else original(path)
        with patch.object(package, 'verify_output', side_effect=lambda p: Path(p)), \
             patch.object(Path, 'read_bytes', snapshot_read):
            with self.assertRaises(ValueError):
                package.create_archive(self.output, self.root)
        self.assertFalse(self.output.exists())

    def test_existing_release_is_not_overwritten(self):
        with patch.object(package, 'verify_output', side_effect=lambda p: Path(p)):
            result = package.create_archive(self.output, self.root)
            before = Path(result['archive']).read_bytes()
            with self.assertRaises((ValueError, OSError)):
                package.create_archive(self.output, self.root)
        self.assertEqual(Path(result['archive']).read_bytes(), before)

    def test_unverified_storage_stops_before_any_output(self):
        with patch.object(package, 'verify_output', side_effect=ValueError('disconnected')):
            with self.assertRaises(ValueError):
                package.create_archive(self.output, self.root)
        self.assertFalse(self.output.exists())

    def test_changed_release_does_not_produce_archive(self):
        (self.root / 'README.md').write_text('changed')
        with patch.object(package, 'verify_output', side_effect=lambda p: Path(p)):
            with self.assertRaises(ValueError):
                package.create_archive(self.output, self.root)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
