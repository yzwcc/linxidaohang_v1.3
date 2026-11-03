"""Explicit passive acquisition; optional private ROS terrain, never motion."""
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'robot-slam'), str(ROOT / 'robot-navigation'), str(ROOT / 'tools')]
import acquisition_bundle
from localization_control import LocalizedNative, direct, digest, strict_json
from mapping_platform import verify_loaded_libraries
from registered_terrain import RegisteredTerrainSource
from lingxi_slam.acquisition_output import NativeAcquisitionOutput
from lingxi_navigation.ground import GroundDeclaration
from lingxi_navigation.occupancy import ClearanceProjection, DEPENDENCIES, canonical, digest_value
from lingxi_navigation.recorded_occupancy import OfflineGeometry
from lingxi_navigation.terrain_lane import TerrainLane, LaneSnapshot
from lingxi_navigation.terrain_process import TerrainProcess
from lingxi_navigation.terrain import TerrainBudget

PURPOSE = 'passive_acquisition_and_declared_terrain_not_navigation'
SOURCES = ('robot-runtime/python/live_acquisition.py', 'robot-runtime/python/acquisition_bundle.py',
    'robot-runtime/python/registered_terrain.py', 'robot-navigation/lingxi_navigation/registered_window.py',
    'robot-navigation/lingxi_navigation/terrain_lane.py', 'robot-navigation/lingxi_navigation/terrain_process.py',
    'robot-navigation/lingxi_navigation/terrain.py', 'robot-navigation/lingxi_navigation/terrain_protocol.py',
    'robot-navigation/lingxi_navigation/terrain_worker.py', 'robot-navigation/lingxi_navigation/ground.py',
    'robot-navigation/lingxi_navigation/occupancy.py', 'robot-navigation/lingxi_navigation/recorded_occupancy.py',
    'tools/check_ground_native.py', 'robot-runtime/python/terrain_bundle.py', 'tools/terrain_target_probe.py')
ROS_SOURCES = ('robot-runtime/python/live_terrain_ros.py',
    'robot-navigation/lingxi_navigation/terrain_ros.py', 'robot-navigation/lingxi_navigation/terrain_feed.py')
LOCALIZATION_ROS_SOURCES = ('robot-runtime/python/live_localization_ros.py',
    'robot-runtime/python/live_base_pose.py', 'robot-slam/lingxi_slam/base_pose.py',
    'robot-slam/lingxi_slam/registered_acquisition.py')


def metadata(path, expected, maximum=262144):
    path = Path(path)
    if not 0 < direct(path).st_size <= maximum or not digest_value(expected) or digest(path.read_bytes()) != expected:
        raise ValueError('pinned_bounded_acquisition_runtime_input_required')
    return path


def terrain_settings(value):
    """Keep native ground/clearance independent from the display height filter."""
    if (type(value) is not dict or set(value) not in ({'geometry', 'projection', 'ground', 'octomap'},
            {'geometry', 'projection', 'ground', 'octomap', 'targetTrial'})):
        raise ValueError('complete_declared_terrain_settings_required')
    geometry = value['geometry']
    if type(geometry) is not dict or set(geometry) != {'metres_per_raw_unit', 'declared_from_raw'}:
        raise ValueError('explicit_declared_terrain_geometry_required')
    declared = OfflineGeometry(geometry['metres_per_raw_unit'], tuple(geometry['declared_from_raw']))
    projection = ClearanceProjection(**value['projection'])
    ground, octomap = value['ground'], value['octomap']
    fields = {'library', 'sha256'} | (set() if 'targetTrial' in value else {'buildReportSha256'})
    if (type(ground) is not dict or set(ground) != fields | {'declaration'}
            or type(octomap) is not dict or set(octomap) != fields | {'dependencies'}
            or type(octomap['dependencies']) is not dict or set(octomap['dependencies']) != DEPENDENCIES
            or not all(map(digest_value, octomap['dependencies'].values()))):
        raise ValueError('explicit_declared_terrain_native_builds_required')
    declaration = GroundDeclaration(**ground['declaration'])
    if 'targetTrial' in value:
        from terrain_bundle import verify_selection
        verify_selection(value['targetTrial'], ground, octomap)
        return declared, projection, declaration
    from check_ground_native import verified_build
    ground_library = metadata(ground['library'], ground['sha256'], 16 * 1024**2)
    ground_report = metadata(ground_library.parent / 'build-report.json', ground['buildReportSha256'])
    verified_build(ground_library, ground['sha256'], ground_report)
    octomap_library = metadata(octomap['library'], octomap['sha256'], 16 * 1024**2)
    report_path = metadata(octomap_library.parent / 'build-report.json', octomap['buildReportSha256'])
    report = strict_json(report_path.read_bytes())
    source = ROOT / 'robot-navigation/native-octomap/octomap_bridge.cpp'
    if (report.get('schemaVersion') != 1 or report.get('octomapVersion') != '1.9.7'
            or report.get('nativeSha256') != octomap['sha256']
            or report.get('sourceSha256') != digest(source.read_bytes())
            or report.get('dependencies') != octomap['dependencies']
            or report.get('freshProcessLoadPassed') is not True
            or report.get('robotContacted') is not False or report.get('navigationReady') is not False):
        raise ValueError('source_bound_declared_terrain_octomap_build_required')
    for name, expected in octomap['dependencies'].items():
        metadata(octomap_library.parent / 'lib' / name, expected, 16 * 1024**2)
    return declared, projection, declaration


