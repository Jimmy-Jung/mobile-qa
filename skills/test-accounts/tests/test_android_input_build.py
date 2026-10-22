"""Android 입력 빌드 안전 경로 검증. Author: JunyoungJung · 2026-10-22."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "skills/test-accounts/scripts"))
SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_android_input.py"
SPEC = importlib.util.spec_from_file_location("android_input_build", SCRIPT)
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)
from mobile_qa import storage


class AndroidBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.volume = self.root / "external"
        self.volume.mkdir()
        self.settings = {"version": 1, "storage_dir": str(self.volume), "mount_point": str(self.volume),
                         "volume_uuid": "11111111-2222-3333-4444-555555555555"}
        for name in ("load_settings", "verify_storage"):
            mocked = patch.object(storage, name, return_value=self.settings)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_selected_volume_must_be_verified_before_resolving_outputs(self):
        with patch.object(storage, "verify_storage", side_effect=ValueError("unmounted")):
            with self.assertRaises(ValueError):
                BUILD.build_directory()
        self.assertEqual(list(self.volume.iterdir()), [])

    def test_same_physical_skill_reuses_directory(self):
        skill = self.root / "skill"
        skill.mkdir()
        first = BUILD.build_directory(skill)
        self.assertEqual(first, BUILD.build_directory(skill / "../skill"))
        other = self.root / "other/skill"
        other.mkdir(parents=True)
        self.assertNotEqual(first, BUILD.build_directory(other))
        self.assertEqual(first.name, "AndroidInput")
        self.assertFalse(first.exists())

    def test_internal_and_symlink_escape_are_rejected(self):
        with self.assertRaises(ValueError):
            BUILD.verify_output_path(self.root / "internal/android-input")
        link = self.volume / "escape"
        link.symlink_to(self.root)
        with self.assertRaises(ValueError):
            BUILD.verify_output_path(link / "android-input")

    def test_manifest_records_release_and_all_tool_hashes(self):
        source, artifact, tool = (self.root / name for name in ("source", "artifact", "tool"))
        source.write_text("synthetic-source")
        artifact.write_text("synthetic-artifact")
        tool.write_text("synthetic-tool")
        with patch.object(BUILD, "current_digest", return_value="a" * 64):
            first = BUILD.manifest(source, artifact, {"java": tool})
            tool.write_text("changed-tool")
            second = BUILD.manifest(source, artifact, {"java": tool})
        self.assertNotEqual(first, second)
        self.assertEqual(first["release_digest"], "a" * 64)
        self.assertEqual(first["tools"]["java"]["path"], str(tool))


if __name__ == "__main__":
    unittest.main()
