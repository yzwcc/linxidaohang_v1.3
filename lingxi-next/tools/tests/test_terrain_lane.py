"""Thread ownership, nonblocking reads, idle health and terminal retirement."""
from dataclasses import replace
from pathlib import Path
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'robot-navigation'), str(Path(__file__).parent)]
from lingxi_navigation.terrain_lane import TerrainLane
from lingxi_navigation.terrain_process import TerrainGrid
from test_ground import observations, spec


def wait_for(predicate, seconds=2.):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError('bounded_lane_test_timeout')
        time.sleep(.002)


class FakeProcess:
    def __init__(self, release, calls):
        self.release, self.calls, self.owner = release, calls, threading.get_ident()
        self.session, self.state, self.grid = 'a' * 32, 'new', None
        self.process = type('Child', (), dict(pid=123))()
        self.dead, self.closed = False, False

    def record(self, name):
        assert threading.get_ident() == self.owner
        self.calls.append(name)

    def start(self):
        self.record('start')
        self.state = 'ready'

    def update(self, data, *, sequence, received_at):
        self.record('update')
        assert self.release.wait(2.)
        self.grid = TerrainGrid(self.session, sequence, data.sha256, data.revision,
            'b' * 64, spec(), bytes(spec().width * spec().height), received_at, received_at + .2)
        return self.grid

    def current(self):
        self.record('current')
        if self.dead:
            self.state, self.grid = 'failed', None
        if self.grid is not None and time.monotonic() >= self.grid.expires_at:
            self.grid = None
        return self.grid

    def close(self):
        self.record('close')
        self.closed = True


class LaneTests(unittest.TestCase):
    def lane(self):
        self.release, self.calls, self.created = threading.Event(), [], []
        def factory():
            process = FakeProcess(self.release, self.calls)
            self.created.append(process)
            return process
        lane = TerrainLane(factory, poll_seconds=.005)
        self.addCleanup(lane.close)
        self.addCleanup(self.release.set)
        wait_for(lambda: lane.snapshot().state == 'ready')
        return lane

    def test_blocked_native_work_does_not_block_reader_or_allow_backlog(self):
        lane = self.lane()
        self.assertTrue(lane.submit(observations(), sequence=0, received_at=time.monotonic()))
        wait_for(lambda: 'update' in self.calls)
        start = time.monotonic()
        for _ in range(100):
            self.assertEqual('busy', lane.snapshot().state)
            self.assertIsNone(lane.snapshot().grid)
        self.assertLess(time.monotonic() - start, .1)
        self.assertFalse(lane.submit(observations(), sequence=1, received_at=time.monotonic()))
        self.release.set()
        wait_for(lambda: lane.snapshot().grid is not None)
        self.assertEqual(1, self.calls.count('update'))

    def test_retire_during_pending_result_cannot_revive(self):
        lane = self.lane()
        lane.submit(observations(), sequence=0, received_at=time.monotonic())
        wait_for(lambda: 'update' in self.calls)
        lane.retire('map_switched')
        self.release.set()
        wait_for(lambda: self.created[0].closed)
        self.assertEqual('retired', lane.snapshot().state)
        self.assertIsNone(lane.snapshot().grid)
        self.assertFalse(lane.submit(observations(), sequence=1, received_at=time.monotonic()))

    def test_idle_expiry_and_child_death_without_more_submissions(self):
        lane = self.lane()
        self.release.set()
        lane.submit(observations(), sequence=0, received_at=time.monotonic())
        wait_for(lambda: lane.snapshot().grid is not None)
        wait_for(lambda: lane.snapshot().grid is None)
        self.assertEqual('terrain_expired', lane.snapshot().failure)
        self.created[0].dead = True
        wait_for(lambda: lane.snapshot().state == 'failed')
        self.assertIsNone(lane.snapshot().grid)
        wait_for(lambda: self.created[0].closed)

    def test_invalid_or_replayed_submission_retires_source(self):
        lane = self.lane()
        self.release.set()
        lane.submit(observations(), sequence=0, received_at=time.monotonic())
        wait_for(lambda: lane.snapshot().grid is not None)
        with self.assertRaises(ValueError):
            lane.submit(observations(), sequence=0, received_at=time.monotonic())
        self.assertEqual('retired', lane.snapshot().state)
        self.assertIsNone(lane.snapshot().grid)

    def test_startup_exception_is_terminal_without_restart(self):
        calls = []
        def factory():
            calls.append(1)
            raise RuntimeError('test')
        lane = TerrainLane(factory)
        self.addCleanup(lane.close)
        wait_for(lambda: lane.snapshot().state == 'failed')
        self.assertEqual([1], calls)
        self.assertIsNone(lane.snapshot().grid)


if __name__ == '__main__':
    unittest.main()
