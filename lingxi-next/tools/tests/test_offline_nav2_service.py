"""Product protocol/ownership tests without ROS; real ROS is an explicit TS integration."""
from dataclasses import asdict
from concurrent.futures import Future
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import offline_nav2_service as product


class Client:
    def __init__(self):
        self.ready, self.requests = True, []

    def service_is_ready(self):
        return self.ready

    def call_async(self, request):
        future = Future()
        self.requests.append(future)
        return future


class LifecycleStartupTests(unittest.TestCase):
    def setUp(self):
        self.clients, self.manager = [Client() for _ in range(4)], Client()
        self.startup = product.LifecycleStartup(self.clients, self.manager, object, object)

    def states(self, state=1, *, index=None):
        for i, client in enumerate(self.clients):
            if index is None or i == index:
                client.requests[-1].set_result(SimpleNamespace(current_state=SimpleNamespace(id=state)))

    def discover(self):
        self.startup.poll(0.)
        self.states()
        self.startup.poll(.2)
        self.assertEqual([], self.manager.requests)
        self.states()
        self.startup.poll(.4)

    def test_two_complete_roundtrips_precede_single_official_startup(self):
        self.discover()
        self.assertEqual(1, len(self.manager.requests))
        self.assertFalse(self.startup.ready(.4))
        self.states(3)
        self.startup.poll(.6)
        self.assertFalse(self.startup.ready(.6), 'active states alone do not confirm manager startup')
        self.manager.requests[0].set_result(SimpleNamespace(success=True))
        self.startup.poll(.7)
        self.assertTrue(self.startup.ready(.7))
        self.startup.poll(1.)
        self.assertEqual(1, len(self.manager.requests))

    def test_missing_or_unresponsive_node_and_manager_never_start(self):
        self.clients[-1].ready = False
        self.startup.poll(0.)
        for now in (.2, .4, .6):
            for client in self.clients[:-1]:
                client.requests[-1].set_result(SimpleNamespace(current_state=SimpleNamespace(id=1)))
            self.startup.poll(now)
        self.assertEqual([], self.manager.requests)
        self.assertFalse(self.startup.ready(.6))
        self.clients[-1].ready = True
        self.startup.poll(1.)
        self.assertEqual([], self.manager.requests)

    def test_lost_startup_reply_is_never_replayed_or_promoted(self):
        self.discover()
        self.states(3)
        self.startup.poll(.7)
        self.startup.poll(4.9)
        self.assertFalse(self.startup.ready(4.9))
        self.assertEqual(1, len(self.manager.requests))

    def test_refused_or_failed_startup_is_terminal_without_retry(self):
        for failure in (False, RuntimeError('synthetic_service_failure')):
            with self.subTest(failure=failure):
                self.setUp()
                self.discover()
                future = self.manager.requests[0]
                if isinstance(failure, Exception):
                    future.set_exception(failure)
                else:
                    future.set_result(SimpleNamespace(success=failure))
                self.startup.poll(.6)
                self.startup.poll(2.)
                self.assertIsNotNone(self.startup.failure)
                self.assertFalse(self.startup.ready(2.))
                self.assertEqual(1, len(self.manager.requests))

    def test_malformed_state_and_service_exception_never_start(self):
        for value in (True, 0, 4, '3'):
            with self.subTest(value=value):
                self.setUp()
                self.startup.poll(0.)
                self.states(value)
                self.startup.poll(.2)
                self.assertEqual('lifecycle_state_invalid', self.startup.failure)
                self.assertEqual([], self.manager.requests)
        self.setUp()
        self.startup.poll(0.)
        self.clients[0].requests[0].set_exception(ValueError('synthetic'))
        self.startup.poll(.2)
        self.assertEqual('lifecycle_service_failed', self.startup.failure)

    def test_stale_states_or_disappeared_services_do_not_grant_readiness(self):
        self.discover()
        self.states(3)
        self.manager.requests[0].set_result(SimpleNamespace(success=True))
        self.startup.poll(.6)
        self.assertTrue(self.startup.ready(.6))
        self.assertFalse(self.startup.ready(1.3))
        self.assertFalse(self.startup.ready(.5))
        self.clients[0].ready = False
        self.assertFalse(self.startup.ready(.7))

    def test_stale_unconfigured_state_cannot_trigger_startup_when_manager_appears_late(self):
        self.manager.ready = False
        self.discover()
        self.assertEqual([], self.manager.requests)
        self.manager.ready = True
        self.startup.poll(1.2)
        self.assertEqual([], self.manager.requests)

    def test_delayed_unconfigured_reply_cannot_be_refreshed_into_startup(self):
        self.manager.ready = False
        self.discover()
        self.states()
        self.manager.ready = True
        self.startup.poll(1.2)
        self.assertEqual([], self.manager.requests)
        self.assertEqual([None] * 4, self.startup.observed)
        self.states()
        self.startup.poll(1.4)
        self.assertEqual(1, len(self.manager.requests))

    def test_delayed_active_reply_never_grants_readiness_but_fresh_queries_recover(self):
        self.discover()
        self.manager.requests[0].set_result(SimpleNamespace(success=True))
        self.states(3)
        self.startup.poll(1.2)
        self.assertFalse(self.startup.ready(1.2))
        self.assertIsNone(self.startup.failure)
        self.states(3)
        self.startup.poll(1.4)
        self.assertTrue(self.startup.ready(1.4))
        self.assertEqual(1, len(self.manager.requests), 'state queries must never replay STARTUP')

    def test_active_reply_expires_from_request_not_processing_time(self):
        self.discover()
        self.manager.requests[0].set_result(SimpleNamespace(success=True))
        self.states(3)
        self.startup.poll(.95)
        self.assertTrue(self.startup.ready(.95))
        self.assertFalse(self.startup.ready(1.05))

    def test_one_delayed_node_cannot_join_three_fresh_nodes_into_readiness(self):
        self.discover()
        self.manager.requests[0].set_result(SimpleNamespace(success=True))
        for client in self.clients[:-1]:
            client.requests[-1].set_result(SimpleNamespace(current_state=SimpleNamespace(id=3)))
        self.startup.poll(.8)
        self.states(3)
        self.startup.poll(1.1)
        self.assertFalse(self.startup.ready(1.1))
        self.assertEqual([.8, .8, .8, None], self.startup.observed)
        self.states(3)
        self.startup.poll(1.3)
        self.assertTrue(self.startup.ready(1.3))

    def test_regressed_or_invalid_poll_cannot_refresh_old_replies(self):
        for now in (.3, float('nan'), float('inf'), True):
            with self.subTest(now=now):
                self.setUp()
                self.discover()
                self.manager.requests[0].set_result(SimpleNamespace(success=True))
                self.states(3)
                self.startup.poll(now)
                self.assertEqual('lifecycle_clock_invalid', self.startup.failure)
                self.assertFalse(self.startup.ready(.7))
                self.assertEqual(1, len(self.manager.requests))


