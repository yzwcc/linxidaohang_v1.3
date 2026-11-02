"""Standalone ARM64 acquisition probe using synthetic frames only, without ROS."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import time


MANIFEST = 'acquisition-trial-manifest.json'
LIBRARY = 'liblingxi_glim_acquisition_output.so'
RESULT = 'arm64_acquisition_synthetic_contracts_passed_not_physical_acceptance'
CONTRACTS = tuple('acquisition_' + name for name in (
    'copy', 'pending_close', 'overflow', 'missing_raw', 'missing_trajectory',
    'incomplete_trajectory', 'unordered_trajectory', 'quaternion', 'points', 'times',
    'pose', 'lidar_pose', 'skipped_id', 'wrong_thread', 'wrong_api_thread', 'capacity',
    'invalid_stamp', 'invalid_frame', 'duplicate_id', 'return_overflow',
    'trajectory_overflow', 'negative_time', 'unordered_time', 'nonfinite_point',
    'nonhomogeneous_point', 'extrinsic', 'raw_stamp', 'null_frame', 'null_metadata'))
CHECKS = dict.fromkeys(('bundleIntegrity', 'isolatedLibraries', 'moduleLifecycle',
    'all29Contracts', 'syntheticExport', 'inputsUnchanged'), True)
BOUNDARIES = dict.fromkeys(('sensorSubscriptionsStarted', 'motionCommandsSent',
    'servicesSwitched', 'systemPackagesInstalled', 'realDataProcessed',
    'liveThroughputValidated', 'physicalScaleValidated', 'groundSupportValidated',
    'navigationReady', 'deploymentApproved'), False)


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def digest(value):
    return type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None


def verify_bundle(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('direct_acquisition_trial_directory_required')
    manifest = root / MANIFEST
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 128 * 1024:
        raise ValueError('bounded_direct_acquisition_trial_manifest_required')
    value = json.loads(manifest.read_text(encoding='utf-8'))
    fields = {'schemaVersion', 'purpose', 'platform', 'inputClassification',
        'componentManifestSha256', 'componentBuildReportSha256', 'sourceSha256',
        'coreLibrariesSha256', 'syntheticExportSha256', 'contracts', 'files', 'boundaries'}
    if (type(value) is not dict or set(value) != fields
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['purpose'] != 'synthetic_arm64_acquisition_trial'
            or value['platform'] != 'linux-aarch64' or value['inputClassification'] != 'synthetic_fixture'
            or not digest(value['componentManifestSha256']) or not digest(value['componentBuildReportSha256'])
            or not digest(value['syntheticExportSha256'])
            or value['contracts'] != list(CONTRACTS) or value['boundaries'] != BOUNDARIES
            or any(item is not False for item in value['boundaries'].values())):
        raise ValueError('explicit_complete_synthetic_acquisition_trial_required')
    core, sources, files = value['coreLibrariesSha256'], value['sourceSha256'], value['files']
    if (type(core) is not dict or not 1 <= len(core) <= 64 or 'libglim.so' not in core
            or any(type(name) is not str or re.fullmatch(r'lib[A-Za-z0-9_-]+\.so(?:\.[0-9]+)*', name) is None
                or name == LIBRARY or not digest(identity) for name, identity in core.items())
            or type(sources) is not dict or not sources
            or any(type(name) is not str or not name or '\\' in name or Path(name).is_absolute()
                or '..' in Path(name).parts or not digest(item) for name, item in sources.items())
            or type(files) is not dict):
        raise ValueError('pinned_acquisition_trial_sources_and_core_required')
    expected = {'acquisition_target_probe.py', 'bin/acquisition_contract', 'lib/' + LIBRARY}
    expected.update('lib/' + name for name in core)
    if set(files) != expected:
        raise ValueError('exact_acquisition_trial_file_inventory_required')
    actual = set()
    for path in root.rglob('*'):
        name = path.relative_to(root).as_posix()
        if path.is_symlink() or (path.is_dir() and name not in ('lib', 'bin')):
            raise ValueError('direct_acquisition_trial_members_required')
        if not path.is_dir():
            actual.add(name)
    if actual != expected | {MANIFEST}:
        raise ValueError('undeclared_acquisition_trial_member')
    for name, entry in files.items():
        path = root / name
        if (type(entry) is not dict or set(entry) != {'bytes', 'sha256', 'executable'}
                or type(entry['bytes']) is not int or not 0 < entry['bytes'] <= 64 * 1024**2
                or not digest(entry['sha256']) or type(entry['executable']) is not bool
                or entry['executable'] != (name == 'bin/acquisition_contract')
                or not path.is_file() or path.stat().st_size != entry['bytes'] or sha256(path) != entry['sha256']):
            raise ValueError('acquisition_trial_member_integrity_mismatch')
    if any(files['lib/' + name]['sha256'] != identity for name, identity in core.items()):
        raise ValueError('acquisition_trial_core_identity_mismatch')
    if sources.get('tools/acquisition_target_probe.py') != files['acquisition_target_probe.py']['sha256']:
        raise ValueError('source_bound_acquisition_probe_required')
    return value


def mapped_libraries(root, manifest, text):
    expected = {Path(name).name: entry['sha256'] for name, entry in manifest['files'].items()
                if name.startswith('lib/')}
    found = {}
    for line in text.splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6 or not Path(fields[5]).is_absolute():
            continue
        path = Path(fields[5])
        # Also reject a versioned/unversioned alias from an external provider.
        key = next((name for name in expected if name.partition('.so')[0] == path.name.partition('.so')[0]), None)
        if key is None:
            continue
        if path.parent != root / 'lib' or path.is_symlink() or sha256(path) != expected[key]:
            raise ValueError('acquisition_library_outside_isolated_trial')
        found[key] = sha256(path)
    if found != expected:
        raise ValueError('all_acquisition_trial_libraries_must_be_observed')
    return found


def lifecycle(library):
    for name in ('open', 'close'):
        function = getattr(library, 'lingxi_acquisition_' + name)
        function.argtypes, function.restype = [], ctypes.c_int
    library.lingxi_acquisition_counts.argtypes = [ctypes.POINTER(ctypes.c_uint64)]
    library.lingxi_acquisition_counts.restype = ctypes.c_int
    library.lingxi_acquisition_take.argtypes = [ctypes.c_uint64, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64)]
    library.lingxi_acquisition_take.restype = ctypes.c_int
    counters, metadata = (ctypes.c_uint64 * 5)(), (ctypes.c_uint64 * 4)()
    if library.lingxi_acquisition_open() != 0:
        raise ValueError('acquisition_target_open_failed')
    try:
        if (library.lingxi_acquisition_counts(counters) != 0 or list(counters) != [0] * 5
                or library.lingxi_acquisition_take(0, None, metadata) != 0 or list(metadata) != [0] * 4):
            raise ValueError('acquisition_target_empty_state_failed')
    finally:
        closed = library.lingxi_acquisition_close()
    if closed != 0 or library.lingxi_acquisition_open() != -1:
        raise ValueError('acquisition_target_terminal_lifecycle_failed')


def validate_export(data):
    if (len(data) != 840 or data[:8] != b'LXOBS002'
            or struct.unpack_from('<Qd', data, 184) != (1, 1002.)
            or any(struct.unpack_from('<d', data, offset)[0] != expected
                for offset, expected in ((624, 2.), (752, 1.), (512, -2.), (384, .2)))):
        raise ValueError('acquisition_target_synthetic_export_mismatch')
    return dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), frameId=1,
        stamp=1002., returnCount=3, trajectorySamples=2)


def probe(root):
    root = Path(root).resolve(strict=True)
    if (platform.system() != 'Linux' or platform.machine() != 'aarch64' or os.geteuid() == 0
            or os.environ.get('LD_LIBRARY_PATH') != str(root / 'lib')
            or any(name in os.environ for name in ('LD_PRELOAD', 'PYTHONPATH', 'ROS_DISTRO', 'AMENT_PREFIX_PATH'))):
        raise ValueError('ordinary_user_clean_arm64_acquisition_process_required')
    manifest = verify_bundle(root)
    identity, started = sha256(root / MANIFEST), time.monotonic()
    libraries = [ctypes.CDLL(str(root / 'lib' / name), mode=os.RTLD_NOW | os.RTLD_LOCAL)
                 for name in manifest['coreLibrariesSha256']]
    library = ctypes.CDLL(str(root / 'lib' / LIBRARY), mode=os.RTLD_NOW | os.RTLD_LOCAL)
    observed = mapped_libraries(root, manifest, Path('/proc/self/maps').read_text())
    lifecycle(library)
    executed = []
    for name in CONTRACTS:
        result = subprocess.run([str(root / 'bin/acquisition_contract'), name.removeprefix('acquisition_')],
            capture_output=True, timeout=10., check=False)
        if result.returncode != 0 or result.stdout or result.stderr:
            raise RuntimeError('acquisition_target_contract_failed_' + name)
        executed.append(dict(name=name, exitCode=0))
    exported = subprocess.run([str(root / 'bin/acquisition_contract'), 'export'],
        capture_output=True, timeout=10., check=True)
    if exported.stderr:
        raise ValueError('acquisition_target_export_stderr')
    export = validate_export(exported.stdout)
    if (export['sha256'] != manifest['syntheticExportSha256']
            or verify_bundle(root) != manifest or sha256(root / MANIFEST) != identity
            or len(libraries) != len(manifest['coreLibrariesSha256'])):
        raise ValueError('acquisition_trial_changed_during_execution')
    return dict(result=RESULT, inputClassification='synthetic_fixture',
        target=dict(machine=platform.machine(), python=platform.python_version(), libc=list(platform.libc_ver())),
        trialManifestSha256=identity, componentManifestSha256=manifest['componentManifestSha256'],
        componentBuildReportSha256=manifest['componentBuildReportSha256'],
        mappedLibrariesSha256=observed, executedContracts=executed, syntheticExport=export,
        probeSeconds=time.monotonic() - started, checks=dict(CHECKS), boundaries=dict(BOUNDARIES))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    print(json.dumps(probe(parser.parse_args().root), allow_nan=False))


if __name__ == '__main__':
    main()
