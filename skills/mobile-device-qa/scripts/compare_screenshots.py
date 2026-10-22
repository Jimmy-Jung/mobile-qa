#!/usr/bin/env python3
"""같은 크기 PNG의 정확한 픽셀 차이와 오버레이. Author: JunyoungJung · 2026-10-22."""

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
import sys
import tempfile
import warnings
import zlib

try:
    import numpy as np
    from PIL import Image, PngImagePlugin
except ImportError:
    np = Image = PngImagePlugin = None
try:
    from PIL import ImageCms
except ImportError:
    ImageCms = None


def validate_png_chunks(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("PNG 파일만 비교할 수 있습니다.")
    offset = 8
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        end = offset + length + 12
        if end > len(data):
            break
        kind = data[offset + 4:offset + 8]
        expected = int.from_bytes(data[end - 4:end], "big")
        if zlib.crc32(data[offset + 4:end - 4]) != expected:
            raise ValueError("PNG 체크섬이 일치하지 않습니다. 원본을 다시 확보하세요.")
        if kind in (b"cICP", b"mDCV", b"cLLI"):
            raise ValueError("준비 필요: HDR/추가 색 청크를 지원하지 않습니다. SDR 8비트 PNG로 내보내세요.")
        if kind == b"IEND":
            if length == 0 and end == len(data):
                return
            break
        offset = end
    raise ValueError("PNG가 잘렸거나 끝 구조가 손상되었습니다.")


def read_png(path):
    data = path.read_bytes()
    validate_png_chunks(data)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                if source.format != "PNG" or getattr(source, "n_frames", 1) != 1:
                    raise ValueError("단일 프레임 PNG만 비교할 수 있습니다.")
                if data[24] > 8 or source.mode not in ("1", "L", "LA", "P", "RGB", "RGBA"):
                    raise ValueError("8비트 이하 PNG로 다시 캡처하거나 내보내세요.")
                if source.getexif().get(274, 1) != 1:
                    raise ValueError("회전 메타데이터 없는 PNG가 필요합니다. 자동 회전하지 않습니다.")
                source.load()
                image = source.copy()
                info = dict(source.info)
    except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("PNG를 안전하게 읽을 수 없습니다. 손상·크기·형식을 확인하세요.") from error
    profile = info.get("icc_profile")
    color = {
        "icc_profile_sha256": hashlib.sha256(profile).hexdigest() if profile else None,
        "srgb_rendering_intent": info.get("srgb"), "gamma": info.get("gamma"),
        "chromaticity": list(info["chromaticity"]) if "chromaticity" in info else None,
    }
    return image, info, {
        "path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
        "size": list(image.size), "mode": image.mode, "color_metadata": color,
    }


def srgb_image(image, info):
    rgba = image.convert("RGBA")
    profile = info.get("icc_profile")
    if profile:
        try:
            rgb = ImageCms.profileToProfile(
                rgba.convert("RGB"), ImageCms.ImageCmsProfile(BytesIO(profile)),
                ImageCms.createProfile("sRGB"), outputMode="RGB", renderingIntent=0,
            )
        except (OSError, ValueError, ImageCms.PyCMSError) as error:
            raise ValueError("준비 필요: ICC 프로파일을 sRGB로 변환할 수 없습니다.") from error
        rgb.putalpha(rgba.getchannel("A"))
        return rgb, "icc_to_srgb"
    gamma = info.get("gamma")
    chromaticity = info.get("chromaticity")
    srgb_chromaticity = (0.3127, 0.3290, 0.6400, 0.3300, 0.3000, 0.6000, 0.1500, 0.0600)
    if ((gamma is not None and abs(gamma - 0.45455) > 0.00001)
            or (chromaticity is not None and any(
                abs(actual - expected) > 0.00001
                for actual, expected in zip(chromaticity, srgb_chromaticity, strict=True)))):
        raise ValueError("준비 필요: ICC 없는 비표준 gamma/chromaticity는 sRGB로 가정할 수 없습니다.")
    return rgba, "declared_srgb" if "srgb" in info else "assumed_srgb_unprofiled"


def png_color_options(info):
    metadata = PngImagePlugin.PngInfo()
    for key, chunk, scale in (("gamma", b"gAMA", 100000), ("chromaticity", b"cHRM", 100000)):
        if key in info:
            values = info[key] if key == "chromaticity" else (info[key],)
            metadata.add(chunk, struct.pack(">" + "I" * len(values), *(round(v * scale) for v in values)))
    if "srgb" in info:
        metadata.add(b"sRGB", bytes([info["srgb"]]))
    return {"pnginfo": metadata, "icc_profile": info.get("icc_profile")}


def bbox(changed):
    rows, columns = np.nonzero(changed)
    return [int(columns.min()), int(rows.min()), int(columns.max()) + 1,
            int(rows.max()) + 1] if rows.size else None


def checked_paths(reference, candidate, output_dir, mask):
    inputs = [Path(p).expanduser().resolve() for p in (reference, candidate)]
    if mask is not None:
        inputs.append(Path(mask).expanduser().resolve())
    if any(not p.is_file() for p in inputs):
        raise ValueError("기준·대상·마스크는 존재하는 PNG 파일이어야 합니다.")
    if any(first.samefile(second) for index, first in enumerate(inputs) for second in inputs[index + 1:]):
        raise ValueError("기준·대상·마스크 입력은 서로 다른 파일이어야 합니다.")
    output = Path(output_dir).expanduser()
    if output.is_symlink():
        raise ValueError("출력 디렉터리는 심볼릭 링크를 사용할 수 없습니다.")
    output = output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("출력은 새 디렉터리 또는 비어 있는 디렉터리여야 합니다.")
    return inputs, output


def comparison_metrics(reference, candidate, allowed):
    difference = np.abs(np.asarray(reference).astype(np.int16) - np.asarray(candidate).astype(np.int16))
    changed = np.any(difference != 0, axis=2)
    counted = changed & allowed
    count = int(np.count_nonzero(counted))
    compared = int(np.count_nonzero(allowed))
    return difference, changed, {
        "compared_pixels": compared, "excluded_pixels": int(allowed.size - compared),
        "changed_pixels": count, "changed_ratio": count / compared,
        "max_channel_difference": int(difference[allowed].max()),
        "mean_absolute_error": float(difference[allowed].mean()),
        "changed_bbox": bbox(counted), "compared_exact": count == 0,
        "full_image_changed_pixels": int(np.count_nonzero(changed)),
        "full_image_changed_bbox": bbox(changed),
    }


def write_results(output, reference, candidate, difference, changed, color_options, result):
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".screen-comparison-", dir=output.parent) as temporary:
        staging = Path(temporary) / "result"
        staging.mkdir(mode=0o700)
        for percentage in (25, 50, 75):
            Image.blend(reference, candidate, percentage / 100).save(
                staging / f"overlay-{percentage}.png", **color_options)
        magnitude = difference.max(axis=2).astype(np.uint8)
        # 차이 크기는 수치로 보존하고 1단계 차이도 눈에 보이도록 밝기를 올린다.
        visible = np.where(changed, np.maximum(magnitude, 96), 0).astype(np.uint8)
        Image.fromarray(np.stack((visible, magnitude, visible), axis=2)).save(staging / "diff.png")
        Image.fromarray(changed.astype(np.uint8) * 255).save(staging / "mismatch-mask.png")
        (staging / "comparison.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        staging.replace(output)


def compare_screenshots(reference, candidate, output_dir, *, mask=None, normalize_srgb=False):
    if Image is None or np is None:
        raise ValueError("준비 필요: 현재 Python 환경의 Pillow와 numpy를 확인하세요.")
    if normalize_srgb and ImageCms is None:
        raise ValueError("준비 필요: sRGB 변환을 지원하는 Pillow ImageCms가 필요합니다.")
    inputs, output = checked_paths(reference, candidate, output_dir, mask)
    reference_image, reference_info, reference_record = read_png(inputs[0])
    candidate_image, candidate_info, candidate_record = read_png(inputs[1])
    if reference_image.size != candidate_image.size:
        raise ValueError("기준과 대상의 픽셀 너비·높이가 같아야 합니다. 자동 크기 변경하지 않습니다.")
    raw_reference = reference_image.convert("RGBA")
    raw_candidate = candidate_image.convert("RGBA")
    raw_changed = np.any(np.asarray(raw_reference) != np.asarray(raw_candidate), axis=2)
    allowed = np.ones((reference_image.height, reference_image.width), dtype=bool)
    mask_record = None
    if mask is not None:
        mask_image, _, mask_record = read_png(inputs[2])
        if mask_image.mode != "L" or mask_image.size != reference_image.size:
            raise ValueError("마스크는 같은 크기의 L 모드 PNG여야 합니다.")
        values = np.asarray(mask_image)
        if np.any((values != 0) & (values != 255)) or not np.any(values == 0):
            raise ValueError("마스크는 0=비교·255=제외만 허용하며 비교할 픽셀이 필요합니다.")
        allowed = values == 0
    if normalize_srgb:
        reference_image, reference_record["color_action"] = srgb_image(reference_image, reference_info)
        candidate_image, candidate_record["color_action"] = srgb_image(candidate_image, candidate_info)
        color_options = {"icc_profile": ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()}
    else:
        if reference_record["color_metadata"] != candidate_record["color_metadata"]:
            raise ValueError("색 메타데이터가 다릅니다. 내보내기 설정을 맞추거나 --normalize-srgb를 명시하세요.")
        reference_image, candidate_image = raw_reference, raw_candidate
        reference_record["color_action"] = candidate_record["color_action"] = "unchanged"
        color_options = png_color_options(reference_info)
    difference, changed, metrics = comparison_metrics(reference_image, candidate_image, allowed)
    names = ["overlay-25.png", "overlay-50.png", "overlay-75.png", "diff.png", "mismatch-mask.png"]
    result = {
        "schema_version": 1, "reference": reference_record, "candidate": candidate_record,
        "mask": mask_record, "raw_exact": not bool(raw_changed.any()),
        "raw_changed_pixels": int(np.count_nonzero(raw_changed)), **metrics,
        "color_processing": {
            "mode": "normalized_srgb" if normalize_srgb else "native_rgba",
            "rendering_intent": "perceptual" if normalize_srgb else None,
            "raw_exact_scope": "full decoded RGBA before color conversion and masking",
            "compared_exact_scope": "RGBA after declared color handling, at unmasked pixels",
        },
        "metric_channels": "RGBA (including alpha and RGB under transparency)",
        "bbox_format": "[left, top, right_exclusive, bottom_exclusive]",
        "mask_rule": "0=compare; 255=exclude from metrics; visual artifacts show the full image",
        "overlay_candidate_opacity": [0.25, 0.5, 0.75],
        "diff_visualization": "full-image absolute RGBA difference; max alpha difference included in RGB; nonzero brightness >=96",
        "exactness_limit": "Image equality in the recorded scope; not an app or overall design approval.",
        "artifacts": {name: str(output / name) for name in [*names, "comparison.json"]},
    }
    write_results(output, reference_image, candidate_image, difference, changed, color_options, result)
    return result


def main():
    parser = argparse.ArgumentParser(description="같은 픽셀 크기 PNG를 비교하고 오버레이·차이·수치를 저장")
    parser.add_argument("--reference", required=True, help="기준 PNG")
    parser.add_argument("--candidate", required=True, help="새 구현의 PNG")
    parser.add_argument("--output-dir", required=True, help="시도별 새 또는 비어 있는 출력 폴더")
    parser.add_argument("--mask", help="같은 크기 L PNG: 0=비교, 255=제외")
    parser.add_argument("--normalize-srgb", action="store_true", help="ICC→sRGB 명시적 변환; 무프로파일은 sRGB 가정 기록")
    args = parser.parse_args()
    try:
        result = compare_screenshots(args.reference, args.candidate, args.output_dir,
                                     mask=args.mask, normalize_srgb=args.normalize_srgb)
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
