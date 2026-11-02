"""Actual session/window/lane composition, with explicit synthetic geometry."""
from dataclasses import asdict, replace
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'robot-runtime/python'), str(ROOT / 'robot-navigation'),
                str(ROOT / 'robot-slam'), str(ROOT / 'tools'), str(Path(__file__).parent)]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('numpy', 'scipy', 'small_gicp'))
if AVAILABLE:
    import numpy as np
    from registered_terrain import RegisteredTerrainSource
    from lingxi_slam.acquisition_output import AcquisitionPacket
    from lingxi_slam.localization_session import LocalizationBinding, LocalizationSession
    from lingxi_slam.point_output import PointPacket
    from lingxi_slam.registered_acquisition import RegisteredAcquisition
    from lingxi_slam.seeded_localization import RegistrationEstimate
    from lingxi_navigation.ground import GroundDeclaration
    from lingxi_navigation.recorded_occupancy import OfflineGeometry, RaySelection
    from lingxi_navigation.registered_window import RegisteredRayWindow
    from lingxi_navigation.terrain import TerrainBudget
    from lingxi_navigation.terrain_lane import TerrainLane
    from lingxi_navigation.terrain_process import TerrainProcess
    from test_terrain_lane import FakeProcess, wait_for
    from test_ground import spec


def readonly(value):
    result = np.array(value, dtype=np.float64)
    result.flags.writeable = False
    return result


if AVAILABLE:
    BINDING = LocalizationBinding('2' * 32, 'synthetic', 'slam-' + '1' * 32,
                                  '3' * 64, '4' * 64, '5' * 64)

    def packet(index, *, count=3, received=None, generation=1):
        origins = np.tile([0., 0., .6], (count, 1))
        endpoints = np.tile([1., 0., 0.], (count, 1))
        return RegisteredAcquisition(BINDING, generation, index, 100. + .1 * index,
            10. + .1 * index if received is None else received, f'{index:064x}',
            readonly(origins), readonly(endpoints), readonly(np.eye(4)))

    def geometry():
        return OfflineGeometry(1., tuple(map(float, np.eye(4).ravel())))


