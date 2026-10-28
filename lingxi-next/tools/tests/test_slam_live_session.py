"""Synthetic live-runner contracts only; no ROS, robot, or native library is started."""
import ast
import importlib.util
import os
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
import run_glim_live_target as coordinator

AVAILABLE = (platform.system() == "Linux" and importlib.util.find_spec("numpy") is not None
             and importlib.util.find_spec("json5") is not None)
if AVAILABLE:
    import numpy as np
    import glim_live_session as live
    import prepare_glim_live_target as prepare


class LiveCommand(unittest.TestCase):
    def test_clean_explicit_environment_external_timeout_and_only_passive_entrypoint(self):
        home = PurePosixPath("/home/synthetic")
        root, core = home / "live", home / "core"
        command = shlex.split(coordinator.command(root, core, home, "header_is_scan_start",
                                                  root / "output", 20))
        self.assertEqual("timeout", command[0])
        for value in ("50s", "--kill-after=10s", "ROS_DISTRO=humble", "ROS_DOMAIN_ID=0",
                      "RMW_IMPLEMENTATION=rmw_fastrtps_cpp", "env", "-i", "nice"):
            self.assertIn(value, command)
        self.assertTrue(any(value.endswith("tools/glim_live_session.py") for value in command))
        for seconds in (True, 0, 61, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                coordinator.command(root, core, home, "header_is_scan_start", root / "out", seconds)
        with self.assertRaises(ValueError):
            coordinator.command(root, core, home, "header_is_scan_start", home / "outside", 20)

    def test_existing_results_are_preserved_before_connection(self):
        with tempfile.TemporaryDirectory() as name, patch.object(coordinator, "connect") as connect:
            with self.assertRaises(ValueError):
                coordinator.run(NS(output=Path(name), user="synthetic"))
            connect.assert_not_called()

    def test_no_actuation_endpoints_or_product_or_vendor_mutation(self):
        tree = ast.parse((ROOT / "tools/glim_live_session.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, (
                    "create_publisher", "publish", "create_client", "create_service",
                    "create_action_client", "call_async", "Popen",
                ))
                self.assertFalse(isinstance(node.func.value, ast.Name)
                                 and node.func.value.id == "os" and node.func.attr == "system")


@unittest.skipUnless(AVAILABLE, "requires Linux isolated NumPy and json5")
class LiveContracts(unittest.TestCase):
    def arrays(self):
        return {
            "cloud_headers_ns": np.array([10, 20, 30], dtype=np.int64),
            "cloud_callback_begin": np.array([.1, .2, .3]),
            "cloud_callback_end": np.array([.101, .201, .301]),
            "imu_headers_ns": np.array([1, 2, 3], dtype=np.int64),
            "imu_callback_begin": np.array([.05, .15, .25]),
            "imu_callback_end": np.array([.051, .151, .251]),
            "frame_headers_ns": np.array([10, 20, 30], dtype=np.int64),
            "frame_callback_delay": np.array([.05, .06, .07]),
            "frame_statistics": np.array([[1., 2., 100, 0]] * 3),
            "pose_headers_ns": np.array([20, 30], dtype=np.int64),
            "poses": np.array([np.eye(4)] * 2),
            "active_consumer_headers_ns": np.array([10, 20], dtype=np.int64),
        }

    def test_same_protocol_constants_and_frozen_runtime_sources(self):
        self.assertEqual(live.PURPOSE, coordinator.PURPOSE)
        self.assertEqual(live.RESULT, coordinator.RESULT)
        self.assertIn("robot-slam/lingxi_slam/passive_ros.py", live.RUNTIME_SOURCES)

    def test_metrics_distinguish_initialization_first_result_and_callback_not_sensor_latency(self):
        metrics = live.metrics(self.arrays())
        self.assertEqual(1, metrics["initializationScans"])
        self.assertEqual(2, metrics["estimatedPoses"])
        self.assertAlmostEqual(.26, metrics["firstPoseResultAfterObservationStartSeconds"])
        self.assertEqual(70, metrics["frameResultAfterCallbackBeginMillisecondsMaximum"])
        self.assertEqual(3, metrics["nativePreprocessAndOdometryMillisecondsP50P95P99"][1])
        self.assertEqual(1, metrics["retiredAtStopFrameResults"])

    def test_diagnostic_can_count_retired_result_without_forwarding_it_to_active_consumer(self):
        observation = live.Observations()
        worker = live.MeasuredWorker(lambda: None, "header_is_scan_start", observation)
        observation.clouds[10] = 0.
        observation.clouds[20] = 0.
        worker.state = "running"
        worker.progress_at = worker.clock()
        worker._frame(10, 1000., None, np.zeros(4))
        worker.state = "stopping"
        worker._frame(20, 1000.1, None, np.zeros(4))
        arrays = observation.arrays()
        np.testing.assert_array_equal(arrays["frame_headers_ns"], [10, 20])
        np.testing.assert_array_equal(arrays["active_consumer_headers_ns"], [10])
        worker.stop()

    def test_metrics_reject_tampered_headers_delays_missing_arrays_and_invalid_poses(self):
        for key, value in (
            ("imu_headers_ns", np.array([1, 1, 3], dtype=np.int64)),
            ("frame_headers_ns", np.array([10, 20, 99], dtype=np.int64)),
            ("pose_headers_ns", np.array([10, 99], dtype=np.int64)),
            ("frame_callback_delay", np.array([.05, float("nan"), .07])),
            ("poses", np.zeros((2, 4, 4))),
            ("active_consumer_headers_ns", np.array([20, 30], dtype=np.int64)),
            ("cloud_callback_begin", np.zeros(3)),
        ):
            arrays = self.arrays()
            arrays[key] = value
            with self.assertRaises(ValueError, msg=key):
                live.metrics(arrays)
        arrays = self.arrays()
        del arrays["poses"]
        with self.assertRaises(ValueError):
            live.metrics(arrays)

    def test_observation_capacity_and_integer_nanoseconds(self):
        observation = live.Observations()
        def message(value):
            return NS(header=NS(stamp=NS(sec=1700000000, nanosec=value)))
        row = observation.before("cloud", message(123))
        observation.after(row)
        self.assertEqual(1700000000000000123, observation.arrays()["cloud_headers_ns"][0])
        with self.assertRaisesRegex(ValueError, "ordered"):
            observation.before("cloud", message(123))
        with patch.object(live, "MAXIMUM_OBSERVATIONS", 1):
            with self.assertRaisesRegex(RuntimeError, "bounded"):
                observation.before("imu", message(124))

    def test_partial_constructor_and_post_open_verification_failure_close_once(self):
        for constructor_failure in (True, False):
            calls = []
            class Native:
                def __init__(self, *values):
                    self.library = object()
                    if constructor_failure:
                        raise RuntimeError("synthetic partial open")
                def close(self):
                    calls.append("actual_close")
            factory = NS(record=calls.append)
            def verify():
                raise ValueError("synthetic loaded path")
            with self.assertRaises((ValueError, RuntimeError)):
                live.NativeOwner(factory, Path("/live"), Path("/core"), verify, native_type=Native)
            self.assertEqual(["create", "close", "actual_close"], calls)

    def test_missing_library_does_not_close_an_unopened_adapter(self):
        calls = []
        class Native:
            def __init__(self, *values):
                raise OSError("synthetic absent library")
            def close(self):
                calls.append("unexpected_close")
        with self.assertRaises(OSError):
            live.NativeOwner(NS(record=calls.append), Path("/live"), Path("/core"),
                             lambda: None, native_type=Native)
        self.assertEqual(["create"], calls)

    def test_owner_close_is_idempotent_and_forwards_only_sensor_calls(self):
        calls = []
        class Native:
            def __init__(self, *values):
                self.library = object()
            def close(self):
                calls.append("actual_close")
            def imu(self, *values):
                return "imu_result"
            def frame(self, *values):
                return "frame_result"
        owner = live.NativeOwner(NS(record=calls.append), Path("/live"), Path("/core"),
                                 lambda: None, native_type=Native)
        self.assertEqual("imu_result", owner.imu())
        self.assertEqual("frame_result", owner.frame())
        owner.close()
        owner.close()
        self.assertEqual(["create", "imu", "frame", "close", "actual_close"], calls)

    def test_live_validation_is_separate_and_offline_validator_stays_strict(self):
        from glim_target_replay import validate_environment as offline
        root, core = Path("/live"), Path("/core")
        with patch.object(live.platform, "system", return_value="Linux"), \
                patch.object(live.platform, "machine", return_value="aarch64"), \
                patch.object(os, "geteuid", return_value=1000), patch.dict(os.environ, {
                    "ROS_DISTRO": "humble", "ROS_DOMAIN_ID": "0",
                    "RMW_IMPLEMENTATION": "rmw_fastrtps_cpp",
                    "FASTRTPS_DEFAULT_PROFILES_FILE": live.DDS_PROFILE,
                    "AMENT_PREFIX_PATH": "/opt/ros/humble", "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
                    "LD_LIBRARY_PATH": "/live/lib:/core/lib:/opt/ros/humble/lib/aarch64-linux-gnu:/opt/ros/humble/lib",
                }, clear=True):
            live.validate_environment(root, core)
            with self.assertRaises(ValueError):
                offline(root, core)
            with patch.dict(os.environ, {"LD_PRELOAD": "synthetic"}):
                with self.assertRaises(ValueError):
                    live.validate_environment(root, core)

    def test_bundle_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "new_ignored"):
                prepare.prepare(NS(worker_bundle=Path(directory), output=Path(directory)))


if __name__ == "__main__":
    unittest.main()
