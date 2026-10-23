#!/usr/bin/env python3
"""프로젝트 테스트 계정 등록·검색. Author: JunyoungJung · 2026-10-20."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from account_catalog import Catalog, bound_device, device_binding, project_key
from keychain_store import Cancelled, Keychain, KeychainError, LOGIN_CODES, STORAGE_CODES
from login_plan import build_login_plan, register_profile


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # 잘못 전달된 비밀번호까지 argparse 오류에 재출력되지 않게 한다.
        raise ValueError("명령 인자가 올바르지 않습니다. --help를 확인하세요.")


def dialog(prompt):
    script = (
        'on run argv\n'
        'set answer to display dialog (item 1 of argv) default answer (item 2 of argv) '
        'with title "테스트 계정 등록" buttons {"취소", "확인"} '
        'default button "확인" cancel button "취소"\n'
        'return text returned of answer\nend run'
    )
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", script, prompt, ""],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        if "(-128)" in result.stderr:
            raise Cancelled()
        raise ValueError("등록 정보 입력 창을 열 수 없습니다. Mac의 로그인 상태를 확인하세요.")
    return result.stdout.removesuffix("\n")


def slug(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value):
        raise ValueError("계정 별칭·환경·용도·기능은 영문 소문자·숫자·하이픈 1~64자여야 합니다.")
    return value


def context(record):
    environments = ", ".join([record["environment"], *record.get("related_environments", [])])
    projects = ", ".join([record["project"], *record.get("related_projects", [])])
    device = bound_device(record)
    suffix = f" / 등록 기기: {device['name']}" if device else ""
    return f"{projects} / {environments} / {record['role']} / {record['id']}" + suffix


def public_record(record):
    device = bound_device(record)
    result = {key: value for key, value in record.items() if key != "keychain_ref"}
    if device:
        binding_key = "device_binding" if "device_binding" in record else "download_device"
        result[binding_key] = {
            "platform": device["platform"], "name": device["name"],
            "identifier_saved": device["identifier"] is not None,
        }
    return result


def register(args, catalog):
    project = project_key(args.project)
    environment = slug(args.environment or dialog("테스트 환경을 입력하세요: production / staging / development"))
    role = slug(args.role or dialog("계정 용도를 입력하세요. 예: tester / member / admin"))
    account_id = slug(args.name or dialog("계정 별칭을 입력하세요. 실제 아이디 대신 구별용 이름을 쓰세요. 예: sample-prod-tester"))
    capabilities = sorted(set(slug(item) for item in args.capability))
    related_projects = sorted({project_key(path) for path in args.related_project} - {project})
    related_environments = sorted({slug(item) for item in args.related_environment} - {environment})
    device_file = args.device_file or args.download_device_file
    device = device_binding(json.loads(device_file.read_text()) if device_file else None)
    binding_key = "download_device" if args.download_device_file else "device_binding"
    if any(record["id"] == account_id for record in catalog.list()):
        raise ValueError("같은 별칭이 이미 있습니다. 비밀값 갱신은 update를 사용하세요.")
    record = {
        "id": account_id, "project": project, "environment": environment,
        "related_projects": related_projects, "related_environments": related_environments,
        "role": role, "capabilities": capabilities, binding_key: device,
    }
    store = Keychain()
    with store.registration(account_id, context(record)) as persistent_ref:
        catalog.add({**record, "keychain_ref": persistent_ref})
    return {"status": "registered", "account": public_record(record)}


def update(args, catalog):
    account_id = slug(args.name)
    record = next((item for item in catalog.list() if item["id"] == account_id), None)
    if record is None:
        raise ValueError("등록된 계정 별칭을 찾지 못했습니다.")
    Keychain().update(account_id, record["keychain_ref"], context(record))
    return {"status": "updated", "id": account_id}


def storage_summary(args, catalog):
    records = catalog.list()
    store = Keychain()
    if args.command == "check":
        result = store.check(records)
        expected = "ready"
    else:
        result = store.migrate_keychain(records, allow_interaction=args.allow_interaction)
        expected = "migrated"
    if (not isinstance(result, dict) or set(result) != {"status", "count"}
            or result["status"] != expected or type(result["count"]) is not int
            or result["count"] != len(records)):
        raise KeychainError("계정 저장소 확인 응답 형식이 올바르지 않습니다.")
    return {"status": expected, "count": len(records)}


def matches_device(record, args):
    registered = bound_device(record)
    if registered is None or registered["platform"] != args.platform:
        return False
    if registered["identifier"] is not None:
        return registered["identifier"] == args.device and (
            args.device_name is None or registered["name"] == args.device_name
        )
    return registered["name"] == (args.device_name or args.device)


def login(args, catalog):
    project = project_key(args.project)
    options = {"app_id": args.app_id, "role": slug(args.role), "project_path": args.project}
    if getattr(args, "login_profile", None) is not None:
        options["login_profile"] = args.login_profile
    plan = build_login_plan(project, slug(args.environment), args.platform, args.device, **options)
    if args.device_name is not None:
        device_binding({"platform": args.platform, "name": args.device_name, "identifier": args.device})
    account_id = slug(args.account) if args.account is not None else None
    capabilities = sorted(set(slug(item) for item in args.capability))
    found = catalog.find(
        project, args.environment, args.role, capabilities,
        device=args.device_name or args.device, platform=args.platform,
    )
    candidates = [record for record in found["candidates"]
                  if bound_device(record) is None or matches_device(record, args)]
    if account_id is not None:
        candidates = [record for record in candidates if record["id"] == account_id]
    if len(candidates) != 1:
        return {"status": "ambiguous" if candidates else "not_found"}

    record = candidates[0]
    response = Keychain().login(record, plan)
    statuses = {"authenticated", "already_authenticated", "not_ready", "failed", "uncertain", "error"}
    if not isinstance(response, dict) or response.get("status") not in statuses:
        raise KeychainError("로그인 도구 응답 형식이 올바르지 않습니다.")
    if response["status"] == "already_authenticated" and response.get("environment_verified") is True:
        raise KeychainError("기존 로그인 세션의 서버 환경은 자동 검증할 수 없습니다.")
    result = {"status": response["status"], "account": record["id"]}
    if "environment_verified" in response:
        if type(response["environment_verified"]) is not bool:
            raise KeychainError("로그인 환경 확인 응답 형식이 올바르지 않습니다.")
        result["environment_verified"] = response["environment_verified"]
    if "code" in response:
        if response["code"] not in LOGIN_CODES:
            raise KeychainError("로그인 오류 코드 형식이 올바르지 않습니다.")
        result["code"] = response["code"]
    if "cleanup" in response:
        if response["cleanup"] not in {"cleared", "failed", "unavailable", "unconfirmed"}:
            raise KeychainError("로그인 입력 정리 응답 형식이 올바르지 않습니다.")
        result["cleanup"] = response["cleanup"]
    return result


def parser():
    result = SafeParser(description="이 Mac의 로컬 저장소에 테스트 계정 등록·메타데이터 검색. 비밀값을 출력하지 않습니다.")
    sub = result.add_subparsers(dest="command", required=True, parser_class=SafeParser)
    add = sub.add_parser("register", help="로컬 보안 입력 창으로 새 계정 등록")
    add.add_argument("--project", type=Path, default=Path.cwd())
    add.add_argument("--name")
    add.add_argument("--environment")
    add.add_argument("--role")
    add.add_argument("--capability", action="append", default=[])
    add.add_argument("--related-project", type=Path, action="append", default=[])
    add.add_argument("--related-environment", action="append", default=[])
    binding = add.add_mutually_exclusive_group()
    binding.add_argument("--device-file", type=Path, help="계정을 귀속할 기기 메타데이터 JSON")
    binding.add_argument("--download-device-file", type=Path, help="기존 기기 귀속 필드로 등록하는 호환 옵션")
    find = sub.add_parser("find", help="프로젝트·환경·용도·기능으로 계정 선택")
    find.add_argument("--project", type=Path, default=Path.cwd())
    find.add_argument("--environment", required=True)
    find.add_argument("--role", required=True)
    find.add_argument("--capability", action="append", default=[])
    find.add_argument("--device", help="계정 귀속을 확인할 기기의 정확한 이름 또는 식별자")
    find.add_argument("--platform", choices=["ios", "android"], help="계정 귀속을 확인할 기기의 운영체제")
    listing = sub.add_parser("list", help="등록된 비밀 아닌 계정 정보 조회")
    listing.add_argument("--project", type=Path)
    edit = sub.add_parser("update", help="기존 계정의 아이디·비밀번호만 보안 입력으로 갱신")
    edit.add_argument("name")
    sub.add_parser("check", help="등록된 모든 계정의 로컬 저장소를 비밀값 출력 없이 확인")
    migrate = sub.add_parser("migrate-keychain", help="기존 키체인 계정을 로컬 저장소로 이전하고 원본은 보존")
    migrate.add_argument("--allow-interaction", action="store_true", help="이번 이전에만 macOS 키체인 접근 승인 창 허용")
    profile = sub.add_parser("register-profile", help="검토한 비밀 없는 로그인 프로필을 현재 프로젝트에 명시적으로 신뢰 등록")
    profile.add_argument("--project", type=Path, required=True)
    profile.add_argument("--file", type=Path, required=True)
    sign_in = sub.add_parser("login", help="선택한 계정으로 확인된 실기기 앱에 보안 로그인")
    sign_in.add_argument("--project", type=Path, required=True)
    sign_in.add_argument("--environment", required=True)
    sign_in.add_argument("--role", required=True)
    sign_in.add_argument("--capability", action="append", default=[])
    sign_in.add_argument("--platform", choices=["ios", "android"], required=True)
    sign_in.add_argument("--device", required=True, help="현재 실기기의 정확한 물리 식별자")
    sign_in.add_argument("--device-name", help="저장된 계정 귀속 기기의 정확한 이름")
    sign_in.add_argument("--account", help="조회 범위 안에서 사용할 계정 별칭")
    sign_in.add_argument("--app-id", help="신뢰할 수 있는 호출자가 확인한 허용 앱 식별자")
    sign_in.add_argument("--login-profile", required=True, help="현재 프로젝트에 명시적으로 등록한 프로필 이름")
    return result


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        if args.command == "register-profile":
            result = register_profile(args.project, args.file)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        catalog = Catalog()
        if args.command == "register":
            result = register(args, catalog)
        elif args.command == "update":
            result = update(args, catalog)
        elif args.command == "login":
            result = login(args, catalog)
        elif args.command in {"check", "migrate-keychain"}:
            result = storage_summary(args, catalog)
        elif args.command == "find":
            result = catalog.find(project_key(args.project), slug(args.environment), slug(args.role), [slug(item) for item in args.capability], device=args.device, platform=args.platform)
            result = {**result, "candidates": [public_record(item) for item in result["candidates"]]}
        else:
            result = {"accounts": [public_record(item) for item in catalog.list(project_key(args.project) if args.project else None)]}
        print(json.dumps(result, ensure_ascii=False))
        if args.command == "login":
            return 0 if (
                result["status"] == "authenticated"
                and result.get("environment_verified") is True
            ) else 1
        return 0
    except Cancelled:
        print(json.dumps({"status": "cancelled"}))
        return 2
    except (ValueError, KeychainError, OSError) as error:
        # OS나 외부 처리의 예외 본문에는 입력값이 섞일 수 있어 그대로 반환하지 않는다.
        result = {"status": "error", "message": "계정 작업에 실패했습니다. 입력 형식·목록 권한·로컬 저장소 상태를 확인하세요."}
        if isinstance(error, KeychainError) and error.code in STORAGE_CODES:
            result["code"] = error.code
        print(json.dumps(result, ensure_ascii=False))
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"status": "cancelled"}))
        return 130
    except Exception:
        print(json.dumps({"status": "error", "message": "계정 처리 중 내부 오류가 발생했습니다. 비밀값을 포함한 로그를 제출하지 마세요."}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
