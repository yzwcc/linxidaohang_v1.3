"""Run actual pinned Patchwork++ and OctoMap on labeled offline fixtures."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import unittest

from evaluate_recorded_clearance import ROOT, digest


def verified_build(library, sha256, build_report):
    lock_path = ROOT / 'robot-navigation/patchworkpp.lock.json'
    lock = json.loads(lock_path.read_bytes())
    if (not build_report.is_absolute() or build_report.resolve(strict=True) != build_report
            or not build_report.is_file() or build_report.stat().st_size > 262144
            or build_report.parent != library.parent):
        raise ValueError('direct_adjacent_ground_build_report_required')
    report = json.loads(build_report.read_bytes())
    if (report.get('schemaVersion') != 1
            or report.get('purpose') != 'offline_ground_candidates_not_navigation_acceptance'
            or report.get('revision') != lock['revision'] or report.get('license') != lock['license']
            or report.get('lockSha256') != digest(lock_path)
            or report.get('upstreamFilesSha256') != lock['files']
            or report.get('adapterSha256') != digest(ROOT / 'robot-navigation/native-ground/ground_bridge.cpp')
            or report.get('nativeSha256') != sha256 or digest(library) != sha256
            or report.get('freshProcessLoadPassed') is not True
            or any(report.get(key) is not False for key in ('robotContacted', 'physicalMotionSent', 'navigationReady'))):
        raise ValueError('current_source_bound_ground_build_required')
    copies = {'ground_bridge.cpp': report['adapterSha256'],
              **{'upstream/' + name: value for name, value in lock['files'].items()}}
    for name, expected in copies.items():
        path = library.parent / name
        if path.resolve(strict=True) != path or digest(path) != expected:
            raise ValueError('unchanged_ground_compiled_input_copies_required')
    return report


def check(args):
    library = args.library
    if (not library.is_absolute() or library.resolve(strict=True) != library
            or not library.is_file() or digest(library) != args.sha256
            or args.output.exists()):
        raise ValueError('verified_direct_ground_library_and_new_report_required')
    lock = json.loads((ROOT / 'robot-navigation/patchworkpp.lock.json').read_bytes())
    build = verified_build(library, args.sha256, args.build_report)
    octomap = json.loads(args.octomap_build_report.read_bytes())
    if (octomap['octomapVersion'] != '1.9.7'
            or octomap['nativeSha256'] != args.octomap_sha256
            or not args.octomap_library.is_absolute()
            or args.octomap_library.resolve(strict=True) != args.octomap_library
            or digest(args.octomap_library) != args.octomap_sha256):
        raise ValueError('matching_host_octomap_build_report_and_library_required')
    os.environ.update(LINGXI_TEST_GROUND_LIBRARY=str(library), LINGXI_TEST_GROUND_SHA256=args.sha256,
        LINGXI_TEST_OCTOMAP_LIBRARY=str(args.octomap_library),
        LINGXI_TEST_OCTOMAP_SHA256=args.octomap_sha256,
        LINGXI_TEST_OCTOMAP_DEPENDENCIES=json.dumps(octomap['dependencies']))
    test_file = ROOT / 'tools/tests/test_ground.py'
    spec = importlib.util.spec_from_file_location('native_ground_contracts', test_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(module))
    passed = result.wasSuccessful() and not result.skipped
    sources = ('robot-navigation/lingxi_navigation/ground.py',
               'robot-navigation/native-ground/ground_bridge.cpp',
               'robot-navigation/patchworkpp.lock.json', 'tools/check_ground_native.py',
               'tools/build_ground_offline.py', 'tools/tests/test_ground.py',
               'robot-navigation/lingxi_navigation/occupancy.py')
    report = {'schemaVersion': 1,
        'result': 'native_synthetic_ground_fusion_passed' if passed else 'failed_or_skipped',
        'machine': platform.machine(), 'python': platform.python_version(),
        'upstreamRevision': lock['revision'], 'license': lock['license'],
        'nativeSha256': args.sha256, 'octomapSha256': args.octomap_sha256,
        'groundBuildReportSha256': digest(args.build_report),
        'compiledAdapterSha256': build['adapterSha256'], 'compiler': build['compiler'],
        'octomapDependenciesSha256': octomap['dependencies'],
        'sourcesSha256': {name: digest(ROOT / name) for name in sources},
        'testsRun': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'skipped': len(result.skipped), 'inputClassification': 'isolated_synthetic_ground_and_rays',
        'robotContacted': False, 'sensorsSubscribed': False, 'physicalMotionSent': False,
        'physicalScaleValidated': False, 'groundSupportValidated': False, 'navigationReady': False}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write('\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('library', 'build-report', 'octomap-library', 'octomap-build-report', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('sha256', 'octomap-sha256'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    report = check(args)
    print(json.dumps(report))
    raise SystemExit(0 if report['result'] == 'native_synthetic_ground_fusion_passed' else 1)