class AcquisitionSettings:
    """Reviewed local sidecar, never HTTP input or implicit native selection."""
    def __init__(self, path, platform):
        self.path, self.platform = Path(path), platform
        if not platform.arm64 or platform.mode != 'live':
            raise ValueError('explicit_arm64_live_acquisition_selection_required')
        self.frozen = self.verify()

    @property
    def identity(self):
        return digest(canonical(self.frozen))

    def child_arguments(self):
        if self.verify() != self.frozen:
            raise ValueError('unchanged_selected_acquisition_inputs_required')
        return ['--acquisition-settings', str(self.path), '--acquisition-integrity-sha256', self.identity]

    def verify(self):
        import json5
        if not 0 < direct(self.path).st_size <= 16384:
            raise ValueError('bounded_local_acquisition_settings_required')
        payload = self.path.read_bytes()
        value = strict_json(payload)
        if (type(value) is not dict or not {'schemaVersion', 'purpose', 'acquisition'} <= set(value)
                or set(value) - {'schemaVersion', 'purpose', 'acquisition', 'terrain', 'rosOutput', 'localizationRosOutput'}
                or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
                or value['purpose'] != PURPOSE or type(value['acquisition']) is not dict
                or set(value['acquisition']) != {'root', 'manifestSha256'}):
            raise ValueError('explicit_passive_acquisition_settings_required')
        self.ros_output = 'rosOutput' in value
        if self.ros_output and ('terrain' not in value or type(value['rosOutput']) is not dict
                or value['rosOutput'] not in ({'mode': 'private_declared_terrain'}, {'mode': 'private_saved_map_terrain'})):
            raise ValueError('explicit_private_declared_terrain_ros_output_required')
        self.localization_ros_output = 'localizationRosOutput' in value
        if self.localization_ros_output and (type(value['localizationRosOutput']) is not dict
                or value['localizationRosOutput'] != {'mode': 'private_saved_map_observations'}):
            raise ValueError('explicit_private_saved_map_observations_required')
        self.saved_map_terrain = self.ros_output and value['rosOutput']['mode'] == 'private_saved_map_terrain'
        if self.saved_map_terrain and not self.localization_ros_output:
            raise ValueError('paired_saved_map_terrain_and_observations_required')
        selection = value['acquisition']
        root = Path(selection['root'])
        bundle = acquisition_bundle.verify_bundle(root, selection['manifestSha256'],
            core_libraries=self.platform.frozen['coreLibrariesSha256'])
        config_path = self.platform.config_dir / 'config_odometry_cpu.json'
        if not 0 < direct(config_path).st_size <= 262144:
            raise ValueError('bounded_acquisition_odometry_configuration_required')
        config_bytes = config_path.read_bytes()
        odometry = json5.loads(config_bytes.decode('utf-8'), allow_duplicate_keys=False)
        if odometry.get('odometry_estimation', {}).get('save_imu_rate_trajectory') is not True:
            raise ValueError('explicit_enabled_acquisition_imu_trajectory_required')
        self.value = value
        self.library = root / 'lib' / acquisition_bundle.LIBRARY
        self.library_sha256 = bundle['files']['lib/' + acquisition_bundle.LIBRARY]['sha256']
        self.terrain = terrain_settings(value['terrain']) if 'terrain' in value else None
        if self.saved_map_terrain:
            import numpy as np
            if not np.array_equal(np.array(self.terrain[0].declared_from_raw).reshape(4, 4), np.eye(4)):
                raise ValueError('identity_saved_map_terrain_geometry_required')
            base = getattr(self.platform, 'value', {}).get('basePose', {})
            if base.get('metresPerMapUnit') != self.terrain[0].metres_per_raw_unit:
                raise ValueError('same_explicit_saved_map_base_and_terrain_scale_required')
        if self.terrain is not None:
            terrain = value['terrain']
            paths = [Path(terrain[name]['library']) for name in ('ground', 'octomap')]
            paths += [paths[1].parent / 'lib' / name for name in terrain['octomap']['dependencies']]
            for path in paths:
                with path.open('rb') as stream:
                    header = stream.read(20)
                if (len(header) != 20 or header[:6] != b'\x7fELF\x02\x01'
                        or int.from_bytes(header[18:20], 'little') != 183):
                    raise ValueError('matching_arm64_terrain_native_libraries_required')
        frozen = dict(settingsSha256=digest(payload), acquisitionManifestSha256=selection['manifestSha256'],
            acquisitionLibrarySha256=self.library_sha256, odometryConfigurationSha256=digest(config_bytes),
            sourceSha256={name: digest((ROOT / name).read_bytes()) for name in
                (*SOURCES, *(ROS_SOURCES if self.ros_output else ()),
                 *(LOCALIZATION_ROS_SOURCES if self.localization_ros_output else ()))})
        if self.ros_output:
            frozen['rosOutputMode'] = value['rosOutput']['mode']
        if self.localization_ros_output:
            frozen['localizationRosOutputMode'] = 'private_saved_map_observations'
        if self.terrain is not None and 'targetTrial' in value['terrain']:
            trial = value['terrain']['targetTrial']
            frozen.update(terrainTrialManifestSha256=trial['manifestSha256'],
                          terrainTargetReportSha256=trial['reportSha256'])
        if self.path.read_bytes() != payload or config_path.read_bytes() != config_bytes:
            raise ValueError('unchanged_acquisition_settings_during_preflight_required')
        return frozen


