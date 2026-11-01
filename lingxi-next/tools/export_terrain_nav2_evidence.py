"""Regrade actual native terrain and all four isolated ROS trial reports."""
import argparse
import hashlib
import json
from pathlib import Path

from export_nav2_evidence import (FALSE_FLAGS, RUNNER, audit, digest, reject_constant)
from prepare_nav2_offline import ROOT, strict_object
from terrain_nav2_fixture import CASES, SOURCE_NAMES


def collect(report_paths, fixture_paths):
    if set(report_paths) != set(CASES) or set(fixture_paths) != set(CASES):
        raise ValueError('all_four_native_terrain_scenes_required')
    summaries, reports = [], []
    for case in CASES:
        raw = Path(report_paths[case]).read_bytes()
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError('bounded_native_nav2_report_required')
        report = json.loads(raw, object_pairs_hook=strict_object, parse_constant=reject_constant)
        if report['case'] != case:
            raise ValueError('matching_native_terrain_case_required')
        summary = audit(report, terrain_fixture=Path(fixture_paths[case]))
        summary['reportSha256'] = hashlib.sha256(raw).hexdigest()
        summaries.append(summary)
        reports.append(report)
    first = reports[0]
    if any(report['preparedSource'] != first['preparedSource'] or report['installedPackages'] != first['installedPackages']
           or report['nativeTerrain']['nativeBuild'] != first['nativeTerrain']['nativeBuild'] for report in reports):
        raise ValueError('same_ros_and_native_builds_required')
    return dict(schemaVersion=1, date='2026-11-01', purpose=first['purpose'],
        installedPackages=first['installedPackages'], upstream=first['preparedSource'],
        sourceSha256={**first['preparationSources'], **{name: digest(ROOT / name) for name in SOURCE_NAMES},
            RUNNER: first['runnerSha256'], 'tools/nav2-offline-isolated.sh': digest(ROOT / 'tools/nav2-offline-isolated.sh'),
            'tools/export_nav2_evidence.py': digest(ROOT / 'tools/export_nav2_evidence.py'),
            'tools/export_terrain_nav2_evidence.py': digest(__file__)},
        cases=summaries, requiredEnvelopeRadiusMetres=first['requiredEnvelopeRadiusMetres'],
        syntheticInputs=True, controllerExecuted=True, liveFreshnessProven=False,
        physicalScaleValidated=False, groundSupportValidated=False, robotContacted=False,
        observedDdsPublicationTimestamp=True,
        publicationTimestampScope='velocity_smoother_dds_write_not_controller_compute',
        **dict.fromkeys(FALSE_FLAGS, False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('report', 'fixture'):
        parser.add_argument('--' + name, nargs=2, action='append', required=True, metavar=('CASE', 'PATH'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if len(args.report) != 4 or len(args.fixture) != 4:
        parser.error('exactly_four_reports_and_fixtures_required')
    value = collect(dict(args.report), dict(args.fixture))
    with args.output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps(dict(cases=4, passed=True, robotContacted=False, physicalMotionSent=False, navigationReady=False)))
