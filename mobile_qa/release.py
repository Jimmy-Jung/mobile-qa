"""Immutable public package inventory. Author: JunyoungJung · 2026-10-22."""

import hashlib
import json
from pathlib import Path
import re


RELEASE_VERSION = "0.1.0"
ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {"plugin.json", "README.md", "README.en.md", "README.ko.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
              "SECURITY.md", "requirements.txt", ".gitignore"}
DIRECTORIES = {".claude-plugin", ".agents", ".github", "skills", "agents", "integrations",
               "mobile_qa", "scripts", "tests", "examples"}
EXTENSIONS = {".py", ".md", ".json", ".swift", ".java", ".yaml", ".yml", ".toml"}
PRIVATE_NAMES = {"credentials.json", "accounts.json", "config.json", "build.json"}
DOCUMENTATION_IMAGES = {"examples/design-review/" + name for name in (
    "reference.png", "candidate-before.png", "candidate-final.png",
    *[stage + "/" + artifact for stage in ("before", "final") for artifact in (
        "overlay-25.png", "overlay-50.png", "overlay-75.png", "diff.png", "mismatch-mask.png")],
)}


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Package JSON contains duplicate fields.")
        value[key] = item
    return value


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_object)


def package_files(root=ROOT):
    root = Path(root).resolve()
    files = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in {".git", "__pycache__"} for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError("Release packages cannot contain symlinks.")
        if not path.is_file() or path.name in {".DS_Store", "release.json"} or path.suffix == ".pyc":
            continue
        permitted = (str(relative) in ROOT_FILES or relative.as_posix() in DOCUMENTATION_IMAGES or (
            relative.parts[0] in DIRECTORIES and path.suffix in EXTENSIONS))
        if not permitted or path.name in PRIVATE_NAMES:
            raise ValueError("Unapproved file in the public package: " + str(relative))
        files[relative.as_posix()] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    if not files:
        raise ValueError("The public package is empty.")
    return files


def safety_check(root=ROOT):
    files = package_files(root)
    private_path = re.compile(r"/Users/[A-Za-z0-9_.-]+/|/Volumes/[A-Za-z0-9_.-]+")
    private_project = re.compile("mega" + "study|smart" + "learning", re.I)
    for name in files:
        if name in DOCUMENTATION_IMAGES:
            if not (Path(root) / name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Documentation images must be PNG files: " + name)
            continue
        source = (Path(root) / name).read_text(encoding="utf-8")
        if private_path.search(source) or private_project.search(source):
            raise ValueError("Private host or project reference in public file: " + name)
    return files


def verify_release(root=ROOT):
    root = Path(root).resolve()
    path = root / "release.json"
    if path.is_symlink():
        raise ValueError("Release inventory must be a regular package file.")
    try:
        inventory = read_json(path)
        if (set(inventory) != {"schema_version", "version", "agent_device_version", "files"}
                or type(inventory["schema_version"]) is not int or inventory["schema_version"] != 1
                or inventory["version"] != RELEASE_VERSION
                or inventory["agent_device_version"] != "0.21.30"
                or inventory["files"] != package_files(root)):
            raise ValueError("Release files differ from the inventory. Rebuild and review the package.")
        for manifest in (root / "plugin.json", root / ".claude-plugin/plugin.json"):
            data = read_json(manifest)
            if data.get("name") != "mobile-qa" or data.get("version") != RELEASE_VERSION:
                raise ValueError("Plugin manifests contain mixed release versions.")
    except (OSError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise ValueError("A complete, verified mobile-qa release is required.") from error
    return inventory


def current_digest():
    verify_release()
    return hashlib.sha256((ROOT / "release.json").read_bytes()).hexdigest()


def write_inventory(root=ROOT):
    """Maintainer operation, never called by setup, login or normal QA."""
    root = Path(root).resolve()
    inventory = {"schema_version": 1, "version": RELEASE_VERSION,
                 "agent_device_version": "0.21.30", "files": safety_check(root)}
    path = root / "release.json"
    if path.is_symlink():
        raise ValueError("Release inventory cannot be a symlink.")
    path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    verify_release(root)
    return inventory
