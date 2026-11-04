"""Build/verify an offline ARM64 Python dependency kit; never install or execute it."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import zipfile

import arm64_python_runtime as runtime

PREFIX = 'lingxi-next-python-cp310-arm64-v1'
PURPOSE = 'offline_arm64_python310_dependency_kit_not_target_acceptance'
INSTALLER = runtime.ROOT / 'tools/arm64_python_runtime.py'
README = b'''# Lingxi Next Offline Python Dependencies

This kit contains the pinned five ARM64 CPython 3.10 wheels and their existing
ordinary-user installer. Verify the supplied archive SHA256 before extraction,
then run `sha256sum -c SHA256SUMS` inside the extracted directory.
Third-party license/notice files remain inside each unmodified wheel; the
manifest records their identities. No robot configuration or cloud secret is
included. This kit is compatible with the 0.1.25 commissioning package.

The robot must already provide Linux ARM64 system Python 3.10 with venv and
ensurepip. Do not replace vendor Python or install these wheels globally.
After the main package has created the service account, create the parent:

    sudo install -d -m 0700 -o lingxi-next -g lingxi-next /var/lib/lingxi-next/python-runtimes

Run from this extracted directory with its actual absolute path:

    KIT=/absolute/extracted/lingxi-next-python-cp310-arm64-v1
    sudo -u lingxi-next -H /usr/bin/python3 -I -B "$KIT/tools/arm64_python_runtime.py" install \\
      --wheelhouse "$KIT/wheelhouse" --output /var/lib/lingxi-next/python-runtimes/cp310-v1

All parent directories must be readable by the service UID. Select a new final
output if one exists; never move a venv, overwrite partial results or run pip
as root. Installation uses no index/network and verifies exact wheel hashes.
It does not edit robot.env, start services, connect sensors or send motion.
After target verification, select the new env/bin/python as LINGXI_SLAM_PYTHON;
see the main package's docs/INSTALLED_SLAM_PYTHON.md. ROS/native ABI, geometry,
mapping, localization, navigation and voice acceptance remain separate.
'''


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def licenses(data):
    with zipfile.ZipFile(io.BytesIO(data)) as wheel:
        names = sorted(item.filename for item in wheel.infolist() if not item.is_dir()
            and any(word in PurePosixPath(item.filename).name.lower() for word in ('license', 'copying', 'notice')))
        if not names or len(names) > 64:
            raise ValueError('wheel_license_inventory_required')
        result = {}
        for name in names:
            if wheel.getinfo(name).file_size > 1024 * 1024:
                raise ValueError('bounded_wheel_license_required')
            result[name] = digest(wheel.read(name))
        return result


def materials(wheelhouse):
    lock = runtime.load_lock()
    runtime.verify_wheelhouse(wheelhouse, lock)
    files = {'tools/arm64_python_runtime.py': INSTALLER.read_bytes(),
             'robot-slam/runtime-arm64-cp310.lock.json': runtime.LOCK.read_bytes(),
             'README.md': README,
             'wheelhouse/requirements.txt': runtime.requirements(lock).encode()}
    notices = {}
    for entry in lock['packages']:
        data = (wheelhouse / entry['filename']).read_bytes()
        if len(data) != entry['bytes'] or digest(data) != entry['sha256']:
            raise ValueError('unchanged_runtime_wheel_required')
        files['wheelhouse/' + entry['filename']] = data
        notices[entry['name']] = licenses(data)
    manifest = {'schemaVersion': 1, 'purpose': PURPOSE, 'platform': lock['platform'],
        'lockSha256': digest(files['robot-slam/runtime-arm64-cp310.lock.json']),
        'files': {name: {'bytes': len(data), 'sha256': digest(data)} for name, data in files.items()},
        'wheelLicenses': notices, 'packages': {entry['name']: entry['version'] for entry in lock['packages']},
        'targetExecuted': False, 'vendorPackagesChanged': False, 'servicesStarted': False,
        'robotContacted': False, 'runtimeCompatibilityAccepted': False, 'navigationReady': False}
    files['kit-manifest.json'] = encoded(manifest)
    files['SHA256SUMS'] = ''.join(digest(data) + '  ' + name + '\n'
        for name, data in sorted(files.items())).encode()
    return files, manifest


def build(wheelhouse, output):
    if (not output.is_absolute() or output.parent.resolve(strict=True) != output.parent
            or any(path.exists() or path.is_symlink() for path in
                   (output, Path(str(output) + '.sha256'), Path(str(output) + '.verification.json')))):
        raise ValueError('new_direct_dependency_kit_output_required')
    files, manifest = materials(wheelhouse)
    with output.open('xb') as stream, gzip.GzipFile(filename='', fileobj=stream, mode='wb',
            mtime=0, compresslevel=1) as compressed, tarfile.open(fileobj=compressed, mode='w') as archive:
        for name, data in sorted(files.items()):
            item = tarfile.TarInfo(PREFIX + '/' + name)
            item.mode, item.size, item.mtime = 0o644, len(data), 0
            archive.addfile(item, io.BytesIO(data))
    expected = runtime.sha256(output)
    result = verify(wheelhouse, output, expected)
    Path(str(output) + '.sha256').write_text(expected + '  ' + output.name + '\n', encoding='ascii')
    Path(str(output) + '.verification.json').write_bytes(encoded(result))
    return result


def verify(wheelhouse, archive, expected):
    files, manifest = materials(wheelhouse)
    if runtime.sha256(archive) != expected:
        raise ValueError('dependency_kit_archive_digest_mismatch')
    seen = set()
    with tarfile.open(archive, 'r|gz') as stream:
        for item in stream:
            name = item.name.removeprefix(PREFIX + '/')
            if (item.name != PREFIX + '/' + name or name not in files or name in seen
                    or not item.isfile() or item.size != len(files[name]) or item.mode != 0o644):
                raise ValueError('exact_dependency_kit_member_required')
            with stream.extractfile(item) as data:
                if data.read(item.size + 1) != files[name]:
                    raise ValueError('dependency_kit_content_mismatch')
            seen.add(name)
    if seen != set(files):
        raise ValueError('complete_dependency_kit_required')
    return {'schemaVersion': 1, 'result': 'verified_offline_python_dependency_kit',
        'archive': archive.name, 'sha256': expected, 'bytes': archive.stat().st_size,
        'verifiedFiles': len(seen), 'manifestSha256': digest(encoded(manifest)),
        'wheelBytes': sum(len(data) for name, data in files.items() if name.endswith('.whl')),
        'packages': manifest['packages'], 'wheelLicenses': manifest['wheelLicenses'],
        'targetExecuted': False, 'robotContacted': False, 'runtimeCompatibilityAccepted': False,
        'navigationReady': False, 'servicesStarted': False, 'vendorPackagesChanged': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheelhouse', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify-sha256')
    args = parser.parse_args()
    result = (verify(args.wheelhouse, args.output, args.verify_sha256) if args.verify_sha256
              else build(args.wheelhouse, args.output))
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    main()
