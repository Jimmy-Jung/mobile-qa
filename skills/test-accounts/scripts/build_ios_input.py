#!/usr/bin/env python3
"""비공개 iOS 로그인 runner 선택 저장소 빌드. Author: JunyoungJung · 2026-10-20."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
from xml.parsers.expat import ExpatError

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from mobile_qa.release import current_digest, read_json, verify_release
from mobile_qa.storage import verify_output
from keychain_store import SKILL_DIR, build_lock, helper_directory


APP_BUNDLE = "com.agenttestaccounts.runner"
TEST_BUNDLE = APP_BUNDLE + ".uitests"
SIMULATOR_APP_BUNDLE = "com.agenttestaccounts.simulator"
SIMULATOR_TEST_BUNDLE = SIMULATOR_APP_BUNDLE + ".uitests"
PORT = 19087
SOURCE_LOCK = Path(__file__).resolve().parents[3] / "mobile_qa/agent-device-0.21.30.json"
IGNORED = {".build", ".swiftpm", "xcuserdata", "SnapshotPresentationConformance", "Tests"}


def agent_device_package():
    explicit = os.environ.get("MOBILE_QA_AGENT_DEVICE_PACKAGE")
    if explicit is not None:
        try:
            path = Path(explicit).expanduser()
            if not explicit or not path.is_absolute():
                raise ValueError
            package = path.resolve(strict=True)
            data = read_json(package / "package.json")
            if (not isinstance(data, dict) or data.get("name") != "agent-device" or data.get("version") != "0.21.30"
                    or not isinstance(data.get("bin"), dict) or not isinstance(data["bin"].get("agent-device"), str)):
                raise ValueError
            entry = (package / data["bin"]["agent-device"]).resolve(strict=True)
            if not entry.is_file() or not entry.is_relative_to(package):
                raise ValueError
            return package
        except (OSError, ValueError, TypeError):
            raise ValueError("MOBILE_QA_AGENT_DEVICE_PACKAGE에 공식 agent-device 0.21.30 패키지 폴더를 지정하세요.") from None
    binary = shutil.which("agent-device")
    if binary is None:
        raise ValueError("agent-device 0.21.30을 PATH에 설치하세요.")
    resolved = Path(binary).resolve(strict=True)
    for directory in resolved.parents:
        metadata = directory / "package.json"
        if not metadata.is_file():
            continue
        data = read_json(metadata)
        if (not isinstance(data, dict) or data.get("name") != "agent-device" or data.get("version") != "0.21.30"
                or not isinstance(data.get("bin"), dict)
                or not isinstance(data["bin"].get("agent-device"), str)
                or (directory / data["bin"]["agent-device"]).resolve() != resolved):
            raise ValueError("검증한 agent-device 0.21.30 CLI 패키지가 아닙니다.")
        return directory
    raise ValueError("agent-device 패키지 출처를 확인할 수 없습니다.")


def developer_directory(environment=None):
    environment = dict(os.environ) if environment is None else environment
    try:
        result = subprocess.run(["/usr/bin/xcrun", "--find", "xcodebuild"], env=environment,
                                capture_output=True, text=True, check=False, timeout=10)
        if result.returncode or not result.stdout.strip():
            raise ValueError
        tool = Path(result.stdout.strip())
        if not tool.is_absolute():
            raise ValueError
        tool = tool.resolve(strict=True)
        developer = tool.parents[2]
        if (tool == Path("/usr/bin/xcodebuild") or tool != developer / "usr/bin/xcodebuild"
                or not tool.is_file() or not os.access(tool, os.X_OK)):
            raise ValueError
        return developer
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        raise ValueError("선택한 Xcode의 실제 Developer 디렉터리와 xcodebuild를 확인하세요.") from None


def package_identity(package=None):
    package = (agent_device_package() if package is None else Path(package)).resolve(strict=True)
    metadata = read_json(package / "package.json")
    if not isinstance(metadata, dict) or metadata.get("name") != "agent-device" or metadata.get("version") != "0.21.30":
        raise ValueError("agent-device 0.21.30 패키지 메타데이터가 아닙니다.")
    apple = package / "dist/apple"
    if SOURCE_LOCK.is_symlink() or not SOURCE_LOCK.is_file():
        raise ValueError("검증한 agent-device 소스 잠금 파일이 필요합니다.")
    lock = read_json(SOURCE_LOCK)
    if (not isinstance(lock, dict)
            or set(lock) != {"schema_version", "version", "dist_integrity", "tarball", "files"}
            or type(lock["schema_version"]) is not int or lock["schema_version"] != 1
            or lock["version"] != "0.21.30"
            or lock["tarball"] != "https://registry.npmjs.org/agent-device/-/agent-device-0.21.30.tgz"
            or not isinstance(lock["dist_integrity"], str)
            or not re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==", lock["dist_integrity"])
            or not isinstance(lock["files"], dict) or not lock["files"]):
        raise ValueError("agent-device 소스 잠금 형식이 올바르지 않습니다.")
    sources = {str(relative): source_hashes(apple / relative) for relative in
               (Path("runner/AgentDeviceRunner"), Path("snapshot-presentation"))}
    actual = {str(Path(root) / name): "sha256:" + digest for root, hashes in sources.items() for name, digest in hashes.items()}
    if actual != lock["files"]:
        raise ValueError("설치된 agent-device 소스가 검증한 공식 0.21.30과 다릅니다.")
    return {"path": str(package), "version": "0.21.30",
            "package_sha256": hashlib.sha256((package / "package.json").read_bytes()).hexdigest(),
            "source_lock_sha256": hashlib.sha256(SOURCE_LOCK.read_bytes()).hexdigest(),
            "source_sha256": sources}


def patch_transport(source):
    begin = source.find("  private func handleRequestBody(\n")
    end = source.find("  func inlineResponse(for command: Command) -> Response? {", begin)
    if (begin < 0 or end < 0 or source.count("  private func handleRequestBody(\n") != 1
            or source.count("  func inlineResponse(for command: Command) -> Response? {") != 1
            or "let command = try JSONDecoder().decode(Command.self, from: body)" not in source[begin:end]):
        raise ValueError("설치된 runner transport 형식이 변경되었습니다.")
    replacement = '''  private func handleRequestBody(
    _ body: Data,
    completion: @escaping @Sendable ((data: Data, shouldFinish: Bool)) -> Void
  ) {
    // This isolated runner accepts only nonce-bound account input, never generic commands.
    guard let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
      object["secureAccountProtocol"] as? String == "agent-test-accounts-v1"
    else {
      completion((secureAccountUnavailableResponse(), false))
      return
    }
    handleSecureAccountRequest(body, completion: completion)
  }

'''
    return source[:begin] + replacement + source[end:]


def patch_issue_recording(source):
    marker = "  override func record(_ issue: XCTIssue) {\n"
    if source.count(marker) != 1:
        raise ValueError("설치된 runner issue 기록 형식이 변경되었습니다.")
    return source.replace(marker, marker + "    if recordSecureAccountIssue() { return }\n", 1)


def signing_settings(team_id, unsigned):
    if unsigned:
        return ["CODE_SIGNING_ALLOWED=NO", "CODE_SIGNING_REQUIRED=NO", "CODE_SIGN_IDENTITY=", "DEVELOPMENT_TEAM="]
    if not team_id or not re.fullmatch(r"[A-Z0-9]{10}", team_id):
        raise ValueError("확인한 Apple 개발 팀 식별자가 필요합니다.")
    return ["CODE_SIGN_STYLE=Automatic", "DEVELOPMENT_TEAM=" + team_id]


def source_hashes(directory):
    if (not directory.is_dir() or any(path.is_symlink() for path in (directory, *directory.parents))
            or any(path.is_symlink() for path in directory.rglob("*"))):
        raise ValueError("runner 소스 경로 또는 심볼릭 링크를 확인하세요.")
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob("*")) if path.is_file()
            and not any(part in IGNORED for part in path.relative_to(directory).parts)}


def runner_tree_digest(directory):
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*"), key=lambda item: str(item.relative_to(directory)).encode()):
        if path.is_symlink():
            raise ValueError("runner 산출물의 심볼릭 링크는 허용하지 않습니다.")
        if path.is_file():
            relative = str(path.relative_to(directory))
            file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            digest.update((relative + "\0" + file_hash + "\n").encode())
    return digest.hexdigest()


def prepare(directory):
    package = agent_device_package()
    identity = package_identity(package)
    release_digest = current_digest()
    slot = hashlib.sha256((json.dumps(identity, sort_keys=True) + release_digest).encode()).hexdigest()[:16]
    apple = package / "dist/apple"
    copy_root = directory / "Source" / slot
    verify_output(copy_root)
    if copy_root.exists() and any(path.is_symlink() for path in copy_root.rglob("*")):
        raise ValueError("runner 복사 경로에 심볼릭 링크가 있습니다.")
    runner = copy_root / "runner/AgentDeviceRunner"
    for relative in [Path("runner/AgentDeviceRunner"), Path("snapshot-presentation")]:
        source_hashes(apple / relative)
        shutil.copytree(apple / relative, copy_root / relative, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(*IGNORED))
    tests = runner / "AgentDeviceRunnerUITests"
    transport = tests / "RunnerTests+Transport.swift"
    transport.write_text(patch_transport(transport.read_text()))
    main = tests / "RunnerTests.swift"
    main.write_text(patch_issue_recording(main.read_text()))
    account_source = (SKILL_DIR / "ios/RunnerTests+AccountLogin.swift").read_text()
    if account_source.count("__MOBILE_QA_RELEASE_DIGEST__") != 1:
        raise ValueError("iOS release pin 소스가 올바르지 않습니다.")
    (tests / "RunnerTests+AccountLogin.swift").write_text(
        account_source.replace("__MOBILE_QA_RELEASE_DIGEST__", release_digest))
    return runner, source_hashes(copy_root)


def verify_separate_build_output(directory, commands):
    if any(str(directory.resolve()) in command for command in commands):
        raise ValueError("같은 iOS runner 출력 경로를 사용하는 Xcode 작업이 진행 중입니다.")


def selected_build_paths(directory):
    paths = {
        "derived_data": directory / "DerivedData",
        "temporary": directory / "tmp",
        "cache_root": directory / "Cache",
        "module_cache": directory / "DerivedData/ModuleCache.noindex",
        "cloned_packages": directory / "DerivedData/SourcePackages",
        "package_cache": directory.parent / "SwiftPM/Cache",
    }
    outputs = [directory, *paths.values(), directory / "Source", directory / "Diagnostics",
               directory / "build.log", directory / "build.json", directory / "build.lock"]
    for path in outputs:
        verify_output(path)
    return paths


def product_plist(path):
    path = verify_output(path)
    if path.resolve() != path:
        raise ValueError("runner 제품 경로에 심볼릭 링크를 사용할 수 없습니다.")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1048576:
            raise ValueError("runner 제품 메타데이터를 확인하세요.")
        data = stream.read(1048577)
    try:
        value = plistlib.loads(data)
    except (plistlib.InvalidFileException, ExpatError, ValueError):
        raise ValueError("runner 제품 메타데이터 형식이 올바르지 않습니다.") from None
    if not isinstance(value, dict):
        raise ValueError("runner 제품 메타데이터는 dictionary여야 합니다.")
    return value


def verify_simulator_bundle(bundle, bundle_id, package_type):
    bundle = verify_output(bundle)
    if not bundle.is_dir() or bundle.resolve() != bundle:
        raise ValueError("Simulator bundle 경로를 확인하세요.")
    info = product_plist(bundle / "Info.plist")
    executable = info.get("CFBundleExecutable")
    if (info.get("CFBundleIdentifier") != bundle_id or executable != bundle.stem
            or info.get("CFBundlePackageType") != package_type
            or info.get("CFBundleSupportedPlatforms") != ["iPhoneSimulator"]
            or info.get("DTPlatformName") != "iphonesimulator"
            or not isinstance(info.get("DTSDKName"), str)
            or re.fullmatch(r"iphonesimulator[0-9]+(?:\.[0-9]+)*(?:\.internal)?", info["DTSDKName"]) is None):
        raise ValueError("Simulator 제품의 실제 식별자·실행 파일·SDK가 일치하지 않습니다.")
    binary = verify_output(bundle / executable)
    info = binary.lstat()
    if not stat.S_ISREG(info.st_mode) or not os.access(binary, os.X_OK) or binary.resolve() != binary:
        raise ValueError("Simulator 제품 실행 파일이 올바르지 않습니다.")


def test_product_path(value, products, host):
    if not isinstance(value, str) or ".." in Path(value).parts:
        raise ValueError("xctestrun 제품 경로가 올바르지 않습니다.")
    for marker, directory in [("__TESTROOT__", products), ("__TESTHOST__", host)]:
        if value.startswith(marker + "/"):
            value = str(directory) + value[len(marker):]
            break
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("xctestrun 제품 경로를 확인하세요.")
    return verify_output(path)


def verify_simulator_products(products, xctestrun):
    products = verify_output(products)
    root = products / "Debug-iphonesimulator"
    app = root / "AgentDeviceRunner.app"
    host = root / "AgentDeviceRunnerUITests-Runner.app"
    test = host / "PlugIns/AgentDeviceRunnerUITests.xctest"
    if set(root.glob("*.app")) != {app, host}:
        raise ValueError("Simulator 제품 구성이 일치하지 않습니다.")
    for bundle, identifier, kind in [(app, SIMULATOR_APP_BUNDLE, "APPL"),
            (host, SIMULATOR_TEST_BUNDLE + ".xctrunner", "APPL"), (test, SIMULATOR_TEST_BUNDLE, "BNDL")]:
        verify_simulator_bundle(bundle, identifier, kind)
    xctestrun = verify_output(xctestrun)
    if xctestrun.parent != products or xctestrun.suffix != ".xctestrun":
        raise ValueError("Simulator xctestrun 위치가 일치하지 않습니다.")
    targets = []

    def collect(value):
        if isinstance(value, dict):
            if "TestBundlePath" in value:
                targets.append(value)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(product_plist(xctestrun))
    if len(targets) != 1:
        raise ValueError("Simulator 테스트 대상을 확정할 수 없습니다.")
    target = targets[0]
    if (target.get("BlueprintName") != "AgentDeviceRunnerUITests" or target.get("IsUITestBundle") is not True
            or target.get("TestHostBundleIdentifier") != SIMULATOR_TEST_BUNDLE + ".xctrunner"
            or target.get("TestBundleIdentifier", SIMULATOR_TEST_BUNDLE) != SIMULATOR_TEST_BUNDLE
            or test_product_path(target.get("TestHostPath"), products, host) != host
            or test_product_path(target.get("TestBundlePath"), products, host) != test
            or test_product_path(target.get("UITargetAppPath"), products, host) != app):
        raise ValueError("Simulator 테스트·호스트·대상 앱 계약이 일치하지 않습니다.")


def build(team_id=None, unsigned=False, *, simulator=False):
    if sys.platform != "darwin":
        raise ValueError("macOS에서만 빌드할 수 있습니다.")
    verify_release()
    directory = helper_directory().parent / ("IOSSimulatorInput" if simulator else "IOSInput")
    paths = selected_build_paths(directory)
    signing = signing_settings(team_id, unsigned or simulator)
    cache_settings = {"CACHE_ROOT": str(paths["cache_root"]),
                      "CLANG_MODULE_CACHE_PATH": str(paths["module_cache"]),
                      "SWIFT_MODULE_CACHE_PATH": str(paths["module_cache"])}
    environment = {**os.environ, **cache_settings, "TMPDIR": str(paths["temporary"]) + "/"}
    environment["DEVELOPER_DIR"] = str(developer_directory(environment))
    verify_separate_builds(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with build_lock(directory):
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)
        _build(directory, paths, signing, cache_settings, environment, unsigned or simulator, simulator=simulator)


def _build(directory, paths, signing, cache_settings, environment, unsigned, *, simulator=False):
    release_digest, identity = current_digest(), package_identity()
    xcodebuild = Path(environment["DEVELOPER_DIR"]) / "usr/bin/xcodebuild"
    xcodebuild_digest = hashlib.sha256(xcodebuild.read_bytes()).hexdigest()
    runner, hashes = prepare(directory)
    sdk = "iphonesimulator" if simulator else "iphoneos"
    app_bundle = SIMULATOR_APP_BUNDLE if simulator else APP_BUNDLE
    test_bundle = SIMULATOR_TEST_BUNDLE if simulator else TEST_BUNDLE
    # The patched source is isolated from the globally installed automation runner.
    command = ["/usr/bin/xcodebuild", "-quiet", "-project", str(runner / "AgentDeviceRunner.xcodeproj"),
               "-scheme", "AgentDeviceRunner", "-configuration", "Debug", "-destination",
               "generic/platform=iOS Simulator" if simulator else "generic/platform=iOS",
               "-derivedDataPath", str(paths["derived_data"]),
               "-clonedSourcePackagesDirPath", str(paths["cloned_packages"]),
               "-packageCachePath", str(paths["package_cache"]), *(["-sdk", sdk] if simulator else []), "build-for-testing",
               "AGENT_DEVICE_IOS_RUNNER_APP_BUNDLE_ID=" + app_bundle,
               "AGENT_DEVICE_IOS_RUNNER_TEST_BUNDLE_ID=" + test_bundle,
               "COMPILER_INDEX_STORE_ENABLE=NO", "ENABLE_CODE_COVERAGE=NO", "ENABLE_PREVIEWS=NO",
               "ENABLE_DEBUG_DYLIB=NO", *[name + "=" + value for name, value in cache_settings.items()], *signing]
    with (directory / "build.log").open("w") as log:
        subprocess.run(command, cwd=runner, env=environment, stdout=log, stderr=log, check=True)
    products = directory / "DerivedData/Build/Products"
    apps = list(products.glob("Debug-" + sdk + "/*.app"))
    if len(apps) != 2:
        raise ValueError("iOS runner 산출물을 확정할 수 없습니다.")
    if not unsigned:
        for app in apps:
            subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                           capture_output=True, check=True)
    candidates = sorted(products.glob("*.xctestrun" if simulator else "*" + sdk + "*.xctestrun"))
    if len(candidates) != 1:
        raise ValueError("선택한 iOS 대상의 xctestrun을 확정할 수 없습니다.")
    xctestrun = candidates[0]
    if simulator:
        verify_simulator_products(products, xctestrun)
    data = plistlib.loads(xctestrun.read_bytes())
    def disable_attachments(value):
        if isinstance(value, dict):
            if "TestBundlePath" in value:
                value.update(SystemAttachmentLifetime="keepNever", UserAttachmentLifetime="keepNever")
            for child in value.values():
                disable_attachments(child)
        elif isinstance(value, list):
            for child in value:
                disable_attachments(child)
    disable_attachments(data)
    xctestrun.write_bytes(plistlib.dumps(data))
    if (current_digest() != release_digest or package_identity() != identity
            or hashlib.sha256(xcodebuild.read_bytes()).hexdigest() != xcodebuild_digest):
        raise ValueError("빌드 중 플러그인 또는 agent-device가 변경되었습니다. 다시 빌드하세요.")
    manifest = {"schema_version": 1, "target_kind": "simulator" if simulator else "physical", "sdk": sdk,
                "release_digest": release_digest,
                "developer_dir": environment["DEVELOPER_DIR"],
                "xcodebuild_sha256": xcodebuild_digest,
                "agent_device_identity": identity, "source_root": str(runner.parents[1]), "agent_device_version": "0.21.30", "port": PORT,
                "app_bundle_id": app_bundle, "test_bundle_id": test_bundle,
                "xctestrun": str(xctestrun), "signed": not unsigned, "source_sha256": hashes,
                "runner_root": str(products), "runner_tree_sha256": runner_tree_digest(products),
                "build_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "account_source_sha256": hashlib.sha256((SKILL_DIR / "ios/RunnerTests+AccountLogin.swift").read_bytes()).hexdigest()}
    for path in [directory / "Source", products, xctestrun, directory / "build.json", directory / "build.log"]:
        verify_output(path)
    (directory / "build.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"status": "built", "manifest": str(directory / "build.json"),
                      "xctestrun": str(xctestrun), "signed": not unsigned, "port": PORT}))


def verify_separate_builds(directory):
    inventory = subprocess.run(["/usr/bin/pgrep", "-lx", "xcodebuild"], capture_output=True, text=True)
    if inventory.returncode not in {0, 1}:
        raise ValueError("진행 중인 Xcode 빌드를 확인할 수 없습니다.")
    if inventory.returncode == 0:
        running_commands = []
        for line in inventory.stdout.splitlines():
            pid = line.split(maxsplit=1)[0]
            if not pid.isdecimal():
                raise ValueError("Xcode 작업 식별자를 확인할 수 없습니다.")
            running = subprocess.run(["/bin/ps", "-p", pid, "-o", "command="], capture_output=True, text=True)
            if running.returncode not in {0, 1}:
                raise ValueError("진행 중인 Xcode 작업 경로를 확인할 수 없습니다.")
            running_commands.append(running.stdout)
        verify_separate_build_output(directory, running_commands)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-id")
    parser.add_argument("--unsigned", action="store_true")
    parser.add_argument("--simulator", action="store_true", help="별도 Simulator runner 빌드; 실기기 서명 불필요")
    options = parser.parse_args()
    try:
        build(options.team_id, options.unsigned, simulator=options.simulator)
    except (OSError, ValueError, subprocess.SubprocessError):
        print(json.dumps({"status": "error", "message": "선택한 저장소·iOS runner 빌드 로그·서명 설정을 확인하세요."}, ensure_ascii=False))
        sys.exit(1)
