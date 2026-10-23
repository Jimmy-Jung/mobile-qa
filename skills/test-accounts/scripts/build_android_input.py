#!/usr/bin/env python3
"""Android stdin 입력 도구의 선택 저장소 빌드. Author: JunyoungJung · 2026-10-22."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from mobile_qa.release import current_digest, verify_release
from mobile_qa.storage import verify_output, workspace_directory
from keychain_store import build_lock

SKILL_DIR = Path(__file__).resolve().parents[1]
MAIN_CLASS = "com.agenttestaccounts.SecureInput"
REMOTE_JAR = "/data/local/tmp/agent-test-accounts-secure-input.jar"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_directory(skill_dir=SKILL_DIR):
    return workspace_directory(skill_dir, category="AndroidInput", create=False)


def verify_output_path(path):
    return verify_output(path)


def android_sdk():
    configured = {Path(os.environ[name]).expanduser().resolve() for name in ("ANDROID_SDK_ROOT", "ANDROID_HOME")
                  if os.environ.get(name)}
    if len(configured) > 1:
        raise ValueError("ANDROID_HOME과 ANDROID_SDK_ROOT가 서로 다릅니다.")
    sdk = next(iter(configured), Path.home() / "Library/Android/sdk").resolve()
    if not sdk.is_dir():
        raise ValueError("ANDROID_HOME 또는 ANDROID_SDK_ROOT로 Android SDK를 지정하세요.")
    return sdk


def valid_java_home(path):
    try:
        root = Path(path).expanduser().resolve(strict=True)
        tools = [(root / "bin" / name).resolve(strict=True) for name in ("java", "javac")]
        if (tools[0].parent != tools[1].parent or tools[0].parent.name != "bin"
                or any(tool in {Path("/usr/bin/java"), Path("/usr/bin/javac")} for tool in tools)
                or any(not tool.is_file() or not os.access(tool, os.X_OK) for tool in tools)):
            return None
        return tools[0].parent.parent
    except (OSError, ValueError, TypeError):
        return None


def studio_java_home():
    for base in (Path("/Applications"), Path.home() / "Applications"):
        bundled = valid_java_home(base / "Android Studio.app/Contents/jbr/Contents/Home")
        if bundled is not None:
            return bundled
    return None


def java_home():
    if "JAVA_HOME" in os.environ:
        selected = valid_java_home(os.environ["JAVA_HOME"]) if os.environ["JAVA_HOME"] else None
        if selected is None:
            raise ValueError("JAVA_HOME에 실행 가능한 java와 javac가 있는 동일 JDK를 지정하세요.")
        return selected
    try:
        result = subprocess.run(["/usr/libexec/java_home"], capture_output=True, text=True, check=False, timeout=10)
        registered = valid_java_home(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip() else None
        if registered is not None:
            return registered
    except (OSError, subprocess.SubprocessError):
        pass
    java, javac = shutil.which("java"), shutil.which("javac")
    if java and javac:
        actual_java, actual_javac = Path(java).resolve(), Path(javac).resolve()
        if actual_java.parent == actual_javac.parent:
            selected = valid_java_home(actual_java.parent.parent)
            if selected is not None:
                return selected
    bundled = studio_java_home()
    if bundled is not None:
        return bundled
    raise ValueError("JAVA_HOME 또는 PATH에 java와 javac가 함께 있는 JDK를 지정하세요.")


def toolchain():
    sdk, jdk = android_sdk(), java_home()
    platforms = [(int(path.parent.name.split("-", 1)[1]), path) for path in sdk.glob("platforms/android-*/android.jar")
                 if re.fullmatch(r"android-\d+", path.parent.name)]
    builds = [(tuple(map(int, path.parents[1].name.split("."))), path)
              for path in sdk.glob("build-tools/*/lib/d8.jar")
              if re.fullmatch(r"\d+\.\d+\.\d+", path.parents[1].name)]
    if not platforms or max(platforms)[0] < 26 or not builds:
        raise ValueError("Android API 26 이상과 안정 버전 Build Tools가 필요합니다.")
    tools = {"android_jar": max(platforms)[1], "d8_jar": max(builds)[1],
             "java": jdk / "bin/java", "javac": jdk / "bin/javac", "adb": sdk / "platform-tools/adb"}
    if any(not path.is_file() for path in tools.values()) or any(
            not os.access(tools[name], os.X_OK) for name in ("java", "javac", "adb")):
        raise ValueError("Android SDK 및 실행 가능한 JDK 도구를 확인하세요.")
    return {name: path.resolve() for name, path in tools.items()}


def tool_identities(tools):
    return {name: {"path": str(path), "sha256": sha256(path)} for name, path in tools.items()}


def manifest(source, artifact, tools=None):
    return {
        "release_digest": current_digest(), "source_sha256": sha256(source),
        "build_script_sha256": sha256(Path(__file__).resolve()), "jar_sha256": sha256(artifact),
        "tools": tool_identities(toolchain() if tools is None else tools),
        "main_class": MAIN_CLASS, "remote_jar": REMOTE_JAR, "min_sdk": 26,
    }


def build():
    if sys.platform != "darwin":
        raise ValueError("macOS에서만 빌드할 수 있습니다.")
    verify_release()
    directory = build_directory()
    tools = toolchain()
    source = SKILL_DIR / "android/SecureInput.java"
    release_digest = current_digest()
    source_slot = hashlib.sha256((sha256(source) + release_digest
                                  + json.dumps(tool_identities(tools), sort_keys=True)).encode()).hexdigest()[:16]
    classes, dex = directory / "classes" / source_slot, directory / "dex" / source_slot
    temporary = directory / "tmp"
    copied = directory / "Source" / source_slot / "SecureInput.java"
    artifact = directory / "secure-input.jar"
    outputs = [directory, classes, dex, temporary, copied.parent, copied, artifact,
               directory / "build.json", directory / "build.log", directory / "build.lock"]
    for path in outputs:
        verify_output_path(path)
    for path in (directory, classes, dex, temporary, copied.parent):
        path.mkdir(parents=True, exist_ok=True)
    with build_lock(directory):
        text = source.read_text()
        if text.count("__MOBILE_QA_RELEASE_DIGEST__") != 1:
            raise ValueError("Android release pin 소스가 올바르지 않습니다.")
        copied.write_text(text.replace("__MOBILE_QA_RELEASE_DIGEST__", release_digest))
        environment = {**os.environ, "TMPDIR": str(temporary) + "/"}
        commands = [
            [str(tools["javac"]), "-J-Djava.io.tmpdir=" + str(temporary), "-encoding", "UTF-8",
             "-source", "8", "-target", "8", "-classpath", str(tools["android_jar"]),
             "-d", str(classes), str(copied)],
            [str(tools["java"]), "-Djava.io.tmpdir=" + str(temporary), "-cp", str(tools["d8_jar"]),
             "com.android.tools.r8.D8", "--min-api", "26", "--lib", str(tools["android_jar"]), "--output", str(dex)],
        ]
        with (directory / "build.log").open("w") as log:
            subprocess.run(commands[0], env=environment, stdout=log, stderr=log, check=True)
            class_files = sorted(classes.rglob("*.class"))
            if not class_files:
                raise ValueError("컴파일 결과가 없습니다.")
            subprocess.run(commands[1] + [str(path) for path in class_files],
                           env=environment, stdout=log, stderr=log, check=True)
        with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr(zipfile.ZipInfo("classes.dex", (1980, 1, 1, 0, 0, 0)), (dex / "classes.dex").read_bytes())
        if current_digest() != release_digest:
            raise ValueError("빌드 중 플러그인 버전이 변경되었습니다. 다시 빌드하세요.")
        (directory / "build.json").write_text(json.dumps(manifest(source, artifact, tools), indent=2) + "\n")
    for path in outputs:
        verify_output_path(path)
    print(json.dumps({"status": "built", "jar": str(artifact), "main_class": MAIN_CLASS}))


if __name__ == "__main__":
    try:
        build()
    except (OSError, ValueError, subprocess.SubprocessError):
        print(json.dumps({"status": "error", "code": "android_input_build_failed"}))
        sys.exit(1)
