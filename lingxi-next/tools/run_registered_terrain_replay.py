"""Paced recorded GLIM -> real registration -> native terrain; no ROS or robot IO."""
import argparse
import base64
from collections import Counter
from dataclasses import asdict
from importlib.metadata import version
import json
import hashlib
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'robot-runtime/python'), str(ROOT / 'robot-slam'), str(ROOT / 'robot-navigation')]
import numpy as np
import run_glim_acquisition_trial as acquisition
import run_glim_point_output_trial as points
import run_glim_supervised_replay as replay
import run_seeded_localization_trial as seeded
from check_ground_native import verified_build
from evaluate_recorded_clearance import configuration
from localization_control import LocalizedNative, load_map
from registered_terrain import RegisteredTerrainSource
from lingxi_slam.acquisition_output import NativeAcquisitionOutput
from lingxi_slam.localization_session import LocalizationSession
from lingxi_slam.point_output import NativePointOutput, PointOutputNative
from lingxi_slam.seeded_localization import SeededMapRegistration
from lingxi_navigation.ground import GroundDeclaration
from lingxi_navigation.navfn import finite
from lingxi_navigation.occupancy import ClearanceProjection, digest_value
from lingxi_navigation.recorded_occupancy import OfflineGeometry
from lingxi_navigation.terrain import TerrainBudget
from lingxi_navigation.terrain_lane import TerrainLane
from lingxi_navigation.terrain_process import TerrainProcess
from verify_slam_capture import sha256

RESULT = 'recorded_registered_chain_closed_not_navigation_acceptance'
FIELDS = ('work', 'adapter', 'points', 'acquisition', 'bundle', 'reference', 'saved_map',
          'point_reference', 'terrain_config', 'output', 'hypothesis', 'rate')
SOURCES = ('tools/run_registered_terrain_replay.py', 'robot-runtime/python/localization_control.py',
    'robot-runtime/python/registered_terrain.py', 'robot-slam/lingxi_slam/localization_session.py',
    'robot-slam/lingxi_slam/localization_tracking.py', 'robot-slam/lingxi_slam/seeded_localization.py',
    'robot-slam/lingxi_slam/registered_acquisition.py', 'robot-navigation/lingxi_navigation/registered_window.py',
    'robot-navigation/lingxi_navigation/terrain_lane.py', 'robot-navigation/lingxi_navigation/terrain_process.py',
    'robot-navigation/lingxi_navigation/terrain_protocol.py', 'robot-navigation/lingxi_navigation/terrain_worker.py',
    'robot-navigation/lingxi_navigation/terrain.py', 'robot-navigation/lingxi_navigation/ground.py',
    'robot-navigation/lingxi_navigation/occupancy.py', 'robot-navigation/lingxi_navigation/recorded_occupancy.py')
BOUNDARIES = dict(recordedPoseUsedForInitialSeed=True, possibleMappingSequenceReuse=True,
    replayIngressReceiptNotHistoricalFreshness=True, declaredGroundFrameNotCalibration=True,
    **dict.fromkeys(('robotContacted', 'sensorsSubscribed', 'rosNodeStarted', 'physicalMotionSent',
        'physicalScaleValidated', 'groundSupportValidated', 'navigationReady', 'arm64Executed',
        'independentLocalizationAccuracyValidated', 'liveThroughputValidated'), False))


