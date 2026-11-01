"""Repeat rolling native terrain production and preserve real frame rejection."""
import argparse
import json
import math
import os
from pathlib import Path
import re

from evaluate_recorded_terrain import FALSE_FLAGS, ROOT, configuration, digest, run


def stable_summary(report):
    if (report.get('offlineOnly') is not True or report.get('sensorPaced') is not False
            or any(report.get(key) is not False for key in FALSE_FLAGS)):
        raise ValueError('offline_terrain_replay_not_acceptance_required')
    rows = [{key: value for key, value in row.items()
             if key not in ('processingSeconds', 'ingressToCompletionSeconds')} for row in report['windows']]
    return dict(input=report['input'], projection=report['projection'], budget=report['budget'],
        windows=rows, coveredSourceScans=report['coveredSourceScans'],
        unrepresentedSourceScans=report['unrepresentedSourceScans'],
        artifactSha256=report['artifactSha256'], artifactBytes=report['artifactBytes'],
        sourcesSha256=report['sourcesSha256'], nativeSha256=report['nativeSha256'],
        dependenciesSha256=report['dependenciesSha256'])


def verify(args):
    from refine_saved_map import private_output
    native = json.loads(args.native_receipt.read_bytes())
    if (native.get('result') != 'native_terrain_windows_passed' or native.get('testsRun', 0) < 23
            or any(native.get(key) != 0 for key in ('failures', 'errors', 'skipped'))
            or any(native.get(key) is not False for key in FALSE_FLAGS)
            or not native.get('sourcesSha256')
            or any(digest(ROOT / name) != expected for name, expected in native['sourcesSha256'].items())):
        raise ValueError('current_actual_native_terrain_tests_required')
    for config in (args.static_config, args.moving_config):
        value = configuration(config)
        if (value['ground']['sha256'] != native['groundNativeSha256']
                or value['octomap']['sha256'] != native['octomapNativeSha256']):
            raise ValueError('same_verified_native_libraries_required')
    output = private_output(args.output)
    output.mkdir(mode=0o700)
    reports = [run(args.static_config, output / name) for name in ('static-first', 'static-repeat')]
    if stable_summary(reports[0]) != stable_summary(reports[1]):
        raise ValueError('identical_terrain_replay_source_windows_and_grids_required')
    rejected = output / 'moving-rejected'
    try:
        run(args.moving_config, rejected)
    except RuntimeError:
        log = (rejected / 'private-worker.log').read_text()
        match = re.search(r'GroundFrameMismatch: .*scan=(\d+), height_error_metres=([\d.]+), origin_span_metres=([\d.]+);', log)
        if not match or any((rejected / name).exists() for name in ('report.json', 'report.pending', 'private-terrain-grids.bin')):
            raise ValueError('explicit_frame_rejection_without_partial_terrain_required')
    else:
        raise ValueError('recorded_geometry_must_not_be_automatically_relaxed')
    first = reports[0]
    timings = sorted(row['processingSeconds'] for row in first['windows'])
    evidence = dict(schemaVersion=1, date='2026-11-01', result='rolling_terrain_repeated_with_frame_rejection',
        verifierSourcesSha256={name: digest(ROOT / name) for name in
            ('tools/verify_recorded_terrain.py', 'tools/tests/test_recorded_terrain_runner.py')},
        nativeVerification=native, rawReportsSha256=[digest(output / name / 'report.json') for name in ('static-first', 'static-repeat')],
        repeatedIdentically=True, staticRecording=stable_summary(first),
        measuredHostProcessingSeconds=dict(p50=timings[len(timings) // 2], p95=timings[math.ceil(.95 * len(timings)) - 1], maximum=max(timings)),
        workerDurationSeconds=[report['workerDurationSeconds'] for report in reports],
        peakRssKiB=[report['peakRssKiB'] for report in reports],
        movingRecording=dict(result='declared_ground_frame_rejected', scan=int(match[1]),
            heightErrorMetres=float(match[2]), originSpanMetres=float(match[3]),
            privateFailureLogSha256=digest(rejected / 'private-worker.log'),
            acceptedReportPublished=False, partialTerrainPublished=False),
        sensorPaced=False, **dict.fromkeys(FALSE_FLAGS, False))
    with (output / 'verification.json').open('x', encoding='utf-8') as stream:
        json.dump(evidence, stream, indent=2, allow_nan=False)
        stream.write('\n')
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('static-config', 'moving-config', 'native-receipt', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    previous = os.umask(0o077)
    try:
        result = verify(parser.parse_args())
        print(json.dumps(dict(result=result['result'], repeatedIdentically=result['repeatedIdentically'],
            windows=len(result['staticRecording']['windows']), timing=result['measuredHostProcessingSeconds'], navigationReady=False)))
    finally:
        os.umask(previous)
