"""사용자가 로컬에 등록한 비밀값 없는 로그인 계약. Author: JunyoungJung · 2026-10-22."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from mobile_qa.release import current_digest
from account_catalog import project_key

_LABEL = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_DEVICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_APP = re.compile(r"[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*)+\Z")
_PROFILE_KEYS = {"schema_version", "name", "platform", "app_id", "environment", "role", "username_selector", "password_selector", "submit_selector", "environment_selector", "success_selectors", "timeout_ms"}


def label(value):
    if not isinstance(value, str) or _LABEL.fullmatch(value) is None:
        raise ValueError("프로필 이름·환경·용도는 영문 소문자·숫자·하이픈 1~64자여야 합니다.")
    return value


def validate_profile(profile):
    if (not isinstance(profile, dict) or set(profile) != _PROFILE_KEYS
            or type(profile["schema_version"]) is not int or profile["schema_version"] != 1
            or not isinstance(profile["platform"], str) or profile["platform"] not in {"ios", "android"}
            or not isinstance(profile["app_id"], str) or len(profile["app_id"]) > 200
            or _APP.fullmatch(profile["app_id"]) is None
            or (profile["platform"] == "android" and "-" in profile["app_id"])
            or type(profile["timeout_ms"]) is not int or not 1000 <= profile["timeout_ms"] <= 15000):
        raise ValueError("로그인 프로필 형식이 올바르지 않습니다.")
    for key in ["name", "environment", "role"]:
        label(profile[key])
    success = profile["success_selectors"]
    if not isinstance(success, list) or not 1 <= len(success) <= 8:
        raise ValueError("로그인 성공 화면을 명시하세요.")
    selectors = [profile[key] for key in ["username_selector", "password_selector", "submit_selector", "environment_selector"]] + success
    for selector in selectors:
        if not isinstance(selector, dict) or len(selector) != 1:
            raise ValueError("화면 요소는 단일 선택 조건이어야 합니다.")
        key, value = next(iter(selector.items()))
        allowed = {"identifier", "label", "placeholder"} if profile["platform"] == "ios" else {"identifier", "label"}
        if (key not in allowed or not isinstance(value, str) or not value
                or len(value.encode()) > 256 or any(unicodedata.category(char) in {"Cc", "Cf"} for char in value)):
            raise ValueError("화면 요소 선택 조건이 올바르지 않습니다.")
        if key == "identifier" and profile["platform"] == "android" and not re.fullmatch(re.escape(profile["app_id"]) + r":id/[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("Android 식별자는 대상 앱의 리소스 ID여야 합니다.")
    if len({json.dumps(item, sort_keys=True) for item in selectors}) != len(selectors):
        raise ValueError("로그인·환경·성공 요소는 서로 구분되어야 합니다.")
    return profile


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("중복 JSON 키는 사용할 수 없습니다.")
        result[key] = value
    return result


def _json(bytes_value):
    if len(bytes_value) > 32768:
        raise ValueError("프로필 파일이 너무 큽니다.")
    try:
        return json.loads(bytes_value, object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("프로필 JSON을 확인하세요.") from error


def profile_context(project_path):
    module_path = Path(__file__).resolve().parents[2] / "mobile-device-qa/scripts/project_store.py"
    spec = importlib.util.spec_from_file_location("mobile_qa_project_store", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    located = module.locate(project_path, create=False)
    folder = Path.home() / "Library/Application Support/mobile-device-qa/projects" / located["project_key"] / "profiles"
    if folder.parent != folder.parent.resolve() or folder.parent != Path(located["store_dir"]):
        raise ValueError("프로필 저장 경로에 심볼릭 링크를 사용할 수 없습니다.")
    return located["project_key"], project_key(project_path), folder


def _private_directory(folder, *, create=False):
    if create:
        folder.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        folder.mkdir(mode=0o700, exist_ok=True)
    for path in [folder.parent, folder]:
        try:
            info = path.lstat()
        except OSError as error:
            raise ValueError("등록한 로그인 프로필을 찾을 수 없습니다.") from error
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700 or path != path.resolve()):
            raise ValueError("프로필 폴더의 소유권·비공개 권한을 확인하세요.")


def register_profile(project_path, source):
    with Path(source).open("rb") as file:
        profile = validate_profile(_json(file.read(32769)))
    digest = current_digest()
    qa_key, account_project, folder = profile_context(project_path)
    _private_directory(folder, create=True)
    destination = folder / (profile["name"] + ".json")
    if destination.exists() or destination.is_symlink():
        _read_private(destination)
    wrapper = {"schema_version": 1, "project_key": qa_key, "account_project": account_project,
               "release_digest": digest, "profile": profile}
    contents = (json.dumps(wrapper, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    descriptor, temporary = tempfile.mkstemp(prefix=".register-", dir=folder)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)
    return {"status": "profile_registered", "name": profile["name"], "project_key": qa_key,
            "profile_sha256": hashlib.sha256(contents).hexdigest(), "release_digest": digest}


def _read_private(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise ValueError("신뢰 프로필을 안전하게 열 수 없습니다.") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise ValueError("신뢰 프로필의 소유권·비공개 권한을 확인하세요.")
        with os.fdopen(descriptor, "rb", closefd=False) as file:
            contents = file.read(32769)
        return contents, _json(contents)
    finally:
        os.close(descriptor)


def build_login_plan(project, environment, platform, device, *, project_path=None, app_id=None, role=None, login_profile=None):
    """저장소의 JSON을 자동 신뢰하지 않고 등록된 정확한 범위만 로드한다."""
    label(environment)
    label(role)
    label(login_profile)
    if not isinstance(platform, str) or platform not in {"ios", "android"} or not isinstance(device, str) or _DEVICE.fullmatch(device) is None or project_path is None:
        raise ValueError("현재 프로젝트·플랫폼·실제 기기 식별자를 명시하세요.")
    qa_key, account_project, folder = profile_context(project_path)
    if project != account_project:
        raise ValueError("현재 프로젝트의 계정 범위가 일치하지 않습니다.")
    _private_directory(folder)
    path = folder / (login_profile + ".json")
    contents, wrapper = _read_private(path)
    if (not isinstance(wrapper, dict) or set(wrapper) != {"schema_version", "project_key", "account_project", "release_digest", "profile"}
            or type(wrapper["schema_version"]) is not int or wrapper["schema_version"] != 1
            or wrapper["project_key"] != qa_key or wrapper["account_project"] != project
            or wrapper["release_digest"] != current_digest()):
        raise ValueError("프로필의 프로젝트·릴리스 신뢰가 일치하지 않습니다. 명시적으로 재등록하세요.")
    profile = validate_profile(wrapper["profile"])
    if (profile["name"] != login_profile or profile["environment"] != environment or profile["platform"] != platform
            or profile["role"] != role or (app_id is not None and profile["app_id"] != app_id)):
        raise ValueError("프로필의 앱·환경·용도 범위가 일치하지 않습니다.")
    return {**{key: value for key, value in profile.items() if key not in {"schema_version", "name"}},
            "device": device, "project_key": qa_key, "account_project": project,
            "trust": {"path": str(path), "profile_sha256": hashlib.sha256(contents).hexdigest(),
                      "release_digest": wrapper["release_digest"]}}