def child_settings(args, platform):
    path = getattr(args, 'acquisition_settings', None)
    expected = getattr(args, 'acquisition_integrity_sha256', None)
    if path is None:
        if expected is not None:
            raise ValueError('paired_acquisition_settings_and_identity_required')
        return None
    settings = AcquisitionSettings(path, platform)
    if not digest_value(expected) or settings.identity != expected:
        raise ValueError('same_parent_selected_acquisition_inputs_required')
    return settings


class AcquisitionComposition:
    """Native hooks stay on the existing sensor owner; terrain has one bounded child."""
    def __init__(self, settings, session, output):
        if type(settings) is not AcquisitionSettings or settings.verify() != settings.frozen:
            raise ValueError('verified_current_acquisition_settings_required')
        self.settings, self.session, self.output = settings, session, Path(output)
        self.lock = threading.Lock()
        self.source = self.lane = self.service = self.rays = None
        self.created = self.closed = self.failed = False
        self.failure_reason = self.retirement_reason = None
        self.counts = None
        self.registered_packets = self.registered_returns = self.submissions = 0
        self.pending_terrain = None
        self.terrain_budget = (TerrainBudget(maximum_span_seconds=.2, maximum_processing_seconds=.2,
            maximum_age_seconds=.3) if settings.saved_map_terrain else TerrainBudget())
        self.localization_ros_output = None
        if settings.localization_ros_output:
            from live_localization_ros import LiveLocalizationRosOutput
            self.localization_ros_output = LiveLocalizationRosOutput(session)
        self.ros_output = None
        if settings.ros_output:
            from live_terrain_ros import LiveTerrainRosOutput
            options = dict(observations=self.localization_ros_output) if settings.saved_map_terrain else {}
            self.ros_output = LiveTerrainRosOutput(settings.terrain[1], self.terrain_snapshot, self.revoke, **options)

    def terrain_snapshot(self):
        with self.lock:
            source, closed = self.source, self.closed
        if closed:
            return LaneSnapshot(0, 'retired', failure='live_acquisition_revoked')
        if source is not None:
            return source.snapshot()
        return LaneSnapshot(0, 'starting', failure='terrain_starting')

    def factory(self, point_factory):
        with self.lock:
            if self.created or self.closed:
                raise RuntimeError('single_acquisition_localization_composition_required')
            self.created = True
        composition = self
        class AcquiringNative(LocalizedNative):
            def close(self):
                if threading.get_ident() != self.owner:
                    raise RuntimeError('same_thread_localization_native_close_required')
                if self.closed:
                    return
                try:
                    return super().close()
                except BaseException:
                    composition.failed = True
                    raise
                finally:
                    composition.failed = composition.failed or self.failed
                    try:
                        composition.counts = composition.rays.counts()
                    finally:
                        composition.revoke()
        try:
            if self.settings.verify() != self.settings.frozen:
                raise ValueError('unchanged_acquisition_inputs_at_native_start_required')
            if self.settings.terrain is not None:
                geometry, projection, declaration = self.settings.terrain
                terrain = self.settings.value['terrain']
                def native_terrain():
                    ground, octomap = terrain['ground'], terrain['octomap']
                    self.service = TerrainProcess(ground_library=Path(ground['library']), ground_sha256=ground['sha256'],
                        octomap_library=Path(octomap['library']), octomap_sha256=octomap['sha256'],
                        dependencies=octomap['dependencies'], projection=projection, declaration=declaration,
                        stderr_path=self.output / 'private-terrain.log', budget=self.terrain_budget)
                    return self.service
                self.lane = TerrainLane(native_terrain)
                self.source = RegisteredTerrainSource(self.session, self.lane, geometry, projection=projection,
                    budget=self.terrain_budget, saved_map=self.settings.saved_map_terrain)
            self.rays = NativeAcquisitionOutput(self.settings.library)
            verify_loaded_libraries({self.settings.library: self.settings.library_sha256}, Path('/proc/self/maps').read_text())
            result = AcquiringNative(self.session, point_factory, acquisition_output=self.rays, on_acquisition=self.accept)
            with self.lock:
                revoked = self.closed
            if revoked:
                result.close()
                raise RuntimeError('acquisition_start_revoked')
            return result
        except BaseException:
            self.failed = True
            self.session.stop_admission()
            try:
                if self.rays is not None:
                    self.rays.close()
                    self.counts = self.rays.counts()
            finally:
                self.revoke()
            raise

    def accept(self, packet):
        self.registered_packets += 1
        self.registered_returns += len(packet.origins)
        if self.localization_ros_output is not None:
            self.localization_ros_output.offer(packet)
        if self.settings.saved_map_terrain:
            self.pending_terrain = None if self.closed else packet
        elif self.source is not None and self.source(packet):
            self.submissions += 1

    def capture_terrain(self, header, algorithm):
        """Join after LiveBasePose.frame on the same native owner, never latest-TF."""
        packet, self.pending_terrain = self.pending_terrain, None
        if packet is None or self.source is None or self.closed:
            return
        base = self.session.state().get('basePose', {})
        if (packet.algorithm_stamp != algorithm or base.get('status') != 'available'
                or base.get('scanHeaderNs') != str(header) or base.get('lastFrame') != packet.native_frame_id):
            return
        if self.source(packet, scan_header_ns=header):
            self.submissions += 1

    def unavailable(self):
        self._health()
        if self.source is None:
            return self.failed
        snapshot = self.source.snapshot()
        self._health(snapshot)
        return self.failed

    def _health(self, snapshot=None):
        if snapshot is None and self.lane is not None:
            snapshot = self.lane.snapshot()
        if snapshot is None:
            return
        with self.lock:
            if self.service is not None and self.service.state == 'failed':
                self.failed = True
                self.failure_reason = self.failure_reason or self.service.failure or 'terrain_process_failed'
            if snapshot.state == 'failed' or snapshot.failure in (
                    'terrain_lane_failed', 'terrain_cleanup_failed', 'mismatched_registered_terrain_result',
                    'wrong_registered_source_owner', 'registered_localization_unavailable',
                    'registered_terrain_input_failed'):
                self.failed = True
                self.failure_reason = self.failure_reason or snapshot.failure or 'terrain_failed'
            if snapshot.state in ('failed', 'retired'):
                self.retirement_reason = self.retirement_reason or snapshot.failure

    def revoke(self):
        self._health()
        self.session.stop_admission()
        if self.localization_ros_output is not None:
            self.localization_ros_output.retire()
        with self.lock:
            self.closed = True
            self.pending_terrain = None
            source = self.source
        if source is not None:
            source.retire('live_acquisition_revoked')
        elif self.lane is not None:
            self.lane.retire('live_acquisition_revoked')
        self._health()

    def finish(self):
        self.revoke()
        try:
            if self.source is not None:
                self.source.close()
            elif self.lane is not None:
                self.lane.close()
        except BaseException:
            self.failed = True
            raise
        finally:
            self._health()
        if self.settings.verify() != self.settings.frozen:
            raise ValueError('unchanged_acquisition_inputs_at_shutdown_required')
        return self.summary()

    def summary(self):
        lane = self.source.snapshot() if self.source is not None else None
        return dict(configured=True, nativeCreated=self.created,
            nativeClosed=self.rays is not None and self.rays.closed, nativeCounts=self.counts,
            registeredPackets=self.registered_packets, registeredReturns=self.registered_returns,
            terrainConfigured=self.settings.terrain is not None, terrainSubmissions=self.submissions,
            sourceRetired=self.source is None or self.source.retired,
            gridAbsent=lane is None or lane.grid is None,
            terrainOwnerClosed=self.lane is None or not self.lane.thread.is_alive(),
            terrainChildCleaned=self.lane is None or self.service is not None and self.service.cleanup_complete,
            failed=self.failed, failureReason=self.failure_reason, retirementReason=self.retirement_reason,
            inputIntegrity=self.settings.frozen,
            **({'terrainRosOutput': self.ros_output.summary()} if self.ros_output is not None else {}),
            **({'localizationRosOutput': self.localization_ros_output.summary()}
               if self.localization_ros_output is not None else {}),
            physicalScaleValidated=False, groundSupportValidated=False, navigationReady=False)