class Generation:
    def __init__(self, owner, ticket, goal):
        self.ticket, self.retiring, self.drained, self.closed = ticket, False, False, False

    def tick(self):
        pass

    def retire(self):
        self.retiring = True

    def quiescent(self):
        return self.retiring and self.drained

    def close(self):
        self.closed = self.retiring = self.drained = True


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.service = product.Service('appearing-wall', Path(self.directory.name))
        self.patcher = patch.object(product, 'Generation', Generation)
        self.patcher.start()

    def tearDown(self):
        self.service.close()
        self.patcher.stop()
        self.directory.cleanup()

    def request(self, action, **changes):
        value = dict(id='a' * 32, action=action)
        if action not in ('state', 'scene'):
            ticket = self.service.runtime.ticket
            value.update(session=self.service.session, identity=asdict(self.service.identity),
                         expectedEpoch=ticket.epoch if ticket else None, taskId='test')
        if action == 'start':
            value.update(pointId='target', goal=dict(x=2, y=0, yaw=0))
        return self.service.request({**value, **changes})

    def ready(self):
        self.service.building.result(timeout=1.)
        self.service.tick()

    def test_state_and_dynamic_point_scene_are_explicitly_synthetic(self):
        first, second = self.request('state'), self.request('state')
        self.assertGreater(second['sequence'], first['sequence'])
        self.assertTrue(first['offlineOnly'])
        self.assertFalse(first['physicalMotionSent'])
        self.assertFalse(first['navigationReady'])
        self.assertEqual([], self.request('scene')['points'])
        self.service.obstacle_activated = True
        scene = self.request('scene')
        self.assertGreater(len(scene['points']), 0)
        self.assertEqual(first['identity'], scene['identity'])

    def test_pause_and_stop_wait_for_retirement_and_resume_changes_epoch(self):
        started = self.request('start')
        self.ready()
        self.assertIsNone(self.request('pause'))
        self.assertFalse(self.request('state')['outputQuiescent'])
        self.service.generation.drained = True
        self.service.tick()
        self.assertEqual('paused', self.request('state')['phase'])
        resumed = self.request('resume')
        self.assertNotEqual(started['ticket']['epoch'], resumed['ticket']['epoch'])
        self.ready()
        self.request('stop')
        self.service.generation.drained = True
        self.service.tick()
        state = self.request('state')
        self.assertEqual('idle', state['phase'])
        self.assertTrue(state['outputQuiescent'])

    def test_stop_during_construction_does_not_acknowledge_or_run_late_generation(self):
        entered, release = threading.Event(), threading.Event()

        def delayed(*args):
            entered.set()
            if not release.wait(2.):
                raise RuntimeError('bounded_test_construction')
            return Generation(*args)

        with patch.object(product, 'Generation', delayed):
            try:
                self.request('start')
                self.assertTrue(entered.wait(1.))
                self.request('stop')
                self.service.tick()
                self.assertFalse(self.request('state')['outputQuiescent'])
            finally:
                release.set()
            self.ready()
        self.assertTrue(self.service.generation.retiring)
        self.service.generation.drained = True
        self.service.tick()
        self.assertEqual('idle', self.request('state')['phase'])
        self.assertEqual(0, self.service.command_count)

    def test_stale_commands_invalid_goals_and_unexpected_fields_never_dispatch(self):
        for changes in (dict(session='b' * 32), dict(expectedEpoch='b' * 32),
                        dict(taskId='../bad'), dict(goal=dict(x=True, y=0, yaw=0)),
                        dict(goal=dict(x=1, y=0, yaw=4)), dict(extra=True)):
            with self.subTest(changes=changes), self.assertRaises(product.RequestError):
                self.request('start', **changes)
            self.assertIsNone(self.service.building)
        self.request('start')
        epoch = self.service.runtime.ticket.epoch
        with self.assertRaises(product.RequestError):
            self.request('stop', taskId='other')
        with self.assertRaises(product.RequestError):
            self.request('pause', expectedEpoch='b' * 32)
        self.assertEqual(epoch, self.service.runtime.ticket.epoch)

    def test_shutdown_closes_completed_and_still_building_generations(self):
        self.request('start')
        value = self.service.building.result(timeout=1.)
        self.service.close()
        self.assertTrue(value.closed)


if __name__ == '__main__':
    unittest.main()
