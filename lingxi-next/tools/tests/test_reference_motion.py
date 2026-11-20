from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import Mock, patch
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
        gate.settings = dict(max_forward_velocity=.25, max_angular_velocity=.8)
        gate.input_name = "test-source"
        gate.local_costmap_stamp = time.time()
        gate.idle_sources = {""}
        gate.motion_task = MotionTask("task", "point", "map", dict(x=0, y=0, yaw=0), mode, phase="running")
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


if __name__ == "__main__":
    unittest.main()
