"""Public package integrity checks. Author: JunyoungJung · 2026-10-22."""

import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from mobile_qa import release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".claude-plugin").mkdir()
        for name in ["plugin.json", ".claude-plugin/plugin.json"]:
            (self.root / name).write_text(json.dumps({"name": "mobile-qa", "version": "0.3.2"}))

    def test_inventory_accepts_complete_release_and_detects_tampering(self):
        release.write_inventory(self.root)
        self.assertEqual(release.verify_release(self.root)["version"], "0.3.2")
        (self.root / "plugin.json").write_text("{}")
        with self.assertRaises(ValueError):
            release.verify_release(self.root)

    def test_extra_files_are_not_silently_added_to_an_existing_release(self):
        release.write_inventory(self.root)
        (self.root / "README.md").write_text("extra")
        with self.assertRaises(ValueError):
            release.verify_release(self.root)

    def test_codex_install_receipt_is_excluded_only_from_installed_integrity(self):
        release.write_inventory(self.root)
        (self.root / ".codex-marketplace-install.json").write_text('{"revision":"synthetic"}')
        self.assertEqual(release.verify_release(self.root)["version"], "0.3.2")
        with self.assertRaises(ValueError):
            release.safety_check(self.root)
        (self.root / "plugin.json").write_text("{}")
        with self.assertRaises(ValueError):
            release.verify_release(self.root)

    def test_install_receipt_exception_rejects_symlinks_and_nested_files(self):
        release.write_inventory(self.root)
        receipt = self.root / ".codex-marketplace-install.json"
        receipt.symlink_to(self.root / "plugin.json")
        with self.assertRaises(ValueError):
            release.verify_release(self.root)
        receipt.unlink()
        (self.root / "scripts").mkdir()
        (self.root / "scripts/.codex-marketplace-install.json").write_text("{}")
        with self.assertRaises(ValueError):
            release.verify_release(self.root)

    def test_claude_in_use_markers_are_excluded_only_from_installed_integrity(self):
        inventory = release.write_inventory(self.root)
        (self.root / ".in_use").mkdir()
        (self.root / ".in_use/40960").write_text('{"pid":40960}')
        self.assertEqual(release.verify_release(self.root)["files"], inventory["files"])
        with self.assertRaises(ValueError):
            release.safety_check(self.root)

    def test_in_use_exception_rejects_symlinks_nested_and_unrelated_files(self):
        release.write_inventory(self.root)
        markers = self.root / ".in_use"
        markers.mkdir()
        (markers / "40960").write_text("synthetic")
        (markers / "54754").symlink_to(self.root / "plugin.json")
        with self.assertRaises(ValueError):
            release.verify_release(self.root)
        (markers / "54754").unlink()
        (markers / "nested").mkdir()
        (markers / "nested/1").write_text("synthetic")
        with self.assertRaises(ValueError):
            release.verify_release(self.root)
        (markers / "nested/1").unlink()
        (markers / "nested").rmdir()
        for name in ["stray.txt", "scripts/.in_use/1"]:
            with self.subTest(name=name):
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic")
                with self.assertRaises(ValueError):
                    release.verify_release(self.root)
                path.unlink()
        self.assertEqual(release.verify_release(self.root)["version"], "0.3.2")

    def test_korean_default_and_english_readme_are_both_in_the_release(self):
        (self.root / 'README.md').write_text('# Mobile QA\n\n한국어 사용 안내\n')
        (self.root / 'README.en.md').write_text('# Mobile QA\n\nEnglish usage guide\n')
        inventory = release.write_inventory(self.root)
        self.assertIn('README.md', inventory['files'])
        self.assertIn('README.en.md', inventory['files'])

    def test_chat_registration_modules_are_packaged_and_hash_checked(self):
        path = self.root / 'skills/test-accounts/scripts/chat_registration.mjs'
        path.parent.mkdir(parents=True)
        path.write_text('export const fixture = "synthetic";\n')
        inventory = release.write_inventory(self.root)
        self.assertIn(path.relative_to(self.root).as_posix(), inventory['files'])
        path.write_text('export const fixture = "changed";\n')
        with self.assertRaises(ValueError):
            release.verify_release(self.root)

    def test_unknown_and_private_files_are_rejected(self):
        for name in ["credentials.json", "accounts.json", "image.png", "state.txt"]:
            with self.subTest(name=name):
                path = self.root / name
                path.write_text("synthetic")
                with self.assertRaises(ValueError):
                    release.write_inventory(self.root)
                path.unlink()

    def test_reviewed_png_examples_are_packaged_and_remain_hash_checked(self):
        def chunk(kind, data):
            return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
        png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 1, 1, 8, 2, 0, 0, 0))
               + chunk(b'IDAT', zlib.compress(b'\0\xff\xff\xff')) + chunk(b'IEND', b''))
        path = self.root / 'examples/design-review/reference.png'
        path.parent.mkdir(parents=True)
        path.write_bytes(png)
        inventory = release.write_inventory(self.root)
        self.assertIn('examples/design-review/reference.png', inventory['files'])
        release.verify_release(self.root)
        path.write_bytes(png + b'changed')
        with self.assertRaises(ValueError):
            release.verify_release(self.root)

    def test_documentation_image_does_not_allow_a_disguised_text_file(self):
        path = self.root / 'examples/design-review/reference.png'
        path.parent.mkdir(parents=True)
        path.write_text('not a PNG')
        with self.assertRaises(ValueError):
            release.write_inventory(self.root)

    def test_symlink_inside_package_is_rejected(self):
        (self.root / "README.md").symlink_to(self.root / "plugin.json")
        with self.assertRaises(ValueError):
            release.write_inventory(self.root)

    def test_inventory_symlink_is_rejected(self):
        (self.root / "release.json").symlink_to(self.root / "plugin.json")
        with self.assertRaises(ValueError):
            release.write_inventory(self.root)

    def test_private_developer_paths_are_not_publishable(self):
        (self.root / "README.md").write_text("/" + "Users/real-person/private")
        with self.assertRaises(ValueError):
            release.write_inventory(self.root)

    def test_private_paths_with_spaces_and_non_ascii_names_are_rejected(self):
        slash = "/"
        backslash = chr(92)
        paths = [
            slash + "Users/Example User/project",
            slash + "Users/가상사용자/project",
            slash + "Users/example-user",
            slash + "home/example-user/project",
            slash + "home/example-user",
            "C:" + backslash + "Users" + backslash + "Example User" + backslash + "project",
            "C:" + backslash + "Users" + backslash + "example-user",
            "C:" + backslash * 2 + "Users" + backslash * 2 + "example-user",
            slash + "System/Volumes/Data/Users/example-user/project",
            slash + "run/media/example-user/disk/project",
            slash + "Volumes/Example Disk/project",
        ]
        for private_path in paths:
            with self.subTest(path=private_path):
                (self.root / "README.md").write_text(private_path)
                with self.assertRaises(ValueError):
                    release.write_inventory(self.root)

    def test_user_relative_defaults_and_public_urls_are_publishable(self):
        (self.root / "README.md").write_text(
            "~/Library/Application Support/mobile-qa/config.json\n"
            "https://example.com/home/profile/\n")
        self.assertIn("README.md", release.write_inventory(self.root)["files"])

    def test_manifests_must_have_the_same_version(self):
        (self.root / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "mobile-qa", "version": "0.0.9"}))
        with self.assertRaises(ValueError):
            release.write_inventory(self.root)

    def test_duplicate_fields_and_invalid_schema_are_rejected(self):
        (self.root / "release.json").write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaises(ValueError):
            release.verify_release(self.root)
        (self.root / "release.json").write_text("[]")
        with self.assertRaises(ValueError):
            release.verify_release(self.root)

    def test_current_digest_verifies_before_returning(self):
        with patch.object(release, "verify_release", side_effect=ValueError("unverified")):
            with self.assertRaises(ValueError):
                release.current_digest()


if __name__ == "__main__":
    unittest.main()
