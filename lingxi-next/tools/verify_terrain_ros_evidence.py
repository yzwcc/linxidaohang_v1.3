"""Regrade preserved synthetic terrain ROS samples against current sources."""
import argparse
import base64
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
from lingxi_navigation.navfn import finite
from lingxi_navigation.occupancy import ClearanceProjection, digest_value
from prepare_nav2_offline import strict_object
from terrain_nav2_fixture import projection, validate_provenance


def verify(report):
    return verify_common(report, purpose='live_native_synthetic_terrain_ros_expiry_not_field_acceptance',
        modes=('expiry', 'death', 'revoke'))


def verify_common(report, *, purpose, modes, sources=None):
    if (type(report) is not dict or report.get('schemaVersion') != 1
            or report.get('purpose') != purpose
            or report.get('mode') not in modes or report.get('passed') is not True
            or report.get('syntheticInputs') is not True or report.get('rosDistro') != 'humble'
            or report.get('lifecycleStates') != [3] * 4
            or report.get('actualCostmapReplacementVerified') is not True
            or any(report.get(key) is not False for key in ('physicalScaleValidated', 'groundSupportValidated',
                'navigationReady', 'physicalMotionSent', 'robotContacted', 'physicalStopAcknowledged'))):
        raise ValueError('explicit_synthetic_only_terrain_ros_evidence_required')
    required_sources = {'tools/probe_terrain_ros.py', 'tools/terrain-ros-isolated.sh',
        'robot-navigation/lingxi_navigation/terrain_lane.py', 'robot-navigation/lingxi_navigation/terrain_ros.py',
        'robot-navigation/lingxi_navigation/terrain_feed.py', 'robot-navigation/lingxi_navigation/terrain_process.py',
        'robot-navigation/lingxi_navigation/terrain_worker.py', 'robot-navigation/lingxi_navigation/terrain_protocol.py',
        'robot-navigation/lingxi_navigation/terrain.py', 'tools/terrain_nav2_fixture.py', 'tools/run_nav2_offline.py'}
    if sources is not None:
        required_sources = set(sources)
    if set(report.get('sourcesSha256', {})) != required_sources:
        raise ValueError('complete_terrain_ros_sources_required')
    preparation_sources = {'robot-navigation/lingxi_navigation/nav2_profile.py',
        'robot-navigation/lingxi_navigation/runtime.py', 'robot-navigation/nav2_offline.launch.py',
        'tools/prepare_nav2_offline.py', 'robot-navigation/lingxi_navigation/traversability.py',
        'robot-navigation/lingxi_navigation/occupancy.py', 'robot-navigation/lingxi_navigation/navfn.py',
        'tools/humble_velocity_source.py'}
    if set(report.get('preparationSources', {})) != preparation_sources:
        raise ValueError('complete_nav2_preparation_sources_required')
    for table in ('sourcesSha256', 'preparationSources'):
        if not report.get(table):
            raise ValueError('source_bound_terrain_ros_evidence_required')
        for name, sha in report[table].items():
            path = ROOT / name
            if (not digest_value(sha) or not path.is_relative_to(ROOT) or path.resolve() != path
                    or hashlib.sha256(path.read_bytes()).hexdigest() != sha):
                raise ValueError('terrain_ros_source_drift')
    validate_provenance(report['nativeBuild'])
    for key in ('groundLibrarySha256', 'octomapLibrarySha256'):
        if not digest_value(report.get(key)):
            raise ValueError('native_terrain_identity_required')
    checks = {'nativeProcessProducedFree', 'nativeWorkDidNotBlockPublication', 'globalReceivedFree',
        'localReceivedFree', 'initialPlannerSucceeded', 'controllerProducedNonzero', 'fullUnknownPublished',
        'steadyTimerIndependentOfPausedClock', 'lateSubscriberUnknown', 'globalRevokedOldFree',
        'localRevokedOldFree', 'plannerRejectedExpiredTerrain', 'controllerAborted', 'noNonzeroAfterConsumption'}
    if set(report.get('checks', {})) != checks or any(report['checks'][key] is not True for key in checks):
        raise ValueError('complete_passing_terrain_ros_checks_required')
    grid = report['actualNativeGrid']
    spec = ClearanceProjection(**grid['projection'])
    costs = base64.b64decode(grid['costsBase64'], validate=True)
    if (spec != projection() or not digest_value(grid.get('observationsSha256'))
            or len(costs) != spec.width * spec.height or not set(costs) <= {0, 254, 255} or not costs.count(0)
            or hashlib.sha256(costs).hexdigest() != grid['costsSha256']
            or grid['cells'] != {str(c): costs.count(c) for c in (0, 254, 255)}):
        raise ValueError('complete_native_terrain_bytes_required')

    def decode_map(value):
        width, height = value['width'], value['height']
        resolution, origin = value['resolution'], value['origin']
        raw = base64.b64decode(value['cellsBase64'], validate=True)
        if (type(width) is not int or type(height) is not int or not 1 <= width <= 4096
                or not 1 <= height <= 4096 or len(raw) != width * height or not finite(resolution)
                or resolution <= 0 or len(origin) != 2 or not all(map(finite, origin))
                or not all(c <= 100 or c == 255 for c in raw)):
            raise ValueError('complete_ros_costmap_samples_required')
        return width, height, resolution, origin, raw

    def cell(value, x, y):
        width, height, resolution, origin, raw = value
        ix, iy = math.floor((x - origin[0]) / resolution), math.floor((y - origin[1]) / resolution)
        return raw[iy * width + ix] if 0 <= ix < width and 0 <= iy < height else None

    before = {name: decode_map(value) for name, value in report['initialMaps'].items()}
    after = {name: decode_map(value) for name, value in report['withdrawnMaps'].items()}
    if set(before) != {'source', 'global', 'local'} or set(after) != {'source', 'global', 'local', 'late'}:
        raise ValueError('both_consumers_and_late_source_samples_required')
    for source in (before['source'], after['source'], after['late']):
        if (source[:2] != (spec.width, spec.height)
                or not math.isclose(source[2], spec.resolution_metres, rel_tol=0., abs_tol=1e-7)
                or source[3] != [spec.origin_x_metres, spec.origin_y_metres]):
            raise ValueError('unchanged_native_source_geometry_required')
    expected_occupancy = bytes({0: 0, 254: 100, 255: 255}[c] for c in costs)
    if before['source'][-1] != expected_occupancy:
        raise ValueError('actual_native_source_map_required')
    if any(set(after[name][-1]) != {255} for name in ('source', 'late')):
        raise ValueError('full_unknown_replacement_required')
    for name in ('global', 'local'):
        if cell(before[name], 1., 0.) != 0:
            raise ValueError('actual_initial_free_consumer_required')
        for i, c in enumerate(costs):
            if c == 0:
                x = spec.origin_x_metres + (i % spec.width + .5) * spec.resolution_metres
                y = spec.origin_y_metres + (i // spec.width + .5) * spec.resolution_metres
                if cell(after[name], x, y) not in (255, 100):
                    raise ValueError('old_supported_cells_not_revoked')
    if (report['controllerStatus'] != 6 or report['expiredPlannerStatus'] != 6
            or type(report['nativePendingPublications']) is not int or report['nativePendingPublications'] <= 3
            or not finite(report['withdrawalSeconds']) or not 0 <= report['withdrawalSeconds'] <= 1.
            or not finite(grid['ingressToResultSeconds']) or not 0 < grid['ingressToResultSeconds'] < grid['ttlSeconds']):
        raise ValueError('bounded_terrain_withdrawal_and_actual_consumer_results_required')
    for command in report['postConsumptionCommands']:
        if len(command) != 3 or not all(map(finite, command)) or any(abs(v) > 1e-6 for v in command):
            raise ValueError('nonzero_command_after_terrain_consumption')
    return dict(mode=report['mode'], passed=True, sourceBound=True, rawCostmapsRegraded=True,
                navigationReady=False, physicalStopAcknowledged=False)


def load(path):
    path = Path(path)
    if path.stat().st_size > 256 * 1024:
        raise ValueError('bounded_terrain_ros_report_required')
    value = json.loads(path.read_bytes(), object_pairs_hook=strict_object)
    return verify(value)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    args = parser.parse_args()
    print(json.dumps([load(path) for path in args.reports], indent=2))
