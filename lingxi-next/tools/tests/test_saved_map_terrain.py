"""Original integer terrain timestamps through the actual asynchronous lane.

Synthetic registrations and native-worker doubles, not real terrain acceptance.
"""
from dataclasses import replace
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from test_localization_output import AVAILABLE, SyntheticLiveFixture
from test_terrain_lane import FakeProcess, wait_for
from test_ground import spec
if AVAILABLE:
    import numpy as np
    from registered_terrain import RegisteredTerrainSource
    from lingxi_navigation.recorded_occupancy import OfflineGeometry
    from lingxi_navigation.terrain import TerrainBudget
    from lingxi_navigation.terrain_lane import TerrainLane
    from live_terrain_ros import LiveTerrainRosOutput


class SavedMapTerrainFixture:
    def terrain(self, *, blocked=False):
        self.now = time.monotonic()
        self.release, self.calls = threading.Event(), []
        if not blocked:
            self.release.set()
        self.lane = TerrainLane(lambda: FakeProcess(self.release, self.calls), poll_seconds=.005)
        self.addCleanup(self.lane.close)
        self.addCleanup(self.release.set)
        wait_for(lambda: self.lane.snapshot().state == 'ready')
        self.geometry = OfflineGeometry(self.session.base_scale,
            tuple(map(float, self.session.base_navigation_from_raw.ravel())))
        self.budget = TerrainBudget(maximum_age_seconds=.3, maximum_span_seconds=.2,
                                   maximum_processing_seconds=.2)
        self.source = RegisteredTerrainSource(self.session, self.lane, self.geometry,
            projection=spec(), budget=self.budget, saved_map=True)
        self.addCleanup(self.source.close)

    def scan(self, frame=1, stamp=1_700_000_000_123_456_789, **kwargs):
        self.now = time.monotonic()
        packet, timing, base = self.offer(frame, stamp, **kwargs)
        accepted = self.source(packet, scan_header_ns=timing.header_ns)
        return packet, timing, accepted

    def result(self):
        wait_for(lambda: self.source.snapshot().grid is not None)
        return self.source.snapshot().grid


