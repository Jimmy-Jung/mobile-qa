#!/usr/bin/env python3
"""Read-only local readiness report. Author: JunyoungJung · 2026-10-22."""

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mobile_qa.release import ROOT, verify_release
from mobile_qa.storage import load_settings, verify_storage, workspace_directory
sys.path.insert(0, str(ROOT / 'skills/test-accounts/scripts'))
from build_ios_input import package_identity
from build_android_input import android_sdk, java_home


def detect_tools(*, simulator=False):
    tools = {'macos': sys.platform == 'darwin', 'python': sys.version.split()[0]}
    for name in ['Pillow', 'numpy']:
        try:
            tools[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            tools[name] = None
    if simulator:
        tools.update(xcrun=None, simctl=None)
        if tools['macos']:
            try:
                xcrun = Path('/usr/bin/xcrun')
                if not xcrun.is_file() or not os.access(xcrun, os.X_OK):
                    return tools
                tools['xcrun'] = str(xcrun)
                result = subprocess.run([str(xcrun), '--find', 'simctl'], capture_output=True, text=True, check=False, timeout=10)
                path = Path(result.stdout.strip())
                if result.returncode == 0 and path.is_absolute() and path.is_file() and os.access(path, os.X_OK):
                    tools['simctl'] = str(path)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
        return tools
    for name in ['agent-device', 'swiftc', 'codesign', 'xcodebuild', 'adb', 'java']:
        tools[name] = shutil.which(name)
    tools['agent_device_version'] = None
    try:
        source = package_identity()
        tools['agent_device_source'] = {'verified': True, 'package': source['path'], 'version': source['version']}
    except (OSError, ValueError) as error:
        tools['agent_device_source'] = {'verified': False, 'reason': str(error)}
    try:
        jdk = java_home()
        tools.update(jdk_home=str(jdk), java=str(jdk / 'bin/java'), javac=str(jdk / 'bin/javac'))
    except (OSError, ValueError) as error:
        tools.update(jdk_home=None, java=None, javac=None, jdk_error=str(error))
    try:
        adb = android_sdk() / 'platform-tools/adb'
        if adb.is_file() and os.access(adb, os.X_OK):
            tools['adb'] = str(adb.resolve())
    except (OSError, ValueError):
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--release-only', action='store_true', help='Only verify packaged files; no local settings or dependency checks.')
    mode.add_argument('--simulator', action='store_true', help='Check Simulator capture and pixel comparison without requiring agent-device; UI control, login, and app execution are checked separately.')
    parser.add_argument('--project-dir', type=Path, help='Report the selected project workspace without creating it.')
    args = parser.parse_args(argv)
    try:
        inventory = verify_release()
        result = {'release': 'verified', 'version': inventory['version']}
        if args.release_only:
            print(json.dumps(result))
            return 0
        tools = detect_tools(simulator=True) if args.simulator else detect_tools()
        result['tools'] = tools
        result['features'] = {'pixel_comparison': bool(tools.get('Pillow') and tools.get('numpy'))}
        if args.simulator:
            result.update(target='simulator', scope='simulator_capture_and_pixel_comparison',
                          not_checked=['ui_control', 'authentication', 'app_execution'])
            result['features']['simulator_capture'] = bool(tools.get('macos') and tools.get('simctl'))
        else:
            result['features'].update(
                device_qa=bool(tools.get('macos') and tools.get('agent-device')),
                secure_input_version_compatible=tools.get('agent_device_source', {}).get('version') == '0.21.30' and tools.get('agent_device_source', {}).get('verified') is True,
            )
        try:
            settings = verify_storage(load_settings())
            result['storage'] = {'status': 'verified', 'directory': settings['storage_dir']}
            if args.project_dir:
                result['project_workspace'] = str(workspace_directory(args.project_dir, create=False))
            if not args.simulator:
                result['account_helper'] = helper_readiness()
        except (OSError, ValueError) as error:
            result['storage'] = {'status': 'needs_setup', 'reason': str(error)}
            result['status'] = 'needs_setup'
            print(json.dumps(result, ensure_ascii=False))
            return 1
        ready = result['features']['pixel_comparison'] and result['features']['simulator_capture' if args.simulator else 'device_qa']
        result['status'] = 'ready' if ready else 'needs_dependencies'
        print(json.dumps(result, ensure_ascii=False))
        return 0 if ready else 1
    except (OSError, ValueError) as error:
        print(json.dumps({'status': 'error', 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
