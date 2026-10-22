#!/usr/bin/env python3
"""Read-only local readiness report. Author: JunyoungJung · 2026-10-22."""

import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mobile_qa.release import ROOT, verify_release
from mobile_qa.storage import load_settings, verify_storage, workspace_directory


def detect_tools():
    tools = {'macos': sys.platform == 'darwin', 'python': sys.version.split()[0]}
    for name in ['Pillow', 'numpy']:
        try:
            tools[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            tools[name] = None
    for name in ['agent-device', 'swiftc', 'codesign', 'xcodebuild', 'adb', 'java']:
        tools[name] = shutil.which(name)
    tools['agent_device_version'] = None
    executable = tools['agent-device']
    if executable:
        for parent in Path(executable).resolve().parents:
            metadata = parent / 'package.json'
            if metadata.is_file():
                try:
                    data = json.loads(metadata.read_text())
                    if data.get('name') == 'agent-device':
                        tools['agent_device_version'] = data.get('version')
                        break
                except (OSError, ValueError, AttributeError):
                    pass
    return tools


def helper_readiness():
    sys.path.insert(0, str(ROOT / 'skills/test-accounts/scripts'))
    try:
        from keychain_store import verified_helper
        return {'verified': True, 'executable': str(verified_helper())}
    except (OSError, ValueError, RuntimeError) as error:
        return {'verified': False, 'reason': str(error)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-only', action='store_true', help='Only verify packaged files; no local settings or dependency checks.')
    parser.add_argument('--project-dir', type=Path, help='Report the selected project workspace without creating it.')
    args = parser.parse_args(argv)
    try:
        inventory = verify_release()
        result = {'release': 'verified', 'version': inventory['version']}
        if args.release_only:
            print(json.dumps(result))
            return 0
        tools = detect_tools()
        result['tools'] = tools
        result['features'] = {
            'pixel_comparison': bool(tools.get('Pillow') and tools.get('numpy')),
            'device_qa': bool(tools.get('macos') and tools.get('agent-device')),
            'secure_input_version_compatible': tools.get('agent_device_version') == '0.21.30',
        }
        try:
            settings = verify_storage(load_settings())
            result['storage'] = {'status': 'verified', 'directory': settings['storage_dir']}
            if args.project_dir:
                result['project_workspace'] = str(workspace_directory(args.project_dir, create=False))
            result['account_helper'] = helper_readiness()
        except (OSError, ValueError) as error:
            result['storage'] = {'status': 'needs_setup', 'reason': str(error)}
            result['status'] = 'needs_setup'
            print(json.dumps(result, ensure_ascii=False))
            return 1
        ready = result['features']['pixel_comparison'] and result['features']['device_qa']
        result['status'] = 'ready' if ready else 'needs_dependencies'
        print(json.dumps(result, ensure_ascii=False))
        return 0 if ready else 1
    except (OSError, ValueError) as error:
        print(json.dumps({'status': 'error', 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
