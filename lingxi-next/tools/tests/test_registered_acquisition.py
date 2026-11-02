"""Original receipt, same-frame registration and retirement without robot IO."""
from dataclasses import asdict, replace
import importlib.util
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam"),
                str(ROOT / "tools"), str(ROOT / "tools/tests")]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy", "small_gicp"))
if AVAILABLE:
    import numpy as np
    from scipy.spatial.transform import Rotation
    from localization_control import LocalizedNative
    from lingxi_slam.acquisition_output import NativeAcquisitionOutput, decode_acquisition
    from lingxi_slam.localization_session import LocalizationBinding, LocalizationSession
    from lingxi_slam.point_output import PointPacket
    from lingxi_slam.registered_acquisition import register_acquisition
    from lingxi_slam.seeded_localization import RegistrationEstimate
    from lingxi_slam.stream import Scan
    from test_acquisition_output import Library, other_thread, packet_bytes, pose


@unittest.skipUnless(AVAILABLE, "requires isolated numerical localization dependencies")
class Geometry(unittest.TestCase):
    def setUp(self):
        self.binding = LocalizationBinding("2" * 32, "synthetic", "slam-" + "1" * 32,
                                           "3" * 64, "4" * 64, "5" * 64)
        self.odom = pose(4, 40) @ np.linalg.inv(pose(.3, 15))
        self.packet = decode_acquisition(packet_bytes(corrected=pose(4, 40), extrinsic=pose(.3, 15)),
                                         (1, 3, 2, 840), 10., self.odom)
        self.points = PointPacket(1, 10., 3, np.ones((3, 3)), self.odom)
        self.map_pose = np.eye(4)
        self.map_pose[:3, :3] = Rotation.from_euler("x", 30, degrees=True).as_matrix()
        self.map_pose[:3, 3] = [-3, 2, 1]

    def register(self, packet=None, points=None, **changes):
        values = dict(binding=self.binding, generation=2, received_at=50., map_from_lidar=self.map_pose)
        values.update(changes)
        return register_acquisition(self.packet if packet is None else packet,
                                    self.points if points is None else points, **values)

    def test_both_measured_ends_use_noncommuting_same_frame_registration(self):
        result = self.register()
        transform = self.map_pose @ np.linalg.inv(self.odom)
        for raw, registered in ((self.packet.origins, result.origins), (self.packet.endpoints, result.endpoints)):
            expected = np.array([(transform @ [*value, 1])[:3] for value in raw])
            np.testing.assert_allclose(registered, expected, rtol=0, atol=1e-12)
            self.assertFalse(np.shares_memory(raw, registered))
            self.assertFalse(registered.flags.writeable)
        self.assertEqual((self.binding, 2, 50., "native-raw", "lingxi_saved_map", False),
            (result.binding, result.generation, result.received_at, result.units,
             result.coordinate_frame, result.navigation_ready))
        self.assertEqual(self.packet.source_sha256, result.source_sha256)
        self.map_pose[0, 3] = 999
        self.assertEqual(-3, result.map_lidar_pose[0, 3])
        self.assertFalse(result.map_lidar_pose.flags.writeable)

    def test_wrong_frame_stamp_pose_kind_or_source_rejected(self):
        for changed in (replace(self.packet, native_frame_id=2), replace(self.packet, algorithm_stamp=11.),
                        replace(self.packet, world_lidar_pose=pose(4, 40)),
                        replace(self.packet, coordinate_frame="saved_map"),
                        replace(self.packet, kind="finalized_submap"), replace(self.packet, source_sha256="bad")):
            with self.subTest(packet=changed.kind), self.assertRaises(ValueError):
                self.register(changed)

    def test_mutable_incomplete_or_nonfinite_geometry_rejected(self):
        bad = self.packet.endpoints.copy()
        bad[0, 0] = float("nan")
        bad.flags.writeable = False
        for changed in (replace(self.packet, origins=self.packet.origins.copy()),
                        replace(self.packet, endpoints=self.packet.endpoints[:2]),
                        replace(self.packet, endpoints=bad), replace(self.packet, origins=np.zeros((0, 3)))):
            with self.subTest(shape=changed.origins.shape), self.assertRaises(ValueError):
                self.register(changed)

    def test_invalid_receipt_generation_binding_and_pose_rejected(self):
        for changes in (dict(received_at=True), dict(received_at=-1), dict(received_at=float("nan")),
                        dict(generation=True), dict(generation=-1), dict(binding=asdict(self.binding)),
                        dict(map_from_lidar=np.eye(3))):
            with self.subTest(changes=tuple(changes)), self.assertRaises(ValueError):
                self.register(**changes)


