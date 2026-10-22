"""User-selected local storage. Author: JunyoungJung · 2026-10-22."""

import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
import uuid


class StorageError(ValueError):
    """Storage cannot safely be used; callers must not choose another location."""


def _path(value):
    try:
        path = Path(value).expanduser()
        if not path.is_absolute() or any(ord(char) < 32 for char in str(path)):
            raise ValueError()
        return path
    except (TypeError, ValueError, OSError):
        raise StorageError("Use an absolute local folder path.") from None


def _no_symlinks(path):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise StorageError("Symbolic links cannot be used for storage or configuration.")


def _config_path(config_path):
    path = _path(config_path if config_path is not None else os.environ.get(
        "MOBILE_QA_CONFIG", Path.home() / "Library/Application Support/mobile-qa/config.json"))
    _no_symlinks(path)
    return path


def _private(path, *, directory=False):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise StorageError("Configuration must be user-owned and private (folder 0700, file 0600).")


def _settings(value):
    keys = {"version", "storage_dir", "volume_uuid", "mount_point"}
    if (not isinstance(value, dict) or set(value) != keys
            or type(value["version"]) is not int or value["version"] != 1):
        raise StorageError("Storage configuration has an unsupported format; run setup again.")
    try:
        storage = str(_path(value["storage_dir"]))
        mount = str(_path(value["mount_point"]))
        volume_uuid = str(uuid.UUID(value["volume_uuid"])).upper()
    except (TypeError, ValueError, AttributeError):
        raise StorageError("Storage configuration is invalid; run setup again.") from None
    return {"version": 1, "storage_dir": storage, "volume_uuid": volume_uuid, "mount_point": mount}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StorageError("Storage configuration contains duplicate fields.")
        result[key] = value
    return result


def load_settings(config_path=None):
    """Read settings without opening a dialog, creating folders, or writing files."""
    try:
        path = _config_path(config_path)
        if not path.exists():
            raise StorageError("Storage is not configured; run scripts/setup.py first.")
        _private(path.parent, directory=True)
        _private(path)
        with path.open("rb") as stream:
            data = stream.read(65537)
        if len(data) > 65536:
            raise StorageError("Storage configuration is too large.")
        return _settings(json.loads(data, object_pairs_hook=_unique_object))
    except StorageError:
        raise
    except (OSError, ValueError, UnicodeError):
        raise StorageError("Cannot read storage configuration; check its format and permissions.") from None


def _choose_folder():
    if sys.platform != "darwin":
        raise StorageError("Folder selection needs macOS; provide --storage-dir in headless use.")
    script = ('POSIX path of (choose folder with prompt '
              '"Select an existing folder on internal or external storage for Mobile QA artifacts")')
    try:
        result = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, timeout=300)
        if result.returncode:
            raise StorageError("Folder selection cancelled or unavailable; existing settings were kept. Use --storage-dir for headless setup.")
        return _path(result.stdout.decode("utf-8").rstrip("\n"))
    except StorageError:
        raise
    except (OSError, UnicodeError, subprocess.SubprocessError):
        raise StorageError("Folder selection unavailable; use --storage-dir for headless setup.") from None


def _volume_device(path):
    try:
        result = subprocess.run(["/bin/df", "-P", str(path)], capture_output=True, timeout=15)
        lines = result.stdout.decode("utf-8").splitlines()
        fields = lines[1].split() if len(lines) == 2 else []
        if result.returncode or len(fields) < 6 or not re.fullmatch(r"/dev/disk[0-9]+(?:s[0-9]+)*", fields[0]):
            raise StorageError("Cannot identify the folder's local volume; check its path and mounted storage.")
        return fields[0]
    except StorageError:
        raise
    except (OSError, UnicodeError, subprocess.SubprocessError):
        raise StorageError("Cannot identify the folder's local volume; check its path and mounted storage.") from None


