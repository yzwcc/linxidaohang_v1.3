"""Passive live composition with synthetic ELF/ctypes/sensors, never robot IO."""
from copy import deepcopy
from dataclasses import asdict, replace
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'robot-runtime/python'), str(ROOT / 'robot-slam'),
               str(ROOT / 'robot-navigation'), str(Path(__file__).parent)]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('numpy', 'scipy', 'json5'))
if AVAILABLE:
    import numpy as np
    import live_acquisition as live
    import controlled_localization
    from lingxi_slam.localization_session import LocalizationBinding, LocalizationSession
    from lingxi_slam.seeded_localization import RegistrationEstimate
    from lingxi_slam.stream import Scan
    from lingxi_slam.point_output import PointPacket
    from test_acquisition_output import Function, Library, other_thread, packet_bytes, pose
    import test_acquisition_bundle as bundle_fixture
    from test_ground import spec
    from test_terrain_lane import FakeProcess, wait_for


class SettingsFixture:
    def prepare(self):
        fixture = bundle_fixture.AcquisitionBundle()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.component = fixture
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.config_dir = self.root / 'config'
        self.config_dir.mkdir()
        self.odometry = self.config_dir / 'config_odometry_cpu.json'
        self.odometry.write_text('{odometry_estimation: {save_imu_rate_trajectory: true}}')
        self.platform = SimpleNamespace(arm64=True, mode='live', config_dir=self.config_dir,
            frozen={'coreLibrariesSha256': fixture.core})
        self.path = self.root / 'settings.json'
        self.value = dict(schemaVersion=1, purpose=live.PURPOSE,
            acquisition=dict(root=str(fixture.root), manifestSha256=fixture.digest))
        self.write()

    def write(self):
        self.path.write_text(json.dumps(self.value), encoding='utf-8')

    def settings(self):
        return live.AcquisitionSettings(self.path, self.platform)

    def terrain(self):
        # Only the ground provenance check is a stub; its own tests check pinned sources.
        self.ground_check = self.enterContext(patch('check_ground_native.verified_build', return_value={}))
        terrain = dict(geometry=dict(metres_per_raw_unit=1.,
            declared_from_raw=list(map(float, np.eye(4).ravel()))), projection=asdict(spec()))
        for name in ('ground', 'octomap'):
            directory = self.root / name
            directory.mkdir()
            library = directory / ('lib' + name + '.so')
            library.write_bytes(b'\x7fELF\x02\x01' + b'\0' * 12 + (183).to_bytes(2, 'little'))
            terrain[name] = dict(library=str(library), sha256=live.digest(library.read_bytes()))
        terrain['ground']['declaration'] = dict(sensor_height_metres=.6, ground_z_metres=0.)
        dependencies = {}
        directory = Path(terrain['octomap']['library']).parent
        (directory / 'lib').mkdir()
        for name in live.DEPENDENCIES:
            dependency = directory / 'lib' / name
            dependency.write_bytes(Path(terrain['octomap']['library']).read_bytes())
            dependencies[name] = live.digest(dependency.read_bytes())
        terrain['octomap']['dependencies'] = dependencies
        report = dict(schemaVersion=1, octomapVersion='1.9.7',
            nativeSha256=terrain['octomap']['sha256'],
            sourceSha256=live.digest((ROOT / 'robot-navigation/native-octomap/octomap_bridge.cpp').read_bytes()),
            dependencies=dependencies, freshProcessLoadPassed=True, robotContacted=False, navigationReady=False)
        for name, payload in (('ground', {}), ('octomap', report)):
            path = Path(terrain[name]['library']).parent / 'build-report.json'
            path.write_text(json.dumps(payload), encoding='utf-8')
            terrain[name]['buildReportSha256'] = live.digest(path.read_bytes())
        self.value['terrain'] = terrain
        self.write()
        return terrain