@unittest.skipUnless(AVAILABLE, 'requires isolated numerical localization dependencies')
class WindowTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.1

    def window(self, **kwargs):
        return RegisteredRayWindow(BINDING, 1, geometry(), projection=spec(), clock=lambda: self.now, **kwargs)

    def test_all_measured_pairs_and_raw_ground_preserved_in_declared_frame(self):
        window = self.window()
        first = window.offer(packet(1))
        self.now += .1
        result = window.offer(packet(2))
        self.assertEqual((10.1, 10.2, 6, 6, 1), (result.received_at, result.latest_received_at,
            result.source_returns, result.selected_returns, result.generation))
        self.assertEqual('declared-metres', result.observations.units)
        self.assertEqual(2, result.observations.revision)
        self.assertNotEqual(first.observations.selection_sha256, result.observations.selection_sha256)
        for scan in result.observations.scans:
            for ray in scan.returns:
                self.assertEqual((0., 0., .6), ray.origin_metres)
                self.assertEqual((1., 0., 0.), ray.endpoint_metres)

    def test_explicit_scale_rotation_translation_apply_to_both_ends(self):
        transform = np.eye(4)
        transform[:3, :3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
        transform[:3, 3] = [2, -1, .3]
        window = RegisteredRayWindow(BINDING, 1, OfflineGeometry(2., tuple(map(float, transform.ravel()))),
            projection=spec(), clock=lambda: self.now)
        ray = window.offer(packet(1)).observations.scans[0].returns[0]
        np.testing.assert_allclose(ray.origin_metres, (2, -1, 1.5))
        np.testing.assert_allclose(ray.endpoint_metres, (2, 1, .3))

    def test_count_ray_span_and_original_receipt_age_bound_retained_arrays(self):
        for budget in (TerrainBudget(maximum_scans=2), TerrainBudget(maximum_rays=6),
                       TerrainBudget(maximum_span_seconds=.15), TerrainBudget(maximum_age_seconds=.15)):
            with self.subTest(budget=budget):
                window = self.window(budget=budget)
                for index in range(1, 101):
                    self.now = 10. + .1 * index
                    value = window.offer(packet(index))
                    self.assertLessEqual(len(window.frames), 2)
                    self.assertLessEqual(sum(len(frame.origins) for frame in window.frames), 6)
                    self.assertEqual(window.frames[0].received_at, value.received_at)

    def test_pair_selection_is_bounded_and_declared_without_averaging_or_mutation(self):
        original = packet(1, count=10)
        window = self.window(selection=RaySelection(maximum_per_scan=2))
        value = window.offer(original)
        self.assertEqual((10, 2), (value.source_returns, value.selected_returns))
        self.assertEqual(10, len(original.origins))
        self.assertEqual(2, len(value.observations.scans[0].returns))
        self.assertNotEqual(value.observations.selection_sha256,
            self.window().offer(original).observations.selection_sha256)

    def test_work_bound_evicts_oldest_whole_scan_and_keeps_original_receipts(self):
        base = spec().width * spec().height * 13
        window = self.window(budget=TerrainBudget(maximum_work_units=base + 2 * 3 * 26))
        for index in range(1, 21):
            self.now = 10. + .1 * index
            value = window.offer(packet(index), materialize=index % 2 == 0)
            self.assertLessEqual(len(window.frames), 2)
            self.assertEqual(len(window.frames), len(window.frame_work))
            if value is not None:
                self.assertEqual(6, value.source_returns)
                self.assertEqual(value.source_returns, value.selected_returns)
                self.assertEqual(window.frames[0].received_at, value.received_at)
                self.assertEqual(base + 156, value.projection_work_units)
                self.assertEqual(index - 2, value.work_evicted_scans)
                self.assertEqual((index - 2) * 3, value.work_evicted_returns)
                self.assertEqual([100. + .1 * i for i in (index - 1, index)],
                                 [scan.stamp for scan in value.observations.scans])

    def test_one_complete_scan_over_work_budget_rejected_even_with_selection(self):
        base = spec().width * spec().height * 13
        window = self.window(budget=TerrainBudget(maximum_work_units=base + 78),
                             selection=RaySelection(maximum_per_scan=1))
        window.offer(packet(1))
        self.now += .1
        with self.assertRaisesRegex(ValueError, 'complete_registered_scan_exceeds_work_budget'):
            window.offer(packet(2, count=4), materialize=False)
        self.assertEqual(([], []), (window.frames, window.frame_work))
        with self.assertRaisesRegex(ValueError, 'registered_projection_exceeds_work_budget'):
            self.window(budget=TerrainBudget(maximum_work_units=base))

    def test_work_uses_declared_scale_and_range_cap_without_changing_measured_returns(self):
        projected = spec()
        original = packet(1)
        for scale in (1., 2., 20.):
            transform = np.eye(4)
            transform[:3, 3] = [10., 20., 30.]
            window = RegisteredRayWindow(BINDING, 1,
                OfflineGeometry(scale, tuple(map(float, transform.ravel()))), projection=projected,
                clock=lambda: self.now)
            result = window.offer(original)
            native_bound = int(np.ceil(np.sqrt(3.) * min(np.sqrt(1.36) * scale, 10.) / .1)) + 4
            self.assertEqual(window.fixed_work + 3 * (native_bound + 1), result.projection_work_units)
            self.assertEqual(3, result.selected_returns)

    def test_wrong_epoch_receipt_frame_order_and_geometry_clear_buffer(self):
        for bad in (replace(packet(2), binding=replace(BINDING, session='a' * 32)),
                    packet(2, generation=2), packet(1), packet(2, received=9.),
                    packet(2, received=11.), replace(packet(2), algorithm_stamp=100.),
                    replace(packet(2), origins=np.zeros((3, 3))),
                    replace(packet(2), endpoints=readonly([[float('nan'), 0, 0]])),
                    replace(packet(2), source_sha256='bad')):
            window = self.window()
            window.offer(packet(1))
            self.now = 10.2
            with self.subTest(frame=bad.native_frame_id), self.assertRaises(ValueError):
                window.offer(bad)
            self.assertEqual([], window.frames)
            self.now = 10.1

    def test_stale_equal_deadline_and_one_scan_over_budget_cannot_partially_enter(self):
        for candidate, budget, now in ((packet(1), TerrainBudget(maximum_age_seconds=.1), 10.21),
                                       (packet(1, count=7), TerrainBudget(maximum_rays=6), 10.1)):
            self.now = now
            with self.assertRaises(ValueError):
                self.window(budget=budget).offer(candidate)

    def test_clock_regression_and_other_thread_are_not_allowed(self):
        window = self.window()
        window.offer(packet(1))
        self.now = 10.
        with self.assertRaises(ValueError):
            window.offer(packet(2))
        errors = []
        def other():
            try:
                window.offer(packet(2))
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=other)
        thread.start()
        thread.join(1.)
        self.assertIsInstance(errors[0], RuntimeError)