def verify_terminal(value, expected, *, estimated_poses):
    if type(value) is not dict:
        raise ValueError('complete_passive_acquisition_cleanup_required')
    ros = value.get('terrainRosOutput')
    if 'rosOutputMode' in expected:
        if (expected['rosOutputMode'] not in ('private_declared_terrain', 'private_saved_map_terrain')
                or value.get('terrainConfigured') is not True
                or type(ros) is not dict or set(ros) != {'mode', 'started', 'closed', 'failed', 'publications',
                    'physicalScaleValidated', 'groundSupportValidated', 'physicalMotionSent', 'navigationReady'}
                or ros.get('mode') != expected['rosOutputMode']
                or ros.get('started') is not True or ros.get('closed') is not True or ros.get('failed') is not False
                or type(ros.get('publications')) is not int or ros['publications'] < 2
                or any(ros.get(key) is not False for key in ('physicalScaleValidated',
                    'groundSupportValidated', 'physicalMotionSent', 'navigationReady'))):
            raise ValueError('complete_passive_ros_terrain_cleanup_required')
    elif ros is not None:
        raise ValueError('explicit_passive_ros_terrain_selection_required')
    observations = value.get('localizationRosOutput')
    if 'localizationRosOutputMode' in expected:
        if (expected['localizationRosOutputMode'] != 'private_saved_map_observations'
                or type(observations) is not dict or set(observations) != {'mode', 'started', 'closed', 'failed',
                    'publications', 'namespace', 'physicalScaleValidated', 'physicalMotionSent', 'navigationReady'}
                or observations.get('mode') != expected['localizationRosOutputMode']
                or observations.get('started') is not True or observations.get('closed') is not True
                or observations.get('failed') is not False
                or type(observations.get('publications')) is not int or observations['publications'] < 0
                or type(observations.get('namespace')) is not str
                or len(observations['namespace']) != 47 or not observations['namespace'].startswith('lingxi_offline_')
                or any(c not in '0123456789abcdef' for c in observations['namespace'][15:])
                or any(observations.get(key) is not False for key in (
                    'physicalScaleValidated', 'physicalMotionSent', 'navigationReady'))):
            raise ValueError('complete_passive_localization_ros_cleanup_required')
    elif observations is not None:
        raise ValueError('explicit_passive_localization_ros_selection_required')
    if (value.get('inputIntegrity') != expected
            or value.get('configured') is not True or value.get('nativeCreated') is not True
            or type(value.get('terrainConfigured')) is not bool or value.get('failureReason') is not None
            or any(value.get(name) is not True for name in ('nativeClosed', 'sourceRetired', 'gridAbsent',
                'terrainOwnerClosed', 'terrainChildCleaned'))
            or any(value.get(name) is not False for name in ('failed', 'physicalScaleValidated',
                'groundSupportValidated', 'navigationReady'))):
        raise ValueError('complete_passive_acquisition_cleanup_required')
    counts = value.get('nativeCounts')
    names = ('callbacks', 'initializationSkips', 'copies', 'takes', 'discardedOnClose')
    if (type(counts) is not dict or set(counts) != set(names)
            or any(type(counts[name]) is not int or counts[name] < 0 for name in names)
            or counts['initializationSkips'] != 1 or counts['copies'] != counts['takes']
            or counts['callbacks'] != counts['copies'] + 1 or counts['discardedOnClose'] != 0
            or type(estimated_poses) is not int or estimated_poses < 1 or counts['callbacks'] != estimated_poses
            or any(type(value.get(name)) is not int or value[name] < 0 for name in (
                'registeredPackets', 'registeredReturns', 'terrainSubmissions'))
            or value['registeredPackets'] > counts['takes']
            or value['terrainSubmissions'] > value['registeredPackets']
            or not value['terrainConfigured'] and value['terrainSubmissions'] != 0):
        raise ValueError('complete_same_owner_acquisition_counter_accounting_required')
