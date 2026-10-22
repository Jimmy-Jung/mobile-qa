#!/usr/bin/env python3
"""Configure local storage once. Author: JunyoungJung · 2026-10-22."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mobile_qa.storage import StorageError, _config_path, configure_storage, load_settings, verify_storage


def main(argv=None):
    parser = argparse.ArgumentParser(description="Select a folder on internal or external storage for Mobile QA. Existing settings are reused.")
    parser.add_argument("--storage-dir", help="Existing folder on internal or external storage; explicitly replaces the saved selection (headless setup).")
    parser.add_argument("--config", help="Optional private local configuration file.")
    args = parser.parse_args(argv)
    try:
        config = _config_path(args.config)
        if args.storage_dir is not None:
            settings = configure_storage(args.storage_dir, config)
        else:
            if config.exists() or config.is_symlink():
                settings = verify_storage(load_settings(config))
            else:
                settings = configure_storage(config_path=config)
        print(json.dumps(settings, ensure_ascii=False))
        return 0
    except StorageError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
