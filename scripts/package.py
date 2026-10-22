#!/usr/bin/env python3
"""Verify or assemble a public release. Author: JunyoungJung · 2026-10-22."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mobile_qa.release import ROOT, _object, safety_check, verify_release, write_inventory
from mobile_qa.storage import verify_output


def create_archive(output_dir, root=ROOT):
    root = Path(root).resolve()
    inventory = verify_release(root)
    if safety_check(root) != inventory['files']:
        raise ValueError('Release changed during verification.')
    inventory_bytes = (root / 'release.json').read_bytes()
    if json.loads(inventory_bytes, object_pairs_hook=_object) != inventory:
        raise ValueError('Release inventory changed during verification.')
    output = verify_output(output_dir)
    archive = output / ('mobile-qa-' + inventory['version'] + '.zip')
    checksum = archive.with_suffix('.zip.sha256')
    if archive.exists() or checksum.exists():
        raise ValueError('Release output exists; choose a new output folder.')
    output.mkdir(parents=True, exist_ok=True)
    verify_output(output)
    created = False
    try:
        with archive.open('xb') as stream:
            created = True
            with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as bundle:
                for name, expected_hash in sorted(inventory['files'].items()):
                    data = (root / name).read_bytes()
                    if "sha256:" + hashlib.sha256(data).hexdigest() != expected_hash:
                        raise ValueError('Release changed during packaging: ' + name)
                    bundle.writestr('mobile-qa/' + name, data)
                bundle.writestr('mobile-qa/release.json', inventory_bytes)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        with checksum.open('x', encoding='utf-8') as stream:
            stream.write(digest + '  ' + archive.name + '\n')
    except BaseException:
        if created:
            archive.unlink(missing_ok=True)
        raise
    return {'archive': str(archive), 'sha256': digest, 'checksum': str(checksum)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--write-inventory', action='store_true', help='Maintainer-only: record reviewed source hashes.')
    mode.add_argument('--check', action='store_true', help='Verify release hashes and public file boundaries without writing.')
    mode.add_argument('--output-dir', type=Path, help='New release output inside the configured storage folder.')
    args = parser.parse_args(argv)
    try:
        if args.write_inventory:
            inventory = write_inventory()
            result = {'status': 'inventoried', 'version': inventory['version'], 'files': len(inventory['files'])}
        elif args.check:
            inventory = verify_release()
            safety_check()
            result = {'status': 'verified', 'version': inventory['version'], 'files': len(inventory['files'])}
        else:
            result = {'status': 'packaged', **create_archive(args.output_dir)}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(json.dumps({'status': 'error', 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
