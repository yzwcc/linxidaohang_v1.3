"""Synthetic pacing/provenance checks; never execute native code or contact a robot."""
import ast
import importlib.util
from pathlib import Path, PurePosixPath
import shlex
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
AVAILABLE = importlib.util.find_spec("numpy") is not None and importlib.util.find_spec("json5") is not None
if AVAILABLE:
    import numpy as np
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        import glim_target_worker as target
        import prepare_glim_worker_target as prepare
        import run_glim_worker_target as coordinator


@unittest.skipUnless(AVAILABLE, "requires isolated NumPy and json5")
class WorkerPacing(unittest.TestCase):
    BASE = 1700000000000000000

    def inputs(self):
        return [("imu", self.BASE, object()), ("cloud", self.BASE + 40_000_000, object()),
                ("imu", self.BASE + 50_000_000, object())]

    def run_pace(self, messages=None):
        now = [1000.]
        calls, waits = [], []

        def sleep(seconds):
            self.assertGreater(seconds, 0)
            self.assertLessEqual(seconds, .02)
            waits.append(seconds)
            now[0] += seconds

        def offer(kind, message):
            calls.append((kind, now[0]))
            now[0] += .001
            return True

        worker = NS(snapshot=lambda: {"state": "running"},
                    offer_cloud=lambda message: offer("cloud", message),
                    offer_imu=lambda message: offer("imu", message))
        result = target.paced_delivery(
            worker, self.inputs() if messages is None else messages,
            {"topicCounts": {"cloud": 1, "imu": 2}},
            clock=lambda: now[0], sleep=sleep,
        )
        return result, calls, waits

    def test_one_times_mixed_arrivals_are_paced_instead_of_batched_or_preloaded(self):
        (counts, arrays), calls, waits = self.run_pace()
        self.assertEqual({"cloud": 1, "imu": 2}, counts)
        self.assertEqual(["imu", "cloud", "imu"], [value[0] for value in calls])
        self.assertTrue(waits)
        np.testing.assert_allclose(arrays["scheduled_relative_seconds"], [0, .04, .05])
        self.assertAlmostEqual(1000.04, calls[1][1])
        self.assertAlmostEqual(1000.05, calls[2][1])
        np.testing.assert_allclose(
            arrays["callback_end_relative_seconds"] - arrays["callback_begin_relative_seconds"], .001)

    def test_epoch_subtraction_preserves_nanoseconds_before_float_conversion(self):
        messages = self.inputs()
        messages[1] = ("cloud", self.BASE + 100, object())
        messages[2] = ("imu", self.BASE + 200, object())
        (counts, arrays), _, _ = self.run_pace(messages)
        self.assertEqual(1e-7, arrays["scheduled_relative_seconds"][1])
        self.assertEqual(2e-7, arrays["scheduled_relative_seconds"][2])

    def test_rollback_wrong_kind_and_missing_recorded_messages_are_rejected(self):
        bad = [
            [("imu", self.BASE, object()), ("cloud", self.BASE - 1, object())],
            [("other", self.BASE, object())],
            [("imu", True, object())],
            [("imu", self.BASE, object())],
        ]
        for messages in bad:
            with self.assertRaises(ValueError):
                self.run_pace(messages)

    def test_rejection_stops_without_retrying_a_packet(self):
        calls = []
        worker = NS(offer_imu=lambda value: calls.append(value) or False)
        with self.assertRaisesRegex(RuntimeError, "rejected"):
            target.paced_delivery(
                worker, self.inputs(), {"topicCounts": {"cloud": 1, "imu": 2}}, clock=lambda: 1000.)
        self.assertEqual(1, len(calls))

    def test_worker_failure_while_waiting_does_not_continue_after_deadline(self):
        worker = NS(snapshot=lambda: {"state": "failed"}, offer_imu=lambda message: True)
        with self.assertRaisesRegex(RuntimeError, "during_wait"):
            target.paced_delivery(
                worker, self.inputs(), {"topicCounts": {"cloud": 1, "imu": 2}}, clock=lambda: 1000.)

    def test_humble_reader_keeps_original_mixed_order_and_reads_one_item_at_a_time(self):
        rows = [(target.IMU, b"imu-a", self.BASE),
                (target.CLOUD, b"cloud", self.BASE + 100),
                (target.IMU, b"imu-b", self.BASE + 200)]
        reads = []

        class Reader:
            def open(self, storage, converter):
                pass

            def get_all_topics_and_types(self):
                return [NS(name=target.CLOUD, type="sensor_msgs/msg/PointCloud2"),
                        NS(name=target.IMU, type="sensor_msgs/msg/Imu")]

            def set_filter(self, filter_):
                self.topics = filter_.topics

            def has_next(self):
                return len(reads) < len(rows)

            def read_next(self):
                row = rows[len(reads)]
                reads.append(row)
                return row

        modules = {}
        for name, values in (
            ("rosbag2_py", {"SequentialReader": Reader, "StorageOptions": lambda **kw: NS(**kw),
                            "ConverterOptions": lambda *args: args,
                            "StorageFilter": lambda **kw: NS(**kw)}),
            ("rclpy", {}),
            ("rclpy.serialization", {"deserialize_message": lambda payload, type_: (payload, type_)}),
            ("sensor_msgs", {}),
            ("sensor_msgs.msg", {"Imu": "synthetic_imu", "PointCloud2": "synthetic_cloud"}),
        ):
            module = ModuleType(name)
            module.__dict__.update(values)
            modules[name] = module
        with patch.dict(sys.modules, modules):
            source = target.mixed_messages(Path("SYNTHETIC_BAG"))
            first = next(source)
            self.assertEqual(1, len(reads))
            self.assertEqual(("imu", self.BASE, (b"imu-a", "synthetic_imu")), first)
            rest = list(source)
            self.assertEqual(["cloud", "imu"], [entry[0] for entry in rest])
            self.assertEqual(self.BASE + 100, rest[0][1])

    def test_delivery_metrics_are_callback_costs_not_dds_or_voice_latency(self):
        (_, arrays), _, _ = self.run_pace()
        arrays["frame_callback_elapsed_seconds"] = np.array([.05])
        result = target.delivery_metrics(arrays)
        self.assertAlmostEqual(1, result["cloudCallbackSnapshotMillisecondsP50P95P99"][0])
        self.assertEqual(50, result["frameResultAfterCloudCallbackBeginMillisecondsMaximum"])
        self.assertEqual(.05, result["recordedReceiveSpanSeconds"])
        for name in result:
            self.assertNotIn("voice", name.lower())
            self.assertNotIn("dds", name.lower())

    def test_invalid_or_overlapping_delivery_observations_are_rejected(self):
        for name, value in (
            ("callback_begin_relative_seconds", np.array([0, .03, .05])),
            ("callback_end_relative_seconds", np.array([0, .1, .06])),
            ("kind_cloud_1_imu_0", np.array([0, 2, 0])),
            ("frame_callback_elapsed_seconds", np.array([float("nan")])),
        ):
            (_, arrays), _, _ = self.run_pace()
            arrays["frame_callback_elapsed_seconds"] = np.array([.05])
            arrays[name] = value
            with self.assertRaises(ValueError):
                target.delivery_metrics(arrays)

    def test_existing_bundle_output_is_preserved_without_native_build_or_ssh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = NS(controlled_bundle=root, core_bundle=root, output=root)
            with patch.object(prepare, "verify_replay_bundle") as verify:
                with self.assertRaisesRegex(ValueError, "new_ignored"):
                    prepare.prepare(args)
                verify.assert_not_called()

    def test_target_command_has_explicit_bounds_paths_and_no_ros_node_or_motion(self):
        self.assertEqual(target.PURPOSE, coordinator.PURPOSE)
        self.assertEqual(target.RESULT, coordinator.RESULT)
        home = PurePosixPath("/home/synthetic")
        remote = home / "lingxi-next-validation-arm64-worker-test"
        core = home / "lingxi-next-validation-arm64-core-test"
        command = shlex.split(coordinator.command(
            remote, core, home, "static", "header_is_scan_start", remote / "result-static"))
        self.assertEqual("timeout", command[0])
        self.assertIn("300s", command)
        self.assertIn("--kill-after=10s", command)
        self.assertIn("env", command)
        self.assertIn("-i", command)
        self.assertNotIn("ROS_DISTRO=humble", command)
        self.assertTrue(any(value.endswith("tools/glim_target_worker.py") for value in command))
        with self.assertRaisesRegex(ValueError, "home_scoped"):
            coordinator.command(remote, core, home, "static", "header_is_scan_start", home / "result")


class WorkerTargetBoundaries(unittest.TestCase):
    def test_target_trial_never_initializes_ros_or_creates_application_network_endpoints(self):
        tree = ast.parse((ROOT / "tools/glim_target_worker.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, (
                    "init", "create_node", "create_publisher", "publish", "create_subscription",
                    "create_client", "create_service", "call_async",
                ))
        self.assertNotIn("glim_target_mapping.run", ast.unparse(tree))


if __name__ == "__main__":
    unittest.main()
