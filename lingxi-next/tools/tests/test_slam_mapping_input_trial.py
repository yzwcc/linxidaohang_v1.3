"""Recorded-boundary fixture and public aggregate checks; never run native SLAM."""
import ast
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
import sys
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("mapping_input_trial", ROOT / "tools/run_mapping_input_trial.py")
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


class RecordedBoundary(unittest.TestCase):
    def test_fixture_preserves_mode_and_exhaustion_without_real_ros(self):
        entries = iter([NS(kind="imu", elapsed_ns=10, payload=b"synthetic-cdr")])
        stop = threading.Event()
        worker = NS(snapshot=lambda: {"state": "running"})
        types = NS(deserialize_cdr=Mock(return_value="synthetic-message"))
        fixture = trial.RecordedSubscriptions(worker, entries, types, stop, 1.)
        modules = fixture.bindings(NS(cloud="/recorded/cloud", imu="/recorded/imu"))
        node = modules["rclpy"].create_node()
        callback = Mock(return_value=True)
        node.create_subscription(None, "/recorded/imu", callback, None)
        executor = modules["rclpy.executors"].SingleThreadedExecutor(context=None)
        executor.spin_once(timeout_sec=.01)
        self.assertEqual(1, fixture.delivered)
        self.assertFalse(stop.is_set())
        executor.spin_once(timeout_sec=.01)
        self.assertTrue(stop.is_set())
        types.deserialize_cdr.assert_called_once_with(b"synthetic-cdr", "sensor_msgs/msg/Imu")
        callback.assert_called_once_with("synthetic-message")

    def test_rejected_fixture_callback_is_not_counted_as_delivery(self):
        fixture = trial.RecordedSubscriptions(
            NS(snapshot=lambda: {"state": "running"}),
            iter([NS(kind="cloud", elapsed_ns=10, payload=b"synthetic")]),
            NS(deserialize_cdr=lambda *args: None), threading.Event(), 1.)
        modules = fixture.bindings(NS(cloud="/recorded/cloud", imu="/recorded/imu"))
        modules["rclpy"].create_node().create_subscription(
            None, "/recorded/cloud", lambda message: False, None)
        executor = modules["rclpy.executors"].SingleThreadedExecutor(context=None)
        with self.assertRaisesRegex(RuntimeError, "callback_rejected"):
            executor.spin_once(timeout_sec=.01)
        self.assertEqual(0, fixture.delivered)

    def test_diagnostic_has_no_robot_connection_or_application_actuation(self):
        tree = ast.parse((ROOT / "tools/run_mapping_input_trial.py").read_text())
        forbidden = {"paramiko", "requests", "socket", "rosbag2_py"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue(all(alias.name.split(".")[0] not in forbidden for alias in node.names))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, ("connect", "create_publisher", "create_client", "publish"))


class PublishedMappingInput(unittest.TestCase):
    def test_save_and_discard_aggregates_bind_recorded_sources_without_live_claims(self):
        evidence = ROOT / "docs/slam-mapping-input-evidence.json"
        value = json.loads(evidence.read_text())
        self.assertEqual([True, False], [run["saveRequested"] for run in value["runs"]])
        for run in value["runs"]:
            self.assertEqual("recorded_passive_boundary_mapping_points_verified", run["result"])
            self.assertEqual("recorded", run["inputMode"])
            self.assertEqual(4144, run["completedMessages"])
            self.assertEqual(196, run["processedScans"])
            self.assertEqual(165, run["estimatedPoses"])
            self.assertEqual(164, run["points"]["packets"])
            self.assertEqual(1, run["nativeCalls"]["create"])
            self.assertEqual(1, run["nativeCalls"]["close"])
            self.assertTrue(run["oneNonMainNativeOwner"])
            self.assertFalse(run["workerThreadAlive"])
            self.assertTrue(all(flag is False for flag in run["boundaries"].values()))
            for name, expected in run["sourceSha256"].items():
                source = subprocess.check_output(["git", "show",
                    "5ac35d3bfb5a548b5cc15f585b03c9097662458f:lingxi-next/" + name], cwd=ROOT)
                self.assertEqual(expected, hashlib.sha256(source).hexdigest(), name)
            self.assertEqual(set(trial.SOURCES), set(run["sourceSha256"]))
        saved, discarded = value["runs"]
        self.assertEqual(165, saved["savedCounts"]["savedFrames"])
        self.assertEqual(2655, saved["roundTrip"]["pointCount"])
        self.assertLessEqual(saved["roundTrip"]["maximumPointDisplacementRawUnits"], 1e-4)
        self.assertIsNone(discarded["savedCounts"])
        self.assertIsNone(discarded["roundTrip"])
        for token in ('"/home/', '"/mnt/', '"session":', '"password":', '"poses":', '"points": ['):
            self.assertNotIn(token, json.dumps(value))


if __name__ == "__main__":
    unittest.main()
