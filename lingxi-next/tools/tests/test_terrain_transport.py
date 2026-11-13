"""Private persistent native transport and expiring full-replacement feed."""
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'robot-navigation'), str(ROOT / 'tools'), str(Path(__file__).parent)]
from lingxi_navigation.ground import GroundDeclaration
from lingxi_navigation.terrain import TerrainBudget
from lingxi_navigation.terrain_feed import ExpiringTerrainFeed
from lingxi_navigation.terrain_process import TerrainGrid, TerrainProcess
from lingxi_navigation.terrain_protocol import (MAXIMUM_MESSAGE_BYTES, decode, decode_observations,
    encode_observations, frame, read_message)
from test_ground import observations, spec


class ProtocolTests(unittest.TestCase):
    def test_typed_source_and_all_raw_origins_roundtrip(self):
        data, budget = observations(), TerrainBudget()
        encoded = encode_observations(data, budget)
        wire = read_message(io.BytesIO(frame(encoded)))
        restored = decode_observations(wire, budget)
        self.assertEqual(data, restored)
        self.assertEqual(data.sha256, restored.sha256)

    def test_partial_truncated_duplicate_nan_oversized_and_nonobjects_fail(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'[]', b''):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode(raw)
        for raw in (b'', b'\x00', struct.pack('!I', 5) + b'{}', struct.pack('!I', MAXIMUM_MESSAGE_BYTES + 1)):
            with self.subTest(raw=raw), self.assertRaises((EOFError, ValueError)):
                read_message(io.BytesIO(raw))

    def test_counts_span_and_per_return_shape_fail_before_native(self):
        for change in ({'scans': []}, {'map_id': 'arbitrary'}, {'navigationReady': True}):
            value = read_message(io.BytesIO(frame(encode_observations(observations(), TerrainBudget()))))
            value.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                decode_observations(value, TerrainBudget())
        value = read_message(io.BytesIO(frame(encode_observations(observations(), TerrainBudget()))))
        value['scans'][0]['returns'][0].pop()
        with self.assertRaises(ValueError):
            decode_observations(value, TerrainBudget())
        with self.assertRaises(ValueError):
            encode_observations(observations(scans=4), TerrainBudget(maximum_rays=3))


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.
        self.projection = spec()
        self.feed = ExpiringTerrainFeed(self.projection, 'a' * 32, clock=lambda: self.now)
        self.costs = bytes([0, 254, 255]) + bytes([255]) * (self.projection.width * self.projection.height - 3)
        self.grid = TerrainGrid('a' * 32, 0, 'b' * 64, 1, 'c' * 64, self.projection, self.costs, 10., 13.)

    def test_idle_expiry_replaces_every_cell_including_old_free(self):
        self.assertEqual({-1}, set(self.feed.offline_occupancy_values()))
        self.feed.replace(self.grid)
        self.assertEqual((0, 100, -1), self.feed.offline_occupancy_values()[:3])
        self.now = 13.
        self.assertEqual({-1}, set(self.feed.offline_occupancy_values()))
        self.assertEqual('terrain_expired', self.feed.failure)

    def test_new_result_replaces_old_support_and_obstacles_not_union(self):
        self.feed.replace(self.grid)
        costs = bytes([255, 0, 254]) + self.costs[3:]
        self.feed.replace(replace(self.grid, sequence=1, map_revision=2, costs=costs))
        self.assertEqual((255, 0, 254), tuple(self.feed.sample()[:3]))
        self.feed.revoke('native_failed')
        self.assertEqual({255}, set(self.feed.sample()))

    def test_stale_wrong_epoch_geometry_session_or_sequence_revokes(self):
        for change in ({}, {'session': 'd' * 32}, {'epoch_sha256': 'e' * 64}, {'map_revision': 1},
                       {'received_at': 7., 'expires_at': 10.}):
            feed = ExpiringTerrainFeed(self.projection, self.grid.session, clock=lambda: self.now)
            feed.replace(replace(self.grid, map_revision=2))
            with self.subTest(change=change), self.assertRaises((ValueError, TimeoutError)):
                feed.replace(replace(self.grid, sequence=1 if change else 0, **change))
            self.assertEqual({255}, set(feed.sample()))

    def test_clock_regression_and_future_result_never_leave_old_grid(self):
        self.feed.replace(self.grid)
        self.now = 9.
        with self.assertRaises(ValueError):
            self.feed.sample()
        self.assertIsNone(self.feed.grid)
        self.now = 10.
        with self.assertRaises(TimeoutError):
            self.feed.replace(replace(self.grid, sequence=1, received_at=11., expires_at=14.))


