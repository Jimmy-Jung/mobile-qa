"""화면 비교의 정확성과 원본 보존 검증. Author: JunyoungJung · 2026-10-22."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

from PIL import Image, ImageCms, PngImagePlugin


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/compare_screenshots.py"
SPEC = importlib.util.spec_from_file_location("compare_screenshots", SCRIPT)
compare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare)


class ScreenshotComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.reference = self.root / "reference.png"
        self.candidate = self.root / "candidate.png"
        self.output = self.root / "attempt-1"
        self.save(self.reference)
        self.save(self.candidate)

    def save(self, path, *, mode="RGBA", color=(20, 30, 40, 255), size=(4, 3), **options):
        Image.new(mode, size, color).save(path, **options)

    def run_compare(self, **kwargs):
        return compare.compare_screenshots(self.reference, self.candidate, self.output, **kwargs)

    def change(self, pixel=(2, 1), value=(21, 30, 40, 255)):
        with Image.open(self.candidate) as image:
            image.putpixel(pixel, value)
            image.save(self.candidate)

    def test_equal_images_have_exact_metrics_and_hashed_manifest(self):
        before = [p.read_bytes() for p in (self.reference, self.candidate)]
        result = self.run_compare()
        self.assertTrue(result["raw_exact"])
        self.assertTrue(result["compared_exact"])
        self.assertEqual(result["changed_pixels"], 0)
        self.assertEqual(result["compared_pixels"], 12)
        self.assertEqual(result["excluded_pixels"], 0)
        self.assertEqual(result["changed_ratio"], 0)
        self.assertEqual(result["max_channel_difference"], 0)
        self.assertEqual(result["mean_absolute_error"], 0)
        self.assertIsNone(result["changed_bbox"])
        self.assertEqual(result["reference"]["sha256"], hashlib.sha256(before[0]).hexdigest())
        self.assertEqual(result["candidate"]["size"], [4, 3])
        self.assertEqual(before, [p.read_bytes() for p in (self.reference, self.candidate)])
        self.assertEqual(json.loads((self.output / "comparison.json").read_text()), result)
        self.assertEqual(set(p.name for p in self.output.iterdir()), {
            "overlay-25.png", "overlay-50.png", "overlay-75.png", "diff.png",
            "mismatch-mask.png", "comparison.json",
        })

    def test_single_rgb_channel_change_is_counted_even_when_alpha_matches(self):
        self.change()
        result = self.run_compare()
        self.assertFalse(result["raw_exact"])
        self.assertFalse(result["compared_exact"])
        self.assertEqual(result["changed_pixels"], 1)
        self.assertEqual(result["changed_ratio"], 1 / 12)
        self.assertEqual(result["max_channel_difference"], 1)
        self.assertEqual(result["mean_absolute_error"], 1 / 48)
        self.assertEqual(result["changed_bbox"], [2, 1, 3, 2])
        with Image.open(self.output / "mismatch-mask.png") as image:
            self.assertEqual(image.getpixel((2, 1)), 255)
            self.assertEqual(image.getpixel((0, 0)), 0)
        with Image.open(self.output / "diff.png") as image:
            self.assertNotEqual(image.getpixel((2, 1)), (0, 0, 0))

    def test_alpha_only_difference_is_visible(self):
        self.change(value=(20, 30, 40, 254))
        result = self.run_compare()
        self.assertEqual(result["changed_pixels"], 1)
        self.assertEqual(result["max_channel_difference"], 1)
        with Image.open(self.output / "diff.png") as image:
            self.assertNotEqual(image.getpixel((2, 1)), (0, 0, 0))

    def test_fully_transparent_rgb_difference_is_not_lost(self):
        self.save(self.reference, color=(20, 30, 40, 0))
        self.save(self.candidate, color=(21, 30, 40, 0))
        result = self.run_compare()
        self.assertEqual(result["changed_pixels"], 12)
        self.assertEqual(result["changed_bbox"], [0, 0, 4, 3])

    def test_overlay_names_mean_candidate_opacity(self):
        self.save(self.reference, color=(0, 0, 0, 255))
        self.save(self.candidate, color=(200, 100, 40, 255))
        result = self.run_compare()
        for percentage in (25, 50, 75):
            with self.subTest(percentage=percentage):
                with Image.open(self.output / f"overlay-{percentage}.png") as image:
                    self.assertEqual(image.getpixel((0, 0)), tuple(
                        int(channel * percentage / 100) for channel in (200, 100, 40)
                    ) + (255,))
        self.assertEqual(result["overlay_candidate_opacity"], [0.25, 0.5, 0.75])

    def test_binary_mask_excludes_metrics_without_hiding_full_image_difference(self):
        self.change()
        mask_path = self.root / "mask.png"
        mask = Image.new("L", (4, 3), 0)
        mask.putpixel((2, 1), 255)
        mask.save(mask_path)
        result = self.run_compare(mask=mask_path)
        self.assertFalse(result["raw_exact"])
        self.assertTrue(result["compared_exact"])
        self.assertEqual(result["changed_pixels"], 0)
        self.assertEqual(result["excluded_pixels"], 1)
        self.assertEqual(result["compared_pixels"], 11)
        self.assertEqual(result["mask"]["sha256"], hashlib.sha256(mask_path.read_bytes()).hexdigest())
        with Image.open(self.output / "mismatch-mask.png") as image:
            self.assertEqual(image.getpixel((2, 1)), 255)
        with Image.open(self.output / "overlay-50.png") as image:
            self.assertEqual(image.size, (4, 3))

    def test_masked_metrics_use_only_remaining_pixels(self):
        self.change(value=(120, 30, 40, 255))
        mask_path = self.root / "mask.png"
        mask = Image.new("L", (4, 3), 255)
        mask.putpixel((2, 1), 0)
        mask.save(mask_path)
        result = self.run_compare(mask=mask_path)
        self.assertEqual(result["changed_ratio"], 1)
        self.assertEqual(result["mean_absolute_error"], 25)
        self.assertEqual(result["compared_pixels"], 1)

    def test_invalid_masks_are_rejected_before_outputs(self):
        for mode, color, size in [("L", 255, (4, 3)), ("L", 1, (4, 3)),
                                  ("L", 0, (5, 3)), ("RGB", (0, 0, 0), (4, 3))]:
            with self.subTest(mode=mode, color=color, size=size):
                path = self.root / "mask.png"
                self.save(path, mode=mode, color=color, size=size)
                with self.assertRaises(ValueError):
                    self.run_compare(mask=path)
                self.assertFalse(self.output.exists())

    def test_different_sizes_are_rejected_without_resizing(self):
        self.save(self.candidate, size=(8, 6))
        with self.assertRaises(ValueError):
            self.run_compare()
        self.assertFalse(self.output.exists())

    def test_corrupt_non_png_and_animated_images_are_rejected(self):
        for kind in ("corrupt", "jpeg", "animated"):
            with self.subTest(kind=kind):
                if kind == "corrupt":
                    self.candidate.write_bytes(b"invalid PNG")
                elif kind == "jpeg":
                    Image.new("RGB", (4, 3)).save(self.candidate, format="JPEG")
                else:
                    Image.new("RGBA", (4, 3)).save(
                        self.candidate, save_all=True,
                        append_images=[Image.new("RGBA", (4, 3), "white")], duration=100,
                    )
                with self.assertRaises(ValueError):
                    self.run_compare()
                self.assertFalse(self.output.exists())

    def test_invalid_png_chunk_checksum_is_rejected(self):
        data = bytearray(self.candidate.read_bytes())
        data[-1] ^= 1
        self.candidate.write_bytes(data)
        with self.assertRaises(ValueError):
            self.run_compare()
        self.assertFalse(self.output.exists())

    def test_unsupported_hdr_color_chunks_are_rejected_even_if_matching(self):
        for name in (b"cICP", b"mDCV", b"cLLI"):
            with self.subTest(chunk=name):
                self.save(self.reference)
                data = self.reference.read_bytes()
                payload = name + b"\x01\x10\x00\x01"
                chunk = (4).to_bytes(4, "big") + payload + zlib.crc32(payload).to_bytes(4, "big")
                data = data[:33] + chunk + data[33:]
                self.reference.write_bytes(data)
                self.candidate.write_bytes(data)
                with self.assertRaisesRegex(ValueError, "준비"):
                    self.run_compare()
                self.assertFalse(self.output.exists())

    def test_high_bit_depth_is_rejected_instead_of_truncating(self):
        self.save(self.candidate, mode="I;16", color=1000)
        with self.assertRaises(ValueError):
            self.run_compare()

    def test_oriented_image_is_rejected_instead_of_rotated(self):
        exif = Image.Exif()
        exif[274] = 6
        self.save(self.candidate, exif=exif)
        with self.assertRaises(ValueError):
            self.run_compare()

    def test_rgb_and_rgba_decode_to_same_pixels(self):
        self.save(self.reference, mode="RGB", color=(20, 30, 40))
        self.assertTrue(self.run_compare()["raw_exact"])

    def test_identical_input_path_symlink_or_hardlink_is_rejected(self):
        self.candidate.unlink()
        for kind in ("same", "symlink", "hardlink"):
            with self.subTest(kind=kind):
                if kind == "same":
                    candidate = self.reference
                elif kind == "symlink":
                    self.candidate.symlink_to(self.reference)
                    candidate = self.candidate
                else:
                    self.candidate.hardlink_to(self.reference)
                    candidate = self.candidate
                with self.assertRaises(ValueError):
                    compare.compare_screenshots(self.reference, candidate, self.output)
                self.assertFalse(self.output.exists())
                if self.candidate.exists():
                    self.candidate.unlink()

    def test_existing_nonempty_output_is_not_overwritten(self):
        self.output.mkdir()
        original = self.output / "comparison.json"
        original.write_text("keep me")
        with self.assertRaises(ValueError):
            self.run_compare()
        self.assertEqual(original.read_text(), "keep me")

    def test_existing_empty_output_is_supported(self):
        self.output.mkdir()
        self.assertTrue(self.run_compare()["compared_exact"])

    def test_symlink_output_is_rejected(self):
        target = self.root / "elsewhere"
        target.mkdir()
        self.output.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.run_compare()
        self.assertEqual(list(target.iterdir()), [])

    def test_different_icc_metadata_requires_explicit_normalization(self):
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        self.save(self.candidate, icc_profile=profile)
        with self.assertRaises(ValueError):
            self.run_compare()
        self.assertFalse(self.output.exists())

    def test_different_gamma_requires_explicit_handling(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add(b"gAMA", (45455).to_bytes(4, "big"))
        self.save(self.candidate, pnginfo=metadata)
        with self.assertRaises(ValueError):
            self.run_compare()

    def test_normalization_records_profile_and_unprofiled_assumption(self):
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        self.save(self.candidate, icc_profile=profile)
        result = self.run_compare(normalize_srgb=True)
        self.assertTrue(result["compared_exact"])
        self.assertEqual(result["color_processing"]["mode"], "normalized_srgb")
        self.assertEqual(result["reference"]["color_action"], "assumed_srgb_unprofiled")
        self.assertEqual(result["candidate"]["color_action"], "icc_to_srgb")
        self.assertEqual(result["candidate"]["color_metadata"]["icc_profile_sha256"],
                         hashlib.sha256(profile).hexdigest())

    def test_normalization_preserves_alpha_and_separates_raw_exactness(self):
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        self.save(self.candidate, color=(20, 30, 40, 120), icc_profile=profile)
        result = self.run_compare(normalize_srgb=True)
        self.assertFalse(result["raw_exact"])
        self.assertFalse(result["compared_exact"])
        self.assertEqual(result["max_channel_difference"], 135)

    def test_unsupported_unprofiled_color_metadata_is_not_silently_assumed(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add(b"gAMA", (100000).to_bytes(4, "big"))
        self.save(self.candidate, pnginfo=metadata)
        with self.assertRaises(ValueError):
            self.run_compare(normalize_srgb=True)

    def test_corrupt_icc_is_reported_when_normalizing(self):
        self.save(self.candidate, icc_profile=b"not a profile")
        with self.assertRaises(ValueError):
            self.run_compare(normalize_srgb=True)
        self.assertFalse(self.output.exists())

    def test_missing_color_transform_support_is_preparation_error(self):
        with patch.object(compare, "ImageCms", None):
            with self.assertRaisesRegex(ValueError, "준비"):
                self.run_compare(normalize_srgb=True)
        self.assertFalse(self.output.exists())

    def test_write_failure_leaves_no_partial_success(self):
        with patch.object(compare.Image.Image, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.run_compare()
        self.assertFalse(self.output.exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["candidate.png", "reference.png"])

    def test_cli_reports_metrics_and_returns_success_even_for_mismatch(self):
        self.change()
        result = subprocess.run([
            os.sys.executable, "-B", str(SCRIPT), "--reference", str(self.reference),
            "--candidate", str(self.candidate), "--output-dir", str(self.output),
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["changed_pixels"], 1)
        self.assertFalse(payload["compared_exact"])

    def test_cli_input_error_is_not_a_success_manifest(self):
        self.candidate.write_bytes(b"bad")
        result = subprocess.run([
            os.sys.executable, "-B", str(SCRIPT), "--reference", str(self.reference),
            "--candidate", str(self.candidate), "--output-dir", str(self.output),
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("error", json.loads(result.stderr))
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
