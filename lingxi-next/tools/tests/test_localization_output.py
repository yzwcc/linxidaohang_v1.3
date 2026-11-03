"""Synthetic same-scan observations; no GLIM, hardware TF or metric acceptance."""
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'robot-runtime/python'), str(ROOT / 'robot-slam'), str(ROOT / 'robot-navigation')]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('numpy', 'scipy'))
if AVAILABLE:
    import numpy as np
    from scipy.spatial.transform import Rotation
    from live_localization_ros import LiveLocalizationRosOutput
    from lingxi_slam.base_pose import BasePoseProjector, BaseLidarTransform, ScanTime
    from lingxi_slam.localization_session import LocalizationBinding, LocalizationSession
    from lingxi_slam.registered_acquisition import RegisteredAcquisition


class SyntheticLiveFixture:
    def prepare(self, now=100.):
        self.now = now
        self.binding = LocalizationBinding('a' * 32, 'live', 'slam-' + 'b' * 32, 'c' * 64, 'd' * 64, 'e' * 64)
        self.session = LocalizationSession(self.binding, lambda: None, clock=lambda: self.now,
                                           base_metres_per_map_unit=2.)
        self.session.owner = threading.get_ident()
        self.session.worker_ready = True
        self.session.phase, self.session.matches = 'tracking', 3
        self.output = LiveLocalizationRosOutput(self.session)
        self.extrinsic = np.eye(4)
        self.extrinsic[:3, 3] = (.2, -.1, .7)
        self.points = np.array([[1., 0., .2], [2., 1., 1.4], [-1., 1., .1]])

    def offer(self, frame=1, stamp=1_000_000_000, x=0., yaw=0., received=None, capture=True):
        timing = ScanTime(stamp + 20_000_000, 20_000_000, 500_000_000, 'header_is_scan_end')
        base = np.eye(4)
        base[:3, :3] = Rotation.from_euler('z', yaw).as_matrix()
        base[0, 3] = x
        sensor = base @ self.extrinsic
        raw = sensor.copy()
        raw[:3, 3] /= self.session.base_scale
        endpoints = self.points / self.session.base_scale @ raw[:3, :3].T + raw[:3, 3]
        origins = np.repeat(raw[:3, 3][None, :], len(endpoints), axis=0)
        for value in (raw, endpoints, origins):
            value.flags.writeable = False
        receipt = self.now if received is None else received
        packet = RegisteredAcquisition(self.binding, self.session.generation, frame, timing.algorithm_stamp,
            receipt, 'f' * 64, origins, endpoints, raw)
        self.session.pose = raw.tolist()
        self.session.last_frame = frame
        self.session.reference = frame, receipt
        self.session.freshness_at = receipt
        self.session.latest = (SimpleNamespace(native_frame_id=frame, algorithm_stamp=timing.algorithm_stamp), receipt)
        projector = BasePoseProjector(lambda ns: BaseLidarTransform(ns, self.extrinsic),
                                     metres_per_map_unit=self.session.base_scale)
        self.session.project_base(timing, projector, algorithm_stamp=timing.algorithm_stamp)
        self.output.offer(packet)
        if capture:
            self.output.capture(timing.header_ns, timing.algorithm_stamp)
        return packet, timing, base


