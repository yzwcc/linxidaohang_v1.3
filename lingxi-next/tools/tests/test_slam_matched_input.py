"""Synthetic capture/replay contracts; no live subscription or native library."""
import ast
import importlib.util
from pathlib import Path, PurePosixPath
import platform
import shlex
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import run_glim_matched_target as coordinator
from slam_callback_journal import Writer

AVAILABLE = (platform.system() == "Linux" and importlib.util.find_spec("numpy") is not None
             and importlib.util.find_spec("json5") is not None)
if AVAILABLE:
    import numpy as np
    import glim_matched_session as matched
    from lingxi_slam.worker import SensorWorker


class MatchedCommand(unittest.TestCase):
    def test_only_live_mode_has_dds_environment_replay_has_explicit_reference(self):
        home = PurePosixPath("/home/synthetic")
        for mode in ("live", "replay"):
            values = shlex.split(coordinator.command(home / "trial", home / "core", home, mode, 20))
            self.assertIn("timeout", values)
            self.assertIn("--kill-after=10s", values)
            self.assertIn("50s", values)
            self.assertTrue(any(value.endswith("tools/glim_matched_session.py") for value in values))
            self.assertEqual(mode == "live", "ROS_DOMAIN_ID=0" in values)
            self.assertEqual(mode == "replay", "--reference" in values)
        for mode, seconds in (("other", 20), ("live", 0), ("live", 61)):
            with self.assertRaises(ValueError):
                coordinator.command(home / "trial", home / "core", home, mode, seconds)

    def test_matched_tools_have_no_application_publishers_or_actuation_clients(self):
        tree = ast.parse((ROOT / "tools/glim_matched_session.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, (
                    "create_publisher", "publish", "create_client", "create_service",
                    "call_async", "create_node", "init", "Popen",
                ))
        self.assertNotIn("pickle", (ROOT / "tools/slam_callback_journal.py").read_text().split("\n", 1)[1])


@unittest.skipUnless(AVAILABLE, "requires isolated Linux NumPy and json5")
class MatchedInput(unittest.TestCase):
    def arrays(self):
        return {
            "frame_headers_ns": np.array([10, 20], dtype=np.int64),
            "frame_algorithm_seconds": np.array([1000., 1000.1]),
            "frame_statistics": np.array([[1., 2., 10., 0.], [1., 2., 10., 1.]]),
            "pose_headers_ns": np.array([10, 20], dtype=np.int64),
            "poses": np.array([np.eye(4), np.eye(4)]),
        }

    def test_comparison_has_no_alignment_or_physical_accuracy_claim(self):
        reference, replay = self.arrays(), self.arrays()
        replay["poses"][1, 0, 3] = .01
        replay["frame_statistics"][:, :2] *= 2
        result = matched.compare(reference, replay)
        self.assertAlmostEqual(.01, result["positionDifferenceRawUnitsP50P95Max"][-1])
        self.assertFalse(result["physicalAccuracyValidated"])
        self.assertTrue(result["posesComparedWithoutAlignmentOrScaleFitting"])

    def test_frame_times_domains_counts_and_finite_results_must_match(self):
        for key in ("frame_headers_ns", "frame_algorithm_seconds", "pose_headers_ns",
                    "frame_statistics", "poses"):
            other = self.arrays()
            if key == "poses":
                other[key][0, 0, 0] = np.nan
            elif key == "frame_statistics":
                other[key][0, 2] += 1
            else:
                other[key][0] += 1
            with self.assertRaises(ValueError, msg=key):
                matched.compare(self.arrays(), other)

    def test_immutable_callback_is_serialized_before_offer_and_rejection_fails_session(self):
        journal, message = NS(append=lambda *values: events.append(values)), object()
        events = []
        worker = matched.JournalWorker(lambda: None, "header_is_scan_start",
                                      matched.MatchedObservations(), journal, lambda value: b"synthetic")
        with patch.object(SensorWorker, "_offer", side_effect=lambda *values: events.append("offer") or True):
            self.assertTrue(worker._offer("imu", message))
        self.assertEqual("imu", events[0][0])
        self.assertEqual(b"synthetic", events[0][2])
        self.assertEqual("offer", events[1])
        with patch.object(SensorWorker, "_offer", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "rejected"):
                worker._offer("imu", message)
        worker.stop()

    def test_replay_consumes_exact_completed_prefix_but_validates_whole_journal(self):
        base = 1_700_000_000_000_000_000
        def header(stamp, frame):
            return NS(stamp=NS(sec=stamp // 10**9, nanosec=stamp % 10**9), frame_id=frame)
        def imu(stamp):
            return NS(header=header(stamp, "lidar_imu_chest_front"),
                      linear_acceleration=NS(x=0., y=0., z=9.8),
                      angular_velocity=NS(x=0., y=0., z=0.),
                      linear_acceleration_covariance=[0.] * 9, angular_velocity_covariance=[0.] * 9)
        def cloud(stamp):
            dtype = np.dtype({"names": ["x", "y", "z", "timestamp"],
                              "formats": ["<f4"] * 3 + ["<f8"],
                              "offsets": [0, 4, 8, 16], "itemsize": 24})
            values = np.zeros(10, dtype=dtype)
            values["x"], values["timestamp"] = np.arange(10), np.arange(10) * 1e6
            return NS(header=header(stamp, "lidar_chest_front"), width=10, height=1,
                      point_step=24, row_step=240, is_bigendian=False, data=values.tobytes(),
                      fields=[NS(name=name, offset=offset, datatype=kind, count=1)
                              for name, offset, kind in (("x", 0, 7), ("y", 4, 7),
                                                         ("z", 8, 7), ("timestamp", 16, 8))])
        messages = [imu(base), cloud(base), imu(base + 20_000_000),
                    cloud(base + 20_000_000), imu(base + 40_000_000)]
        decoded = []
        def deserialize(kind, payload):
            index = int(payload)
            decoded.append(index)
            return messages[index]
        class Native:
            def imu(self, *values):
                pass
            def frame(self, stamp, points, offsets):
                return np.eye(4), np.array([1., 2., len(points), 0.])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic"
            writer = Writer(path)
            for index, kind in enumerate(("imu", "cloud", "imu", "cloud", "imu", "imu")):
                writer.append(kind, index * 10_000_000, str(index).encode())
            writer.finish()
            state, arrays = matched.replay_prefix(path, 5, "header_is_scan_start", Native(), deserialize)
            self.assertEqual(list(range(5)), decoded)
            self.assertEqual(3, state["counts"]["receivedImu"])
            self.assertEqual(2, state["counts"]["processedScans"])
            self.assertEqual(2, len(arrays["poses"]))
            with self.assertRaises(ValueError):
                matched.replay_prefix(path, 7, "header_is_scan_start", Native(), lambda *values: imu(base))


if __name__ == "__main__":
    unittest.main()