@unittest.skipUnless(AVAILABLE, 'requires isolated numerical localization dependencies')
class SourceTests(unittest.TestCase):
    def setUp(self):
        self.now = time.monotonic()
        class Registrar:
            def register(self, scan, seed):
                return RegistrationEstimate(True, (), seed, {})
        self.session = LocalizationSession(BINDING, Registrar, clock=lambda: self.now)
        self.session.open_worker()
        self.addCleanup(self.session.close_worker)
        self.index = 0
        self.observe()
        self.command('seed')
        for _ in range(3):
            self.observe()
        self.assertEqual('tracking', self.session.state()['state'])
        self.release, self.calls, self.created = threading.Event(), [], []
        self.release.set()
        def factory():
            value = FakeProcess(self.release, self.calls)
            self.created.append(value)
            return value
        self.lane = TerrainLane(factory, poll_seconds=.005)
        self.addCleanup(self.lane.close)
        self.addCleanup(self.release.set)
        wait_for(lambda: self.lane.snapshot().state == 'ready')
        self.source = RegisteredTerrainSource(self.session, self.lane, geometry(), projection=spec())

    def command(self, action):
        return self.session.request(action, asdict(BINDING), self.session.state()['generation'],
            **(dict(seed=np.eye(4), reference_frame=self.session.state()['referenceFrame']) if action == 'seed' else {}))

    def observe(self):
        self.index += 1
        self.now = time.monotonic()
        self.points = PointPacket(self.index, 100. + .1 * self.index, 3, np.ones((3, 3)), np.eye(4))
        self.session.observe(self.points, session=BINDING.session, input_mode='synthetic', received_at=self.now)
        return self.registered()

    def registered(self):
        value = packet(self.index, received=self.now, generation=self.session.state()['generation'])
        raw = AcquisitionPacket(value.native_frame_id, value.algorithm_stamp, value.source_sha256,
            value.origins, value.endpoints, readonly(np.eye(4)))
        return self.session.register_acquisition(raw, self.points, received_at=self.now,
            expected_generation=self.session.state()['generation'])

    def accepted(self):
        value = self.registered()
        self.assertTrue(self.source(value))
        wait_for(lambda: self.source.snapshot().grid is not None)
        return self.source.snapshot().grid

    def test_current_session_original_receipt_and_full_result_binding(self):
        grid = self.accepted()
        self.assertEqual(self.now, grid.received_at)
        self.assertEqual(self.index, grid.map_revision)
        self.assertEqual(self.source.submitted.observations.sha256, grid.observations_sha256)
        self.assertEqual(0., spec().minimum_z_metres - .1)
        self.assertEqual(1.4, spec().maximum_z_metres)

    def test_pause_with_no_more_scans_retires_old_grid(self):
        self.accepted()
        self.command('pause')
        self.assertIsNone(self.source.snapshot().grid)
        self.assertTrue(self.source.retired)
        self.assertEqual([], self.source.window.frames)
        self.assertEqual('retired', self.lane.snapshot().state)

    def test_expiry_without_new_packet_retires_published_grid(self):
        self.accepted()
        self.now += 1.01
        self.assertIsNone(self.source.snapshot().grid)
        self.assertTrue(self.source.retired)

    def test_retirement_while_native_busy_never_revives_late_result(self):
        self.release.clear()
        self.source(self.registered())
        wait_for(lambda: 'update' in self.calls)
        self.command('seed')
        self.assertIsNone(self.source.snapshot().grid)
        self.release.set()
        wait_for(lambda: self.created[0].closed)
        self.assertIsNone(self.source.snapshot().grid)

    def test_backpressure_coalesces_window_not_native_jobs_or_receipt_refresh(self):
        self.release.clear()
        self.assertTrue(self.source(self.registered()))
        wait_for(lambda: 'update' in self.calls)
        oldest = self.now
        for _ in range(4):
            self.assertFalse(self.source(self.observe()))
        self.assertEqual(1, self.calls.count('update'))
        self.release.set()
        wait_for(lambda: self.lane.snapshot().state == 'ready')
        self.assertTrue(self.source(self.observe()))
        wait_for(lambda: self.source.snapshot().grid is not None)
        self.assertEqual(oldest, self.source.snapshot().grid.received_at)
        self.assertEqual(2, self.calls.count('update'))
        self.assertEqual(6, len(self.source.submitted.observations.scans))

    def test_busy_lane_retains_bounded_raw_pairs_without_rebuilding_declared_geometry(self):
        self.release.clear()
        self.assertTrue(self.source(self.registered()))
        wait_for(lambda: 'update' in self.calls)
        with patch.object(OfflineGeometry, 'apply', side_effect=AssertionError('busy_conversion_not_allowed')):
            for _ in range(4):
                self.assertFalse(self.source(self.observe()))
        self.assertFalse(self.source.retired)
        self.assertEqual(5, len(self.source.window.frames))
        self.assertEqual(1, self.calls.count('update'))

    def test_wrong_current_frame_or_binding_cannot_enter(self):
        self.accepted()
        old = self.registered()
        self.observe()
        self.assertFalse(self.source(old))
        self.assertTrue(self.source.retired)
        self.assertIsNone(self.source.snapshot().grid)

    def test_wrong_owner_retires_and_replay_cannot_renew_grid(self):
        self.accepted()
        values = []
        # Capture on the actual owner; only consumer invocation runs elsewhere.
        candidate = self.registered()
        def consume():
            try:
                self.source(candidate)
            except Exception as error:
                values.append(error)
        thread = threading.Thread(target=consume)
        thread.start()
        thread.join(1.)
        self.assertIsInstance(values[0], RuntimeError)
        self.assertTrue(self.source.retired)

    def test_altered_result_is_withdrawn_and_does_not_refresh_receipt(self):
        self.accepted()
        with self.lane.condition:
            self.lane.value = replace(self.lane.value, grid=replace(self.lane.value.grid, observations_sha256='a' * 64))
        self.assertIsNone(self.source.snapshot().grid)
        self.assertEqual('mismatched_registered_terrain_result', self.source.snapshot().failure)

    def test_wrong_epoch_result_is_withdrawn(self):
        self.accepted()
        with self.lane.condition:
            self.lane.value = replace(self.lane.value, grid=replace(self.lane.value.grid, epoch_sha256='a' * 64))
        self.assertIsNone(self.source.snapshot().grid)
        self.assertTrue(self.source.retired)

    def test_wrong_native_session_result_is_withdrawn(self):
        self.accepted()
        with self.lane.condition:
            self.lane.value = replace(self.lane.value, grid=replace(self.lane.value.grid, session='b' * 32))
        self.assertIsNone(self.source.snapshot().grid)
        self.assertTrue(self.source.retired)

    def test_wrong_projection_result_is_withdrawn(self):
        self.accepted()
        with self.lane.condition:
            grid = replace(self.lane.value.grid, projection=replace(spec(), origin_x_metres=-3.1))
            self.lane.value = replace(self.lane.value, grid=grid)
        self.assertIsNone(self.source.snapshot().grid)
        self.assertTrue(self.source.retired)

    def test_single_scan_work_rejection_retires_source_without_partial_native_job(self):
        self.source = RegisteredTerrainSource(self.session, self.lane, geometry(), projection=spec(),
            budget=TerrainBudget(maximum_work_units=spec().width * spec().height * 13 + 77))
        with self.assertRaisesRegex(ValueError, 'complete_registered_scan_exceeds_work_budget'):
            self.source(self.registered())
        self.assertTrue(self.source.retired)
        self.assertIsNone(self.source.snapshot().grid)
        self.assertNotIn('update', self.calls)

    def test_pause_during_window_transform_cannot_submit(self):
        original = OfflineGeometry.apply
        def interrupted(geometry, values):
            result = original(geometry, values)
            self.command('pause')
            return result
        candidate = self.registered()
        with patch.object(OfflineGeometry, 'apply', interrupted):
            self.assertFalse(self.source(candidate))
        self.assertTrue(self.source.retired)
        self.assertNotIn('update', self.calls)

    def test_only_exclusive_new_lane_and_typed_explicit_geometry_accepted(self):
        self.accepted()
        with self.assertRaises(ValueError):
            RegisteredTerrainSource(self.session, self.lane, geometry(), projection=spec())
        with self.assertRaises(ValueError):
            RegisteredTerrainSource(self.session, self.lane, None, projection=spec())


