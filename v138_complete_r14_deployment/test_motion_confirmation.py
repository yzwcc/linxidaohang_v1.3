"""Simulate the production confirmation methods without ROS or physical motion."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace as NS
import threading
import unittest


class MotionConfirmationTests(unittest.TestCase):
    def setUp(self):
        default = Path(__file__).parent / "candidate/source/navigation/robot_local_navigation_v3/sdk_motion_controller.py"
        path = Path(os.environ.get("SDK_CONTROLLER", default))
        tree = ast.parse(path.read_bytes())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SdkMotionController")
        names = {"play_preset_motion_and_wait", "_handle_motion_result", "_response_message", "_is_stability_rejection"}
        cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
        markers = next(n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "STABILITY_REJECTION_MARKERS" for t in n.targets))
        program = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), markers, cls], type_ignores=[])
        env = {"CommonState": NS(SUCCESS=1, RUNNING=400, CREATED=200, PENDING=300, NOT_READY=0),
               "threading": threading, "DEFAULT_MOTION_CONFIRMATION_TIMEOUT_SEC": 6,
               "STABLE_STAND_ACTION": "STAND_DEFAULT",
               "is_request_acknowledged": lambda code, state, accepted: int(code) == 0 if code is not None else state in accepted}
        exec(compile(ast.fix_missing_locations(program), str(path), "exec"), env)
        self.controller = env["SdkMotionController"]()
        self.events = []
        self.controller.node = NS(get_logger=lambda: NS(info=lambda *_: None, warning=lambda *_: None))
        self.controller._notify_motion_result = lambda callback, accepted, message: callback(accepted, message)
        self.controller._request_stable_stand = lambda *a: self.events.append("recover")
        self.controller._schedule_motion_retry = lambda *a, **k: self.events.append("retry")

    def test_lost_reply_is_unknown_and_never_resubmitted(self):
        def fail(): raise OSError("reply lost after command submission")
        def submit(*args, on_result, cancel_event, **kwargs):
            self.events.append("submit")
            future = NS(result=fail)
            self.controller._handle_motion_result(future, NS(motion_id=1003, area_id=2), 1, on_result, cancel_event)
            return future
        self.controller.play_preset_motion = submit
        result = self.controller.play_preset_motion_and_wait("handshake")
        self.assertEqual(result["status"], "confirmation_unknown")
        self.assertFalse(result["accepted"])
        self.assertEqual(self.events, ["submit"])

    def test_timeout_is_unknown_and_cancels_late_retries(self):
        captured = []
        self.controller.play_preset_motion = lambda *a, cancel_event, **k: captured.append(cancel_event) or object()
        result = self.controller.play_preset_motion_and_wait("handshake", timeout_sec=0.1)
        self.assertEqual(result["status"], "confirmation_timeout")
        self.assertTrue(captured[0].is_set())
        self.assertEqual(len(captured), 1)

    def test_ack_zero_does_not_trigger_false_stand_recovery(self):
        def submit(*args, on_result, cancel_event, **kwargs):
            reply = NS(response=NS(state=NS(value=0), header=NS(code=0)))
            future = NS(result=lambda: reply)
            self.controller._handle_motion_result(future, NS(motion_id=1003, area_id=2), 1, on_result, cancel_event)
            return future
        self.controller.play_preset_motion = submit
        result = self.controller.play_preset_motion_and_wait("handshake")
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["accepted"])
        self.assertEqual(self.events, [])

    def test_explicit_rejection_remains_rejected(self):
        def submit(*args, on_result, **kwargs):
            on_result(False, "permission denied")
            return object()
        self.controller.play_preset_motion = submit
        result = self.controller.play_preset_motion_and_wait("handshake")
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["accepted"])


if __name__ == "__main__":
    unittest.main()
