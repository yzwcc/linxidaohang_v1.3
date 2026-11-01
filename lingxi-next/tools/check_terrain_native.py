"""Actual native terrain window tests, with source and dependency identities."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import unittest

from check_ground_native import verified_build
from evaluate_recorded_clearance import ROOT, digest


def check(args):
    build = verified_build(args.library, args.sha256, args.build_report)
    octomap = json.loads(args.octomap_build_report.read_bytes())
    if (octomap.get('octomapVersion') != '1.9.7' or octomap.get('nativeSha256') != args.octomap_sha256
            or digest(args.octomap_library) != args.octomap_sha256 or args.output.exists()):
        raise ValueError('verified_octomap_and_new_terrain_receipt_required')
    os.environ.update(LINGXI_TEST_GROUND_LIBRARY=str(args.library), LINGXI_TEST_GROUND_SHA256=args.sha256,
        LINGXI_TEST_OCTOMAP_LIBRARY=str(args.octomap_library), LINGXI_TEST_OCTOMAP_SHA256=args.octomap_sha256,
        LINGXI_TEST_OCTOMAP_DEPENDENCIES=json.dumps(octomap['dependencies']))
    suite = unittest.TestSuite()
    for name in ('test_terrain', 'test_terrain_transport'):
        test = ROOT / ('tools/tests/' + name + '.py')
        spec = importlib.util.spec_from_file_location('actual_native_' + name, test)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.wasSuccessful() and not result.skipped
    sources = ('robot-navigation/lingxi_navigation/terrain.py', 'tools/tests/test_terrain.py',
        'robot-navigation/lingxi_navigation/ground.py', 'robot-navigation/lingxi_navigation/occupancy.py',
        'tools/tests/test_ground.py', 'tools/check_terrain_native.py', 'tools/tests/test_terrain_transport.py',
        'robot-navigation/lingxi_navigation/terrain_process.py', 'robot-navigation/lingxi_navigation/terrain_worker.py',
        'robot-navigation/lingxi_navigation/terrain_protocol.py', 'robot-navigation/lingxi_navigation/terrain_feed.py')
    report = dict(schemaVersion=1, result='native_terrain_windows_passed' if passed else 'failed_or_skipped',
        testsRun=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        sourcesSha256={name: digest(ROOT / name) for name in sources},
        groundNativeSha256=args.sha256, octomapNativeSha256=args.octomap_sha256,
        groundBuildReportSha256=digest(args.build_report), octomapBuildReportSha256=digest(args.octomap_build_report),
        dependenciesSha256=octomap['dependencies'], upstreamRevision=build['revision'],
        machine=platform.machine(), python=platform.python_version(),
        inputClassification='isolated_synthetic_rays_and_ground', robotContacted=False,
        physicalMotionSent=False, physicalScaleValidated=False, groundSupportValidated=False, navigationReady=False)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write('\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('library', 'build-report', 'octomap-library', 'octomap-build-report', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    for name in ('sha256', 'octomap-sha256'):
        parser.add_argument('--' + name, required=True)
    report = check(parser.parse_args())
    print(json.dumps(report))
    raise SystemExit(0 if report['result'] == 'native_terrain_windows_passed' else 1)