@unittest.skipUnless(AVAILABLE, 'requires isolated numerical Python runtime')
class SavedMapTerrainTests(SavedMapTerrainFixture, SyntheticLiveFixture, unittest.TestCase):
    def setUp(self):
        self.prepare(now=time.monotonic())

    def test_same_map_geometry_exact_nanoseconds_and_cached_async_result(self):
        self.terrain()
        packet, timing, accepted = self.scan()
        self.assertTrue(accepted)
        grid = self.result()
        self.assertEqual('lingxi_saved_map', grid.coordinate_frame)
        self.assertEqual((timing.effective_ns, timing.effective_ns), (grid.oldest_scan_ns, grid.latest_scan_ns))
        self.assertIs(grid, self.source.snapshot().grid)
        self.assertIsNone(self.lane.snapshot().grid.coordinate_frame)
        rays = self.source.submitted.observations.scans[0].returns
        np.testing.assert_allclose([ray.endpoint_metres for ray in rays], packet.endpoints * self.session.base_scale)
        np.testing.assert_allclose([ray.origin_metres for ray in rays], packet.origins * self.session.base_scale)
        self.assertFalse(self.session.state()['basePose']['physicalScaleValidated'])

    def test_oldest_window_stamp_is_not_renewed_by_latest_scan(self):
        self.terrain()
        _, first, _ = self.scan()
        self.result()
        _, second, _ = self.scan(2, first.effective_ns + 100_000_000)
        grid = self.result()
        self.assertEqual((first.effective_ns, second.effective_ns), (grid.oldest_scan_ns, grid.latest_scan_ns))
        self.assertEqual(self.source.submitted.received_at, grid.received_at)
        self.assertLessEqual(grid.expires_at, grid.received_at + .3)

    def test_busy_native_result_keeps_its_submitted_scan_not_latest_base_time(self):
        self.terrain(blocked=True)
        # Make cleanup release the synthetic child before joining its owner.
        self.addCleanup(self.release.set)
        _, first, _ = self.scan()
        wait_for(lambda: 'update' in self.calls)
        self.assertFalse(self.scan(2, first.effective_ns + 50_000_000)[2])
        self.assertFalse(self.scan(3, first.effective_ns + 100_000_000)[2])
        self.release.set()
        grid = self.result()
        self.assertEqual(1, grid.sequence)
        self.assertEqual(first.effective_ns, grid.latest_scan_ns)
        self.assertNotEqual(int(self.session.state()['basePose']['effectiveScanNs']), grid.latest_scan_ns)

    def test_loss_during_pending_result_cannot_revive_terrain(self):
        self.terrain(blocked=True)
        self.addCleanup(self.release.set)
        self.scan()
        self.session.phase = 'lost'
        self.assertIsNone(self.source.snapshot().grid)
        self.release.set()
        self.session.phase = 'tracking'
        self.assertIsNone(self.source.snapshot().grid)
        self.assertTrue(self.source.retired)

    def test_retry_clears_old_scan_window_and_preserves_original_timestamp_watermark(self):
        self.terrain()
        _, first, _ = self.scan()
        self.result()
        self.session.phase, self.session.reason = 'acquiring', 'registration_retry_after_rejection'
        self.assertIsNone(self.source.snapshot().grid)
        self.assertFalse(self.source.retired)
        self.session.phase, self.session.reason = 'tracking', 'diagnostic_quality_only'
        _, second, _ = self.scan(2, first.effective_ns + 100_000_000)
        grid = self.result()
        self.assertEqual(second.effective_ns, grid.oldest_scan_ns)
        self.assertEqual(second.effective_ns, grid.latest_scan_ns)
        self.assertEqual(1, len(self.source.submitted.observations.scans))
        self.session.phase, self.session.reason = 'acquiring', 'confirmation_required'
        self.assertIsNone(self.source.snapshot().grid)
        self.session.phase, self.session.reason = 'tracking', 'diagnostic_quality_only'
        packet, timing, _ = self.offer(3, second.effective_ns, capture=False)
        with self.assertRaisesRegex(ValueError, 'increasing_original_terrain'):
            self.source(packet, scan_header_ns=timing.header_ns)
        self.assertTrue(self.source.retired)

    def test_missing_or_mismatched_original_base_time_retires_source(self):
        self.terrain()
        packet, timing, _ = self.offer()
        with self.assertRaisesRegex(ValueError, 'same_frame_original_scan'):
            self.source(packet, scan_header_ns=timing.header_ns + 1)
        self.assertTrue(self.source.retired)
        self.assertIsNone(self.source.snapshot().grid)

    def test_repeated_original_timestamp_does_not_get_new_receipt(self):
        self.terrain()
        _, timing, _ = self.scan()
        self.result()
        packet, _, _ = self.offer(2, timing.effective_ns, capture=False)
        with self.assertRaisesRegex(ValueError, 'increasing_original_terrain'):
            self.source(packet, scan_header_ns=timing.header_ns)
        self.assertIsNone(self.source.snapshot().grid)

    def test_mismatched_geometry_rotation_translation_scale_and_long_window_are_not_relabelled(self):
        self.terrain()
        for key in ('rotation', 'translation', 'scale', 'window'):
            matrix, scale, budget = np.eye(4), self.session.base_scale, self.budget
            if key == 'rotation':
                matrix[:2, :2] = [[0., -1.], [1., 0.]]
            elif key == 'translation':
                matrix[0, 3] = .1
            elif key == 'scale':
                scale *= 2
            else:
                budget = TerrainBudget()
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'same_saved_map_scale'):
                RegisteredTerrainSource(self.session, self.lane,
                    OfflineGeometry(scale, tuple(map(float, matrix.ravel()))), projection=spec(),
                    budget=budget, saved_map=True)

    def test_same_declared_3d_frame_transforms_both_ray_ends_without_changing_time_or_scale(self):
        from scipy.spatial.transform import Rotation
        frame = np.eye(4)
        frame[:3, :3] = Rotation.from_euler('xyz', (.2, -.3, 1.1)).as_matrix()
        frame[:3, 3] = (2., -1., .8)
        self.prepare(now=time.monotonic(), navigation_from_raw=frame)
        self.terrain()
        packet, timing, accepted = self.scan()
        self.assertTrue(accepted)
        rays = self.source.submitted.observations.scans[0].returns
        np.testing.assert_allclose([ray.endpoint_metres for ray in rays], self.geometry.apply(packet.endpoints))
        np.testing.assert_allclose([ray.origin_metres for ray in rays], self.geometry.apply(packet.origins))
        self.assertEqual(timing.effective_ns, self.result().latest_scan_ns)
        self.assertEqual(self.geometry.sha256, self.source.submitted.observations.geometry_declaration_sha256)

    def test_saved_grid_requires_all_integer_ordered_timestamp_metadata(self):
        self.terrain()
        self.scan()
        grid = self.result()
        for changes in (dict(oldest_scan_ns=None), dict(latest_scan_ns=1.),
                dict(oldest_scan_ns=grid.latest_scan_ns + 1), dict(coordinate_frame='other')):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'original_scan_saved_map'):
                replace(grid, **changes)

    def test_composed_ros_owner_shares_observation_namespace_but_not_motion_admission(self):
        self.terrain()
        owner = LiveTerrainRosOutput(spec(), self.source.snapshot, self.source.retire, observations=self.output)
        self.assertEqual(self.output.namespace, owner.namespace)
        self.assertEqual('private_saved_map_terrain', owner.summary()['mode'])
        self.assertFalse(owner.summary()['navigationReady'])
        owner.close()
        self.assertIsNone(owner.snapshot().grid)


if __name__ == '__main__':
    unittest.main()