@unittest.skipUnless(AVAILABLE, 'requires isolated numerical Python runtime')
class SettingsTests(SettingsFixture, unittest.TestCase):
    def setUp(self):
        self.prepare()

    def test_explicit_selection_is_read_only_and_preserves_current_core(self):
        before = self.path.read_bytes(), self.odometry.read_bytes()
        settings = self.settings()
        self.assertIsNone(settings.terrain)
        self.assertEqual(settings.frozen, settings.verify())
        self.assertEqual(before, (self.path.read_bytes(), self.odometry.read_bytes()))
        self.assertEqual(settings.library, self.component.library)

    def test_recorded_or_wrong_architecture_cannot_select_a_module(self):
        for arm64, mode in ((False, 'live'), (True, 'recorded')):
            self.platform.arm64, self.platform.mode = arm64, mode
            with self.subTest(arm64=arm64, mode=mode), self.assertRaisesRegex(ValueError, 'arm64_live'):
                self.settings()

    def test_strict_schema_duplicates_extra_fields_and_bounds_are_rejected(self):
        original = deepcopy(self.value)
        for key, value in (('schemaVersion', True), ('purpose', 'navigation'), ('extra', 1)):
            self.value = dict(original, **{key: value})
            self.write()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.settings()
        for raw in (b'{"schemaVersion":1,"schemaVersion":1}', b' ' * 16385):
            self.path.write_bytes(raw)
            with self.assertRaises(ValueError):
                self.settings()

    def test_trajectory_must_already_be_explicitly_enabled(self):
        for value in (False, 1, None):
            payload = json.dumps(dict(odometry_estimation=dict(save_imu_rate_trajectory=value)))
            self.odometry.write_text(payload)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'enabled_acquisition'):
                self.settings()
            self.assertEqual(payload, self.odometry.read_text())

    def test_core_module_and_source_manifest_drift_are_rejected(self):
        self.platform.frozen['coreLibrariesSha256'] = {'libglim.so': '9' * 64}
        with self.assertRaises(ValueError):
            self.settings()
        self.platform.frozen['coreLibrariesSha256'] = self.component.core
        self.component.library.write_bytes(self.component.library.read_bytes() + b'changed')
        with self.assertRaises(ValueError):
            self.settings()

    def test_parent_child_identity_and_recheck_reject_reselection(self):
        settings = self.settings()
        args = SimpleNamespace(acquisition_settings=self.path, acquisition_integrity_sha256=settings.identity)
        self.assertEqual(settings.frozen, live.child_settings(args, self.platform).frozen)
        self.assertEqual(['--acquisition-settings', str(self.path), '--acquisition-integrity-sha256', settings.identity],
                         settings.child_arguments())
        self.path.write_bytes(self.path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'same_parent_selected'):
            live.child_settings(args, self.platform)
        with self.assertRaisesRegex(ValueError, 'unchanged_selected'):
            settings.child_arguments()

    def test_absent_unpaired_and_recorded_options_fail_before_launch(self):
        self.assertIsNone(live.child_settings(SimpleNamespace(), self.platform))
        with self.assertRaisesRegex(ValueError, 'paired_acquisition'):
            live.child_settings(SimpleNamespace(acquisition_integrity_sha256='a' * 64), self.platform)
        with self.assertRaisesRegex(ValueError, 'same_parent_selected'):
            live.child_settings(SimpleNamespace(acquisition_settings=self.path), self.platform)
        with patch.object(controlled_localization, 'verified_configuration') as configuration:
            with self.assertRaisesRegex(ValueError, 'explicit_live'):
                controlled_localization.run(SimpleNamespace(acquisition_settings=self.path))
            configuration.assert_not_called()

    def test_declared_terrain_defaults_do_not_change_display_height_or_grant_readiness(self):
        self.terrain()
        settings = self.settings()
        geometry, projection, declaration = settings.terrain
        self.assertEqual((1., .6), (geometry.metres_per_raw_unit, declaration.sensor_height_metres))
        self.assertEqual(spec(), projection)
        self.assertFalse(settings.ros_output)
        self.ground_check.assert_called()
        self.assertNotIn('navigationReady', self.value)

    def test_ros_output_requires_explicit_private_mode_and_existing_declared_terrain(self):
        self.value['rosOutput'] = {'mode': 'private_declared_terrain'}
        self.write()
        with self.assertRaisesRegex(ValueError, 'explicit_private_declared_terrain'):
            self.settings()
        self.terrain()
        settings = self.settings()
        self.assertTrue(settings.ros_output)
        self.assertEqual('private_declared_terrain', settings.frozen['rosOutputMode'])
        self.assertTrue(set(live.ROS_SOURCES) <= set(settings.frozen['sourceSha256']))
        for value in (None, True, {'mode': 'live_navigation'}, {'mode': 'private_declared_terrain', 'topic': '/map'}):
            self.value['rosOutput'] = value
            self.write()
            with self.assertRaisesRegex(ValueError, 'explicit_private_declared_terrain'):
                self.settings()

    def test_localization_ros_output_is_opt_in_source_bound_and_does_not_require_terrain(self):
        self.assertFalse(self.settings().localization_ros_output)
        self.value['localizationRosOutput'] = {'mode': 'private_saved_map_observations'}
        self.write()
        settings = self.settings()
        self.assertTrue(settings.localization_ros_output)
        self.assertIsNone(settings.terrain)
        self.assertFalse(settings.ros_output)
        self.assertEqual('private_saved_map_observations', settings.frozen['localizationRosOutputMode'])
        for name in live.LOCALIZATION_ROS_SOURCES:
            self.assertEqual(live.digest((ROOT / name).read_bytes()), settings.frozen['sourceSha256'][name])
        self.assertEqual(settings.frozen, settings.verify())
        for value in (None, True, {}, {'mode': 'live_navigation'},
                {'mode': 'private_saved_map_observations', 'topic': '/tf'}):
            self.value['localizationRosOutput'] = value
            self.write()
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'explicit_private_saved_map'):
                self.settings()

    def test_saved_map_terrain_requires_paired_observations_and_same_explicit_scale_and_frame(self):
        terrain = self.terrain()
        self.value['rosOutput'] = {'mode': 'private_saved_map_terrain'}
        self.write()
        with self.assertRaisesRegex(ValueError, 'paired_saved_map'):
            self.settings()
        self.value['localizationRosOutput'] = {'mode': 'private_saved_map_observations'}
        self.write()
        with self.assertRaisesRegex(ValueError, 'same_explicit_saved_map'):
            self.settings()
        self.platform.value = dict(basePose=dict(metresPerMapUnit=1.))
        settings = self.settings()
        self.assertTrue(settings.saved_map_terrain)
        self.assertEqual('private_saved_map_terrain', settings.frozen['rosOutputMode'])
        self.platform.value['basePose']['metresPerMapUnit'] = 2.
        with self.assertRaisesRegex(ValueError, 'same_explicit_saved_map'):
            self.settings()
        terrain['geometry']['declared_from_raw'][3] = .1
        self.write()
        with self.assertRaisesRegex(ValueError, 'same_explicit_saved_map'):
            self.settings()
        self.platform.value['basePose']['metresPerMapUnit'] = 1.
        self.platform.value['basePose']['navigationFromRaw'] = [terrain['geometry']['declared_from_raw'][i:i+4]
                                                                for i in range(0, 16, 4)]
        self.assertTrue(self.settings().saved_map_terrain)

    def test_wrong_terrain_architecture_build_source_or_dependency_is_rejected(self):
        terrain = self.terrain()
        dependency = Path(terrain['octomap']['library']).parent / 'lib' / sorted(live.DEPENDENCIES)[0]
        original = dependency.read_bytes()
        dependency.write_bytes(original[:18] + (62).to_bytes(2, 'little'))
        terrain['octomap']['dependencies'][dependency.name] = live.digest(dependency.read_bytes())
        report_path = dependency.parent.parent / 'build-report.json'
        report = json.loads(report_path.read_bytes())
        report['dependencies'] = terrain['octomap']['dependencies']
        report_path.write_text(json.dumps(report))
        terrain['octomap']['buildReportSha256'] = live.digest(report_path.read_bytes())
        self.write()
        with self.assertRaisesRegex(ValueError, 'matching_arm64'):
            self.settings()
        dependency.write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'pinned_bounded'):
            self.settings()
        terrain['octomap']['dependencies'][dependency.name] = live.digest(original)
        report['sourceSha256'] = '0' * 64
        report_path.write_text(json.dumps(report))
        terrain['octomap']['buildReportSha256'] = live.digest(report_path.read_bytes())
        self.write()
        with self.assertRaisesRegex(ValueError, 'source_bound_declared'):
            self.settings()