@unittest.skipUnless(AVAILABLE, 'requires isolated numerical Python runtime')
class ObservationTests(SyntheticLiveFixture, unittest.TestCase):
    def setUp(self):
        self.prepare()

    def test_two_actual_poses_required_then_original_effective_time_scale_and_cloud_align(self):
        self.offer()
        self.assertIsNone(self.output.snapshot())
        self.now += .1
        _, timing, base = self.offer(2, 1_100_000_000, .1)
        value = self.output.snapshot()
        self.assertEqual(timing.effective_ns, value.stamp_ns)
        self.assertNotEqual(timing.header_ns, value.stamp_ns)
        self.assertEqual(2, value.frame)
        np.testing.assert_allclose(base, value.map_from_base, atol=1e-12)
        np.testing.assert_allclose(self.extrinsic, value.base_from_sensor, atol=1e-12)
        np.testing.assert_allclose(self.points, value.sensor_points, atol=1e-12)
        np.testing.assert_allclose((1., 0., 0.), value.linear, atol=1e-12)
        np.testing.assert_allclose((0., 0., 0.), value.angular, atol=1e-12)
        self.assertFalse(value.sensor_points.flags.writeable)
        self.assertFalse(self.session.state()['basePose']['physicalScaleValidated'])

    def test_finite_difference_twist_is_in_current_body_frame(self):
        self.offer(yaw=.1)
        self.now += .1
        self.offer(2, 1_100_000_000, .1, .2)
        value = self.output.snapshot()
        np.testing.assert_allclose((np.cos(.2), -np.sin(.2), 0.), value.linear, atol=1e-12)
        np.testing.assert_allclose((0., 0., 1.), value.angular, atol=1e-12)

    def test_large_epoch_preserves_integer_nanoseconds_with_rotated_sensor_extrinsic(self):
        self.extrinsic[:3, :3] = Rotation.from_euler('xyz', (.3, -.2, .4)).as_matrix()
        epoch = 1_700_000_000_123_456_789
        self.offer(stamp=epoch, x=1., yaw=.3)
        self.now += .1
        _, timing, base = self.offer(2, epoch + 100_000_000, 1.05, .4)
        sample = self.output.snapshot()
        self.assertEqual(epoch + 100_000_000, sample.stamp_ns)
        self.assertEqual(timing.effective_ns, sample.stamp_ns)
        np.testing.assert_allclose(self.points, sample.sensor_points, atol=1e-12)
        np.testing.assert_allclose(base, sample.map_from_base, atol=1e-12)
        np.testing.assert_allclose(self.extrinsic, sample.base_from_sensor, atol=1e-12)
        self.assertFalse(sample.map_from_base.flags.writeable)
        self.assertFalse(sample.base_from_sensor.flags.writeable)

    def test_missing_mismatched_or_unavailable_exact_projection_produces_no_output(self):
        self.offer()
        _, timing, _ = self.offer(2, 1_100_000_000, capture=False)
        self.output.capture(timing.header_ns + 1, timing.algorithm_stamp)
        self.assertIsNone(self.output.snapshot())
        self.offer(3, 1_200_000_000, capture=False)
        self.session.revoke_base('timestamped_projection_unavailable')
        self.output.capture(1_220_000_000, 1000.7)
        self.assertIsNone(self.output.snapshot())

    def test_expiry_never_restamps_and_generation_change_cannot_reopen_old_source(self):
        self.offer()
        self.offer(2, 1_100_000_000)
        self.assertIsNotNone(self.output.snapshot())
        self.now += .301
        self.assertIsNone(self.output.snapshot())
        self.session.generation += 1
        self.offer(3, 1_200_000_000)
        self.assertTrue(self.output.revoked)
        self.assertIsNone(self.output.snapshot())

    def test_loss_pause_pending_and_stop_withdraw_even_if_native_worker_is_blocked(self):
        for phase, pending in (('lost', None), ('paused', None), ('stopped', None), ('tracking', (1, 'seed', None, None))):
            with self.subTest(phase=phase, pending=pending):
                self.prepare()
                self.offer()
                self.offer(2, 1_100_000_000)
                self.session.phase, self.session.pending = phase, pending
                self.assertIsNone(self.output.snapshot())
                self.session.phase, self.session.pending = 'tracking', None
                self.assertIsNone(self.output.snapshot())

    def test_stale_original_receipt_and_long_pose_gap_do_not_invent_velocity(self):
        self.offer(received=self.now - .301)
        self.assertIsNone(self.output.snapshot())
        self.offer(2, 1_100_000_000)
        self.offer(3, 1_500_000_000)
        self.assertIsNone(self.output.snapshot())
        self.offer(4, 1_600_000_000)
        self.assertIsNotNone(self.output.snapshot())

    def test_packet_identity_order_and_native_thread_are_enforced(self):
        packet, timing, _ = self.offer()
        with self.assertRaisesRegex(ValueError, 'same_session'):
            self.output.offer(replace(packet, binding=replace(self.binding, session='1' * 32)))
        errors = []
        def wrong():
            try:
                self.output.offer(packet)
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=wrong)
        thread.start(); thread.join(1.)
        self.assertEqual(['same_native_localization_observation_owner_required'], errors)
        with self.assertRaisesRegex(ValueError, 'increasing_original'):
            self.offer()
        self.assertTrue(self.output.revoked)

    def test_retired_source_clears_pending_and_late_native_results(self):
        self.offer(capture=False)
        self.output.retire()
        self.offer(2, 1_100_000_000)
        self.assertIsNone(self.output.pending)
        self.assertIsNone(self.output.snapshot())
        self.output.close(); self.output.close()
        self.assertTrue(self.output.summary()['closed'])
        self.assertFalse(self.output.summary()['navigationReady'])


if __name__ == '__main__':
    unittest.main()
