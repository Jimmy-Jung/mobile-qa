#!/usr/bin/env python3
"""AI가 사용하는 프로젝트 계정 저장·조회. Author: JunyoungJung · 2026-10-29."""

import argparse
import getpass
import json
from pathlib import Path
import sys

from account_catalog import Catalog, device_binding, project_key
from credential_store import CredentialStore, credentials


def label(value):
    import re
    if not isinstance(value, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value) is None:
        raise ValueError("별칭·환경·용도·기능은 영문 소문자·숫자·하이픈 1~64자로 지정하세요.")
    return value


def public_record(record):
    return {key: value for key, value in record.items() if key != "keychain_ref"}


def input_credentials(args):
    if args.credentials_stdin:
        return credentials(json.load(sys.stdin))
    if not sys.stdin.isatty():
        raise ValueError("자동 저장에는 --credentials-stdin으로 아이디·비밀번호 JSON을 전달하세요.")
    return credentials({"username": input("아이디: "), "password": getpass.getpass("비밀번호: ")})


def selected_record(catalog, name):
    record = next((item for item in catalog.list() if item["id"] == label(name)), None)
    if record is None:
        raise ValueError("등록된 별칭을 찾지 못했습니다. list로 계정을 확인하세요.")
    return record


def register(args, catalog, store):
    name = label(args.name)
    if any(item["id"] == name for item in catalog.list()):
        raise ValueError("이미 등록된 별칭입니다. update로 갱신하세요.")
    project = project_key(args.project)
    environment, role = label(args.environment), label(args.role)
    record = {
        "id": name, "project": project, "environment": environment, "role": role,
        "capabilities": sorted({label(item) for item in args.capability}),
        "related_projects": sorted({project_key(path) for path in args.related_project} - {project}),
        "related_environments": sorted({label(item) for item in args.related_environment} - {environment}),
        "device_binding": device_binding(json.loads(args.device_file.read_text()) if args.device_file else None),
    }
    value = store.read(selected_record(catalog, args.from_account)) if args.from_account else input_credentials(args)
    with store.registration(name, value) as locator:
        catalog.add({**record, "keychain_ref": locator})
    return {"status": "registered", "account": record}


def find(args, catalog):
    candidates = catalog.list(project_key(args.project))
    if args.environment:
        environment = label(args.environment)
        candidates = [item for item in candidates if environment in
                      [item["environment"], *item.get("related_environments", [])]]
    if args.role:
        candidates = [item for item in candidates if item["role"] == label(args.role)]
    required = {label(item) for item in args.capability}
    candidates = [item for item in candidates if required.issubset(item["capabilities"])]
    if args.platform or args.device:
        candidates = [item for item in candidates if (
            (binding := item.get("device_binding", item.get("download_device"))) is None
            or ((not args.platform or binding["platform"] == args.platform)
                and (not args.device or args.device in (binding["name"], binding["identifier"]))))]
    return {"status": "not_found" if not candidates else "selected" if len(candidates) == 1 else "ambiguous",
            "candidates": [public_record(item) for item in candidates]}


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    add = sub.add_parser("register", help="계정 저장. 전용 입력기나 빌드 불필요")
    add.add_argument("--project", type=Path, default=Path.cwd())
    add.add_argument("--name", required=True)
    add.add_argument("--environment", default="default")
    add.add_argument("--role", default="tester")
    source = add.add_mutually_exclusive_group()
    source.add_argument("--credentials-stdin", action="store_true", help="stdin JSON: username, password")
    source.add_argument("--from-account", help="기존 별칭의 계정값 재사용")
    add.add_argument("--capability", action="append", default=[])
    add.add_argument("--related-project", type=Path, action="append", default=[])
    add.add_argument("--related-environment", action="append", default=[])
    add.add_argument("--device-file", "--download-device-file", type=Path, dest="device_file")
    listing = sub.add_parser("list", help="저장 계정 목록")
    listing.add_argument("--project", type=Path)
    query = sub.add_parser("find", help="프로젝트 계정 검색. 환경·용도·기기는 선택 조건")
    query.add_argument("--project", type=Path, default=Path.cwd())
    query.add_argument("--environment")
    query.add_argument("--role")
    query.add_argument("--capability", action="append", default=[])
    query.add_argument("--platform", choices=["ios", "android"])
    query.add_argument("--device")
    get = sub.add_parser("get", help="선택한 별칭의 실제 아이디·비밀번호 반환")
    get.add_argument("name")
    edit = sub.add_parser("update", help="기존 별칭의 아이디·비밀번호 갱신")
    edit.add_argument("name")
    edit.add_argument("--credentials-stdin", action="store_true")
    sub.add_parser("check", help="목록과 계정 파일 확인. 네이티브 도구 불필요")
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # 이전 자동 입력기를 명시적으로 호출하는 기존 사용자와 호환한다.
    if argv and argv[0] in {"login", "register-profile", "migrate-keychain"}:
        from native_accounts import main as native_main
        return native_main(argv)
    try:
        args = parser().parse_args(argv)
        catalog, store = Catalog(), CredentialStore()
        if args.command == "register":
            result = register(args, catalog, store)
        elif args.command == "get":
            record = selected_record(catalog, args.name)
            result = {"status": "found", "account": public_record(record), **store.read(record)}
        elif args.command == "update":
            record = selected_record(catalog, args.name)
            store.update(record, input_credentials(args))
            result = {"status": "updated", "id": record["id"]}
        elif args.command == "find":
            result = find(args, catalog)
        elif args.command == "check":
            result = store.check(catalog.list())
        else:
            result = {"accounts": [public_record(item) for item in catalog.list(
                project_key(args.project) if args.project else None)]}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except UnicodeError:
        print(json.dumps({"status": "error", "message": "계정값과 로컬 파일의 UTF-8 인코딩을 확인하세요."}, ensure_ascii=False))
        return 1
    except (ValueError, OSError, EOFError) as error:
        print(json.dumps({"status": "error", "message": str(error) or "계정 입력을 확인하세요."}, ensure_ascii=False))
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
