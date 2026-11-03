"""Product protocol/ownership tests without ROS; real ROS is an explicit TS integration."""
from dataclasses import asdict
from concurrent.futures import Future
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

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


class CallbackBudgetTests(unittest.TestCase):
    def test_timing_instrumentation_preserves_all_publisher_qos(self):
        node = SimpleNamespace(create_publisher=Mock(), create_client=Mock())
        modules = {
            'rclpy': SimpleNamespace(create_node=Mock(return_value=node)),
            'rclpy.action': SimpleNamespace(ActionClient=Mock()),
            'rclpy.qos': SimpleNamespace(QoSProfile=SimpleNamespace,
                DurabilityPolicy=SimpleNamespace(TRANSIENT_LOCAL='latched', VOLATILE='volatile'),
                ReliabilityPolicy=SimpleNamespace(RELIABLE='reliable', BEST_EFFORT='best_effort')),
            'nav_msgs.msg': SimpleNamespace(OccupancyGrid=object(), Odometry=object()),
            'nav2_msgs.action': SimpleNamespace(NavigateToPose=object()),
            'sensor_msgs.msg': SimpleNamespace(PointCloud2=object()),
            'tf2_msgs.msg': SimpleNamespace(TFMessage=object()),
            'rosgraph_msgs.msg': SimpleNamespace(Clock=object()),
            'lifecycle_msgs.srv': SimpleNamespace(GetState=SimpleNamespace(Request=object)),
            'nav2_msgs.srv': SimpleNamespace(ManageLifecycleNodes=SimpleNamespace(Request=object)),
        }
        with tempfile.TemporaryDirectory() as raw, patch.dict(sys.modules, modules), \
                patch.dict(product.os.environ, ROS_DOMAIN_ID='174'), \
                patch.object(product, 'prepare', return_value={'namespace': 'synthetic'}), \
                patch.object(product, 'HumbleTwistSource'), patch.object(product, 'LifecycleStartup'), \
                patch.object(product.subprocess, 'Popen'):
            output = Path(raw)
            (output / ('a' * 32)).mkdir()
            owner = SimpleNamespace(output=output, runtime=SimpleNamespace(command_timeout=.15))
            generation = product.Generation(owner, SimpleNamespace(epoch='a' * 32), None)
            try:
                profiles = {call.args[1]: call.args[2] for call in node.create_publisher.call_args_list}
                self.assertEqual({'traversable_map', 'odom', 'tf', 'registered_obstacles', '/clock'}, set(profiles))
                self.assertEqual(SimpleNamespace(depth=1, reliability='reliable', durability='latched'),
                    profiles['traversable_map'])
                self.assertEqual({'odom': 10, 'tf': 20, 'registered_obstacles': 10, '/clock': 10},
                    {name: profiles[name] for name in ('odom', 'tf', 'registered_obstacles', '/clock')})
            finally:
                generation.log.close()

    def test_construction_phases_preserve_their_actual_steady_time_intervals(self):
        generation = product.Generation.__new__(product.Generation)
        generation.construction_phases, generation.construction_previous = [], 2.
        with patch.object(product.time, 'monotonic', side_effect=(2.4, 2.7)):
            generation.constructed('node')
            generation.constructed('publishers')
        first, second = generation.construction_phases
        self.assertEqual(('node', 2.), (first['stage'], first['startedAt']))
        self.assertEqual(('publishers', 2.4), (second['stage'], second['startedAt']))
        self.assertAlmostEqual(.4, first['elapsedSeconds'])
        self.assertAlmostEqual(.3, second['elapsedSeconds'])

    def generation(self):
        generation = product.Generation.__new__(product.Generation)
        generation.retiring, generation.sent, generation.next_sensor = False, True, 0.
        generation.tick_phase_maximum_seconds = {}
        generation.node, generation.publish = object(), Mock()
        generation.velocity_source = SimpleNamespace(take=lambda: None)
        generation.lifecycle = SimpleNamespace(failure=None)
        generation.process = SimpleNamespace(poll=lambda: None)
        return generation

    def test_each_publisher_is_measured_without_altering_message_or_return(self):
        generation = self.generation()
        clock = [0.]
        for name, duration in (('clock', .001), ('tf', .002), ('odom', .003),
                               ('map', .335), ('cloud', .004)):
            with self.subTest(name=name):
                message, result = object(), object()
                def publish(value):
                    self.assertIs(message, value)
                    clock[0] += duration
                    return result
                publisher = Mock(publish=Mock(side_effect=publish))
                generation.publishers = {name: publisher}
                with patch.object(product.time, 'monotonic', side_effect=lambda: clock[0]):
                    self.assertIs(result, generation.publish_one(name, message))
                publisher.publish.assert_called_once_with(message)
                self.assertAlmostEqual(duration, generation.tick_phase_maximum_seconds['publish_' + name])

    def test_failed_publish_is_timed_and_not_hidden_or_retried(self):
        generation = self.generation()
        generation.publishers = {'map': Mock(publish=Mock(side_effect=RuntimeError('synthetic')))}
        with patch.object(product.time, 'monotonic', side_effect=(0., .4)), \
                self.assertRaisesRegex(RuntimeError, 'synthetic'):
            generation.publish_one('map', object())
        self.assertAlmostEqual(.4, generation.tick_phase_maximum_seconds['publish_map'])
        self.assertEqual(1, generation.publishers['map'].publish.call_count)

    def test_callback_batch_yields_at_five_milliseconds(self):
        generation = self.generation()
        clock = [0.]
        def spin(*args, **kwargs):
            clock[0] += .003
        ros = SimpleNamespace(spin_once=Mock(side_effect=spin))
        with patch.dict(sys.modules, {'rclpy': ros}), \
                patch.object(product.time, 'monotonic', side_effect=lambda: clock[0]):
            generation.tick()
        self.assertEqual(2, ros.spin_once.call_count)
        self.assertAlmostEqual(.006, generation.tick_phase_maximum_seconds['callbacks'])
        ros.spin_once.assert_called_with(generation.node, timeout_sec=0.)

    def test_instant_callbacks_still_have_a_count_bound(self):
        generation = self.generation()
        ros = SimpleNamespace(spin_once=Mock())
        with patch.dict(sys.modules, {'rclpy': ros}), patch.object(product.time, 'monotonic', return_value=0.):
            generation.tick()
        self.assertEqual(40, ros.spin_once.call_count)

    def test_retirement_inside_callback_stops_dispatch_immediately(self):
        generation = self.generation()
        ros = SimpleNamespace(spin_once=Mock(side_effect=lambda *args, **kwargs: setattr(generation, 'retiring', True)))
        with patch.dict(sys.modules, {'rclpy': ros}), patch.object(product.time, 'monotonic', return_value=0.):
            generation.tick()
        self.assertEqual(1, ros.spin_once.call_count)


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
