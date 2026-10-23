"""Private project account metadata. Author: JunyoungJung. Date: 2026-10-20."""

from __future__ import annotations

from contextlib import contextmanager
import base64
import binascii
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import unicodedata
from urllib.parse import urlsplit


DEFAULT_CATALOG_PATH = Path.home() / ".config" / "agent-test-accounts" / "accounts.json"
_FIELDS = {"id", "project", "environment", "role", "capabilities", "keychain_ref"}
_OPTIONAL_FIELDS = {"related_projects", "related_environments", "device_binding", "download_device"}
_LABEL = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]+)?\Z")


def _label(value: object) -> bool:
    return isinstance(value, str) and _LABEL.fullmatch(value) is not None


def _project(value: object) -> bool:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        return False
    if value.startswith("path:"):
        local = value[5:]
        return os.path.isabs(local) and os.path.normpath(local) == local
    host, separator, path = value.partition("/")
    return bool(
        separator and _HOST.fullmatch(host) and path
        and not any(char in value for char in "@?#\\")
        and all(part and part not in {".", ".."} for part in path.split("/"))
        and not path.endswith(".git")
    )


def _capabilities(value: object) -> list[str]:
    if not isinstance(value, list) or not all(_label(item) for item in value):
        raise ValueError("Capabilities must be a list of valid labels.")
    if len(set(value)) != len(value):
        raise ValueError("Capabilities must be unique.")
    return sorted(value)


def _related_projects(value: object, project: str) -> list[str]:
    if not isinstance(value, list) or not all(_project(item) for item in value):
        raise ValueError("Related projects must be a list of valid project keys.")
    if len(set(value)) != len(value) or project in value:
        raise ValueError("Related projects must be unique and exclude the primary project.")
    return sorted(value)


def _related_environments(value: object, environment: str) -> list[str]:
    if not isinstance(value, list) or not all(_label(item) for item in value):
        raise ValueError("Related environments must be a list of valid labels.")
    if len(set(value)) != len(value) or environment in value:
        raise ValueError("Related environments must be unique and exclude the primary environment.")
    return sorted(value)


def _device_text(value: object, limit: int) -> bool:
    return (
        isinstance(value, str) and 1 <= len(value) <= limit and bool(value.strip())
        and not any(unicodedata.category(char) == "Cc" for char in value)
    )


