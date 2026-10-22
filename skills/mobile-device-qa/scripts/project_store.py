#!/usr/bin/env python3
"""기기 조작 없이 프로젝트 QA 저장 위치를 찾는다. Author: JunyoungJung · 2026-10-22."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from urllib.parse import unquote, urlsplit


def network_identity(scheme, host, port, path, user, query):
    defaults = {"https": 443, "http": 80, "ssh": 22, "git": 9418}
    port = port or defaults[scheme]
    path = path.rstrip("/").removesuffix(".git")
    if not path.strip("/"):
        raise ValueError("저장소 경로가 비어 있습니다.")
    hosted = host in {"github.com", "gitlab.com", "bitbucket.org"}
    if hosted and port == defaults[scheme] and (scheme == "https" or (scheme == "ssh" and user == "git")):
        path = path.lower() if host == "github.com" else path
        parts = ["hosted", host, path.strip("/")]
    else:
        user_key = hashlib.sha256(user.encode()).hexdigest() if user is not None else None
        query_key = hashlib.sha256(query.encode()).hexdigest() if query else None
        parts = [scheme, host, port, path, user_key, query_key]
    return "git:" + json.dumps(parts), path.rsplit("/", 1)[-1]


def normalize_remote(remote, project):
    """인증 원문을 노출하지 않고 저장소 식별값과 이름을 반환한다."""
    if not remote or any(ord(char) < 32 for char in remote):
        raise ValueError("저장소 주소 형식을 확인하세요.")
    if "://" in remote:
        parsed = urlsplit(remote)
        if parsed.scheme == "file":
            if parsed.netloc not in ("", "localhost"):
                raise ValueError("원격 file 주소는 지원하지 않습니다.")
            location = Path(unquote(parsed.path)).resolve()
            return "local:" + str(location), location.name.removesuffix(".git")
        if parsed.scheme not in ("https", "http", "ssh", "git") or not parsed.hostname:
            raise ValueError("확인할 수 있는 Git 저장소 주소를 사용하세요.")
        return network_identity(parsed.scheme, parsed.hostname.lower(), parsed.port,
                                parsed.path, parsed.username, parsed.query)
    scp = re.fullmatch(r"(?:([^/@:]+)@)?(\[[^\]]+\]|[^/:]+):(.+)", remote)
    if scp:
        return network_identity("ssh", scp.group(2).strip("[]").lower(), 22, scp.group(3), scp.group(1), "")
    location = (project / Path(remote).expanduser()).resolve()
    return "local:" + str(location), location.name.removesuffix(".git")


def git(project, *args):
    return subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True,
        timeout=10, env={**{key: value for key, value in os.environ.items()
                           if not key.startswith("GIT_")}, "GIT_OPTIONAL_LOCKS": "0"},
    )


def project_identity(project):
    common = git(project, "rev-parse", "--git-common-dir")
    if common.returncode:
        if any((parent / ".git").exists() for parent in (project, *project.parents)):
            raise ValueError("Git 프로젝트 정보를 확인할 수 없습니다.")
        return "path:" + str(project), project.name, "physical_path"
    common_dir = (project / common.stdout.strip()).resolve()
    origin = git(project, "config", "--local", "--get", "remote.origin.url")
    if origin.returncode == 0:
        base = common_dir.parent if common_dir.name == ".git" else common_dir
        identity, name = normalize_remote(origin.stdout.strip(), base)
        return identity, name, "origin"
    if origin.returncode != 1:
        raise ValueError("Git origin 설정을 확인할 수 없습니다.")
    name = common_dir.parent.name if common_dir.name == ".git" else common_dir.name.removesuffix(".git")
    return "git-common:" + str(common_dir), name, "git_common_dir"


def locate(project, storage_root=None, *, create=False):
    project = Path(project).expanduser().resolve()
    if not project.is_dir():
        raise ValueError("존재하는 프로젝트 디렉터리를 지정하세요.")
    identity, name, source = project_identity(project)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "project"
    key = slug + "-" + hashlib.sha256(identity.encode()).hexdigest()[:12]
    root = Path(storage_root) if storage_root is not None else (
        Path.home() / "Library/Application Support/mobile-device-qa/projects"
    )
    root = root.expanduser().resolve()
    folder = root / key
    if folder.is_symlink() or not folder.resolve().is_relative_to(root):
        raise ValueError("프로젝트 저장 위치가 전용 폴더를 벗어납니다.")
    if folder.exists() and not folder.is_dir():
        raise ValueError("프로젝트 저장 위치가 디렉터리가 아닙니다.")
    if create:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.is_dir():
        info = folder.stat()
        if (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077
                or not os.access(folder, os.R_OK | os.W_OK | os.X_OK)):
            raise ValueError("기존 프로젝트 폴더의 소유권·비공개 권한·접근 권한을 확인하세요.")
    return {
        "project_key": key, "store_dir": str(folder),
        "identity_source": source, "exists": folder.is_dir(),
    }


def main():
    parser = argparse.ArgumentParser(description="프로젝트별 로컬 QA 지식 저장 위치")
    parser.add_argument("--project", required=True, help="현재 프로젝트 또는 checkout 경로")
    parser.add_argument("--init", action="store_true", help="전용 프로젝트 폴더만 생성")
    args = parser.parse_args()
    try:
        result = locate(args.project, create=args.init)
    except (ValueError, OSError, subprocess.SubprocessError):
        print(json.dumps({"error": "프로젝트 식별 또는 QA 저장 위치 확인 실패. 경로·Git 설정·권한을 확인하세요."}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
