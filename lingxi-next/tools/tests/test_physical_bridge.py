"""Offline contracts for the physical navigation admission and retirement bridge."""
from dataclasses import asdict
import os
from pathlib import Path
import select
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-navigation"))

from lingxi_navigation.physical_bridge import (
    CHECKS,
    BridgeError,
    MapEvidence,
    Observation,
    PhysicalNavigationBridge,
    Retirement,
)
from lingxi_navigation.physical_socket import PhysicalNavigationSocket


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def evidence(seed="a"):
    return MapEvidence("slam-" + seed * 32, "a" * 64, "b" * 64, "c" * 64)


class FakeBackend:
    def __init__(self, clock, identity, *, auto_accept=True):
        self.clock = clock
        self.identity = identity
        self.auto_accept = auto_accept
        self.checks = dict.fromkeys(CHECKS, True)
        self.pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        self.events_queue = []
        self.retirement_available = True
        self.retire_calls = []
        self.start_calls = []
        self.tick_calls = 0

    def tick(self):
        self.tick_calls += 1

    def observe(self):
        return Observation(self.identity, self.clock(), self.pose, self.checks, False)

    def events(self):
        events, self.events_queue = tuple(self.events_queue), []
        return events

    def start(self, ticket, goal):
        self.start_calls.append((ticket, goal))
        if self.auto_accept:
            self.events_queue.append((ticket, "accepted"))

    def retire(self, ticket, nonce):
        self.retire_calls.append((ticket, nonce))

    def retirement(self, nonce):
        if not self.retirement_available or not self.retire_calls:
            return None
        return Retirement(nonce, self.identity, self.retire_calls[-1][0], self.clock(), True)

    def activate_map(self, map_id, initial_pose):
        if map_id == self.identity.mapId:
            return self.identity
        self.identity = MapEvidence(map_id, "d" * 64, "e" * 64, "f" * 64)
        return self.identity