@unittest.skipUnless(AVAILABLE, "requires isolated numerical localization dependencies")
class PairedOwner(unittest.TestCase):
    def setUp(self):
        self.now, self.delay, self.delivered = time.monotonic(), 0., []
        self.after_points = lambda: None
        self.after_native = lambda: None
        self.change_packet = lambda packet: packet
        self.consume = self.delivered.append
        self.suppress_points = self.suppress_acquisition = False
        self.binding = LocalizationBinding("2" * 32, "synthetic", "slam-" + "1" * 32,
                                           "3" * 64, "4" * 64, "5" * 64)
        case = self

        class Registrar:
            def register(self, scan, seed):
                return RegistrationEstimate(True, (), seed, {})

        self.session = LocalizationSession(self.binding, Registrar, clock=lambda: self.now)
        self.library = Library()
        mocker = patch("lingxi_slam.acquisition_output.ctypes.CDLL", return_value=self.library)
        mocker.start()
        self.addCleanup(mocker.stop)
        self.output = NativeAcquisitionOutput("synthetic.so")

        class Native:
            def __init__(self, accept):
                self.accept, self.index = accept, 0

            def imu(self, *args):
                pass

            def frame(self, stamp, points, offsets):
                self.index += 1
                case.now += case.delay
                if not case.suppress_acquisition:
                    case.library.data = packet_bytes(self.index, stamp)
                    case.library.metadata = (self.index, 3, 2, 840)
                if not case.suppress_points:
                    self.accept(case.change_packet(PointPacket(self.index, stamp, 3, np.ones((3, 3)), pose())))
                case.after_points()
                case.after_native()
                return pose(), np.zeros(4)

            def close(self):
                case.library.calls.append("native_close")

        self.Native = Native
        self.native = LocalizedNative(self.session, Native, acquisition_output=self.output,
                                      on_acquisition=lambda value: self.consume(value))
        self.addCleanup(self.native.close)

    def frame(self, index, received=None):
        received = self.now if received is None else received
        points, offsets = np.ones((3, 3)), np.linspace(0, .02, 3)
        points.flags.writeable = offsets.flags.writeable = False
        return self.native.frame_received(1000. + index * .1,
            Scan(1_700_000_000_000_000_000, points, offsets, received, 96))

    def command(self, action):
        return self.session.request(action, asdict(self.binding), self.session.state()["generation"],
            **({"seed": pose(5, 30), "reference_frame": self.session.state()["referenceFrame"]}
               if action == "seed" else {}))

    def tracking(self):
        self.frame(1)
        self.assertEqual([], self.delivered)
        self.command("seed")
        for index in (2, 3, 4):
            self.now += .1
            self.frame(index)
        self.assertEqual("tracking", self.session.state()["state"])
        self.assertEqual(1, len(self.delivered))

    def test_original_receipt_and_current_generation_survive_native_delay(self):
        self.tracking()
        self.delivered.clear()
        original = self.now
        self.delay = .3
        self.frame(5, original)
        result, = self.delivered
        self.assertEqual((original, self.binding, self.session.state()["generation"], 5),
            (result.received_at, result.binding, result.generation, result.native_frame_id))
        self.assertNotEqual(self.now, result.received_at)
        transform = pose(5, 30)
        np.testing.assert_allclose(result.origins[0], transform[:3, 3], atol=1e-12)
        self.assertIsNone(self.native.current_scan)
        self.assertIsNone(self.native.current_points)

    def test_localized_native_callback_to_registered_terrain_and_stop_withdrawal(self):
        from registered_terrain import RegisteredTerrainSource
        from lingxi_navigation.recorded_occupancy import OfflineGeometry
        from lingxi_navigation.terrain_lane import TerrainLane
        from test_terrain_lane import FakeProcess, wait_for
        release, calls = threading.Event(), []
        release.set()
        lane = TerrainLane(lambda: FakeProcess(release, calls), poll_seconds=.005)
        self.addCleanup(lane.close)
        self.addCleanup(release.set)
        wait_for(lambda: lane.snapshot().state == 'ready')
        # Match TerrainLane's monotonic clock; virtual future receipts are not fresh input.
        self.now = time.monotonic()
        self.frame(1)
        self.command('seed')
        for index in (2, 3, 4):
            self.now = time.monotonic()
            self.frame(index)
        self.assertEqual('tracking', self.session.state()['state'])
        source = RegisteredTerrainSource(self.session, lane,
            OfflineGeometry(1., tuple(map(float, np.eye(4).ravel()))))
        self.consume = source
        self.now = time.monotonic()
        original = self.now
        self.frame(5)
        wait_for(lambda: source.snapshot().grid is not None)
        grid = source.snapshot().grid
        self.assertEqual((5, original), (grid.sequence, grid.received_at))
        self.assertEqual(3, source.submitted.source_returns)
        self.assertIsNone(self.native.current_scan)
        self.command('stop')
        self.assertIsNone(source.snapshot().grid)
        self.assertTrue(source.retired)

    def test_pause_stop_and_reseed_after_points_cannot_deliver_old_rays(self):
        self.tracking()
        self.delivered.clear()
        self.after_points = lambda: self.command("pause")
        self.now += .1
        self.frame(5)
        self.assertEqual([], self.delivered)
        self.command("seed")
        self.after_points = lambda: None
        for index in (6, 7, 8):
            self.now += .1
            self.frame(index)
        self.assertEqual(1, len(self.delivered))
        self.delivered.clear()
        self.after_points = lambda: self.command("seed")
        self.now += .1
        self.frame(9)
        self.assertEqual([], self.delivered)
        self.after_points = lambda: self.command("stop")
        self.now += .1
        self.frame(10)
        self.assertEqual([], self.delivered)

    def test_stale_native_completion_cannot_deliver(self):
        self.tracking()
        self.delivered.clear()
        original = self.now
        self.delay = 1.01
        self.frame(5, original)
        self.assertEqual([], self.delivered)
        self.assertEqual("lost", self.session.state()["state"])

    def test_expiry_between_point_registration_and_raw_copy_cannot_deliver(self):
        self.tracking()
        self.delivered.clear()
        self.after_points = lambda: setattr(self, "now", self.now + 1.01)
        self.frame(5)
        self.assertEqual([], self.delivered)

    def test_management_retirement_during_transform_is_rechecked(self):
        self.tracking()
        self.delivered.clear()
        import lingxi_slam.registered_acquisition as module
        original = module.register_acquisition

        def interrupted(*args, **kwargs):
            value = original(*args, **kwargs)
            self.command("pause")
            return value

        with patch.object(module, "register_acquisition", side_effect=interrupted):
            self.now += .1
            self.frame(5)
        self.assertEqual([], self.delivered)

    def test_expiry_during_transform_is_rechecked(self):
        self.tracking()
        self.delivered.clear()
        import lingxi_slam.registered_acquisition as module
        original = module.register_acquisition

        def delayed(*args, **kwargs):
            value = original(*args, **kwargs)
            self.now += 1.01
            return value

        with patch.object(module, "register_acquisition", side_effect=delayed):
            self.frame(5)
        self.assertEqual([], self.delivered)

    def test_mismatched_point_or_missing_acquisition_fails_without_fallback(self):
        self.tracking()
        self.delivered.clear()
        self.suppress_acquisition = True
        with self.assertRaisesRegex(RuntimeError, "same_frame_point"):
            self.frame(5)
        self.assertTrue(self.native.failed)
        self.assertFalse(self.session.accepting)
        self.assertEqual([], self.delivered)
        self.assertIsNone(self.native.current_scan)
        with self.assertRaises(RuntimeError):
            self.frame(5)

    def test_acquisition_without_points_fails(self):
        self.suppress_points = True
        with self.assertRaisesRegex(RuntimeError, "same_frame_point"):
            self.frame(1)
        self.assertEqual([], self.delivered)

    def test_different_frame_pose_is_not_accepted(self):
        self.tracking()
        self.delivered.clear()
        self.change_packet = lambda packet: replace(packet, world_lidar_pose=pose(1))
        with self.assertRaisesRegex(ValueError, "same_frame_corrected_lidar_pose"):
            self.frame(5)
        self.assertFalse(self.session.accepting)

    def test_consumer_failure_revokes_and_cannot_repeat(self):
        self.tracking()
        self.delivered.clear()

        def failed(value):
            self.delivered.append(value)
            raise RuntimeError("injected_consumer_failure")

        self.consume = failed
        with self.assertRaisesRegex(RuntimeError, "injected_consumer_failure"):
            self.frame(5)
        with self.assertRaises(RuntimeError):
            self.frame(5)
        self.assertEqual(1, len(self.delivered))
        self.assertFalse(self.session.accepting)

    def test_wrong_thread_cannot_take_or_close_and_cleanup_stays_owned(self):
        for action in (lambda: self.frame(1), self.native.close):
            self.assertIsInstance(other_thread(action)[0], RuntimeError)
        self.native.close()
        self.native.close()
        self.assertEqual(["native_close", "close"], self.library.calls[-2:])
        self.assertTrue(self.session.state()["cleanupComplete"])

    def test_constructor_failure_closes_acquisition_and_session(self):
        self.native.close()
        output = NativeAcquisitionOutput("synthetic2.so")
        session = LocalizationSession(self.binding, lambda: (_ for _ in ()).throw(RuntimeError("factory")))
        with self.assertRaisesRegex(RuntimeError, "factory"):
            LocalizedNative(session, self.Native, acquisition_output=output, on_acquisition=self.delivered.append)
        self.assertTrue(output.closed)
        self.assertTrue(session.state()["cleanupComplete"])


if __name__ == "__main__":
    unittest.main()