def load_inputs(args, session):
    frozen = {**points.integrity(args), **acquisition.recorded_integrity(args)}
    preserved = replay.load(args.point_reference / 'report.json')
    for key in ('journalSha256', 'profileSha256', 'configurationSha256', 'nativeLibrariesSha256'):
        if preserved['inputIntegrity'][key] != frozen[key]:
            raise ValueError('same_recorded_seed_reference_required')
    sample = args.point_reference / 'private-first-points.npz'
    point_report = replay.load(args.point_reference / 'point-report.json')
    if (preserved['pointReportSha256'] != sha256(args.point_reference / 'point-report.json')
            or point_report['firstSampleSha256'] != sha256(sample)):
        raise ValueError('unchanged_preserved_first_seed_required')
    _, seed, seed_sha = seeded.load_recorded_scan(sample, seeded.RegistrationConfig())
    with np.load(sample, allow_pickle=False) as archive:
        if int(archive['frame_id']) != 1:
            raise ValueError('first_recorded_seed_frame_required')
        seed_stamp = float(archive['algorithm_stamp'])
    receipt = replay.load(args.saved_map / 'map-ready.json')
    binding, map_points = load_map(args.saved_map, 'slam-' + receipt['session'],
        sha256(args.saved_map / 'map-ready.json'), args.bundle / 'private-profile.json',
        frozen['profileSha256'], session=session, input_mode='recorded',
        sensor_provenance=receipt.get('sensorProvenance'))
    value = configuration(args.terrain_config)
    ground = value['ground']
    ground_library = Path(ground['library'])
    verified_build(ground_library, ground['sha256'], ground_library.parent / 'build-report.json')
    octomap = value['octomap']
    octomap_library = Path(octomap['library'])
    octomap_report = replay.load(octomap_library.parent / 'build-report.json')
    if (sha256(octomap_library) != octomap['sha256'] or octomap_report['nativeSha256'] != octomap['sha256']
            or octomap_report['dependencies'] != octomap['dependencies']):
        raise ValueError('unchanged_recorded_terrain_native_build_required')
    geometry = OfflineGeometry(value['geometry']['metresPerRawUnit'], tuple(value['geometry']['declaredFromRaw']))
    projection = ClearanceProjection(**value['projection'])
    declaration = GroundDeclaration(**ground['declaration'])
    import small_gicp
    frozen.update(chainSourceSha256={name: sha256(ROOT / name) for name in SOURCES},
        mapBinding={key: item for key, item in asdict(binding).items() if key != 'session'},
        seedSha256=seed_sha, seedStamp=seed_stamp, terrainConfigurationSha256=sha256(args.terrain_config),
        groundBuildSha256=sha256(ground_library.parent / 'build-report.json'),
        octomapBuildSha256=sha256(octomap_library.parent / 'build-report.json'),
        smallGicpSha256=sha256(Path(small_gicp.__file__)), geometry=asdict(geometry),
        projection=asdict(projection), groundDeclaration=asdict(declaration), budget=asdict(TerrainBudget()))
    # Private child reports are JSON; compare the same representation on both sides.
    frozen = json.loads(json.dumps(frozen, allow_nan=False))
    return frozen, binding, map_points, seed, value, geometry, projection, declaration


