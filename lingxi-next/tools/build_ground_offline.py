"""Compile pinned Patchwork++ without ROS, downloads or physical robot IO."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from evaluate_recorded_clearance import ROOT, digest


def upstream(source, lock):
    source = Path(source).resolve(strict=True)
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != lock['revision']:
        raise ValueError('pinned_patchwork_commit_required')
    for name, expected in lock['files'].items():
        path = source / name
        if path.resolve(strict=True) != path or not path.is_file() or digest(path) != expected:
            raise ValueError('direct_unchanged_pinned_patchwork_files_required')
    return source


def build(source, eigen, output):
    from refine_saved_map import private_output
    lock_path = ROOT / 'robot-navigation/patchworkpp.lock.json'
    lock_hash = digest(lock_path)
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    source, eigen, output = upstream(source, lock), Path(eigen).resolve(strict=True), private_output(output)
    if not (eigen / 'Eigen/Core').is_file():
        raise ValueError('explicit_local_eigen_headers_required')
    headers = {path.relative_to(eigen).as_posix(): digest(path)
               for path in (eigen / 'Eigen').rglob('*') if path.is_file()}
    if any((eigen / name).resolve(strict=True) != eigen / name for name in headers):
        raise ValueError('direct_eigen_headers_required')
    adapter = ROOT / 'robot-navigation/native-ground/ground_bridge.cpp'
    adapter_hash = digest(adapter)
    output.mkdir(mode=0o700)
    for name in lock['files']:
        target = output / 'upstream' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    for name in headers:
        target = output / 'eigen' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(eigen / name, target)
    shutil.copyfile(adapter, output / 'ground_bridge.cpp')
    native = output / 'liblingxi_ground.so'
    command = ['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Wpedantic', '-shared', '-fPIC', '-pthread',
        '-DLINGXI_PATCHWORKPP_REVISION="' + lock['revision'] + '"', '-I' + str(output / 'eigen'),
        '-I' + str(output / 'upstream/cpp/patchworkpp/include'),
        '-I' + str(output / 'upstream/cpp/common/include'), str(output / 'ground_bridge.cpp'),
        str(output / 'upstream/cpp/patchworkpp/src/patchworkpp.cpp'),
        str(output / 'upstream/cpp/common/src/plane_fit.cpp'), '-o', str(native)]
    with (output / 'private-build.log').open('x') as stream:
        subprocess.run(command, stdout=stream, stderr=stream, check=True, timeout=180)
        subprocess.run([sys.executable, '-c', 'import ctypes, sys; n=ctypes.CDLL(sys.argv[1]); '
            'n.lingxi_ground_revision.restype=ctypes.c_char_p; assert n.lingxi_ground_abi()==1; '
            'assert n.lingxi_ground_revision().decode()==sys.argv[2]', str(native), lock['revision']],
            stdout=stream, stderr=stream, check=True, timeout=10)
    upstream(source, lock)
    if (digest(lock_path) != lock_hash or digest(adapter) != adapter_hash
            or digest(output / 'ground_bridge.cpp') != adapter_hash
            or any(digest(output / 'upstream' / name) != before for name, before in lock['files'].items())
            or any(digest(eigen / name) != before or digest(output / 'eigen' / name) != before
                   for name, before in headers.items())):
        raise ValueError('unchanged_ground_build_inputs_required')
    report = {'schemaVersion': 1, 'purpose': 'offline_ground_candidates_not_navigation_acceptance',
        'revision': lock['revision'], 'license': lock['license'], 'upstreamFilesSha256': lock['files'],
        'lockSha256': lock_hash, 'adapterSha256': adapter_hash, 'nativeSha256': digest(native),
        'eigenHeadersSha256': headers, 'freshProcessLoadPassed': True,
        'compiler': subprocess.check_output(['g++', '--version'], text=True).splitlines()[0],
        'robotContacted': False, 'physicalMotionSent': False, 'navigationReady': False}
    (output / 'build-report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--eigen', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        report = build(args.source, args.eigen, args.output)
        print(json.dumps({key: report[key] for key in ('revision', 'nativeSha256', 'freshProcessLoadPassed', 'navigationReady')}))
    finally:
        os.umask(previous)