def request_id(number):
    return f"{number:032x}"


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.identity = evidence()
        self.backend = FakeBackend(self.clock, self.identity)
        self.bridge = PhysicalNavigationBridge(
            self.backend,
            self.identity,
            clock=self.clock,
            input_timeout=.3,
            acceptance_timeout=3.0,
            retirement_timeout=1.0,
        )

    def command(self, number, action, *, task="tour", expected_epoch=None, goal=None):
        value = {
            "id": request_id(number),
            "action": action,
            "session": self.bridge.session,
            "expectedEpoch": expected_epoch,
            "taskId": task,
            "mapId": self.identity.mapId,
            "evidence": asdict(self.identity),
        }
        if action == "start":
            value.update(pointId="point-a", goal=goal or {"x": 1.0, "y": 0.0, "yaw": 0.0})
        elif self.bridge.point_id is not None:
            value["pointId"] = self.bridge.point_id
        return value

    def recover_initial_stop(self):
        result = self.bridge.request(self.command(1, "stop", task="startup"))
        self.assertIsNone(result)
        state = self.bridge.state()
        self.assertEqual("idle", state["phase"])
        self.assertFalse(state["requiresStop"])
        self.assertTrue(state["outputQuiescent"])
        return state

    def test_pose_evidence_uses_observation_age_not_state_poll_time(self):
        self.backend.observe = lambda: Observation(self.identity, 99.8, self.backend.pose, self.backend.checks, False)
        with patch('lingxi_navigation.physical_bridge.time.time', return_value=1000.):
            state = self.bridge.state()
        self.assertEqual(999800, state['poseObservedAtMs'])
        self.clock.advance(.2)
        self.assertIsNone(self.bridge.state(poll=False)['poseObservedAtMs'])

    def test_default_lost_and_future_pose_have_no_current_evidence(self):
        self.assertIsNone(self.bridge.state(poll=False)['poseObservedAtMs'])
        self.assertIsNotNone(self.bridge.state()['poseObservedAtMs'])
        self.backend.observe = lambda: None
        self.assertIsNone(self.bridge.state()['poseObservedAtMs'])
        self.backend.observe = lambda: Observation(self.identity, self.clock() + .1, self.backend.pose, self.backend.checks, False)
        self.assertIsNone(self.bridge.state()['poseObservedAtMs'])

    def test_pose_evidence_requires_localization_and_tf_not_terrain(self):
        self.backend.checks['terrain'] = False
        self.assertIsNotNone(self.bridge.state()['poseObservedAtMs'])
        for gate in ('localization', 'tf'):
            self.backend.checks[gate] = False
            self.assertIsNone(self.bridge.state()['poseObservedAtMs'])
            self.backend.checks[gate] = True

    def start_running(self, number=10):
        state = self.recover_initial_stop()
        self.bridge.request(self.command(number, "start", expected_epoch=state["ticket"]))
        running = self.bridge.state()
        self.assertEqual("running", running["phase"])
        return running

    def test_startup_is_fail_closed_until_explicit_stop_is_confirmed(self):
        state = self.bridge.state()
        self.assertEqual("failed", state["phase"])
        self.assertTrue(state["requiresStop"])
        self.assertFalse(state["navigationReady"])
        self.recover_initial_stop()

    def test_start_requires_all_seven_current_checks(self):
        self.recover_initial_stop()
        self.backend.checks["terrain"] = False
        with self.assertRaisesRegex(BridgeError, "navigation_admission_not_ready"):
            self.bridge.request(self.command(2, "start"))

    def test_pause_and_resume_fence_old_epoch_and_late_events(self):
        self.start_running()
        old_ticket = self.bridge.ticket
        self.assertIsNone(self.bridge.request(self.command(11, "pause", expected_epoch=old_ticket.epoch)))
        paused = self.bridge.state()
        self.assertEqual("paused", paused["phase"])
        self.assertTrue(paused["outputQuiescent"])
        self.bridge.request(self.command(12, "resume", expected_epoch=old_ticket.epoch))
        new_ticket = self.bridge.ticket
        self.assertNotEqual(old_ticket.epoch, new_ticket.epoch)
        self.backend.events_queue.insert(0, (old_ticket, "succeeded"))
        resumed = self.bridge.state()
        self.assertEqual("running", resumed["phase"])
        self.assertEqual(new_ticket.epoch, resumed["ticket"]["epoch"])

    def test_success_requires_arrival_pose_and_then_retires_output(self):
        self.start_running()
        ticket = self.bridge.ticket
        self.backend.pose = {"x": 1.0, "y": 0.0, "yaw": 0.0}
        self.backend.events_queue.append((ticket, "succeeded"))
        finished = self.bridge.state()
        self.assertEqual("finished", finished["phase"])
        self.assertTrue(finished["outputQuiescent"])

    def test_success_far_from_goal_fails_closed(self):
        self.start_running()
        ticket = self.bridge.ticket
        self.backend.pose = {"x": 2.0, "y": 0.0, "yaw": 0.0}
        self.backend.events_queue.append((ticket, "succeeded"))
        failed = self.bridge.state()
        self.assertEqual("failed", failed["phase"])
        self.assertTrue(failed["requiresStop"])
        self.assertEqual("arrival_pose_unconfirmed", failed["detail"])

    def test_acceptance_timeout_retires_without_replaying_start(self):
        self.recover_initial_stop()
        self.backend.auto_accept = False
        self.bridge.request(self.command(20, "start"))
        self.clock.advance(3.1)
        failed = self.bridge.state()
        self.assertEqual("failed", failed["phase"])
        self.assertTrue(failed["requiresStop"])
        self.assertEqual(1, len(self.backend.start_calls))

    def test_retirement_timeout_fails_closed(self):
        self.recover_initial_stop()
        self.bridge.request(self.command(30, "start"))
        self.bridge.state()
        ticket = self.bridge.ticket
        self.backend.retirement_available = False
        self.assertIsNone(self.bridge.request(self.command(31, "stop", expected_epoch=ticket.epoch)))
        self.clock.advance(1.1)
        failed = self.bridge.state()
        self.assertEqual("failed", failed["phase"])
        self.assertEqual("output_retirement_timeout", failed["detail"])
        self.assertTrue(failed["requiresStop"])

    def test_repeated_stop_does_not_replace_retirement_nonce(self):
        self.start_running()
        ticket = self.bridge.ticket
        self.backend.retirement_available = False
        self.assertIsNone(self.bridge.request(self.command(40, "stop", expected_epoch=ticket.epoch)))
        first_nonce = self.bridge.retirement_nonce
        with self.assertRaisesRegex(BridgeError, "navigation_retirement_in_progress"):
            self.bridge.request(self.command(41, "stop", expected_epoch=ticket.epoch))
        self.assertEqual(first_nonce, self.bridge.retirement_nonce)

    def test_duplicate_id_and_stale_session_are_rejected(self):
        request = {"id": request_id(50), "action": "state"}
        self.bridge.request(request)
        with self.assertRaisesRegex(BridgeError, "navigation_duplicate_request"):
            self.bridge.request(request)
        stale = self.command(51, "start")
        stale["session"] = "0" * 32
        with self.assertRaisesRegex(BridgeError, "navigation_session_mismatch"):
            self.bridge.request(stale)

    def test_map_activation_requires_confirmed_stop_and_preserves_evidence(self):
        self.recover_initial_stop()
        new_id = "slam-" + "9" * 32
        result = self.bridge.request({
            "id": request_id(60),
            "action": "activate_map",
            "session": self.bridge.session,
            "mapId": new_id,
        })
        self.assertIsNone(result)
        self.assertTrue(self.bridge.requires_stop)
        self.assertFalse(any(self.bridge.last_checks.values()))
        self.assertIsNone(self.bridge.state(poll=False)['poseObservedAtMs'])
        self.assertEqual('stopping', self.bridge.phase)
        self.bridge.tick()
        result = self.bridge.state(poll=False)
        self.assertEqual(new_id, result["identity"]["mapId"])
        self.assertEqual(new_id, self.bridge.identity.mapId)
        self.assertEqual("idle", result["phase"])
        self.assertEqual('navigation_map_activated', result['detail'])
        self.assertIsNone(self.backend.retire_calls[-1][0])

    def test_close_keeps_closed_state_and_requires_no_new_command(self):
        self.recover_initial_stop()
        self.bridge.close()
        self.bridge.tick()
        state = self.bridge.state()
        self.assertFalse(state["navigationReady"])
        self.assertFalse(any(state["checks"].values()))
        self.assertIsNone(state['poseObservedAtMs'])
        with self.assertRaisesRegex(BridgeError, "navigation_bridge_closed"):
            self.bridge.request({"id": request_id(70), "action": "state"})


