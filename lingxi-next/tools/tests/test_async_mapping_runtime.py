"""Runtime full-mapper contracts using isolated synthetic counters, not sensor accuracy."""
import importlib.util
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-slam"))
AVAILABLE = importlib.util.find_spec("numpy") is not None
if AVAILABLE:
    import numpy as np
    from lingxi_slam.async_mapping import AsyncMappingLibrary, checked_worker, compare_corrected_points


@unittest.skipUnless(AVAILABLE, "requires isolated numerical environment")
class RuntimeMappingContracts(unittest.TestCase):
    def test_saved_snapshot_permits_only_double_precision_transform_roundoff(self):
        points = np.array([[1., 2., 3.], [4., 5., 6.]])
        comparison = compare_corrected_points(points, np.nextafter(points, np.inf))
        self.assertTrue(comparison["withinDoublePrecisionRoundoff"])
        self.assertFalse(compare_corrected_points(points, points + 1e-9)["withinDoublePrecisionRoundoff"])
        self.assertFalse(compare_corrected_points(points, points[::-1])["withinDoublePrecisionRoundoff"])

    def stats(self):
        return np.array([100, 98, 4, 128, 2000, 1990, 20, 8192, 0, 0, .1, 4000, 5000])

    def native(self):
        value = object.__new__(AsyncMappingLibrary)
        value.owner, value.opened, value.closed, value.finishing = threading.get_ident(), True, False, False
        value.imu_samples = value.estimated_frames = 0
        value.preview, value.preview_checked_at = None, 0.
        value.cache_lock, value.cache = threading.Lock(), None
        value.library = SimpleNamespace(lingxi_full_error=lambda: b"synthetic_native_failure")
        return value

    def test_inflight_work_is_reported_without_calling_it_complete(self):
        value = checked_worker(self.stats())
        self.assertEqual(98, value["completedFrames"])
        self.assertEqual(4000, value["maximumWaitMs"])
        with self.assertRaises(ValueError):
            checked_worker(self.stats(), complete=True)

    def test_inconsistent_capacity_or_counts_are_rejected(self):
        for index, value in ((0, 97), (1, 101), (2, 129), (3, 256), (4, 1980), (6, 8193),
                             (7, 4096), (8, 1), (9, 1), (10, float("nan")), (11, -1), (0, .5)):
            stats = self.stats()
            stats[index] = value
            with self.subTest(index=index, value=value), self.assertRaises((ValueError, RuntimeError)):
                checked_worker(stats)

    def test_real_imu_vectors_are_validated_and_forwarded_once(self):
        native = self.native()
        calls = []
        def feed(stamp, acc, gyro):
            calls.append((stamp, np.ctypeslib.as_array(acc, shape=(3,)).copy(),
                          np.ctypeslib.as_array(gyro, shape=(3,)).copy()))
            return 0
        native.library.lingxi_full_imu = feed
        native.feed_imu(1000., [0, 0, 9.81], [0, .1, 0])
        self.assertEqual(1, native.imu_samples)
        self.assertEqual(1, len(calls))
        self.assertEqual([0, 0, 9.81], calls[0][1].tolist())
        for acc, gyro in (([0, 0], [0, 0, 0]), ([0, 0, float("nan")], [0, 0, 0])):
            with self.assertRaises(ValueError):
                native.feed_imu(1001., acc, gyro)
        self.assertEqual(1, native.imu_samples)

    def test_pose_accounting_uses_returned_estimates_not_marginalization_count(self):
        native = self.native()
        native.check_health = lambda: None
        native.observe_frame((None, None))
        native.observe_frame((np.eye(4), None))
        self.assertEqual(1, native.estimated_frames)

    def test_empty_or_growing_snapshots_are_not_fabricated_or_truncated(self):
        native = self.native()
        native.library.lingxi_async_snapshot = lambda header, xyz, capacity: 0
        self.assertIsNone(native.snapshot())
        def growing(pointer, xyz, capacity):
            header = np.ctypeslib.as_array(pointer, shape=(6,))
            header[:] = [1, 1, 1 if xyz is None else capacity + 1, 0, 0, 0]
            return 0 if xyz is None else 1
        native.library.lingxi_async_snapshot = growing
        with self.assertRaisesRegex(RuntimeError, "retry_later"):
            native.snapshot()

    def test_snapshot_coordinates_have_a_distinct_optimized_map_frame_and_are_immutable(self):
        native = self.native()
        def snapshot(pointer, xyz, capacity):
            np.ctypeslib.as_array(pointer, shape=(6,))[:] = [3, 2, 2, 1, 0, 1]
            if xyz is not None:
                np.ctypeslib.as_array(xyz, shape=(6,))[:] = [1, 2, 3, 4, 5, 6]
            return 0
        native.library.lingxi_async_snapshot = snapshot
        value = native.snapshot()
        self.assertEqual("lingxi_glim_map", value.coordinate_frame)
        self.assertEqual("globally_corrected_submaps", value.kind)
        self.assertEqual(3, value.revision)
        self.assertFalse(value.points.flags.writeable)
        self.assertEqual([[1, 2, 3], [4, 5, 6]], value.points.tolist())

    def test_control_thread_can_read_cached_health_but_cannot_call_native(self):
        native = self.native()
        native.cache = {"engine": "glim-full-async-v1", "acceptedFrames": 10}
        output = []
        def read():
            output.append(native.cached_health())
            try:
                native.snapshot()
            except RuntimeError as error:
                output.append(str(error))
        thread = threading.Thread(target=read)
        thread.start()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(10, output[0]["acceptedFrames"])
        self.assertIn("same_owner", output[1])
        output[0]["acceptedFrames"] = 999
        self.assertEqual(10, native.cached_health()["acceptedFrames"])


if __name__ == "__main__":
    unittest.main()
