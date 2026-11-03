"""Actual Humble action/lifecycle DDS contracts, with no physical output owner.

The server speaks NavigateToPose but does not run a planner or controller. Output
receipts and sensor observations are explicit test doubles, not robot evidence.
"""
from dataclasses import asdict
import os
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-navigation"))
from lingxi_navigation.nav2_action import Nav2ActionDriver, RosNavigationBackend
from lingxi_navigation.physical_bridge import CHECKS, MapEvidence, Observation, PhysicalNavigationBridge, Retirement, Ticket


class ObservedOutput:
    """No publisher: a test must explicitly acknowledge each zero request."""
    physical_motion_sent = False

    def __init__(self, identity):
        self.identity, self.ticket, self.nonce = identity, None, None
        self.receipt, self.completed = None, None
        self.calls = []

    def poll(self):
        pass

    def ready(self, identity):
        return identity == self.identity and self.nonce is None

    def arm(self, ticket, identity):
        if not self.ready(identity):
            return False
        self.ticket, self.completed = ticket, None
        self.calls.append(("arm", ticket))
        return True

    def retire(self, ticket, nonce):
        if ticket != self.ticket:
            raise ValueError("test_output_ticket_mismatch")
        self.nonce, self.receipt = nonce, None
        self.calls.append(("zero", ticket))

    def acknowledge(self):
        self.receipt = Retirement(self.nonce, self.identity, self.ticket, time.monotonic(), True)

    def complete_retirement(self, nonce):
        self.calls.append(("complete", self.ticket))
        if self.receipt is None or self.receipt.nonce != nonce:
            return None
        self.nonce, self.completed = None, self.receipt
        return self.completed