class Composition:
    """Aggregate only, with a single native sensor owner and no automatic reseed."""
    def __init__(self, args, binding, map_points, seed, value, geometry, projection, declaration):
        self.args, self.seed, self.value = args, seed, value
        self.geometry, self.projection, self.declaration = geometry, projection, declaration
        self.session = LocalizationSession(binding, lambda: SeededMapRegistration(map_points))
        self.states, self.registrations = Counter(), 0
        self.submissions, self.results = {}, {}
        self.seed_attempts, self.packet_count, self.total_returns = 0, 0, 0
        self.owner = self.native = self.source = self.service = None
        self.final = None

    def sample(self):
        if self.source is None:
            return
        state = self.source.snapshot()
        grid = state.grid
        if grid is not None and grid.sequence not in self.results:
            submitted = self.submissions[grid.sequence]
            if grid.received_at != submitted['oldestReceipt']:
                raise ValueError('original_oldest_replay_ingress_receipt_required')
            self.results[grid.sequence] = dict(sequence=grid.sequence,
                receivedAt=grid.received_at, observedAt=time.monotonic(), expiresAt=grid.expires_at,
                costsSha256=hashlib.sha256(grid.costs).hexdigest(),
                costsBase64=base64.b64encode(grid.costs).decode('ascii'),
                cells={str(code): grid.costs.count(code) for code in (0, 254, 255)})

    def accept(self, packet):
        self.packet_count += 1
        state = self.native.accept(packet)
        self.states[state['state']] += 1
        if not self.seed_attempts:
            if packet.native_frame_id != 1:
                raise ValueError('one_first_frame_recorded_seed_required')
            self.seed_attempts += 1
            self.session.request('seed', asdict(self.session.binding), state['generation'],
                seed=self.seed, reference_frame=packet.native_frame_id)

    def registered(self, packet):
        self.registrations += 1
        self.total_returns += len(packet.origins)
        self.sample()
        if self.source(packet):
            window = self.source.submitted
            self.submissions[packet.native_frame_id] = dict(sequence=packet.native_frame_id,
                oldestReceipt=window.received_at, latestReceipt=window.latest_received_at,
                firstStamp=window.observations.scans[0].stamp, lastStamp=window.observations.scans[-1].stamp,
                scans=len(window.observations.scans), sourceReturns=window.source_returns,
                selectedReturns=window.selected_returns, observationsSha256=window.observations.sha256,
                projectionWorkUnits=window.projection_work_units,
                workEvictedScans=window.work_evicted_scans, workEvictedReturns=window.work_evicted_returns)

    def factory(self):
        from run_glim_offline_trial import Native
        self.owner = threading.get_ident()
        ground, octomap = self.value['ground'], self.value['octomap']
        def terrain_factory():
            self.service = TerrainProcess(ground_library=Path(ground['library']), ground_sha256=ground['sha256'],
                octomap_library=Path(octomap['library']), octomap_sha256=octomap['sha256'],
                dependencies=octomap['dependencies'], projection=self.projection, declaration=self.declaration,
                stderr_path=self.args.output / 'private-terrain.log')
            return self.service
        self.source = RegisteredTerrainSource(self.session, TerrainLane(terrain_factory), self.geometry,
            projection=self.projection)
        point_output = ray_output = None
        composition = self
        class MeasuredNative(LocalizedNative):
            def imu(self, *args):
                composition.sample()
                return super().imu(*args)

            def close(self):
                if self.closed:
                    return
                try:
                    composition.sample()
                    before = self.session.state()
                    lane = composition.source.snapshot()
                    self.session.stop_admission()
                    withdrawn = composition.source.snapshot()
                    composition.final = dict(beforeState=before['state'], laneState=lane.state,
                        laneFailure=lane.failure, retired=composition.source.retired, gridAbsent=withdrawn.grid is None)
                finally:
                    try:
                        super().close()
                    finally:
                        composition.source.close()
                composition.final.update(workerClosed=self.session.state()['workerClosed'],
                    terrainOwnerClosed=not composition.source.lane.thread.is_alive(),
                    terrainChildCleaned=composition.service is not None and composition.service.cleanup_complete,
                    pointCounts=point_output.counts(), acquisitionCounts=ray_output.counts())
        try:
            point_output = NativePointOutput(self.args.points / 'build' / points.LIBRARY)
            ray_output = NativeAcquisitionOutput(self.args.acquisition / 'build' / acquisition.LIBRARY)
            self.native = MeasuredNative(self.session, lambda accept: PointOutputNative(
                lambda: Native(self.args.adapter / 'build/liblingxi_glim_offline.so', self.args.bundle / 'config',
                    self.args.work / 'prefix/lib/libodometry_estimation_cpu.so'), point_output, self.accept),
                acquisition_output=ray_output, on_acquisition=self.registered)
            return self.native
        except BaseException:
            try:
                if point_output is not None:
                    point_output.close()
            finally:
                try:
                    if ray_output is not None:
                        ray_output.close()
                finally:
                    self.source.close()
            raise

    def report(self):
        failure = None
        log = self.args.output / 'private-terrain.log'
        if log.is_file() and not log.is_symlink() and log.stat().st_size <= 1024 * 1024:
            text = log.read_text(encoding='utf-8', errors='replace')
            for reason in ('octomap_work_budget_exhausted', 'terrain_native_processing_deadline_exceeded',
                           'declared_ground_height_and_bounded_deskewed_scan_required'):
                if reason in text:
                    failure = reason
                    break
        return dict(packets=self.packet_count, registeredPackets=self.registrations,
            registeredReturns=self.total_returns, seedAttempts=self.seed_attempts,
            pointStateCounts=dict(self.states), submissions=list(self.submissions.values()),
            observedResults=list(self.results.values()), final=self.final,
            projection=asdict(self.projection),
            budget=asdict(TerrainBudget()), maximumPendingNativeJobs=1, nativeFailureReason=failure)


