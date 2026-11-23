from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import MagicMock, Mock, patch
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "robot-runtime" / "python"))
from reference_motion import MotionTask, TrackedActionClient, gateway_type


class MotionFenceTests(unittest.TestCase):
    def gateway(self, mode):
        modules = {}
        for name, bindings in ({"aimdk_msgs.msg": {"McCommonState": object},
                                 "nav2_msgs.msg": {"Costmap": object},
                                 "rclpy.qos": {"qos_profile_sensor_data": None},
                                 "robot_nav_tasks.geometry": {"pose_payload": lambda value: value},
                                 "robot_nav_tasks.localization_timing": {"localization_timing_fresh": Mock()},
                                 "robot_nav_tasks.navigation_motion": {"measured_motion": Mock()}}).items():
            modules[name] = ModuleType(name)
            modules[name].__dict__.update(bindings)

        class Gateway:
            _publish_mc = Mock()
            _publish_point = Mock()

        with patch.dict(sys.modules, modules):
            gate_type = gateway_type(Gateway)
        gate = gate_type.__new__(gate_type)
        gate.lock = threading.RLock()
        gate.settings = dict(max_forward_velocity=.25, max_angular_velocity=.8,
                             goal_position_tolerance_m=.24, goal_yaw_tolerance_rad=.15)
        gate.input_name = "test-source"
        gate.local_costmap_stamp = time.time()
        gate.idle_sources = {""}
        gate.motion_task = MotionTask("task", "point", "map", dict(x=0, y=0, yaw=0), mode, phase="running", run_id="run")
        gate.navigation_frame = {"run_id": "run", "stamp": time.time()}
        gate.revoke = Mock(side_effect=lambda task, *_: task.cancelled.set())
        gate._motion_evidence = Mock(return_value=True)
        gate._fresh_mc = Mock(return_value=(time.time(), time.monotonic(), gate.input_name, 3))
        return gate

    def test_both_modes_revoke_when_localization_or_obstacles_expire(self):
        for mode in ("point", "nav2"):
            with self.subTest(mode=mode):
                gate = self.gateway(mode)
                if mode == "point":
                    output = lambda command: gate._publish_point(gate.motion_task, command)
                    publisher = super(type(gate), gate)._publish_point
                else:
                    output = gate._publish_mc
                    publisher = super(type(gate), gate)._publish_mc
                output((.1, 0., 0.))
                publisher.assert_called_once()
                gate._motion_evidence.return_value = False
                output((.1, 0., 0.))
                gate.revoke.assert_called_once()
                publisher.assert_called_once()
                output((0., 0., 0.))
                self.assertEqual(publisher.call_count, 2)

    def test_remote_takeover_cannot_be_reacquired_by_same_task(self):
        gate = self.gateway("point")
        self.assertTrue(gate._permit_output((.1, 0., 0.)))
        gate._fresh_mc.return_value = (time.time(), time.monotonic(), "remote", 3)
        self.assertFalse(gate._permit_output((.1, 0., 0.)))
        gate._fresh_mc.return_value = (time.time(), time.monotonic(), gate.input_name, 3)
        self.assertFalse(gate._permit_output((.1, 0., 0.)))

    def test_nav2_cancellation_ack_is_not_terminal_result(self):
        gate = self.gateway("nav2")
        submission, result = Future(), Future()
        handle = SimpleNamespace(accepted=True, get_result_async=Mock(return_value=result), cancel_goal_async=Mock())
        driver = TrackedActionClient(SimpleNamespace(send_goal_async=Mock(return_value=submission)), gate)
        driver.send_goal_async(object())
        gate.motion_task.cancelled.set()
        with self.assertRaises(InterruptedError):
            driver.send_goal_async(object())
        self.assertFalse(gate._actions_terminal(gate.motion_task))
        submission.set_result(handle)
        self.assertFalse(gate._actions_terminal(gate.motion_task))
        handle.cancel_goal_async.assert_called_once()
        result.set_result(SimpleNamespace(status=5))
        self.assertTrue(gate._actions_terminal(gate.motion_task))

    def test_new_localization_run_revokes_nonzero_output(self):
        gate = self.gateway("point")
        gate.navigation_frame["run_id"] = "replacement"
        self.assertFalse(gate._permit_output((.1, 0., 0.)))
        gate.revoke.assert_called_once()

    def stopped_gateway(self, x=0, yaw=0):
        gate = self.gateway("nav2")
        task = gate.motion_task
        task.phase, task.result = "stopping", "finished"
        task.stop_stamp = gate.navigation_frame["stamp"] - .1
        gate.map_from_odom = type("Alignment", (), {"__matmul__": lambda *_: dict(x=x, y=0, yaw=yaw)})()
        gate.nav_base_pose = object()
        gate._location_ready = Mock(return_value=True)
        gate.motion_record = MagicMock()
        return gate, task

    def test_controller_success_is_not_arrival_outside_original_goal(self):
        gate, task = self.stopped_gateway(x=.251)
        gate._complete_retirement(task, gate._fresh_mc())
        self.assertEqual(task.phase, "failed")
        self.assertIn("0.251 m", task.detail)
        gate.motion_record.unlink.assert_called_once()

    def test_arrival_uses_shortest_yaw_error_and_checks_heading(self):
        gate, task = self.stopped_gateway(x=.1, yaw=-3.13)
        task.goal["yaw"] = 3.13
        gate._complete_retirement(task, gate._fresh_mc())
        self.assertEqual(task.phase, "finished")
        gate, task = self.stopped_gateway(yaw=.3)
        gate._complete_retirement(task, gate._fresh_mc())
        self.assertEqual(task.phase, "failed")
        self.assertIn("0.300 rad", task.detail)

    def test_does_not_confirm_arrival_without_current_post_stop_localization_and_control(self):
        for missing_evidence in ("lost", "stale", "old_stamp", "new_run", "takeover", "nonfinite"):
            with self.subTest(missing_evidence=missing_evidence):
                gate, task = self.stopped_gateway(x=float("nan") if missing_evidence == "nonfinite" else 0)
                mc = gate._fresh_mc()
                if missing_evidence == "lost":
                    gate.navigation_frame = None
                elif missing_evidence == "stale":
                    gate._location_ready.return_value = False
                elif missing_evidence == "old_stamp":
                    gate.navigation_frame["stamp"] = task.stop_stamp
                elif missing_evidence == "new_run":
                    gate.navigation_frame["run_id"] = "replacement"
                elif missing_evidence == "takeover":
                    mc = (mc[0], mc[1], "remote", mc[3])
                gate._complete_retirement(task, mc)
                self.assertEqual(task.phase, "failed")

    def test_late_controller_success_cannot_override_a_retiring_task(self):
        gate, task = self.stopped_gateway()
        gate.motion_task = None
        gate.motion_session = "test-session"
        gate.ready = Mock(return_value=True)
        gate._navigation_busy = Mock(return_value=False)
        task.phase = "requested"
        callbacks = []
        gate.navigate = lambda _goal, callback, _cancelled: callbacks.append(callback)
        with patch("reference_motion.os.fsync"):
            gate.submit(task)
        self.assertTrue(task.dispatched.wait(1))
        task.phase, task.result = "stopping", "idle"
        callbacks[0]("succeeded", {})
        gate.revoke.assert_not_called()
        gate._complete_retirement(task, gate._fresh_mc())
        self.assertEqual(task.phase, "idle")


if __name__ == "__main__":
    unittest.main()