@unittest.skipUnless(AVAILABLE, 'requires isolated numerical Python runtime')
class CompositionTests(SettingsFixture, unittest.TestCase):
    def setUp(self):
        self.prepare()
        self.now = time.monotonic()
        self.binding = LocalizationBinding('2' * 32, 'synthetic', 'slam-' + '1' * 32,
                                           '3' * 64, '4' * 64, '5' * 64)
        class Registrar:
            def register(self, scan, seed):
                return RegistrationEstimate(True, (), seed, {})
        self.session = LocalizationSession(self.binding, Registrar, clock=lambda: self.now)
        self.library = Library()
        self.frames = 0
        self.native_calls = []
        self.release, self.terrain_calls = threading.Event(), []
        self.release.set()
        self.enterContext(patch('lingxi_slam.acquisition_output.ctypes.CDLL', return_value=self.library))
        self.enterContext(patch.object(live, 'verify_loaded_libraries'))
        case = self
        def counts(destination):
            case.native_calls.append(('counts', threading.get_ident()))
            for index, value in enumerate((case.frames, min(case.frames, 1), max(case.frames - 1, 0),
                                           max(case.frames - 1, 0), 0)):
                destination[index] = value
            return 0
        self.library.lingxi_acquisition_counts = Function(counts)
        class Native:
            def __init__(self, accept):
                self.accept = accept
                case.native_calls.append(('create', threading.get_ident()))
            def imu(self, *args):
                case.native_calls.append(('imu', threading.get_ident()))
            def frame(self, stamp, points, offsets):
                index = case.frames
                case.frames += 1
                if index:
                    case.library.data = packet_bytes(index, stamp)
                    case.library.metadata = (index, 3, 2, 840)
                    self.accept(PointPacket(index, stamp, 3, np.ones((3, 3)), pose()))
                return pose(), np.zeros(4)
            def close(self):
                case.native_calls.append(('close', threading.get_ident()))
        self.Native = Native
        self.composition = self.native = None

    def create(self, terrain=False):
        if terrain:
            self.terrain()
            case = self
            class Process(FakeProcess):
                failure = 'synthetic_terrain_failure'
                @property
                def cleanup_complete(self):
                    return self.closed
            def process(**kwargs):
                case.process = Process(case.release, case.terrain_calls)
                return case.process
            self.enterContext(patch.object(live, 'TerrainProcess', side_effect=process))
        self.composition = live.AcquisitionComposition(self.settings(), self.session, self.root)
        self.addCleanup(self.composition.finish)
        self.addCleanup(self.release.set)
        return self.composition

    def open(self, terrain=False):
        self.create(terrain)
        self.native = self.composition.factory(self.Native)
        self.addCleanup(self.native.close)
        if terrain:
            wait_for(lambda: self.composition.lane.snapshot().state == 'ready')

    def frame(self):
        self.now = time.monotonic()
        points, offsets = np.ones((3, 3)), np.linspace(0, .02, 3)
        points.flags.writeable = offsets.flags.writeable = False
        return self.native.frame_received(1000. + self.frames * .1,
            Scan(1_700_000_000_000_000_000, points, offsets, self.now, 96))

    def command(self, action):
        return self.session.request(action, asdict(self.binding), self.session.state()['generation'],
            **({'seed': pose(5, 30), 'reference_frame': self.session.state()['referenceFrame']}
               if action == 'seed' else {}))

    def track(self, terrain=True):
        self.open(terrain)
        self.frame()
        self.frame()
        self.command('seed')
        for _ in range(3):
            self.frame()
        self.assertEqual('tracking', self.session.state()['state'])
        if terrain:
            wait_for(lambda: self.composition.source.snapshot().grid is not None)

    def test_real_wrapper_same_owner_packets_and_terminal_counts(self):
        self.track(terrain=False)
        self.assertEqual(1, self.composition.registered_packets)
        self.assertEqual(3, self.composition.registered_returns)
        self.native.close()
        report = self.composition.finish()
        live.verify_terminal(report, self.composition.settings.frozen, estimated_poses=self.frames)
        self.assertFalse(report['navigationReady'])
        self.assertEqual({threading.get_ident()}, {owner for _, owner in self.native_calls})
        self.assertEqual(['open', 'close'], [name for name in self.library.calls if isinstance(name, str)])

    def test_native_factory_and_counter_cleanup_stay_on_one_nonmain_owner(self):
        composition = self.create()
        def run():
            native = composition.factory(self.Native)
            native.imu(1.)
            native.close()
        self.assertEqual([], other_thread(run))
        self.assertEqual(1, len({owner for _, owner in self.native_calls}))
        self.assertNotEqual(threading.get_ident(), self.native_calls[0][1])
        self.assertTrue(composition.summary()['nativeClosed'])

    def test_explicit_ros_owner_starts_without_source_and_closed_composition_hides_late_grid(self):
        self.terrain()
        self.value['rosOutput'] = {'mode': 'private_declared_terrain'}
        self.write()
        composition = live.AcquisitionComposition(self.settings(), self.session, self.root)
        self.addCleanup(composition.finish)
        self.assertIsNotNone(composition.ros_output)
        self.assertFalse(composition.ros_output.started)
        self.assertEqual('starting', composition.terrain_snapshot().state)
        composition.revoke()
        source = Mock()
        with patch.object(composition, 'source', source):
            self.assertIsNone(composition.terrain_snapshot().grid)
            source.snapshot.assert_not_called()

    def test_saved_terrain_submission_waits_for_exact_post_projection_hook_and_revoke_clears_slot(self):
        from test_localization_output import SyntheticLiveFixture
        fixture = SyntheticLiveFixture()
        fixture.prepare()
        terrain = self.terrain()
        terrain['geometry']['metres_per_raw_unit'] = fixture.session.base_scale
        self.platform.value = dict(basePose=dict(metresPerMapUnit=fixture.session.base_scale))
        self.value['rosOutput'] = {'mode': 'private_saved_map_terrain'}
        self.value['localizationRosOutput'] = {'mode': 'private_saved_map_observations'}
        self.write()
        composition = live.AcquisitionComposition(self.settings(), fixture.session, self.root)
        self.addCleanup(composition.revoke)
        source = Mock()
        source.snapshot.return_value = live.LaneSnapshot(0, 'ready')
        composition.source = source
        packet, timing, _ = fixture.offer(capture=False)
        composition.accept(packet)
        source.assert_not_called()
        self.assertIs(packet, composition.pending_terrain)
        composition.capture_terrain(timing.header_ns + 1, timing.algorithm_stamp)
        source.assert_not_called()
        self.assertIsNone(composition.pending_terrain)
        composition.accept(packet)
        composition.capture_terrain(timing.header_ns, timing.algorithm_stamp)
        source.assert_called_once_with(packet, scan_header_ns=timing.header_ns)
        self.assertEqual(1, composition.submissions)
        self.assertEqual(composition.localization_ros_output.namespace, composition.ros_output.namespace)
        composition.accept(packet)
        composition.revoke()
        self.assertIsNone(composition.pending_terrain)
        composition.accept(packet)
        composition.capture_terrain(timing.header_ns, timing.algorithm_stamp)
        self.assertIsNone(composition.pending_terrain)
        self.assertEqual(1, source.call_count)

    def test_localization_output_requires_live_scale_and_receives_exact_registered_packet(self):
        self.value['localizationRosOutput'] = {'mode': 'private_saved_map_observations'}
        self.write()
        with self.assertRaisesRegex(ValueError, 'explicit_live_scaled_localization'):
            self.create()
        self.binding = replace(self.binding, input_mode='live')
        self.session = LocalizationSession(self.binding, self.session.factory,
            clock=lambda: self.now, base_metres_per_map_unit=1.)
        self.track(terrain=False)
        owner = self.composition.localization_ros_output
        self.assertIsNone(self.composition.ros_output)
        self.assertFalse(owner.started)
        self.assertEqual(self.binding, owner.pending.binding)
        self.assertEqual(self.session.state()['lastFrame'], owner.pending.native_frame_id)
        self.assertEqual(1, self.composition.registered_packets)
        self.native.close()
        self.assertTrue(owner.revoked)
        self.assertIsNone(owner.snapshot())

    def test_wrong_owner_close_and_duplicate_factory_do_not_touch_native(self):
        self.open()
        self.assertIsInstance(other_thread(self.native.close)[0], RuntimeError)
        with self.assertRaisesRegex(RuntimeError, 'single_acquisition'):
            self.composition.factory(self.Native)
        self.assertNotIn('close', self.library.calls)

    def test_native_or_library_loading_failure_closes_owned_hooks(self):
        composition = self.create()
        with patch.object(live, 'verify_loaded_libraries', side_effect=ValueError('synthetic_load_failure')):
            with self.assertRaises(ValueError):
                composition.factory(self.Native)
        self.assertTrue(composition.failed)
        self.assertTrue(composition.rays.closed)
        self.assertTrue(composition.closed)
        self.assertFalse(self.session.state()['workerReady'])

    def test_point_factory_failure_closes_acquisition_and_localization(self):
        composition = self.create()
        with self.assertRaisesRegex(RuntimeError, 'synthetic_constructor'):
            composition.factory(Mock(side_effect=RuntimeError('synthetic_constructor')))
        self.assertTrue(composition.rays.closed)
        self.assertTrue(composition.failed)
        self.assertTrue(self.session.state()['cleanupComplete'])

    def test_revoke_during_factory_cannot_return_an_active_owner(self):
        composition = self.create()
        def factory(accept):
            self.assertEqual([], other_thread(composition.revoke))
            return self.Native(accept)
        with self.assertRaisesRegex(RuntimeError, 'start_revoked'):
            composition.factory(factory)
        self.assertTrue(composition.rays.closed)
        self.assertTrue(self.session.state()['workerClosed'])

    def test_source_constructor_failure_keeps_lane_cleanup_handle(self):
        composition = self.create(terrain=True)
        with patch.object(live, 'RegisteredTerrainSource', side_effect=RuntimeError('synthetic_source_failure')):
            with self.assertRaises(RuntimeError):
                composition.factory(self.Native)
        composition.finish()
        self.assertIsNone(composition.source)
        self.assertFalse(composition.lane.thread.is_alive())
        self.assertTrue(composition.failed)

    def test_pause_reseed_and_stop_withdraw_without_another_sensor_callback(self):
        self.track()
        self.command('pause')
        self.assertFalse(self.composition.unavailable())
        self.assertIsNone(self.composition.source.snapshot().grid)
        self.assertTrue(self.composition.source.retired)
        self.command('seed')
        for _ in range(3):
            self.frame()
        self.assertIsNone(self.composition.source.snapshot().grid)
        self.command('stop')
        self.assertIsNone(self.composition.source.snapshot().grid)

    def test_loss_withdraws_without_another_sensor_callback(self):
        self.track()
        self.now += 1.01
        self.assertFalse(self.composition.unavailable())
        self.assertTrue(self.composition.source.retired)
        self.assertIsNone(self.composition.source.snapshot().grid)

    def test_child_failure_is_latched_before_terminal_retirement(self):
        self.track()
        self.process.dead = True
        wait_for(lambda: self.composition.lane.snapshot().state == 'failed')
        self.assertTrue(self.composition.unavailable())
        self.native.close()
        report = self.composition.finish()
        self.assertEqual('synthetic_terrain_failure', report['failureReason'])
        self.assertTrue(report['failed'])
        with self.assertRaises(ValueError):
            live.verify_terminal(report, self.composition.settings.frozen, estimated_poses=self.frames)

    def test_stop_during_blocked_terrain_hides_result_before_join(self):
        self.track()
        self.release.clear()
        previous = self.terrain_calls.count('update')
        self.frame()
        wait_for(lambda: self.terrain_calls.count('update') > previous)
        started = time.monotonic()
        self.composition.revoke()
        self.assertLess(time.monotonic() - started, .2)
        self.assertIsNone(self.composition.source.snapshot().grid)
        self.release.set()
        self.native.close()
        report = self.composition.finish()
        live.verify_terminal(report, self.composition.settings.frozen, estimated_poses=self.frames)
        self.assertTrue(self.process.closed)

    def test_final_input_replacement_and_counter_mutations_fail(self):
        self.track(terrain=False)
        self.native.close()
        report = self.composition.finish()
        for key, value in (('nativeClosed', False), ('sourceRetired', False), ('navigationReady', True),
                ('failed', True), ('inputIntegrity', {}), ('terrainSubmissions', 1), ('registeredPackets', 999)):
            changed = dict(report, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                live.verify_terminal(changed, self.composition.settings.frozen, estimated_poses=self.frames)
        with self.assertRaisesRegex(ValueError, 'counter_accounting'):
            live.verify_terminal(report, self.composition.settings.frozen, estimated_poses=self.frames + 1)
        before = self.path.read_bytes()
        self.path.write_bytes(before + b' ')
        with self.assertRaisesRegex(ValueError, 'unchanged_acquisition'):
            self.composition.finish()
        self.path.write_bytes(before)

    def test_ros_terminal_requires_selected_mode_and_complete_owned_shutdown(self):
        self.track()
        self.native.close()
        report = self.composition.finish()
        expected = dict(self.composition.settings.frozen, rosOutputMode='private_declared_terrain')
        ros = dict(mode='private_declared_terrain', started=True, closed=True, failed=False,
            publications=2, physicalScaleValidated=False, groundSupportValidated=False,
            physicalMotionSent=False, navigationReady=False)
        value = dict(report, inputIntegrity=expected, terrainRosOutput=ros)
        live.verify_terminal(value, expected, estimated_poses=self.frames)
        for malformed in (None, [], 'invalid', 1):
            for identity in (expected, self.composition.settings.frozen):
                with self.subTest(malformed=malformed), self.assertRaisesRegex(ValueError, 'passive_acquisition_cleanup'):
                    live.verify_terminal(malformed, identity, estimated_poses=self.frames)
        for key, change in (('started', False), ('closed', False), ('failed', True),
                ('publications', 1), ('publications', True), ('navigationReady', True), ('physicalMotionSent', True)):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'passive_ros_terrain_cleanup'):
                live.verify_terminal(dict(value, terrainRosOutput=dict(ros, **{key: change})), expected,
                    estimated_poses=self.frames)
        with self.assertRaisesRegex(ValueError, 'explicit_passive_ros_terrain_selection'):
            live.verify_terminal(dict(report, terrainRosOutput=ros), self.composition.settings.frozen,
                estimated_poses=self.frames)

    def test_localization_ros_terminal_requires_explicit_identity_and_complete_cleanup(self):
        self.track(terrain=False)
        self.native.close()
        report = self.composition.finish()
        expected = dict(self.composition.settings.frozen,
            localizationRosOutputMode='private_saved_map_observations')
        ros = dict(mode='private_saved_map_observations', started=True, closed=True, failed=False,
            publications=0, namespace='lingxi_offline_' + 'a' * 32,
            physicalScaleValidated=False, physicalMotionSent=False, navigationReady=False)
        value = dict(report, inputIntegrity=expected, localizationRosOutput=ros)
        live.verify_terminal(value, expected, estimated_poses=self.frames)
        for key, change in (('mode', 'live_navigation'), ('started', False), ('closed', False),
                ('failed', True), ('publications', -1), ('publications', True),
                ('namespace', '/tf'), ('namespace', 'lingxi_offline_' + 'z' * 32),
                ('physicalScaleValidated', True), ('navigationReady', True), ('physicalMotionSent', True)):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'passive_localization_ros_cleanup'):
                live.verify_terminal(dict(value, localizationRosOutput=dict(ros, **{key: change})), expected,
                    estimated_poses=self.frames)
        for output in (None, {}, []):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'passive_localization_ros_cleanup'):
                live.verify_terminal(dict(value, localizationRosOutput=output), expected, estimated_poses=self.frames)
        with self.assertRaisesRegex(ValueError, 'explicit_passive_localization_ros_selection'):
            live.verify_terminal(dict(report, localizationRosOutput=ros), self.composition.settings.frozen,
                estimated_poses=self.frames)