@unittest.skipUnless(AVAILABLE and os.environ.get('LINGXI_TEST_GROUND_LIBRARY')
    and os.environ.get('LINGXI_TEST_OCTOMAP_LIBRARY'), 'requires actual pinned native terrain builds')
class ActualNativeComposition(unittest.TestCase):
    def test_window_work_upper_bound_matches_actual_octomap_for_scaled_transformed_rays(self):
        from lingxi_navigation.occupancy import ClearanceProjection, OctomapProjector
        projector = OctomapProjector(Path(os.environ['LINGXI_TEST_OCTOMAP_LIBRARY']),
            os.environ['LINGXI_TEST_OCTOMAP_SHA256'], json.loads(os.environ['LINGXI_TEST_OCTOMAP_DEPENDENCIES']))
        rng = np.random.default_rng(42)
        origins = rng.uniform(-.2, .2, (128, 3))
        endpoints = rng.uniform(-15., 15., (128, 3))
        for scale, resolution, maximum_range in ((1., .1, .5), (2., .05, 10.), (.3, .025, 30.)):
            projected = ClearanceProjection(8, 8, resolution, 0., 0., maximum_range_metres=maximum_range)
            transform = np.eye(4)
            transform[:3, :3] = [[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]]
            transform[:3, 3] = [1., -1., .6]
            window = RegisteredRayWindow(BINDING, 1,
                OfflineGeometry(scale, tuple(map(float, transform.ravel()))), projection=projected, clock=lambda: 10.1)
            candidate = replace(packet(1, count=128), origins=readonly(origins), endpoints=readonly(endpoints))
            value = window.offer(candidate)
            result = projector.project_declared(value.observations, projected,
                maximum_work_units=value.projection_work_units)
            self.assertEqual(128, result.statistics[0])
            self.assertLessEqual(result.statistics[-1], value.projection_work_units)
            self.assertLessEqual(value.projection_work_units, result.statistics[-1] + 256)

    def test_registered_window_to_real_supervised_patchwork_octomap_and_pause_retirement(self):
        from terrain_nav2_fixture import acquisition, projection
        data, spec = acquisition('open'), projection()
        directory = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        gate = threading.Event()
        budget = TerrainBudget(maximum_rays=131072, maximum_processing_seconds=10.,
            maximum_age_seconds=15., maximum_work_units=16000000)
        class Registrar:
            def register(self, scan, seed):
                return RegistrationEstimate(True, (), seed, {})
        session = LocalizationSession(BINDING, Registrar, maximum_age_seconds=5.)
        session.open_worker()
        self.addCleanup(session.close_worker)
        def point(index):
            now = time.monotonic()
            value = PointPacket(index, 100. + .1 * index, 3, np.ones((3, 3)), np.eye(4))
            session.observe(value, session=BINDING.session, input_mode='synthetic', received_at=now)
            return value, now
        point(1)
        session.request('seed', asdict(BINDING), session.state()['generation'], seed=np.eye(4), reference_frame=1)
        point(2)
        point(3)
        point(4)
        def factory():
            if not gate.wait(10.):
                raise TimeoutError('test_start_gate')
            return TerrainProcess(ground_library=Path(os.environ['LINGXI_TEST_GROUND_LIBRARY']),
                ground_sha256=os.environ['LINGXI_TEST_GROUND_SHA256'],
                octomap_library=Path(os.environ['LINGXI_TEST_OCTOMAP_LIBRARY']),
                octomap_sha256=os.environ['LINGXI_TEST_OCTOMAP_SHA256'],
                dependencies=json.loads(os.environ['LINGXI_TEST_OCTOMAP_DEPENDENCIES']),
                projection=spec, declaration=GroundDeclaration(.6, 0.), budget=budget,
                stderr_path=directory / 'native.log')
        lane = TerrainLane(factory, poll_seconds=.005)
        self.addCleanup(lane.close)
        self.addCleanup(gate.set)
        source = RegisteredTerrainSource(session, lane, geometry(), projection=spec, budget=budget)
        receipts = []
        for ordinal, scan in enumerate(data.scans):
            points, receipt = point(5 + ordinal)
            receipts.append(receipt)
            raw = AcquisitionPacket(points.native_frame_id, points.algorithm_stamp, scan.source_sha256,
                readonly([ray.origin_metres for ray in scan.returns]),
                readonly([ray.endpoint_metres for ray in scan.returns]), readonly(np.eye(4)))
            registered = session.register_acquisition(raw, points, received_at=receipt,
                expected_generation=session.state()['generation'])
            if ordinal == len(data.scans) - 1:
                gate.set()
                wait_for(lambda: lane.snapshot().state == 'ready', seconds=10.)
            accepted = source(registered)
            self.assertEqual(ordinal == len(data.scans) - 1, accepted)
        wait_for(lambda: lane.snapshot().grid is not None or lane.snapshot().state == 'failed', seconds=10.)
        grid = source.snapshot().grid
        self.assertIsNotNone(grid)
        self.assertGreater(grid.costs.count(0), 0)
        self.assertGreater(grid.costs.count(255), 0)
        self.assertEqual(receipts[0], grid.received_at)
        self.assertEqual(len(data.scans), len(source.submitted.observations.scans))
        self.assertEqual(sum(len(scan.returns) for scan in data.scans), source.submitted.selected_returns)
        self.assertEqual((.1, 1.4), (spec.minimum_z_metres, spec.maximum_z_metres))
        session.request('pause', asdict(BINDING), session.state()['generation'])
        self.assertIsNone(source.snapshot().grid)
        source.close()
        self.assertFalse(lane.thread.is_alive())


if __name__ == '__main__':
    unittest.main()