@unittest.skipUnless(os.environ.get("LINGXI_VERIFY_PHYSICAL_ACTION_ROS") == "1", "explicit isolated Humble required")
class ActualPhysicalActionTests(unittest.TestCase):
    def setUp(self):
        if (set(os.listdir("/sys/class/net")) != {"lo"} or os.environ.get("ROS_DISTRO") != "humble"
                or os.environ.get("ROS_LOCALHOST_ONLY") != "1"
                or os.environ.get("RMW_IMPLEMENTATION") != "rmw_fastrtps_cpp"):
            raise RuntimeError("isolated_loopback_humble_test_required")
        import rclpy
        from rclpy.action import ActionServer, CancelResponse, GoalResponse
        from rclpy.context import Context
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.task import Future
        from lifecycle_msgs.srv import GetState
        from nav2_msgs.action import NavigateToPose
        self.Future, self.GoalResponse = Future, GoalResponse
        self.context = Context()
        rclpy.init(args=[], context=self.context)
        self.addCleanup(self.context.try_shutdown)
        self.node = rclpy.create_node("test_owned_physical_action", context=self.context,
            use_global_arguments=False, enable_rosout=False, start_parameter_services=False)
        self.addCleanup(self.node.destroy_node)
        self.executor = SingleThreadedExecutor(context=self.context)
        self.executor.add_node(self.node)
        self.addCleanup(lambda: self.executor.shutdown(timeout_sec=1.))
        self.handles, self.result_gates, self.cancelled = [], [], []
        self.accept_gate = None
        self.reject = False
        async def goal(request):
            if self.accept_gate is not None:
                await self.accept_gate
            return GoalResponse.REJECT if self.reject else GoalResponse.ACCEPT
        def cancel(handle):
            self.cancelled.append(handle)
            return CancelResponse.ACCEPT
        async def execute(handle):
            gate = Future(executor=self.executor)
            self.handles.append(handle)
            self.result_gates.append(gate)
            await gate
            if handle.is_cancel_requested:
                handle.canceled()
            else:
                handle.succeed()
            return NavigateToPose.Result()
        self.server = ActionServer(self.node, NavigateToPose, "navigate_to_pose", execute_callback=execute,
            goal_callback=goal, cancel_callback=cancel)
        self.addCleanup(self.server.destroy)
        def state(request, response):
            response.current_state.id = 3
            return response
        self.services = [self.node.create_service(GetState, name + "/get_state", state)
            for name in ("planner_server", "controller_server", "bt_navigator", "velocity_smoother")]
        self.driver = Nav2ActionDriver(self.node, "map")
        self.addCleanup(self.driver.close)
        self.identity = MapEvidence("slam-" + "a" * 32, "a" * 64, "b" * 64, "c" * 64)
        self.output = ObservedOutput(self.identity)
        identity = self.identity
        class Sensors:
            def observe(self):
                return Observation(identity, time.monotonic(), dict(x=0., y=0., yaw=0.), dict.fromkeys(CHECKS, True), False)
        self.backend = RosNavigationBackend(self.driver, Sensors(), self.output,
            lambda map_id, initial: identity, self.spin)
        self.until(self.driver.ready)

    def spin(self):
        self.executor.spin_once(timeout_sec=.005)

    def until(self, predicate, seconds=4., tick=None):
        deadline = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("bounded_actual_action_timeout")
            (tick or self.spin)()
            self.driver.poll()

    @staticmethod
    def ticket(character):
        return Ticket("tour", character * 32)

    def start(self, character="1"):
        ticket = self.ticket(character)
        self.backend.start(ticket, dict(x=1., y=0., yaw=0.))
        self.until(lambda: self.driver.generation.handle is not None and bool(self.handles))
        self.assertIn((ticket, "accepted"), self.driver.events())
        return ticket

    def test_cancel_ack_alone_does_not_release_output_or_action(self):
        ticket = self.start()
        nonce = "a" * 32
        self.backend.retire(ticket, nonce)
        self.until(lambda: self.driver.generation.cancel.done())
        self.output.acknowledge()
        self.assertFalse(self.driver.action_retired(ticket))
        self.assertIsNone(self.backend.retirement(nonce))
        self.assertNotIn(("complete", ticket), self.output.calls)
        self.result_gates[0].set_result(None)
        self.until(lambda: self.driver.action_retired(ticket))
        self.assertIsNotNone(self.backend.retirement(nonce))
        self.assertTrue(self.output.ready(self.identity))
        self.assertEqual([("arm", ticket), ("zero", ticket), ("complete", ticket)], self.output.calls)

    def test_late_accept_after_pause_is_cancelled_without_accepted_event(self):
        self.accept_gate = self.Future(executor=self.executor)
        old = self.ticket("1")
        self.backend.start(old, dict(x=1., y=0., yaw=0.))
        self.backend.retire(old, "a" * 32)
        self.accept_gate.set_result(None)
        self.until(lambda: bool(self.cancelled))
        self.assertNotIn((old, "accepted"), self.driver.events())
        self.result_gates[0].set_result(None)
        self.until(lambda: self.driver.action_retired(old))
        self.output.acknowledge()
        self.assertIsNotNone(self.backend.retirement("a" * 32))
        new = self.start("2")
        self.assertNotEqual(old, new)
        self.assertIsNone(self.backend.retirement("a" * 32))
        self.backend.retire(new, "b" * 32)
        self.until(lambda: self.driver.generation.cancel.done())
        self.result_gates[1].set_result(None)
        self.until(lambda: self.driver.action_retired(new))

    def test_explicit_bridge_start_pause_resume_fences_old_terminal_callbacks(self):
        bridge = PhysicalNavigationBridge(self.backend, self.identity)
        number = 0
        def command(action):
            nonlocal number
            number += 1
            value = dict(id=f"{number:032x}", action=action, session=bridge.session,
                expectedEpoch=bridge.ticket.epoch if bridge.ticket else None, taskId="tour",
                mapId=self.identity.mapId, evidence=asdict(self.identity))
            if action == "start":
                value.update(pointId="point", goal=dict(x=1., y=0., yaw=0.))
            elif bridge.point_id is not None:
                value["pointId"] = bridge.point_id
            return bridge.request(value)
        command("stop")
        self.output.acknowledge()
        self.until(lambda: bridge.phase == "idle", tick=bridge.tick)
        command("start")
        self.until(lambda: bridge.phase == "running" and len(self.handles) == 1, tick=bridge.tick)
        old_generation, old_ticket = self.driver.generation, bridge.ticket
        command("pause")
        self.until(lambda: bool(self.cancelled), tick=bridge.tick)
        self.result_gates[0].set_result(None)
        self.output.acknowledge()
        self.until(lambda: bridge.phase == "paused", tick=bridge.tick)
        self.assertEqual(old_ticket, bridge.ticket)
        self.assertTrue(bridge.output_quiescent)
        command("resume")
        self.until(lambda: bridge.phase == "running" and len(self.handles) == 2, tick=bridge.tick)
        new_ticket = bridge.ticket
        self.assertNotEqual(old_ticket.epoch, new_ticket.epoch)
        self.driver._finished(old_generation, old_generation.result)
        bridge.tick()
        self.assertEqual(new_ticket, bridge.ticket)
        self.assertEqual("running", bridge.phase)
        command("stop")
        self.until(lambda: len(self.cancelled) == 2, tick=bridge.tick)
        self.result_gates[1].set_result(None)
        self.output.acknowledge()
        self.until(lambda: bridge.phase == "idle", tick=bridge.tick)
        self.assertFalse(bridge.requires_stop)
        self.assertFalse(bridge.physical_motion_sent)

    def test_rejected_goal_and_expired_lifecycle_cannot_start_another_generation(self):
        self.reject = True
        ticket = self.ticket("1")
        self.backend.start(ticket, dict(x=1., y=0., yaw=0.))
        self.until(lambda: self.driver.action_retired(ticket))
        self.assertEqual(((ticket, "failed"),), self.driver.events())
        self.backend.retire(ticket, "a" * 32)
        self.output.acknowledge()
        self.assertIsNotNone(self.backend.retirement("a" * 32))
        time.sleep(.65)
        self.assertFalse(self.driver.ready())
        with self.assertRaisesRegex(RuntimeError, "output_generation_not_confirmed"):
            self.backend.start(self.ticket("2"), dict(x=1., y=0., yaw=0.))


if __name__ == "__main__":
    unittest.main()