@unittest.skipUnless(sys.platform == 'linux', 'Linux native process ownership required')
class ProcessFaultTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name).resolve()
        self.library = self.directory / 'placeholder.so'
        self.library.write_bytes(b'not-a-native-library')

    def service(self, **kwargs):
        return TerrainProcess(ground_library=self.library, ground_sha256='a' * 64,
            octomap_library=self.library, octomap_sha256='b' * 64,
            dependencies=dict.fromkeys(('liboctomap.so.1.9.7', 'liboctomath.so.1.9.7'), 'c' * 64),
            projection=spec(), declaration=GroundDeclaration(.6, 0.),
            stderr_path=self.directory / ('log-' + str(time.monotonic_ns())), **kwargs)

    def fake(self, service, program):
        original = subprocess.Popen
        def spawn(command, **kwargs):
            return original([sys.executable, '-B', '-c', program], **kwargs)
        with patch('lingxi_navigation.terrain_process.subprocess.Popen', side_effect=spawn):
            return service.start()

    def test_actual_child_bad_hash_never_readies(self):
        service = self.service()
        with self.assertRaises(RuntimeError):
            service.start()
        self.assertEqual('failed', service.state)
        self.assertTrue(service.cleanup_complete)
        self.assertIsNotNone(service.process.poll())
        self.assertEqual(0o600, service.log.stat().st_mode & 0o777)

    def test_child_isolates_dependencies_from_inherited_ros_loader_paths(self):
        service = self.service()
        program = ("from lingxi_navigation.terrain_protocol import *; import os,sys,time; "
                   "r=read_message(sys.stdin.buffer); "
                   "expected=os.path.join(os.path.dirname(r['configuration']['octomapLibrary']),'lib'); "
                   "assert os.environ['LD_LIBRARY_PATH']==expected; "
                   "sys.stdout.buffer.write(frame(dict(version=1,kind='ready',session=r['session']))); "
                   "sys.stdout.buffer.flush(); time.sleep(20)")
        inherited = '/opt/ros/humble/lib/aarch64-linux-gnu:/unrelated/vendor/lib'
        try:
            with patch.dict(os.environ, {'LD_LIBRARY_PATH': inherited}):
                self.fake(service, program)
                self.assertEqual('ready', service.state)
                self.assertEqual(inherited, os.environ['LD_LIBRARY_PATH'])
        finally:
            service.close()
        self.assertTrue(service.cleanup_complete)
        self.assertIsNotNone(service.process.poll())

    def test_startup_hang_partial_and_oversized_reply_are_bounded_and_reaped(self):
        for program in ('import time; time.sleep(20)',
                        "import os,time; os.write(1,b'\\0'); time.sleep(20)",
                        "import os,struct; os.write(1,struct.pack('!I',40000000))"):
            service = self.service(startup_seconds=.15)
            started = time.monotonic()
            with self.subTest(program=program), self.assertRaises((TimeoutError, ValueError, RuntimeError)):
                self.fake(service, program)
            self.assertLess(time.monotonic() - started, 2.)
            self.assertTrue(service.cleanup_complete)
            self.assertIsNotNone(service.process.poll())
            service.close()

    def test_worker_hang_with_full_input_pipe_cannot_block_owner_or_reuse_grid(self):
        service = self.service(budget=TerrainBudget(maximum_processing_seconds=.1))
        program = "from lingxi_navigation.terrain_protocol import *; import sys,time; r=read_message(sys.stdin.buffer); sys.stdout.buffer.write(frame(dict(version=1,kind='ready',session=r['session']))); sys.stdout.buffer.flush(); time.sleep(20)"
        self.fake(service, program)
        data = observations(((1., .01, 0.),) * 4096)
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            service.update(data, sequence=0, received_at=started)
        self.assertLess(time.monotonic() - started, 2.)
        self.assertIsNone(service.current())
        self.assertEqual('failed', service.state)
        self.assertTrue(service.cleanup_complete)
        with self.assertRaises(RuntimeError):
            service.start()

    def test_owner_thread_is_enforced(self):
        service, errors = self.service(), []
        def other():
            try:
                service.current()
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=other)
        thread.start()
        thread.join(2)
        self.assertEqual(['single_terrain_process_owner_required'], errors)

    def test_wrong_parent_fails_before_waiting_for_configuration(self):
        environment = {**os.environ, 'PYTHONPATH': str(ROOT / 'robot-navigation')}
        result = subprocess.run([sys.executable, '-B', '-m', 'lingxi_navigation.terrain_worker', '1'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, timeout=3.)
        self.assertNotEqual(0, result.returncode)
        self.assertIn(b'current_terrain_owner_required', result.stderr)

    def test_abrupt_owner_death_kills_worker_even_when_worker_is_not_reading(self):
        environment = {**os.environ, 'PYTHONPATH': str(ROOT / 'robot-navigation')}
        program = ('import os,sys,time; from lingxi_navigation.terrain_worker import bind_parent; '
                   'bind_parent(int(sys.argv[1])); print(os.getpid(),flush=True); time.sleep(20)')
        owner_program = ('import os,subprocess,sys,time; '
                         'subprocess.Popen([sys.executable,"-B","-c",sys.argv[1],str(os.getpid())]); '
                         'time.sleep(20)')
        owner = subprocess.Popen([sys.executable, '-B', '-c', owner_program, program],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        child_pid = None
        def running():
            try:
                return Path('/proc/' + str(child_pid) + '/stat').read_text().split(') ', 1)[1][0] != 'Z'
            except (FileNotFoundError, ProcessLookupError):
                return False
        try:
            import selectors
            with selectors.DefaultSelector() as selector:
                selector.register(owner.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(3.), 'worker startup must be bounded')
                child_pid = int(owner.stdout.readline())
            self.assertTrue(running())
            owner.kill()
            owner.wait(timeout=2.)
            deadline = time.monotonic() + 2.
            while running() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertFalse(running(), 'a blocked worker must not outlive its owner')
        finally:
            if owner.poll() is None:
                owner.kill()
            owner.wait(timeout=2.)
            if child_pid is not None and running():
                os.kill(child_pid, signal.SIGKILL)
            owner.stdout.close()
            owner.stderr.close()


@unittest.skipUnless(os.environ.get('LINGXI_TEST_GROUND_LIBRARY') and os.environ.get('LINGXI_TEST_OCTOMAP_LIBRARY'),
                     'requires actual pinned native terrain builds')
class NativeProcessTests(unittest.TestCase):
    def setUp(self):
        from terrain_nav2_fixture import acquisition, projection
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name).resolve()
        self.data, self.projection = acquisition('open'), projection()
        self.service = TerrainProcess(ground_library=Path(os.environ['LINGXI_TEST_GROUND_LIBRARY']),
            ground_sha256=os.environ['LINGXI_TEST_GROUND_SHA256'],
            octomap_library=Path(os.environ['LINGXI_TEST_OCTOMAP_LIBRARY']),
            octomap_sha256=os.environ['LINGXI_TEST_OCTOMAP_SHA256'],
            dependencies=json.loads(os.environ['LINGXI_TEST_OCTOMAP_DEPENDENCIES']),
            projection=self.projection, declaration=GroundDeclaration(.6, 0.),
            budget=TerrainBudget(maximum_rays=131072, maximum_processing_seconds=10., maximum_age_seconds=15., maximum_work_units=16000000),
            stderr_path=self.directory / 'native.log')
        self.service.start()
        self.addCleanup(self.service.close)

    def test_persistent_native_replacement_matches_direct_build_and_revokes_old_support(self):
        first = self.service.update(self.data, sequence=0, received_at=time.monotonic())
        self.assertGreater(first.costs.count(0), 0)
        pid = self.service.process.pid
        self.assertIs(first, self.service.current())
        feed = ExpiringTerrainFeed(self.projection, self.service.session)
        feed.replace(first)
        # Same native owner, a new acquisition with no ground evidence.
        scans = tuple(replace(scan, stamp=scan.stamp + 5., returns=tuple(
            ray for ray in scan.returns if ray.endpoint_metres[2] > .1))
            for scan in self.data.scans if any(ray.endpoint_metres[2] > .1 for ray in scan.returns))
        scans = tuple(replace(scan, ordinal=i) for i, scan in enumerate(scans))
        changed = replace(self.data, scans=scans, revision=2)
        second = self.service.update(changed, sequence=1, received_at=time.monotonic())
        self.assertEqual(pid, self.service.process.pid)
        self.assertNotIn(0, second.costs)
        feed.replace(second)
        self.assertNotIn(0, feed.sample())
        self.assertFalse(self.service.summary['navigationReady'])
        self.service.close()
        feed.revoke('source_closed')
        self.assertEqual({-1}, set(feed.offline_occupancy_values()))
        self.assertTrue(self.service.cleanup_complete)

    def test_same_capture_does_not_refresh_expiry_and_wrong_epoch_retires_process(self):
        received = time.monotonic()
        first = self.service.update(self.data, sequence=0, received_at=received)
        second = self.service.update(replace(self.data, revision=2), sequence=1, received_at=time.monotonic())
        self.assertEqual(first.expires_at, second.expires_at)
        self.assertEqual(first.costs, second.costs)
        with self.assertRaises(RuntimeError):
            self.service.update(replace(self.data, binding_sha256='f' * 64), sequence=2, received_at=time.monotonic())
        self.assertIsNone(self.service.current())
        self.assertTrue(self.service.cleanup_complete)

    def test_actual_native_child_death_revokes_accepted_grid(self):
        self.service.update(self.data, sequence=0, received_at=time.monotonic())
        os.kill(self.service.process.pid, signal.SIGKILL)
        self.service.process.wait(timeout=2.)
        self.assertIsNone(self.service.current())
        self.assertTrue(self.service.cleanup_complete)


if __name__ == '__main__':
    unittest.main()