def grade_chain(value, estimated_poses, projection):
    """A closed diagnostic may correctly expose a rejected default workload."""
    budget = TerrainBudget()
    spec = ClearanceProjection(**projection)
    states = value['pointStateCounts']
    if (type(estimated_poses) is not int or not 3 <= estimated_poses < 2**53
            or value['projection'] != projection
            or value['budget'] != asdict(budget) or value['maximumPendingNativeJobs'] != 1
            or type(states) is not dict or not set(states) <= {'unseeded', 'acquiring', 'tracking', 'lost'}
            or any(type(count) is not int or count < 0 for count in states.values())
            or any(type(value[key]) is not int for key in
                ('packets', 'registeredPackets', 'registeredReturns', 'seedAttempts'))
            or value['packets'] != estimated_poses - 1 or value['seedAttempts'] != 1
            or sum(states.values()) != value['packets']
            or not 0 < value['registeredPackets'] <= value['packets']
            or value['registeredPackets'] != states.get('tracking', 0)
            or not value['registeredReturns'] >= value['registeredPackets']
            or any(value['final'][key] is not True for key in
                ('retired', 'gridAbsent', 'workerClosed', 'terrainOwnerClosed', 'terrainChildCleaned'))):
        raise ValueError('complete_registered_replay_accounting_and_retirement_required')
    expected = dict(callbacks=estimated_poses, initializationSkips=1,
        copies=value['packets'], takes=value['packets'], discardedOnClose=0)
    if any(value['final'][key] != expected for key in ('pointCounts', 'acquisitionCounts')):
        raise ValueError('complete_paired_native_output_counters_required')
    submissions, results = value['submissions'], value['observedResults']
    if (type(submissions) is not list or not 1 <= len(submissions) <= value['registeredPackets']
            or type(results) is not list or not 0 <= len(results) <= len(submissions)
            or any(type(row['sequence']) is not int or not 1 <= row['sequence'] <= value['packets']
                for row in submissions + results)
            or any(a['sequence'] >= b['sequence'] for a, b in zip(submissions, submissions[1:]))):
        raise ValueError('ordered_bounded_registered_submissions_required')
    by_sequence = {row['sequence']: row for row in submissions}
    for row in submissions:
        if (any(type(row[key]) is not int for key in ('scans', 'selectedReturns', 'sourceReturns',
                    'projectionWorkUnits', 'workEvictedScans', 'workEvictedReturns'))
                or not 1 <= row['scans'] <= budget.maximum_scans
                or not 1 <= row['selectedReturns'] == row['sourceReturns'] <= budget.maximum_rays
                or not 1 <= row['projectionWorkUnits'] <= budget.maximum_work_units
                or not 0 <= row['workEvictedScans'] <= value['registeredPackets']
                or not 0 <= row['workEvictedReturns'] <= value['registeredReturns']
                or not all(finite(row[key]) for key in ('oldestReceipt', 'latestReceipt', 'firstStamp', 'lastStamp'))
                or not 0 <= row['oldestReceipt'] <= row['latestReceipt']
                or row['latestReceipt'] - row['oldestReceipt'] >= budget.maximum_age_seconds
                or not 0 < row['firstStamp'] <= row['lastStamp']
                or row['lastStamp'] - row['firstStamp'] > budget.maximum_span_seconds
                or not digest_value(row['observationsSha256'])):
            raise ValueError('bounded_original_receipt_windows_required')
    if (any(a['sequence'] >= b['sequence'] for a, b in zip(results, results[1:]))
            or any(row['sequence'] not in by_sequence for row in results)):
        raise ValueError('ordered_submitted_terrain_results_required')
    for row in results:
        submitted = by_sequence[row['sequence']]
        if (not all(finite(row[key]) for key in ('receivedAt', 'observedAt', 'expiresAt'))
                or row['receivedAt'] != submitted['oldestReceipt']
                or row['expiresAt'] != row['receivedAt'] + budget.maximum_age_seconds
                or not row['receivedAt'] <= row['observedAt'] < row['expiresAt']
                or not submitted['latestReceipt'] <= row['observedAt']
                or not set(row['cells']) == {'0', '254', '255'}
                or any(type(count) is not int or count < 0 for count in row['cells'].values())):
            raise ValueError('fresh_original_ingress_registered_result_required')
        if (type(row['costsBase64']) is not str
                or len(row['costsBase64']) != 4 * ((spec.width * spec.height + 2) // 3)
                or not digest_value(row['costsSha256'])):
            raise ValueError('complete_raw_registered_terrain_required')
        try:
            costs = base64.b64decode(row['costsBase64'], validate=True)
        except ValueError as error:
            raise ValueError('complete_raw_registered_terrain_required') from error
        if (len(costs) != spec.width * spec.height or not set(costs) <= {0, 254, 255}
                or hashlib.sha256(costs).hexdigest() != row['costsSha256']
                or row['cells'] != {str(code): costs.count(code) for code in (0, 254, 255)}):
            raise ValueError('raw_registered_terrain_digest_and_counts_required')
    return dict(completeInput=True, retirementVerified=True,
        defaultBudgetAccepted=(value['final']['laneState'] in ('ready', 'busy')
            and value['nativeFailureReason'] is None and 'lost' not in value['pointStateCounts']
            and value['final']['beforeState'] == 'tracking' and bool(results)),
        pendingAtEnd=value['final']['laneState'] == 'busy', observedTerrainResults=len(results),
        supportedFreeCellsObserved=any(row['cells']['0'] > 0 for row in results),
        navigationReady=False)


def child(args, sink):
    from rosbags.typesys import Stores, get_typestore
    from run_glim_stream_trial import actual_libraries
    from mapping_platform import verify_loaded_libraries
    import small_gicp
    session = os.environ['LINGXI_SLAM_SESSION']
    frozen, binding, map_points, seed, value, geometry, projection, declaration = load_inputs(args, session)
    if any(os.environ.get(key) != item for key, item in replay.child_environment(args.work).items()):
        raise ValueError('explicit_recorded_chain_child_environment_required')
    composition = Composition(args, binding, map_points, seed, value, geometry, projection, declaration)
    types = get_typestore(Stores.ROS2_HUMBLE)
    for name in ('Imu', 'PointCloud2'):
        types.get_msgdef('sensor_msgs/msg/' + name)
    loaded_extensions = {}
    def loaded():
        observed = actual_libraries(args.work, args.adapter, frozen['nativeLibrariesSha256'])
        loaded_extensions.update(points.loaded_extension(args, frozen['pointExtensionLibrarySha256']))
        loaded_extensions.update(acquisition.loaded_extension(args, frozen['acquisitionLibrarySha256']))
        path = Path(small_gicp.__file__).resolve(strict=True)
        verify_loaded_libraries({path: frozen['smallGicpSha256']}, Path('/proc/self/maps').read_text())
        loaded_extensions[path.name] = frozen['smallGicpSha256']
        return observed
    try:
        return replay.recorded_child(args, sink, integrity=frozen, native_factory=composition.factory,
            verify_loaded=loaded, verify_inputs=lambda: load_inputs(args, session)[0],
            decode=lambda kind, data: types.deserialize_cdr(data,
                'sensor_msgs/msg/' + ('Imu' if kind == 'imu' else 'PointCloud2')),
            versions={name: version(name) for name in ('numpy', 'scipy', 'small-gicp', 'rosbags')})
    finally:
        replay.private_json(args.output / 'chain-report.json', dict(composition.report(),
            actualLoadedExtensionsSha256=loaded_extensions, boundaries=BOUNDARIES))


def run(args):
    frozen = load_inputs(args, '1' * 32)[0]
    output = points.private_output(args.output)
    output.mkdir(mode=0o700, parents=True)
    command = [sys.executable, '-B', str(Path(__file__).resolve()), 'child', '--acknowledge-recording-reuse']
    for name in FIELDS:
        command.extend(('--' + name.replace('_', '-'), str(getattr(args, name))))
    owner = replay.ProcessSupervisor(command, cwd=output, environment=replay.child_environment(args.work),
        stderr_path=output / 'private-child.log', limits=replay.LIMITS)
    report = dict(schemaVersion=1, result='running', inputIntegrity=frozen, boundaries=BOUNDARIES,
        replayRate=args.rate, limits=asdict(replay.LIMITS))
    try:
        report['process'] = replay.coordinate(owner, frozen['journal']['records'], 110.)
        native = replay.load(output / 'child-report.json')
        replay.complete_child(native, frozen['journal'])
        chain = replay.load(output / 'chain-report.json')
        if (native['result'] != replay.CHILD_RESULT or native['inputIntegrity'] != frozen
                or load_inputs(args, '1' * 32)[0] != frozen or chain['boundaries'] != BOUNDARIES):
            raise ValueError('unchanged_complete_registered_chain_required')
        report['grade'] = grade_chain(chain, native['worker']['stream']['counts']['estimatedPoses'], frozen['projection'])
        report['result'] = RESULT
    except BaseException as error:
        report.update(result='failed_or_interrupted', errorType=type(error).__name__)
        raise
    finally:
        report['process'] = owner.stop()
        report['filesSha256'] = {name: sha256(output / name) for name in
            ('child-report.json', 'chain-report.json', 'private-child.log', 'private-terrain.log')
            if (output / name).is_file()}
        replay.private_json(output / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'child'))
    for field in FIELDS:
        option = '--' + field.replace('_', '-')
        if field == 'rate':
            parser.add_argument(option, required=True, type=float, choices=(.5, 1.))
        elif field == 'hypothesis':
            parser.add_argument(option, required=True, choices=('header_is_scan_start', 'header_is_scan_end'))
        elif field == 'output':
            parser.add_argument(option, required=True, type=lambda value: Path(value).absolute())
        else:
            parser.add_argument(option, required=True, type=lambda value: Path(value).resolve(strict=True))
    parser.add_argument('--acknowledge-recording-reuse', required=True, action='store_true')
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        if args.command == 'child':
            with os.fdopen(os.dup(1), 'wb', buffering=0) as sink:
                os.dup2(2, 1)
                return child(args, sink)
        report = run(args)
        print(json.dumps(dict(result=report['result'], grade=report['grade'])))
        return 0
    finally:
        os.umask(previous)


if __name__ == '__main__':
    raise SystemExit(main())