def device_binding(value: object) -> dict | None:
    """Validate user-reported device metadata without inspecting a connected device."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"platform", "name", "identifier"}:
        raise ValueError("Device binding metadata has unsupported or missing fields.")
    if (
        value["platform"] not in ("ios", "android")
        or not _device_text(value["name"], 128)
        or (value["identifier"] is not None and not _device_text(value["identifier"], 256))
    ):
        raise ValueError("Device binding metadata is invalid.")
    return dict(value)


def download_device(value: object) -> dict | None:
    """Compatibility validator for the original metadata field."""
    return device_binding(value)


def bound_device(record: dict) -> dict | None:
    """Resolve one explicit binding, preserving the legacy field without migration."""
    if "device_binding" in record and "download_device" in record:
        raise ValueError("Account metadata must use only one device binding field.")
    return device_binding(record.get("device_binding", record.get("download_device")))


def _record(value: object) -> dict:
    if (
        not isinstance(value, dict) or not _FIELDS.issubset(value)
        or not set(value).issubset(_FIELDS | _OPTIONAL_FIELDS)
    ):
        raise ValueError("Account metadata has unsupported or missing fields.")
    if not all(_label(value[field]) for field in ("id", "environment", "role")):
        raise ValueError("Account metadata contains an invalid label.")
    if not _project(value["project"]):
        raise ValueError("Account metadata contains an invalid project key.")
    reference(value["keychain_ref"])
    binding_key = "device_binding" if "device_binding" in value else "download_device"
    return {
        **value,
        "capabilities": _capabilities(value["capabilities"]),
        "related_projects": _related_projects(value.get("related_projects", []), value["project"]),
        "related_environments": _related_environments(value.get("related_environments", []), value["environment"]),
        binding_key: bound_device(value),
    }


def reference(value: object) -> str:
    """Validate an opaque credential locator without resolving its secret data."""
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 8192:
            raise ValueError
        decoded = base64.b64decode(value, validate=True)
        if not decoded or base64.b64encode(decoded).decode("ascii") != value:
            raise ValueError
    except (ValueError, binascii.Error):
        raise ValueError("Credential reference is invalid.") from None
    return value


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Catalog JSON contains duplicate fields.")
        result[key] = value
    return result


def _remote_key(remote: str) -> str:
    if not remote or any(char.isspace() for char in remote):
        raise ValueError("Project origin is invalid.")
    if "://" not in remote:
        match = re.fullmatch(r"(?:[^/@:]+@)?([^/:]+):(.+)", remote)
        if match is None:
            raise ValueError("Project origin is unsupported.")
        remote = "ssh://" + match[1] + "/" + match[2]
    try:
        parsed = urlsplit(remote)
        if parsed.scheme not in {"https", "http", "ssh", "git"} or not parsed.hostname:
            raise ValueError
        host = parsed.hostname.lower()
        if parsed.port is not None:
            host += ":" + str(parsed.port)
        path = parsed.path.strip("/").removesuffix(".git")
        key = host + "/" + path
        if not _project(key):
            raise ValueError
        return key
    except ValueError:
        raise ValueError("Project origin is invalid.") from None


def project_key(path: Path) -> str:
    """Return a credential-free origin key, or the physical path without an origin."""
    try:
        physical = Path(path).expanduser().resolve(strict=True)
        if not physical.is_dir():
            raise ValueError
        result = subprocess.run(
            ["git", "-C", str(physical), "config", "--local", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=10, check=False,
            env={**{key: value for key, value in os.environ.items() if not key.startswith("GIT_")}, "GIT_OPTIONAL_LOCKS": "0"},
        )
        if result.returncode in {1, 128}:
            return "path:" + str(physical)
        if result.returncode != 0:
            raise ValueError
        return _remote_key(result.stdout.strip())
    except (OSError, RuntimeError, UnicodeError, subprocess.SubprocessError, ValueError):
        raise ValueError("Project identity could not be determined safely.") from None


def _check_private(info: os.stat_result, directory: bool = False) -> None:
    expected_mode = 0o700 if directory else 0o600
    correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (
        not correct_type or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != expected_mode
        or (not directory and info.st_nlink != 1)
    ):
        raise ValueError("Catalog storage must be owned by this user with private permissions.")


@contextmanager
def _directory(path: Path):
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            created = False
            try:
                os.mkdir(component, mode=0o700, dir_fd=descriptor)
                created = True
            except FileExistsError:
                pass
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
            if created:
                os.fchmod(descriptor, 0o700)
        _check_private(os.fstat(descriptor), directory=True)
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _lock(directory: int, name: str):
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        descriptor = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
    except FileExistsError:
        descriptor = os.open(name, flags, dir_fd=directory)
    else:
        os.fchmod(descriptor, 0o600)
    try:
        _check_private(os.fstat(descriptor))
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


class Catalog:
    """Store metadata only; passwords and usernames belong in the credential store."""

    def __init__(self, path: Path = DEFAULT_CATALOG_PATH):
        try:
            self.path = Path(os.path.abspath(Path(path).expanduser()))
        except (OSError, TypeError, ValueError):
            raise ValueError("Catalog path is invalid.") from None
        with self._transaction() as directory:
            if self._read(directory) is None:
                self._write(directory, [])

    @contextmanager
    def _transaction(self):
        try:
            with _directory(self.path.parent) as directory:
                with _lock(directory, self.path.name + ".lock"):
                    yield directory
        except OSError:
            raise ValueError("Catalog storage is not safe or accessible.") from None

    def _read(self, directory: int) -> list[dict] | None:
        try:
            descriptor = os.open(
                self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
        except FileNotFoundError:
            return None
        try:
            _check_private(os.fstat(descriptor))
            with os.fdopen(descriptor, encoding="utf-8", closefd=False) as stream:
                contents = json.load(stream, object_pairs_hook=_unique_object)
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            raise ValueError("Catalog JSON is invalid.") from None
        finally:
            os.close(descriptor)
        if not isinstance(contents, list):
            raise ValueError("Catalog must contain a list of account metadata.")
        records = [_record(item) for item in contents]
        if len({item["id"] for item in records}) != len(records):
            raise ValueError("Account IDs must be globally unique.")
        return records

    def _write(self, directory: int, records: list[dict], on_commit=None) -> None:
        temporary = ".accounts-" + secrets.token_hex(12)
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=directory,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as stream:
                json.dump(records, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(descriptor)
            os.replace(temporary, self.path.name, src_dir_fd=directory, dst_dir_fd=directory)
            if on_commit is not None:
                on_commit()
        finally:
            os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    def list(self, project: str | None = None) -> list[dict]:
        """Return independent records, optionally restricted to an exact project key."""
        if project is not None and not _project(project):
            raise ValueError("Project key is invalid.")
        with self._transaction() as directory:
            records = self._read(directory)
            if records is None:
                raise ValueError("Catalog disappeared after initialization.")
        return [
            item for item in records
            if project is None or item["project"] == project or project in item["related_projects"]
        ]

    def add(self, record: dict) -> None:
        """Atomically register one metadata record under the shared file lock."""
        validated = _record(record)
        committed = False

        def mark_committed():
            nonlocal committed
            committed = True

        try:
            with self._transaction() as directory:
                records = self._read(directory)
                if records is None:
                    raise ValueError("Catalog disappeared after initialization.")
                if any(item["id"] == validated["id"] for item in records):
                    raise ValueError("Account ID is already registered.")
                self._write(directory, [*records, validated], on_commit=mark_committed)
        except ValueError:
            # 확정 뒤 close 오류로 계정 보상을 수행하면 이미 저장된 목록이 깨진다.
            if not committed:
                raise

    def find(
        self, project: str, environment: str, role: str, capabilities: list[str],
        *, device: str | None = None, platform: str | None = None,
    ) -> dict:
        """Select only an unambiguous exact match with all required capabilities."""
        if not _project(project):
            raise ValueError("Project key is invalid.")
        if not _label(environment) or not _label(role):
            raise ValueError("Environment and role must be valid labels.")
        required = set(_capabilities(capabilities))
        if device is not None and not _device_text(device, 256):
            raise ValueError("Device selection is invalid.")
        if platform is not None and platform not in ("ios", "android"):
            raise ValueError("Device platform selection is invalid.")
        candidates = [
            item for item in self.list(project)
            if (item["environment"] == environment or environment in item["related_environments"])
            and item["role"] == role
            and required.issubset(item["capabilities"])
        ]
        bindings = [(item, bound_device(item)) for item in candidates]
        if any(binding is not None for _, binding in bindings) and (device is None or platform is None):
            return {"status": "needs_device", "candidates": []}
        candidates = [
            item for item, binding in bindings if binding is None
            or (binding["platform"] == platform and device in (binding["name"], binding["identifier"]))
        ]
        status = "not_found" if not candidates else "selected" if len(candidates) == 1 else "ambiguous"
        return {"status": status, "candidates": candidates}