def _selected_volume(path):
    try:
        _no_symlinks(path)
        if not path.is_dir() or path.resolve() != path or not os.access(path, os.W_OK | os.X_OK):
            raise StorageError("Select an existing writable physical folder on internal or external storage.")
        device = _volume_device(path)
        result = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", device],
                                capture_output=True, timeout=15)
        if result.returncode:
            raise StorageError("Cannot verify the selected volume; check its connection or run setup again.")
        info = plistlib.loads(result.stdout)
        mount = _path(info["MountPoint"])
        _no_symlinks(mount)
        volume_uuid = str(uuid.UUID(info["VolumeUUID"])).upper()
        # APFS firmlinks can put a user path outside the actual volume's mount path.
        if (info.get("DeviceNode") != device or info.get("WritableVolume") is not True
                or not mount.is_dir() or mount.resolve() != mount or _volume_device(mount) != device):
            raise StorageError("The selected folder must be on a mounted, writable local volume.")
        return {"version": 1, "storage_dir": str(path), "volume_uuid": volume_uuid, "mount_point": str(mount)}
    except StorageError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, plistlib.InvalidFileException,
            subprocess.SubprocessError):
        raise StorageError("Cannot verify the selected volume; check its connection or run setup again.") from None


def verify_storage(settings):
    """Verify actual mount identity every time; never trust folder existence alone."""
    expected = _settings(settings)
    actual = _selected_volume(Path(expected["storage_dir"]))
    if actual != expected:
        raise StorageError("Volume identity changed; restore the selected storage or run setup again.")
    return actual


def _write_settings(settings, config_path):
    temporary = None
    try:
        path = _config_path(config_path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _no_symlinks(path)
        _private(path.parent, directory=True)
        if path.exists():
            _private(path)
        descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(settings, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except StorageError:
        raise
    except OSError:
        raise StorageError("Cannot save storage configuration; existing settings were kept.") from None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def configure_storage(path=None, config_path=None):
    """Choose once, verify the actual local volume, then atomically save settings."""
    config_path = _config_path(config_path)
    selected = _choose_folder() if path is None else _path(path)
    settings = _selected_volume(selected)
    _write_settings(settings, config_path)
    return settings


def _output_path(path, root):
    try:
        output = _path(path)
        _no_symlinks(output)
        output = output.resolve()
        if not output.is_relative_to(root):
            raise StorageError("Output must stay inside the selected storage folder.")
        ancestor = output
        while not ancestor.exists():
            ancestor = ancestor.parent
        if ancestor.stat().st_dev != root.stat().st_dev or _volume_device(ancestor) != _volume_device(root):
            raise StorageError("Output cannot use a different volume mounted inside the storage folder.")
        return output
    except OSError:
        raise StorageError("Cannot verify the output folder; check its path and permissions.") from None


def verify_output(path, config_path=None):
    """Return an allowed output path without creating it or its parent folders."""
    settings = verify_storage(load_settings(config_path))
    return _output_path(path, Path(settings["storage_dir"]))


def workspace_directory(project_path, *, category=None, config_path=None, create=False):
    """Find a stable workspace for a physical checkout, creating it only on request."""
    settings = verify_storage(load_settings(config_path))
    try:
        project = Path(project_path).expanduser().resolve(strict=True)
        if not project.is_dir():
            raise StorageError("Use an existing project folder.")
        if category is not None and (not isinstance(category, str)
                                     or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", category)):
            raise StorageError("Category must be a single safe folder name.")
        name = re.sub(r"[\x00-\x1f\x7f]", "-", project.name)[:96] or "project"
        key = name + "-" + hashlib.sha256(str(project).encode()).hexdigest()[:12]
        folder = Path(settings["storage_dir"]) / "AgentBuilds" / key
        if category is not None:
            folder /= category
        output = _output_path(folder, Path(settings["storage_dir"]))
        if create:
            output.mkdir(mode=0o700, parents=True, exist_ok=True)
            _output_path(output, Path(settings["storage_dir"]))
        return output
    except StorageError:
        raise
    except (OSError, TypeError, ValueError):
        raise StorageError("Cannot access the project workspace; check its path and permissions.") from None