@unittest.skipUnless(AVAILABLE and sys.platform == 'linux', 'requires Linux runtime dependencies')
class EntryPointTests(SettingsFixture, unittest.TestCase):
    def setUp(self):
        self.prepare()
        self.platform.profile = self.odometry
        self.platform.sensor_provenance = lambda: {}
        self.platform.verify = lambda: self.platform.frozen

    def test_on_demand_service_forwards_fixed_selection_and_rechecks_each_launch(self):
        import localization_service as service
        maps, runs = self.root / 'maps', self.root / 'runs'
        maps.mkdir(mode=0o700)
        runs.mkdir(mode=0o700)
        chosen = SimpleNamespace(directory=maps / 'synthetic',
            binding=SimpleNamespace(map_id='slam-' + 'a' * 32, receipt_sha256='b' * 64))
        with patch.object(service, 'verified_configuration', return_value=({'inputMode': 'live'}, {})), patch.object(
                service, 'MappingPlatform', return_value=self.platform), patch.object(
                service, 'SavedMapResolver'), patch.object(service, 'LocalizationManager') as manager:
            service.create_manager(self.odometry, maps, runs, acquisition_settings=self.path)
            self.assertIsNone(manager.call_args.kwargs['process_limits'])
            command = manager.call_args.args[2]
            selected = self.settings()
            self.assertEqual(selected.child_arguments(), command(chosen, runs / 'new')[-4:])
            self.path.write_bytes(self.path.read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, 'unchanged_selected'):
                command(chosen, runs / 'second')

    def test_on_demand_terrain_has_time_to_clean_up(self):
        import localization_service as service
        self.terrain()
        maps, runs = self.root / 'maps', self.root / 'runs'
        maps.mkdir(mode=0o700)
        runs.mkdir(mode=0o700)
        with patch.object(service, 'verified_configuration', return_value=({'inputMode': 'live'}, {})), patch.object(
                service, 'MappingPlatform', return_value=self.platform), patch.object(
                service, 'SavedMapResolver'), patch.object(service, 'LocalizationManager') as manager:
            service.create_manager(self.odometry, maps, runs, acquisition_settings=self.path)
        self.assertEqual(30., manager.call_args.kwargs['process_limits'].shutdown_seconds)

    def test_shared_service_rejects_changed_parent_identity_before_manager_or_catalog_access(self):
        import localization_service as service
        with patch.object(service, 'verified_configuration', return_value=({'inputMode': 'live'}, {})), patch.object(
                service, 'MappingPlatform', return_value=self.platform), patch.object(
                service, 'SavedMapResolver') as resolver, patch.object(service, 'LocalizationManager') as manager:
            for selection in (None, self.path):
                with self.subTest(selection=selection), self.assertRaisesRegex(ValueError, 'same_shared_parent'):
                    service.create_manager(self.odometry, self.root / 'absent-maps', self.root / 'absent-runs',
                        acquisition_settings=selection, acquisition_integrity_sha256='0' * 64)
            resolver.assert_not_called()
            manager.assert_not_called()

    def shared_arguments(self):
        node, bundle = self.root / 'node', self.root / 'api.mjs'
        for file in (node, bundle):
            file.write_bytes(b'synthetic-only')
        maps = self.root / 'maps'
        maps.mkdir(mode=0o700)
        return SimpleNamespace(config=self.odometry, maps=maps, output=self.root / 'output', seconds=None,
            node=node, api_bundle=bundle, node_sha256=live.digest(node.read_bytes()),
            api_sha256=live.digest(bundle.read_bytes()), acquisition_settings=self.path,
            acquisition_settings_sha256=live.digest(self.path.read_bytes()))

    def test_shared_parent_forwards_verified_identity_without_starting_native(self):
        import managed_slam as runtime
        args = self.shared_arguments()
        selected = self.settings()
        with patch.object(runtime, 'verified_configuration', return_value=({'inputMode': 'live'}, {})), patch(
                'mapping_platform.MappingPlatform', return_value=self.platform), patch.object(
                runtime, 'production_api_environment', return_value={}), patch.object(runtime.signal, 'signal'), patch.object(
                runtime, 'ManagedSlamOwner') as owner:
            owner.return_value.run.return_value = {'result': 'stopped'}
            self.assertEqual(0, runtime.run(args))
        self.assertEqual(selected.child_arguments(), owner.call_args.args[0][-4:])
        self.assertFalse(args.output.exists())

    def test_shared_parent_rejects_wrong_digest_missing_pair_and_frozen_input_overlap(self):
        import managed_slam as runtime
        args = self.shared_arguments()
        with patch('mapping_platform.MappingPlatform', return_value=self.platform):
            args.acquisition_settings_sha256 = '0' * 64
            with self.assertRaisesRegex(ValueError, 'unchanged_deployed'):
                runtime.acquisition_selection(args, {})
            args.acquisition_settings = None
            with self.assertRaisesRegex(ValueError, 'paired_shared'):
                runtime.acquisition_selection(args, {})
            args.acquisition_settings = self.path
            args.acquisition_settings_sha256 = live.digest(self.path.read_bytes())
            for output, maps in ((self.component.root / 'new', args.maps), (args.output, self.component.root)):
                with self.subTest(output=output, maps=maps), self.assertRaisesRegex(ValueError, 'overlap_frozen'):
                    runtime.acquisition_selection(SimpleNamespace(**{**vars(args), 'output': output, 'maps': maps}), {})
            self.path = args.maps / 'settings.json'
            self.write()
            args.acquisition_settings = self.path
            with self.assertRaisesRegex(ValueError, 'overlap_frozen'):
                runtime.acquisition_selection(args, {})
        self.assertFalse(args.output.exists())

    def test_shared_slam_entry_forwards_only_operator_selected_file(self):
        import slam_service
        arguments = ['slam_service.py', '--config', str(self.odometry), '--maps', str(self.root / 'maps'),
            '--runs', str(self.root / 'runs'), '--socket', str(self.root / 'mapping.sock'),
            '--localization-socket', str(self.root / 'localization.sock'), '--acquisition-settings', str(self.path),
            '--acquisition-integrity-sha256', self.settings().identity]
        with patch.object(sys, 'argv', arguments), patch.object(slam_service, 'create_mapping'), patch.object(
                slam_service, 'create_localization') as localization, patch.object(slam_service, 'serve'):
            slam_service.main()
        self.assertEqual(self.path, localization.call_args.kwargs['acquisition_settings'])
        self.assertEqual(self.settings().identity, localization.call_args.kwargs['acquisition_integrity_sha256'])

    def test_managed_launcher_forwards_identity_and_verifies_terminal_selection(self):
        import managed_localization as runtime
        self.terrain()
        selected = self.settings()
        expected_arguments = selected.child_arguments()
        node, bundle = self.root / 'node', self.root / 'api.mjs'
        for file in (node, bundle):
            file.write_bytes(b'synthetic-only')
        saved = self.root / 'maps' / 'map'
        saved.mkdir(parents=True)
        args = SimpleNamespace(seconds=5., config=self.odometry, node=node, api_bundle=bundle,
            node_sha256=live.digest(node.read_bytes()), api_sha256=live.digest(bundle.read_bytes()),
            saved_map=saved, output=self.root / 'new', map_id='synthetic', receipt_sha256='b' * 64,
            profile_sha256='c' * 64, acquisition_settings=self.path)
        for name in ('child-report.json', 'localization-final.json'):
            (self.root / name).write_bytes(b'{}')
        api = dict(LINGXI_TLS_KEY=str(self.root / 'key'), LINGXI_TLS_CERT=str(self.root / 'cert'))
        owner = Mock(native_output=self.root)
        owner.native.session = 'd' * 32
        observed = []
        def factory(command, *_, **kwargs):
            self.assertEqual(30., kwargs['process_limits'].shutdown_seconds)
            observed.extend(command(self.root / 'child')[-4:])
            return owner
        def execute(*_, **kwargs):
            kwargs['verify_complete']()
            kwargs['verify_inputs']()
            self.path.write_bytes(self.path.read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, 'unchanged_managed'):
                kwargs['verify_inputs']()
            return {'result': 'completed'}
        owner.run.side_effect = execute
        with patch.object(runtime, 'verified_configuration', return_value=({'inputMode': 'live', 'maxSeconds': 90.}, {})), patch.object(
                runtime, 'MappingPlatform', return_value=self.platform), patch.object(runtime, 'api_configuration',
                return_value=(api, {})), patch.object(runtime, 'load_map', return_value=(Mock(), None)), patch.object(
                runtime, 'ManagedLocalizationOwner', side_effect=factory), patch('live_localization.verify_completion') as verify:
            self.assertEqual(0, runtime.run(args))
        self.assertEqual(expected_arguments, observed)
        self.assertEqual(selected.frozen, verify.call_args.kwargs['acquisition_integrity'])


if __name__ == '__main__':
    unittest.main()
