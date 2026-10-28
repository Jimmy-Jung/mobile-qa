"""비밀값을 받지 않는 네이티브 로컬 저장소 연결. Author: JunyoungJung · 2026-10-20."""

import fcntl
import hashlib
import os
import json
import re
from contextlib import contextmanager
from pathlib import Path
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from mobile_qa.release import current_digest, verify_release
from mobile_qa.storage import verify_output, workspace_directory
from account_catalog import reference


IDENTIFIER = "com.agent-test-accounts.keychain-helper"
SKILL_DIR = Path(__file__).resolve().parents[1]
STORAGE_CODES = {
    "invalid_arguments", "invalid_credentials", "invalid_reference", "foreign_reference",
    "storage_failed", "storage_not_ready", "migration_requires_approval", "internal_error",
}


class KeychainError(RuntimeError):
    """비밀값을 포함하지 않는 계정 저장소 오류."""

    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code if isinstance(code, str) and code in STORAGE_CODES else None


class Cancelled(Exception):
    pass


@contextmanager
def isolated_credentials_input():
    """입력 원문을 읽지 않고 fd0에서 분리해 명시적으로 넘길 때만 상속한다."""
    input_fd = null_fd = None
    original_inheritable = False
    replaced = False
    try:
        try:
            mode = os.fstat(0).st_mode
            if os.isatty(0) or not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
                raise ValueError
            original_inheritable = os.get_inheritable(0)
            input_fd = os.dup(0)  # Python의 복제 FD는 기본적으로 비상속이다.
            if input_fd < 3:
                raise ValueError
            null_fd = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
            replaced = True
            os.dup2(null_fd, 0)
        except (OSError, ValueError):
            raise KeychainError("계정 입력에는 비대화형 전용 파이프가 필요합니다.") from None
        yield input_fd
    finally:
        try:
            if replaced:
                os.dup2(input_fd, 0, inheritable=original_inheritable)
        finally:
            if null_fd is not None:
                os.close(null_fd)
            if input_fd is not None:
                os.close(input_fd)


def helper_directory():
    return workspace_directory(SKILL_DIR, category="NativeHelper", create=False)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_digest():
    digest = hashlib.sha256()
    for path in sorted((SKILL_DIR / "native").glob("*.swift")):
        digest.update((path.name + "\0" + sha256(path) + "\n").encode())
    return digest.hexdigest()


def helper_manifest(executable):
    return {
        "release_digest": current_digest(),
        "source_sha256": source_digest(),
        "executable_sha256": sha256(executable),
        "driver_pins_sha256": sha256(executable.parent / "DriverPins.swift"),
        "identifier": IDENTIFIER,
    }


@contextmanager
def build_lock(directory):
    """Do not overwrite a build another invocation is preparing."""
    verify_output(directory / "build.lock")
    descriptor = os.open(directory / "build.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("같은 출력 경로의 빌드가 진행 중입니다.") from None
        yield
    finally:
        os.close(descriptor)


def verified_helper():
    if sys.platform != "darwin":
        raise KeychainError("macOS에서만 사용할 수 있습니다.")
    try:
        verify_release()
        directory = helper_directory()
        executable = verify_output(directory / "keychain-helper")
        verify_output(directory / "build.json")
        verify_output(directory / "DriverPins.swift")
        manifest = json.loads((directory / "build.json").read_text(), object_pairs_hook=_unique_object)
        if manifest != helper_manifest(executable):
            raise ValueError
        checked = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--strict", str(executable)],
            capture_output=True, check=False,
        )
        if checked.returncode:
            raise ValueError
    except (OSError, ValueError, subprocess.SubprocessError):
        raise KeychainError("저장소 설정·플러그인 버전·서명된 계정 도구를 확인하고 필요하면 다시 빌드하세요.") from None
    return executable


