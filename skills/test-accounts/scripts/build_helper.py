#!/usr/bin/env python3
"""전용 계정 도구의 선택한 저장소 빌드. Author: JunyoungJung · 2026-10-22."""

import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from mobile_qa.release import current_digest, verify_release
from mobile_qa.storage import load_settings, verify_output, verify_storage
from keychain_store import IDENTIFIER, SKILL_DIR, build_lock, helper_directory, helper_manifest, sha256, _unique_object


def driver_pins(directory):
    settings = verify_storage(load_settings())
    android = verify_output(directory.parent / "AndroidInput/secure-input.jar")
    adb = None
    if android.exists():
        from build_android_input import manifest, toolchain
        tools = toolchain()
        actual = json.loads((android.parent / "build.json").read_text(), object_pairs_hook=_unique_object)
        if actual != manifest(SKILL_DIR / "android/SecureInput.java", android, tools):
            raise ValueError("Android 입력 도구를 다시 빌드하세요.")
        adb = tools["adb"]
    else:
        found = shutil.which("adb")
        if found:
            adb = Path(found).resolve(strict=True)
    ios_manifest = verify_output(directory.parent / "IOSInput/build.json")
    ios = json.loads(ios_manifest.read_text(), object_pairs_hook=_unique_object) if ios_manifest.exists() else {}
    if not isinstance(ios, dict):
        raise ValueError("iOS 입력 도구 기록이 올바르지 않습니다.")
    if ios:
        from build_ios_input import package_identity, runner_tree_digest, source_hashes
        if not isinstance(ios.get("developer_dir"), str) or not ios["developer_dir"]:
            raise ValueError("선택한 Xcode 기록이 있는 iOS 입력 도구를 다시 빌드하세요.")
        ios_root = (directory.parent / "IOSInput").resolve()
        runner_root = verify_output(Path(ios["runner_root"]))
        source_root = verify_output(Path(ios["source_root"]))
        xctestrun = verify_output(Path(ios["xctestrun"]))
        developer = Path(ios["developer_dir"])
        xcodebuild = developer / "usr/bin/xcodebuild"
        if (ios.get("signed") is not True or ios.get("release_digest") != current_digest()
                or not developer.is_absolute() or developer.resolve() != developer
                or not xcodebuild.is_file() or not os.access(xcodebuild, os.X_OK)
                or sha256(xcodebuild) != ios.get("xcodebuild_sha256")
                or not runner_root.is_relative_to(ios_root) or not source_root.is_relative_to(ios_root)
                or not xctestrun.is_relative_to(runner_root)
                or ios.get("agent_device_identity") != package_identity()
                or source_hashes(source_root) != ios.get("source_sha256")
                or ios.get("build_script_sha256") != sha256(SKILL_DIR / "scripts/build_ios_input.py")
                or ios.get("account_source_sha256") != sha256(SKILL_DIR / "ios/RunnerTests+AccountLogin.swift")
                or runner_tree_digest(runner_root) != ios.get("runner_tree_sha256")):
            raise ValueError("서명된 iOS 입력 도구를 다시 빌드하세요.")
        apps = list(runner_root.glob("Debug-iphoneos/*.app"))
        if len(apps) != 2:
            raise ValueError("서명된 iOS runner 산출물을 확정할 수 없습니다.")
        for app in apps:
            subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                           capture_output=True, check=True)
    values = {
        "releaseDigest": current_digest(), "volumeMount": settings["mount_point"],
        "volumeUUID": settings["volume_uuid"], "workRoot": str(directory),
        "adbPath": str(adb) if adb is not None else "", "adbSHA256": sha256(adb) if adb is not None else "",
        "androidJARPath": str(android), "androidJARSHA256": sha256(android) if android.exists() else "",
        "iosXCTestRunPath": ios.get("xctestrun"), "iosRunnerRoot": ios.get("runner_root"),
        "iosRunnerTreeSHA256": ios.get("runner_tree_sha256"),
        "iosDeveloperDirectory": ios.get("developer_dir"),
        "iosXcodeBuildSHA256": ios.get("xcodebuild_sha256"),
    }
    lines = ["// Generated local driver identities; contains no credentials.", "enum DriverPins {"]
    for name, value in values.items():
        optional = name.startswith("ios")
        if value is not None and (not isinstance(value, str) or any(ord(char) < 32 for char in value)):
            raise ValueError("입력 도구 경로가 올바르지 않습니다.")
        literal = "nil" if value is None else json.dumps(value, ensure_ascii=False)
        lines.append(f"    static let {name}: String{'?' if optional else ''} = {literal}")
    path = verify_output(directory / "DriverPins.swift")
    path.write_text("\n".join([*lines, "}", ""]))
    return path


def build():
    if sys.platform != "darwin":
        raise ValueError("macOS에서만 빌드할 수 있습니다.")
    verify_release()
    release_digest = current_digest()
    directory = helper_directory()
    temporary = directory / "tmp"
    cache = directory.parent / "ModuleCache"
    executable = directory / "keychain-helper"
    outputs = [directory, temporary, cache, executable, directory / "DriverPins.swift",
               directory / "build.log", directory / "build.json", directory / "build.lock"]
    for path in outputs:
        verify_output(path)
    architecture = platform.machine()
    if architecture not in {"arm64", "x86_64"}:
        raise ValueError("지원하지 않는 Mac 아키텍처입니다.")
    for path in (directory, temporary, cache):
        path.mkdir(parents=True, exist_ok=True)
    with build_lock(directory):
        environment = {**os.environ, "TMPDIR": str(temporary) + "/", "CLANG_MODULE_CACHE_PATH": str(cache)}
        pins = driver_pins(directory)
        commands = [
            ["/usr/bin/xcrun", "swiftc", "-parse-as-library", "-O", "-target", architecture + "-apple-macos13.0",
             "-module-cache-path", str(cache), *[str(path) for path in sorted((SKILL_DIR / "native").glob("*.swift"))],
             str(pins), "-o", str(executable)],
            ["/usr/bin/codesign", "--force", "--sign", "-", "--identifier", IDENTIFIER, str(executable)],
            ["/usr/bin/codesign", "--verify", "--strict", str(executable)],
        ]
        with (directory / "build.log").open("w") as log:
            for command in commands:
                subprocess.run(command, env=environment, stdout=log, stderr=log, check=True)
        if current_digest() != release_digest:
            raise ValueError("빌드 중 플러그인 버전이 변경되었습니다. 다시 빌드하세요.")
        (directory / "build.json").write_text(json.dumps(helper_manifest(executable), indent=2) + "\n")
    for path in outputs:
        verify_output(path)
    print(json.dumps({"status": "built", "helper": str(executable)}))


if __name__ == "__main__":
    try:
        build()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print(json.dumps({"status": "error", "message": "저장소 설정·플러그인 버전·Swift 도구·빌드 로그를 확인하세요."}, ensure_ascii=False))
        sys.exit(1)