@unittest.skipUnless(
    sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() != 0,
    "private Unix socket tests require a non-root Linux runtime",
)
class SocketTests(BridgeTests):
    def drive(self, server, client, *, timeout=1.0):
        end = self.clock.value + timeout
        while self.clock.value < end:
            server.step(0)
            readable, _, _ = select.select([client], [], [], .01)
            if readable:
                return client.recv(65536).decode()
            self.clock.advance(.01)
        self.fail("socket response timeout")

    def test_stop_response_survives_pending_retirement(self):
        self.recover_initial_stop()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "private"
            directory.mkdir()
            directory.chmod(0o700)
            server = PhysicalNavigationSocket(directory / "navigation.sock", self.bridge)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(directory / "navigation.sock"))
                request = self.command(80, "stop", task="socket")
                import json
                client.sendall((json.dumps(request) + chr(10)).encode())
                response = self.drive(server, client)
                payload = json.loads(response)
                self.assertTrue(payload["ok"])
                self.assertEqual("idle", payload["value"]["phase"])
            finally:
                client.close()
                server.close()

    def test_map_selection_response_waits_for_new_map_retirement(self):
        import json
        self.recover_initial_stop()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            directory.chmod(0o700)
            server = PhysicalNavigationSocket(directory / 'navigation.sock', self.bridge)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(server.endpoint))
                new_id = 'slam-' + '9' * 32
                request = dict(id=request_id(81), action='activate_map', session=self.bridge.session, mapId=new_id)
                self.backend.retirement_available = False
                client.sendall((json.dumps(request) + '\n').encode())
                server.step(0)
                server.step(0)
                self.assertEqual('stopping', self.bridge.phase)
                self.assertFalse(select.select([client], [], [], 0)[0])
                self.backend.retirement_available = True
                payload = json.loads(self.drive(server, client))
                self.assertTrue(payload['ok'])
                self.assertEqual(new_id, payload['value']['identity']['mapId'])
                self.assertEqual('navigation_map_activated', payload['value']['detail'])
                self.assertIsNone(payload['value']['ticket'])
            finally:
                client.close()
                server.close()


if __name__ == "__main__":
    unittest.main()
