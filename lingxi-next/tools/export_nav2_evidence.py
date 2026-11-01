"""Summarize six preserved ROS trials, rechecking sources and raw trajectories.

This exports offline regression evidence, not field or binary/source acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path

from prepare_nav2_offline import ROOT, strict_object
from run_nav2_offline import (CASES, FUSED_CASES, assess_fused_costmaps,
                             assess_native_costmaps, assess_trial, world)


PREPARATION_SOURCES = (
    'robot-navigation/lingxi_navigation/nav2_profile.py',
    'robot-navigation/lingxi_navigation/runtime.py',
    'robot-navigation/nav2_offline.launch.py', 'tools/prepare_nav2_offline.py',
    'robot-navigation/lingxi_navigation/traversability.py',
    'robot-navigation/lingxi_navigation/occupancy.py',
    'robot-navigation/lingxi_navigation/navfn.py',
    'tools/humble_velocity_source.py',
)
FALSE_FLAGS = ('groundSupportMeasured', 'observedTwistHasSourceTimestamp',
               'productionCommandAgeProven', 'physicalMotionSent', 'navigationReady',
               'installedBinaryMatchesPinnedSourceVerified')
RUNNER = 'tools/run_nav2_offline.py'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(report, *, terrain_fixture=None):
    case = report['case']
    if case not in CASES:
        raise ValueError('known_offline_scene_required')
    native = terrain_fixture is not None
    if native:
        from terrain_nav2_fixture import load
        source, evidence = load(terrain_fixture, case)
        if report.get('nativeTerrain') != evidence:
            raise ValueError('unchanged_actual_native_terrain_fixture_required')
    fused = case in FUSED_CASES or native
    sources = {name: digest(ROOT / name) for name in PREPARATION_SOURCES}
    policy = ('full_support_fused_revisions_no_secondary_clearing' if fused
              else 'mark_only_latched_no_automatic_clearing')
    if (report['preparationSources'] != sources
            or report['runnerSha256'] != digest(ROOT / RUNNER)
            or report['purpose'] != ('synthetic_native_terrain_nav2_integration_not_field_acceptance' if native
                                     else 'synthetic_planar_nav2_integration_not_field_acceptance')
            or report['passed'] is not True or report['syntheticInputs'] is not True
            or report['lifecycleStates'] != [3] * 4
            or report.get('observedDdsPublicationTimestamp') is not True
            or type(report.get('publicationSamplesVerified')) is not int
            or report['publicationSamplesVerified'] < report['commandCount']
            or report['publicationSamplesVerified'] < 0
            or (case != 'blocked' and report['publicationSamplesVerified'] == 0)
            or (report['publicationSamplesVerified'] == 0
                and report.get('maximumPublicationAgeSeconds') != 0)
            or type(report.get('maximumPublicationAgeSeconds')) not in (int, float)
            or not 0 <= report['maximumPublicationAgeSeconds'] <= .15
            or report.get('publicationTimestampScope') != 'velocity_smoother_dds_write_not_controller_compute'
            or any(report[key] is not False for key in FALSE_FLAGS)
            or report['dynamicObstaclePolicy'] != policy
            or report['syntheticOracleNotOctomap'] is not (fused and not native)
            or report['runtimeState']['offlineOnly'] is not True
            or any(report['runtimeState'][key] is not False
                   for key in ('physicalMotionSent', 'navigationReady'))):
        raise ValueError('current_source_bound_offline_trial_required')
    if not native:
        source = world(case)
    if case == 'disappearing-obstacle':
        source['obstacle_removed_pose_index'] = next(
            (index for index, pose in enumerate(report['trajectory']) if pose[0] >= .15), 0)
    assessment = assess_trial(case, source, report['trajectory'], report['observedCommands'],
                              report['globalPlans'], report['runtimeState'])
    if fused:
        assessment['checks'].update(assess_fused_costmaps(case, report['costmapSamples']))
        if native:
            assessment['checks'].update(assess_native_costmaps(source, report['costmapSamples']))
        assessment['passed'] = all(assessment['checks'].values())
    if (assessment['passed'] is not True or assessment['checks'] != report['checks']
            or report['requiredEnvelopeRadiusMetres'] != assessment['requiredEnvelopeRadiusMetres']
            or report['minimumSweptObstacleDistanceMetres'] != assessment['minimumSweptObstacleDistanceMetres']
            or report['finalPose'] != report['trajectory'][-1]
            or report['commandCount'] != len(report['observedCommands'])
            or report['controllerExecuted'] is not bool(report['observedCommands'])
            or report['maximumAbsY'] != max(abs(pose[1]) for pose in report['trajectory'])
            or (fused and not native and (type(report['fusedMapRevision']) is not int
                           or report['fusedMapRevision'] <= 0
                           or len(report['fusedSnapshotSha256']) != 64))
            or (native and (report['fusedMapRevision'] is not None or report['fusedSnapshotSha256'] is not None))):
        raise ValueError('raw_trajectory_costmap_and_summary_must_agree')
    keys = ('case', 'passed', 'checks', 'finalPose', 'minimumSweptObstacleDistanceMetres',
            'maximumAbsY', 'commandCount', 'elapsedSeconds', 'dynamicObstaclePolicy',
            'syntheticOracleNotOctomap', 'fusedMapRevision', 'fusedSnapshotSha256',
            'publicationSamplesVerified', 'maximumPublicationAgeSeconds')
    return {key: report[key] for key in (*keys, *(('nativeTerrain',) if native else ()))}


def reject_constant(value):
    raise ValueError('nonfinite_report_json')


def collect(paths):
    summaries, reports = {}, []
    for path in paths:
        data = Path(path).read_bytes()
        if len(data) > 16 * 1024 * 1024:
            raise ValueError('bounded_offline_report_required')
        report = json.loads(data, object_pairs_hook=strict_object, parse_constant=reject_constant)
        summary = audit(report)
        if summary['case'] in summaries:
            raise ValueError('one_report_per_scene_required')
        summary['reportSha256'] = hashlib.sha256(data).hexdigest()
        summaries[summary['case']] = summary
        reports.append(report)
    if set(summaries) != set(CASES):
        raise ValueError('all_six_current_source_scenes_required')
    first = reports[0]
    if any(report['preparedSource'] != first['preparedSource']
           or report['installedPackages'] != first['installedPackages'] for report in reports):
        raise ValueError('same_upstream_and_installed_packages_required')
    return dict(schemaVersion=2, date='2026-11-01', purpose=first['purpose'],
        upstream={**first['preparedSource'], 'installedBinaryMatchesPinnedSourceVerified': False},
        installedPackages=first['installedPackages'],
        sourceSha256={**first['preparationSources'], RUNNER: first['runnerSha256'],
                      'tools/export_nav2_evidence.py': digest(__file__)},
        requiredEnvelopeRadiusMetres=first['requiredEnvelopeRadiusMetres'],
        cases=[summaries[case] for case in CASES], syntheticInputs=True,
        controllerExecuted=True, observedDdsPublicationTimestamp=True,
        publicationTimestampScope='velocity_smoother_dds_write_not_controller_compute',
        **dict.fromkeys(FALSE_FLAGS, False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = collect(args.report)
    with args.output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps(dict(cases=len(result['cases']), physicalMotionSent=False, navigationReady=False)))