class Keychain:
    """원문 입력·저장은 전용 서명 도구가 맡고 Python에는 참조만 반환한다."""

    def __init__(self):
        self.executable = verified_helper()

    def _run(self, arguments, expected):
        try:
            result = subprocess.run(
                [str(self.executable), *arguments], stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, check=False,
            )
            data = json.loads(result.stdout, object_pairs_hook=_unique_object)
            if data == {"status": "cancelled"}:
                raise Cancelled()
            if result.returncode or not isinstance(data, dict) or data.get("status") != expected:
                raise ValueError
            return data
        except (OSError, ValueError):
            raise KeychainError("계정 저장소 도구 작업에 실패했습니다.") from None

    @staticmethod
    def _finish(process, decision, expected):
        output, _ = process.communicate(input=decision + "\n")
        if process.returncode or json.loads(output, object_pairs_hook=_unique_object) != {"status": expected}:
            raise KeychainError("계정 등록의 확정 또는 취소를 확인할 수 없습니다.")

    @contextmanager
    def registration(self, account_id, context, *, credentials_fd=None, copy_from=None):
        process = None
        try:
            input_fd = None
            arguments = [str(self.executable), "create", "--id", account_id, "--context", context]
            input_options = {}
            try:
                if copy_from is not None:
                    if (credentials_fd is not None or not isinstance(copy_from, tuple) or len(copy_from) != 2
                            or not isinstance(copy_from[0], str) or copy_from[0] == account_id
                            or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", copy_from[0]) is None):
                        raise ValueError
                    source_id, source_ref = copy_from
                    arguments = [str(self.executable), "create-copy", "--id", account_id, "--context", context,
                                 "--source-id", source_id, "--source-ref", reference(source_ref)]
                if credentials_fd is not None:
                    if type(credentials_fd) is not int or credentials_fd < 3 or os.isatty(credentials_fd):
                        raise ValueError
                    # Python은 원문을 읽지 않고 기존 입력 파이프의 복제본만 넘긴다.
                    input_fd = os.dup(credentials_fd)
                    arguments = [str(self.executable), "create-stdin", "--id", account_id,
                                 "--context", context, "--input-fd", str(input_fd)]
                    input_options = {"pass_fds": (input_fd,)}
                process = subprocess.Popen(
                    arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, **input_options,
                )
            finally:
                if input_fd is not None:
                    os.close(input_fd)
            data = json.loads(process.stdout.readline(16384), object_pairs_hook=_unique_object)
            if data == {"status": "cancelled"}:
                raise Cancelled()
            if not isinstance(data, dict) or set(data) != {"status", "persistent_ref"} or data["status"] != "created":
                raise ValueError
            persistent_ref = reference(data["persistent_ref"])
            try:
                yield persistent_ref
            except BaseException:
                self._finish(process, "rollback", "rolled_back")
                raise
            else:
                self._finish(process, "commit", "committed")
        except (OSError, ValueError):
            raise KeychainError("계정 등록 도구 작업에 실패했습니다.") from None
        finally:
            if process is not None and process.poll() is None:
                # 부모가 중단되어도 자식은 표준 입력 종료를 받아 자기 미확정 항목만 정리한다.
                try:
                    process.communicate(input="rollback\n", timeout=5)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    process.terminate()
                    process.communicate()

    def update(self, account_id, persistent_ref, context):
        data = self._run(["update", "--id", account_id, "--ref", reference(persistent_ref), "--context", context], "updated")
        if set(data) != {"status"}:
            raise KeychainError("계정 저장소 응답 형식이 올바르지 않습니다.")

    def _summary(self, arguments, records, expected):
        accounts = [{"id": record["id"], "reference": reference(record["keychain_ref"])} for record in records]
        try:
            result = subprocess.run(
                [str(self.executable), *arguments], input=json.dumps({"accounts": accounts}) + "\n",
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=180, check=False,
            )
            data = json.loads(result.stdout, object_pairs_hook=_unique_object)
            if (isinstance(data, dict) and set(data) == {"status", "code"}
                    and data["status"] == "error" and isinstance(data["code"], str)
                    and data["code"] in STORAGE_CODES):
                raise KeychainError("계정 저장소 작업을 완료할 수 없습니다.", code=data["code"])
            if (result.returncode or not isinstance(data, dict) or set(data) != {"status", "count"}
                    or data["status"] != expected or type(data["count"]) is not int
                    or data["count"] != len(accounts)):
                raise ValueError
            return {"status": expected, "count": len(accounts)}
        except (OSError, ValueError, subprocess.SubprocessError):
            raise KeychainError("계정 저장소 도구의 결과를 확인할 수 없습니다.") from None

    def check(self, records):
        return self._summary(["check"], records, "ready")

    def migrate_keychain(self, records, *, allow_interaction=False):
        arguments = ["migrate-keychain", "--allow-interaction"] if allow_interaction else ["migrate-keychain"]
        return self._summary(arguments, records, "migrated")

    def login(self, record, plan, *, preflight_only=False):
        if type(preflight_only) is not bool:
            raise KeychainError("로그인 준비 확인 모드가 올바르지 않습니다.")
        if preflight_only:
            if (plan.get("platform") != "ios" or not isinstance(plan.get("device"), str)
                    or re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", plan["device"]) is None):
                raise KeychainError("준비 확인은 실행 중 iOS Simulator에서만 사용할 수 있습니다.")
            request = {"plan": plan}
        else:
            request = {"id": record["id"], "reference": reference(record["keychain_ref"]), "plan": plan}
        try:
            result = subprocess.run(
                [str(self.executable), "login-preflight" if preflight_only else "login"], input=json.dumps(request) + "\n",
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                timeout=360 if plan.get("platform") == "ios" else 180, check=False,
            )
            data = json.loads(result.stdout, object_pairs_hook=_unique_object)
            statuses = {"authenticated", "already_authenticated", "not_ready", "failed", "uncertain", "error"}
            if preflight_only:
                statuses = {"ready", "already_authenticated", "error", "uncertain"}
            if (not isinstance(data, dict) or set(data) - {"status", "code", "cleanup", "environment_verified"}
                    or data.get("status") not in statuses):
                raise ValueError
            if "environment_verified" in data and type(data["environment_verified"]) is not bool:
                raise ValueError
            # 환경 제어 없음(null)으로 등록한 프로필은 화면 환경을 관측하지 않으므로 false만 받는다.
            observed = plan.get("environment_selector", {}) is not None
            if data["status"] == "authenticated" and data.get("environment_verified") is not observed:
                raise ValueError
            if preflight_only and ((data["status"] == "ready" and data.get("environment_verified") is not observed)
                                   or (data["status"] == "already_authenticated" and data.get("environment_verified") is not False)):
                raise ValueError
            if data["status"] == "already_authenticated" and data.get("environment_verified") is True:
                raise ValueError
            if result.returncode and data["status"] in {"authenticated", "already_authenticated", "ready"}:
                raise ValueError
            if "cleanup" in data and (not isinstance(data["cleanup"], str) or data["cleanup"] not in {"cleared", "failed", "unavailable", "unconfirmed"}):
                raise ValueError
            if "code" in data and (not isinstance(data["code"], str) or data["code"] not in LOGIN_CODES):
                raise ValueError
            return data
        except subprocess.TimeoutExpired:
            return {"status": "uncertain", "code": "timed_out", "cleanup": "unconfirmed",
                    "environment_verified": False}
        except (OSError, ValueError, subprocess.SubprocessError):
            raise KeychainError("앱 로그인 도구의 결과를 확인할 수 없습니다.") from None


LOGIN_CODES = {
    "invalid_arguments", "invalid_plan", "invalid_reference", "foreign_reference", "keychain_failed",
    "invalid_keychain_result", "driver_not_prepared", "driver_changed", "device_not_found",
    "device_not_ready", "automation_unavailable", "wrong_app", "field_not_found", "ambiguous_field",
    "environment_selection_required", "environment_not_verified", "input_failed", "submit_failed",
    "login_not_verified", "input_closed", "input_timeout", "internal_error", "transport_failed",
    "runner_busy", "runner_not_ready", "ios_not_prepared", "android_not_prepared", "timed_out",
    "APP_NOT_FOREGROUND", "ENVIRONMENT_NOT_VERIFIED", "INPUT_UNAVAILABLE", "INVALID_CREDENTIALS",
    "LOGIN_VIEW_NOT_READY", "LOGIN_VIEW_READY", "NO_AUTHORIZED_PROTOCOL", "RUNNER_BUSY", "SESSION_REUSED",
    "UI_FAILURE", "SUCCESS_OBSERVED", "SUCCESS_NOT_OBSERVED", "app_not_allowed", "authentication_unconfirmed",
    "LOGIN_VIEW_STILL_PRESENT", "SUCCESS_MARKER_MISSING", "SUCCESS_MARKER_NOT_UNIQUE_OR_HITTABLE",
    "driver_not_trusted", "environment_not_unique", "environment_unverifiable", "input_too_large",
    "invalid_credentials_input", "invalid_login_plan", "invalid_login_request", "ios_runner_unavailable",
    "login_elements_not_unique", "login_safety_self_test_failed", "password_input_failed",
    "runner_ownership_unavailable", "screen_unavailable", "secure_login_failed", "self_check_failed",
    "transport_closed", "transport_response_invalid", "transport_start_failed", "transport_timeout",
    "tree_limit", "unsafe_login_fields", "unsafe_submit", "unsupported_sdk", "usb_device_unavailable",
    "usb_protocol_invalid", "usb_unavailable", "username_input_failed",
    "selectors_not_allowed", "runner_inventory_unavailable", "ios_runner_cleanup_unconfirmed",
    "transport_not_trusted",
    "ENVIRONMENT_FIELDS_NOT_READY", "ENVIRONMENT_NOT_UNIQUE", "ENVIRONMENT_NOT_BUTTON",
    "ENVIRONMENT_NOT_ENABLED", "ENVIRONMENT_NOT_HITTABLE", "ENVIRONMENT_NOT_SELECTED",
    "ENVIRONMENT_ALIASES_LOGIN_FIELD",
    "storage_not_ready", "storage_failed", "invalid_credentials", "ios_target_unverified",
    "simulator_not_booted", "app_instance_changed", "ios_artifact_cleanup_unconfirmed",
}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result
