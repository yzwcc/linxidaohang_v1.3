"""Product protocol/ownership tests without ROS; real ROS is an explicit TS integration."""
from dataclasses import asdict
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import offline_nav2_service as product


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
