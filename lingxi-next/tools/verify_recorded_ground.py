"""Replay ground fusion twice and preserve declared-frame rejection, without robot IO."""
import argparse
import json
import os
from pathlib import Path
import re

from check_ground_native import verified_build
from evaluate_recorded_clearance import ROOT, configuration, digest, run, source_names

FALSE_FLAGS = ('robotContacted', 'sensorsSubscribed', 'physicalMotionSent',
               'physicalScaleValidated', 'groundSupportValidated', 'navigationReady')
SUMMARY_KEYS = ('mapReceiptSha256', 'bindingSha256', 'archiveSha256', 'registeredGeometrySha256',
    'geometryDeclarationSha256', 'observationsSha256', 'raySelection', 'scans', 'sourceReturns',
    'selectedReturns', 'omittedReturns', 'projection', 'statistics', 'clearanceSha256',
    'clearanceBytesSha256', 'nativeSha256', 'loadedDependenciesSha256')


def native_receipt(value, library, sha256, build_report):
    if (value.get('result') != 'native_synthetic_ground_fusion_passed'
            or value.get('nativeSha256') != sha256 or digest(library) != sha256
            or value.get('groundBuildReportSha256') != digest(build_report)
            or value.get('compiledAdapterSha256') != digest(ROOT / 'robot-navigation/native-ground/ground_bridge.cpp')
            or type(value.get('testsRun')) is not int or value['testsRun'] <= 0
            or any(type(value.get(key)) is not int or value[key] != 0 for key in ('failures', 'errors', 'skipped'))
            or any(value.get(key) is not False for key in FALSE_FLAGS)
            or type(value.get('sourcesSha256')) is not dict or not value['sourcesSha256']
            or any(digest(ROOT / name) != expected for name, expected in value['sourcesSha256'].items())):
        raise ValueError('current_source_native_ground_verification_required')
    verified_build(library, sha256, build_report)


def stable_summary(report):
    if (any(report.get(key) is not False for key in FALSE_FLAGS)
            or report.get('groundFrameValidated') is not False
            or report.get('obstacleCompletenessValidated') is not False
            or report.get('offlineOnly') is not True):
        raise ValueError('declared_offline_recording_not_acceptance_required')
    ground = {key: value for key, value in report['ground'].items() if key != 'durationSeconds'}
    return {**{key: report[key] for key in SUMMARY_KEYS}, 'ground': ground}


def verify(args):
    from refine_saved_map import private_output
    native = json.loads(args.native_receipt.read_bytes())
    native_receipt(native, args.library, args.sha256, args.build_report)
    static, moving = configuration(args.static_config), configuration(args.moving_config)
    sources = {name: digest(ROOT / name) for name in set(source_names(static)) | {
        'tools/verify_recorded_ground.py', 'tools/check_ground_native.py', 'tools/build_ground_offline.py'}}
    output = private_output(args.output)
    output.mkdir(mode=0o700)
    configs = []
    for name, value in (('static', static), ('moving', moving)):
        if 'ground' not in value:
            raise ValueError('existing_ground_declaration_required_no_automatic_calibration')
        value['ground']['library'], value['ground']['sha256'] = str(args.library), args.sha256
        path = output / (name + '-config.json')
        with path.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, allow_nan=False)
        configs.append(path)
    reports = [run(configs[0], output / name) for name in ('static-first', 'static-repeat')]
    summary = stable_summary(reports[0])
    if stable_summary(reports[1]) != summary:
        raise ValueError('identical_recorded_ground_and_clearance_replay_required')
    rejected = output / 'moving-rejected'
    try:
        run(configs[1], rejected)
    except RuntimeError:
        log = (rejected / 'private-worker.log').read_text(encoding='utf-8')
        match = re.search(r'GroundFrameMismatch: .*scan=(\d+), height_error_metres=([\d.]+), origin_span_metres=([\d.]+);', log)
        if not match or any(path.exists() for path in (rejected / name for name in (
                'report.json', 'report.pending', 'private-declared-clearance.bin',
                'private-declared-ground.bin', 'private-declared-traversability.bin'))):
            raise ValueError('explicit_frame_rejection_without_partial_artifacts_required')
    else:
        raise ValueError('moving_declaration_must_not_be_silently_relaxed')
    if any(digest(ROOT / name) != expected for name, expected in sources.items()):
        raise ValueError('unchanged_ground_verification_sources_required')
    evidence = dict(schemaVersion=1, date='2026-11-01',
        result='recorded_ground_fusion_repeated_with_frame_rejection_not_navigation',
        inputClassification='preserved_recordings_with_uncalibrated_explicit_geometry',
        sourcesSha256=sources, nativeVerification=native,
        nativeVerificationReceiptSha256=digest(args.native_receipt),
        rawReportsSha256=[digest(output / name / 'report.json') for name in ('static-first', 'static-repeat')],
        staticRecording=summary, identicalRepeat=True,
        staticWorkerSeconds=[report['workerDurationSeconds'] for report in reports],
        staticPeakRssKiB=[report['peakRssKiB'] for report in reports],
        movingRecording=dict(configurationSha256=digest(configs[1]),
            result='declared_ground_frame_mismatch', scan=int(match[1]),
            heightErrorMetres=float(match[2]), originSpanMetres=float(match[3]),
            privateFailureLogSha256=digest(rejected / 'private-worker.log'),
            acceptedReportPublished=False, partialArtifactsPublished=False),
        **dict.fromkeys(FALSE_FLAGS, False))
    with (output / 'verification.json').open('x', encoding='utf-8') as stream:
        json.dump(evidence, stream, indent=2, allow_nan=False)
        stream.write('\n')
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('static-config', 'moving-config', 'library', 'build-report', 'native-receipt', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        result = verify(args)
        print(json.dumps(dict(result=result['result'], identicalRepeat=result['identicalRepeat'],
            traversability=result['staticRecording']['ground']['traversabilityCells'], navigationReady=False)))
    finally:
        os.umask(previous)
