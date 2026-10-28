"""Fake Humble boundaries only; these checks never start a DDS participant."""
import ast
import importlib.util
import os
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
AVAILABLE = importlib.util.find_spec("numpy") is not None
if AVAILABLE:
    with patch.object(sys, "path", [str(ROOT / "robot-slam")] + sys.path):
        from lingxi_slam.messages import SensorProfile
        from lingxi_slam.passive_ros import Topics, run_passive
        from lingxi_slam.worker import SensorWorker


@unittest.skipUnless(AVAILABLE, "requires isolated NumPy")
class PassiveHumble(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.worker = SensorWorker(
            self.factory, SensorProfile("cloud", "imu", "frame_relative_nanoseconds_hypothesis"),
            "header_is_scan_start",
        )
        self.addCleanup(self.worker.stop)
        self.stop = threading.Event()
        self.subscriptions = []
        self.topics = Topics("/synthetic/cloud", "/synthetic/imu")
        self.modules = self.bindings()
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, self.modules).start()
        patch.dict(os.environ, {"ROS_DISTRO": "humble"}).start()

    def factory(self):
        self.calls.append("native_create")
        return NS(close=lambda: self.calls.append("native_close"))

    def bindings(self, *, subscription_error=False, destroy_error=False, spin_error=False,
                 shutdown_timeout=False):
        owner = self

        class Context:
            def ok(self):
                return True

            def try_shutdown(self):
                owner.calls.append("context_shutdown")

        class Node:
            def create_subscription(self, type_, topic, callback, qos):
                owner.subscriptions.append((type_, topic, callback, qos))
                if subscription_error:
                    raise RuntimeError("synthetic subscription failure")
                return object()

            def destroy_node(self):
                owner.calls.append("node_destroy")
                if destroy_error:
                    raise RuntimeError("synthetic node cleanup failure")

        class Executor:
            def __init__(self, *, context):
                owner.calls.append("single_executor")

            def add_node(self, node):
                owner.calls.append("node_added")

            def spin_once(self, *, timeout_sec):
                owner.calls.append("spin")
                if spin_error:
                    raise RuntimeError("synthetic spin failure")
                owner.stop.set()

            def shutdown(self, *, timeout_sec):
                owner.calls.append("executor_shutdown")
                return not shutdown_timeout

        def create_node(name, **options):
            owner.node_options = options
            owner.calls.append("node_create")
            return Node()

        root = ModuleType("rclpy")
        root.init = lambda **options: owner.calls.append("ros_init")
        root.create_node = create_node
        result = {"rclpy": root}
        for name, entries in (
            ("rclpy.context", {"Context": Context}),
            ("rclpy.executors", {"SingleThreadedExecutor": Executor}),
            ("rclpy.qos", {
                "QoSProfile": lambda **options: NS(**options),
                "HistoryPolicy": NS(KEEP_LAST="keep_last"),
                "ReliabilityPolicy": NS(BEST_EFFORT="best_effort"),
                "DurabilityPolicy": NS(VOLATILE="volatile"),
            }),
            ("sensor_msgs", {}),
            ("sensor_msgs.msg", {"Imu": type("SyntheticImu", (), {}),
                                 "PointCloud2": type("SyntheticCloud", (), {})}),
        ):
            module = ModuleType(name)
            module.__dict__.update(entries)
            result[name] = module
        return result

    def run_session(self):
        return run_passive(self.worker, self.topics, 1, stop_event=self.stop)

    def test_only_explicit_sensor_subscriptions_and_no_global_ros_overrides(self):
        report = self.run_session()
        self.assertEqual("requested_stop", report["stopReason"])
        self.assertEqual([self.topics.cloud, self.topics.imu], [entry[1] for entry in self.subscriptions])
        self.assertEqual([8, 256], [entry[3].depth for entry in self.subscriptions])
        for entry in self.subscriptions:
            self.assertEqual("keep_last", entry[3].history)
            self.assertEqual("best_effort", entry[3].reliability)
            self.assertEqual("volatile", entry[3].durability)
        for option in ("enable_rosout", "start_parameter_services", "use_global_arguments"):
            self.assertFalse(self.node_options[option])
        self.assertTrue(all(value is False for value in report["boundaries"].values()))
        self.assertFalse(report["worker"]["threadAlive"])
        self.assertEqual(0, report["worker"]["counts"]["acceptedClouds"])
        self.assertEqual(1, self.calls.count("native_close"))
        self.assertEqual("context_shutdown", self.calls[-1])

    def test_setup_failure_does_not_create_native_and_cleans_every_created_resource(self):
        patch.dict(sys.modules, self.bindings(subscription_error=True)).start()
        with self.assertRaisesRegex(RuntimeError, "subscription failure"):
            self.run_session()
        self.assertNotIn("native_create", self.calls)
        self.assertIn("executor_shutdown", self.calls)
        self.assertIn("node_destroy", self.calls)
        self.assertIn("context_shutdown", self.calls)

    def test_ros_spin_failure_still_joins_and_closes_native(self):
        patch.dict(sys.modules, self.bindings(spin_error=True)).start()
        with self.assertRaisesRegex(RuntimeError, "spin failure"):
            self.run_session()
        self.assertFalse(self.worker.snapshot()["threadAlive"])
        self.assertEqual(1, self.calls.count("native_close"))
        self.assertIn("context_shutdown", self.calls)

    def test_ros_cleanup_failure_does_not_skip_worker_or_context_cleanup(self):
        patch.dict(sys.modules, self.bindings(destroy_error=True)).start()
        with self.assertRaisesRegex(RuntimeError, "cleanup_failed"):
            self.run_session()
        self.assertFalse(self.worker.snapshot()["threadAlive"])
        self.assertEqual(1, self.calls.count("native_close"))
        self.assertIn("context_shutdown", self.calls)

    def test_executor_shutdown_timeout_is_not_reported_as_clean_exit(self):
        patch.dict(sys.modules, self.bindings(shutdown_timeout=True)).start()
        with self.assertRaisesRegex(RuntimeError, "cleanup_failed"):
            self.run_session()
        self.assertIn("node_destroy", self.calls)
        self.assertIn("context_shutdown", self.calls)

    def test_unsourced_or_other_ros_version_is_rejected_before_import_or_start(self):
        with patch.dict(os.environ, {"ROS_DISTRO": "jazzy"}):
            with self.assertRaisesRegex(RuntimeError, "sourced_humble"):
                self.run_session()
        self.assertEqual([], self.calls)
        self.assertEqual("new", self.worker.snapshot()["state"])

    def test_duration_invalid_topics_and_reused_workers_are_rejected(self):
        for seconds in (True, 0, float("nan"), 301):
            with self.assertRaises(ValueError):
                run_passive(self.worker, self.topics, seconds)
        for cloud, imu in (("relative", "/imu"), ("/cloud", "/cloud"), ("/cloud/", "/imu")):
            with self.assertRaises(ValueError):
                Topics(cloud, imu)
        self.worker.stop()
        with self.assertRaises(ValueError):
            self.run_session()

    def test_preexisting_stop_does_not_spin_or_produce_a_pose(self):
        self.stop.set()
        report = self.run_session()
        self.assertNotIn("spin", self.calls)
        self.assertNotIn("native_create", self.calls)
        self.assertEqual("requested_stop", report["stopReason"])
        self.assertEqual(0, report["worker"]["counts"]["completedMessages"])

    def test_stop_retires_native_before_ros_teardown(self):
        self.run_session()
        self.assertLess(self.calls.index("native_close"), self.calls.index("executor_shutdown"))


class PassiveBoundaries(unittest.TestCase):
    def test_no_application_publishers_clients_motion_or_file_network_operations(self):
        tree = ast.parse((ROOT / "robot-slam/lingxi_slam/passive_ros.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, (
                    "create_publisher", "publish", "create_client", "create_service",
                    "create_action_client", "call", "call_async", "open", "write_text",
                ))
        self.assertNotIn("MultiThreadedExecutor", ast.unparse(tree))


if __name__ == "__main__":
    unittest.main()
