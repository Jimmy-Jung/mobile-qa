"""기존 로컬 계정 파일 읽기·쓰기. Author: JunyoungJung · 2026-10-29."""

from contextlib import contextmanager
import base64
import json
import os
from pathlib import Path
import secrets

from account_catalog import _check_private, _directory, _lock, _unique_object, reference


DEFAULT_PATH = Path.home() / ".config" / "agent-test-accounts" / "credentials.json"


def credentials(value):
    if (not isinstance(value, dict) or set(value) != {"username", "password"}
            or not all(isinstance(item, str) and 0 < len(item.encode("utf-8")) <= 16384
                       for item in value.values())):
        raise ValueError("아이디와 비밀번호를 문자열로 입력하세요.")
    return dict(value)


class CredentialStore:
    def __init__(self, path=None):
        self.path = Path(os.path.abspath(Path(path or DEFAULT_PATH).expanduser()))

    @contextmanager
    def transaction(self):
        with _directory(self.path.parent) as directory:
            # 기존 Swift 저장 도구와 같은 잠금을 사용한다.
            with _lock(directory, "credentials.lock"):
                yield directory

    def load(self, directory):
        try:
            descriptor = os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
        except FileNotFoundError:
            return []
        try:
            _check_private(os.fstat(descriptor))
            with os.fdopen(descriptor, encoding="utf-8", closefd=False) as stream:
                value = json.load(stream, object_pairs_hook=_unique_object)
        finally:
            os.close(descriptor)
        if (not isinstance(value, dict) or set(value) != {"version", "accounts"}
                or type(value["version"]) is not int or value["version"] != 1
                or not isinstance(value["accounts"], list) or len(value["accounts"]) > 1024):
            raise ValueError("계정 파일 형식을 확인하세요.")
        entries = value["accounts"]
        for item in entries:
            if (not isinstance(item, dict) or set(item) != {"id", "reference", "username", "password"}
                    or not isinstance(item["id"], str) or not item["id"]):
                raise ValueError("계정 파일 항목을 확인하세요.")
            reference(item["reference"])
            credentials({key: item[key] for key in ("username", "password")})
        if (len({item["id"] for item in entries}) != len(entries)
                or len({item["reference"] for item in entries}) != len(entries)):
            raise ValueError("계정 파일에 중복 항목이 있습니다.")
        return entries

    def save(self, directory, entries):
        if len(entries) > 1024:
            raise ValueError("계정 파일의 최대 등록 수를 초과했습니다.")
        data = json.dumps({"version": 1, "accounts": entries}, ensure_ascii=False, indent=2) + "\n"
        if len(data.encode("utf-8")) > 40 * 1024 * 1024:
            raise ValueError("계정 파일의 최대 크기를 초과했습니다.")
        name = ".credentials-" + secrets.token_hex(12) + ".tmp"
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(descriptor)
            os.rename(name, self.path.name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(descriptor)
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass

    def read(self, record):
        with self.transaction() as directory:
            for item in self.load(directory):
                if item["id"] == record["id"] and item["reference"] == record["keychain_ref"]:
                    return {key: item[key] for key in ("username", "password")}
        raise ValueError("저장된 계정값을 찾지 못했습니다.")

    @contextmanager
    def registration(self, account_id, value):
        value = credentials(value)
        with self.transaction() as directory:
            entries = self.load(directory)
            if any(item["id"] == account_id for item in entries):
                raise ValueError("이미 등록된 별칭입니다. update를 사용하세요.")
            locator = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
            self.save(directory, [*entries, {"id": account_id, "reference": locator, **value}])
            try:
                yield locator
            except BaseException:
                self.save(directory, entries)
                raise

    def update(self, record, value):
        value = credentials(value)
        with self.transaction() as directory:
            entries = self.load(directory)
            for index, item in enumerate(entries):
                if item["id"] == record["id"] and item["reference"] == record["keychain_ref"]:
                    entries[index] = {**item, **value}
                    self.save(directory, entries)
                    return
        raise ValueError("갱신할 계정값을 찾지 못했습니다.")

    def check(self, records):
        with self.transaction() as directory:
            entries = self.load(directory)
        if {(item["id"], item["reference"]) for item in entries} != {
                (item["id"], item["keychain_ref"]) for item in records}:
            raise ValueError("계정 목록과 저장된 계정값이 일치하지 않습니다.")
        return {"status": "ready", "count": len(records)}
