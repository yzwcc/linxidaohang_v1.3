"""Fake Humble boundaries only; these checks never start a DDS participant."""
import ast
import importlib.util
import os
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[2]
AVAILABLE = importlib.util.find_spec("numpy") is not None
if AVAILABLE:
    with patch.object(sys, "path", [str(ROOT / "robot-slam")] + sys.path):
        from lingxi_slam.messages import SensorProfile
        from lingxi_slam.passive_ros import Topics, run_passive, wait_for_publishers
        from lingxi_slam import passive_ros
        from lingxi_slam.sensor_admission import SensorAdmission
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
            def count_publishers(self, topic):
                return 1

            def create_subscription(self, type_, topic, callback, qos):
                owner.subscriptions.append((type_, topic, callback, qos))
                if subscription_error:
                    raise RuntimeError("synthetic subscription failure")
                return NS(get_publisher_count=lambda: 1)

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

    def test_extra_setup_uses_owned_node_and_cleanup_precedes_native_teardown(self):
        def setup(node):
            self.calls.append("extra_setup")
            return lambda: self.calls.append("extra_cleanup")
        run_passive(self.worker, self.topics, 1, stop_event=self.stop, setup=setup)
        self.assertLess(self.calls.index("node_added"), self.calls.index("extra_setup"))
        self.assertLess(self.calls.index("extra_cleanup"), self.calls.index("native_close"))

    def test_optional_metadata_hook_runs_before_the_original_receiver(self):
        values = []
        with patch.object(self.worker, "offer_cloud", side_effect=lambda message: values.append("receiver")):
            run_passive(self.worker, self.topics, 1, stop_event=self.stop,
                        on_sensor=lambda kind, message: values.append(kind))
        with patch.object(self.worker, "snapshot", return_value={"state": "running"}):
            self.subscriptions[0][2](object())
        self.assertEqual(["cloud", "receiver"], values)

    def test_starting_or_terminal_worker_does_not_admit_stale_ros_callbacks(self):
        values = []
        with patch.object(self.worker, "offer_cloud", side_effect=lambda message: values.append("receiver")):
            run_passive(self.worker, self.topics, 1, stop_event=self.stop,
                        on_sensor=lambda kind, message: values.append("metadata"))
        for state in ("new", "starting", "stopping", "stopped", "failed"):
            with self.subTest(state=state), \
                    patch.object(self.worker, "snapshot", return_value={"state": state}):
                self.assertFalse(self.subscriptions[0][2](object()))
        self.assertEqual([], values)

    def mapping_session(self):
        admission = SensorAdmission(self.worker)

        def finalize(reason):
            self.calls.append("finalize_" + reason)
            self.assertTrue(admission.closed)
            self.assertEqual("context_shutdown", self.calls[-2])
            self.worker.stop()
            return {"saved": False}

        return run_passive(self.worker, self.topics, 1, stop_event=self.stop,
                           admission=admission, on_input_closed=finalize)

    def test_opt_in_finalization_follows_ros_teardown_and_closes_admission(self):
        report = self.mapping_session()
        self.assertEqual({"saved": False}, report["inputFinalization"])
        self.assertIn("finalize_requested_stop", self.calls)
        self.assertLess(self.calls.index("context_shutdown"), self.calls.index("native_close"))
        self.assertFalse(report["worker"]["threadAlive"])

    def test_opt_in_spin_failure_calls_failed_finalization_and_joins(self):
        patch.dict(sys.modules, self.bindings(spin_error=True)).start()
        with self.assertRaisesRegex(RuntimeError, "spin failure"):
            self.mapping_session()
        self.assertIn("finalize_input_failed", self.calls)
        self.assertEqual(1, self.calls.count("native_close"))

    def test_opt_in_teardown_failure_cannot_be_finalized_as_save(self):
        patch.dict(sys.modules, self.bindings(destroy_error=True)).start()
        with self.assertRaisesRegex(RuntimeError, "cleanup_failed"):
            self.mapping_session()
        self.assertIn("finalize_input_failed", self.calls)
        self.assertEqual(1, self.calls.count("native_close"))

    def test_failing_finalizer_does_not_leave_native_worker_alive(self):
        admission = SensorAdmission(self.worker)

        def failed(reason):
            raise RuntimeError("synthetic_finalize_failure")

        with self.assertRaisesRegex(RuntimeError, "cleanup_failed"):
            run_passive(self.worker, self.topics, 1, stop_event=self.stop,
                        admission=admission, on_input_closed=failed)
        self.assertTrue(admission.closed)
        self.assertFalse(self.worker.snapshot()["threadAlive"])
        self.assertEqual(1, self.calls.count("native_close"))

    def test_partial_or_foreign_lifecycle_is_rejected_before_ros(self):
        admission = SensorAdmission(self.worker)
        with self.assertRaises(ValueError):
            run_passive(self.worker, self.topics, 1, admission=admission)
        with self.assertRaises(ValueError):
            run_passive(self.worker, self.topics, 1, on_input_closed=lambda reason: None)
        self.assertEqual([], self.calls)

    def test_discovery_waits_for_both_sensor_publishers_without_starting_native(self):
        node = Mock()
        node.count_publishers.side_effect = [0, 1, 1]
        with patch.object(passive_ros.time, "monotonic", return_value=0.), \
                patch.object(self.stop, "wait") as waiting:
            self.assertTrue(wait_for_publishers(node, self.topics, 1., self.stop))
        waiting.assert_called_once_with(.02)
        self.assertEqual([self.topics.cloud, self.topics.cloud, self.topics.imu],
                         [call.args[0] for call in node.count_publishers.call_args_list])
        self.assertNotIn("native_create", self.calls)

    def test_discovery_timeout_or_cancel_never_starts_native(self):
        node = Mock()
        node.count_publishers.return_value = 0
        with patch.object(passive_ros.time, "monotonic", return_value=2.):
            with self.assertRaisesRegex(TimeoutError, "publisher_discovery"):
                wait_for_publishers(node, self.topics, 1., self.stop)
        self.stop.set()
        self.assertFalse(wait_for_publishers(node, self.topics, 1., self.stop))
        self.assertNotIn("native_create", self.calls)

    def test_failed_discovery_cleans_ros_without_opening_worker(self):
        with patch.object(passive_ros, "wait_for_publishers", side_effect=TimeoutError("discovery")):
            with self.assertRaises(TimeoutError):
                self.run_session()
        self.assertNotIn("native_create", self.calls)
        self.assertIn("context_shutdown", self.calls)
        self.assertFalse(self.worker.snapshot()["threadAlive"])


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
