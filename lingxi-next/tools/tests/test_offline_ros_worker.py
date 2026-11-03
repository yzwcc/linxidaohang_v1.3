"""Pure IPC contracts plus real Linux process fencing; never ROS or robot IO."""
from dataclasses import asdict
import json
from pathlib import Path
import signal
import sys
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import offline_nav2_worker as worker
import offline_nav2_service as product


class Stream:
    def __init__(self):
        self.incoming, self.outgoing = [], []
        self.blocked = False

    def setblocking(self, value):
        pass

    def send(self, raw):
        if self.blocked:
            raise BlockingIOError()
        self.outgoing.append(raw)
        return len(raw)

    def recv(self, length):
        if not self.incoming:
            raise BlockingIOError()
        return self.incoming.pop(0)


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.ticket = worker.Ticket('contract', 'a' * 32)
        self.stream = Stream()
        self.channel = worker.Channel(self.stream, self.ticket)

    def packet(self, **changes):
        value = dict(schema=1, ticket=asdict(self.ticket), sequence=1, issued=2., kind='accepted', value={})
        return json.dumps({**value, **changes}).encode()

    def test_atomic_packets_bind_ticket_sequence_and_original_time(self):
        with patch.object(worker.time, 'monotonic', return_value=2.):
            self.channel.send('accepted', {})
        self.stream.incoming.append(self.stream.outgoing[0])
        value = self.channel.receive()
        self.assertEqual(self.ticket, worker.Ticket(**value['ticket']))
        self.assertEqual(2., value['issued'])
        self.assertIsNone(self.channel.receive())

    def test_wrong_epoch_replay_bad_types_and_extra_fields_are_rejected(self):
        for changes in (dict(ticket=dict(task_id='other', epoch='b' * 32)), dict(sequence=True),
                        dict(sequence=0), dict(issued=True), dict(kind=3), dict(value=[]),
                        dict(schema=True), dict(extra=True)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.stream.incoming.append(self.packet(**changes))
                self.channel.receive()
        self.stream.incoming.extend((self.packet(), self.packet()))
        self.channel.receive()
        with self.assertRaises(ValueError):
            self.channel.receive()

    def test_oversized_duplicate_nonfinite_and_eof_packets_fail_closed(self):
        for raw in (b'x' * (worker.LIMIT + 1), b'{"schema":1,"schema":1}',
                    self.packet(issued=float('nan'))):
            self.stream.incoming.append(raw)
            with self.assertRaises(ValueError):
                self.channel.receive()
        self.stream.incoming.append(b'')
        with self.assertRaises(EOFError):
            self.channel.receive()

    def test_bounded_output_and_backpressure_never_queue_old_commands(self):
        with self.assertRaises(ValueError):
            self.channel.send('diagnostic', dict(data='x' * worker.LIMIT))
        self.stream.blocked = True
        self.assertFalse(self.channel.send('snapshot', {}, optional=True))
        self.assertEqual(0, self.channel.sent)
        with self.assertRaisesRegex(RuntimeError, 'consumer_not_draining'):
            self.channel.send('velocity', {})

    def test_partial_packet_is_not_retried_or_marked_sent(self):
        self.stream.send = Mock(return_value=1)
        with self.assertRaisesRegex(RuntimeError, 'atomic_ros_ipc'):
            self.channel.send('accepted', {})
        self.stream.send.assert_called_once()
        self.assertEqual(0, self.channel.sent)


class ConstructionTests(unittest.TestCase):
    def test_log_open_failure_closes_both_private_sockets_before_spawn(self):
        parent, child = Mock(), Mock()
        owner = NS(output=Path('unused'))
        with patch.object(worker.socket, 'socketpair', return_value=(parent, child)), \
                patch.object(worker.socket, 'SOCK_SEQPACKET', 5, create=True), \
                patch.object(Path, 'open', side_effect=OSError('log_unavailable')), \
                patch.object(worker.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(OSError, 'log_unavailable'):
                worker.WorkerGeneration(owner, worker.Ticket('contract', 'a' * 32), None)
        parent.close.assert_called_once()
        child.close.assert_called_once()
        spawn.assert_not_called()


class EventTests(unittest.TestCase):
    def test_old_cold_start_snapshots_wait_without_dispatch_but_running_expiry_fails(self):
        owner = NS(inputs_current=lambda: False, runtime=NS(accepted_at=None))
        self.assertFalse(worker.inputs_ready(owner))
        owner.inputs_current = lambda: True
        self.assertTrue(worker.inputs_ready(owner))
        owner.runtime.accepted_at = 1.
        owner.inputs_current = lambda: False
        with self.assertRaisesRegex(RuntimeError, 'parent_snapshot_expired'):
            worker.inputs_ready(owner)

    def setUp(self):
        self.generation = worker.WorkerGeneration.__new__(worker.WorkerGeneration)
        self.generation.ticket = worker.Ticket('contract', 'a' * 32)
        self.generation.retiring = False
        runtime = Mock(command_timeout=.15)
        self.generation.owner = NS(runtime=runtime, pose=[0., 0., 0.])
        self.generation.goal = NS(x_metres=1., y_metres=0., yaw_radians=0.)
        self.runtime = runtime

    def event(self, kind, value=None, issued=2., now=2.):
        self.generation.event(dict(kind=kind, value=value or {}, issued=issued), now)

    def test_velocity_keeps_source_timestamp_and_runtime_owns_admission(self):
        self.event('velocity', dict(sequence=7, generated=1.99, forward=.1, lateral=0., yaw=0.))
        self.runtime.velocity.assert_called_once_with(self.generation.ticket, 7, 1.99, .1, 0., 0.)
        self.event('accepted')
        self.runtime.accepted.assert_called_once_with(self.generation.ticket)

    def test_old_or_future_events_cannot_revive_readiness_or_velocity(self):
        for kind in ('accepted', 'velocity', 'terminal'):
            for issued in (1.8, 2.01):
                self.event(kind, issued=issued)
        self.assertEqual(6, self.runtime.reject_velocity_source.call_count)
        self.runtime.accepted.assert_not_called()
        self.runtime.velocity.assert_not_called()
        self.runtime.terminal.assert_not_called()

    def test_retiring_generation_fences_all_late_callbacks(self):
        self.generation.retiring = True
        self.event('accepted')
        self.event('source_invalid')
        self.event('terminal', dict(result='succeeded'))
        self.assertEqual([], self.runtime.mock_calls)

    def test_success_is_independently_checked_at_parent_pose(self):
        self.event('terminal', dict(result='succeeded'))
        self.runtime.terminal.assert_called_with(self.generation.ticket, 'failed')
        self.generation.owner.pose = [1., 0., 0.]
        self.event('terminal', dict(result='succeeded'))
        self.runtime.terminal.assert_called_with(self.generation.ticket, 'succeeded')

    def test_unknown_events_and_diagnostic_collisions_are_rejected(self):
        for kind, value in (('motion', {}), ('terminal', dict(result='unknown')),
                            ('diagnostic', dict(runtimeDetail='forged'))):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.event(kind, value)

    def test_escalation_uses_unchanged_generation_group_without_blocking(self):
        generation = self.generation
        generation.process, generation.retired_at, generation.escalation = NS(pid=1234), 2., 0
        with patch.object(worker.os, 'killpg', create=True) as kill, \
                patch.object(worker, 'signal', NS(SIGTERM=15, SIGKILL=9)):
            generation.escalate(2.19)
            kill.assert_not_called()
            generation.escalate(2.21)
            generation.escalate(2.41)
            generation.escalate(4.)
        self.assertEqual([(1234, 15), (1234, 9)],
                         [call.args for call in kill.call_args_list])

    @unittest.skipUnless(sys.platform.startswith('linux'), 'process group proof requires Linux')
    def test_worker_exit_or_signal_delivery_alone_cannot_ack_live_descendants(self):
        generation = self.generation
        generation.retiring, generation.retired_at, generation.escalation = True, 2., 2
        generation.process, generation.log = NS(pid=1234, poll=lambda: 0), Mock()
        with patch.object(worker.os, 'killpg'), patch.object(worker.os, 'waitpid', side_effect=ChildProcessError):
            self.assertFalse(generation.quiescent())
        generation.log.close.assert_not_called()


@unittest.skipUnless(sys.platform.startswith('linux'), 'real process groups require Linux')
class LinuxWorkerTests(unittest.TestCase):
    def run_case(self, scenario):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        service = product.Service('open', Path(temporary.name))
        self.addCleanup(service.close)
        fixture = Path(__file__).parent / 'fixtures/offline_ros_peer.py'
        def command(fd, case, output, ticket):
            return [sys.executable, '-B', str(fixture), '--fd', str(fd), '--task', ticket.task_id,
                    '--epoch', ticket.epoch, '--scenario', scenario]
        patcher = patch.object(worker, 'worker_command', side_effect=command)
        patcher.start()
        self.addCleanup(patcher.stop)
        service.runtime.start('contract', product.Goal(service.identity, .9, 0., 0.), service.inputs())
        return service

    def wait(self, service, predicate, timeout=4.):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            service.tick()
            value = service.state()
            if predicate(value):
                return value
            time.sleep(.005)
        self.fail('bounded real child observation timed out: ' + repr(service.state()))

    def test_blocked_startup_worker_does_not_block_owner_or_age_local_inputs(self):
        service = self.run_case('startup-block')
        self.wait(service, lambda state: state['phase'] == 'running')
        self.assertLess(service.maximum_loop_gap, .1)
        self.assertFalse(service.runtime.requires_stop)
        service.runtime.stop()
        final = self.wait(service, lambda state: state['outputQuiescent'])
        self.assertEqual('idle', final['phase'])

    def test_running_worker_stall_still_expires_150ms_velocity_watchdog(self):
        service = self.run_case('running-block')
        self.wait(service, lambda state: state['commandCount'] > 0)
        observed = service.runtime.last_command
        final = self.wait(service, lambda state: state['outputQuiescent'])
        self.assertEqual('failed', final['phase'])
        self.assertEqual('controller_output_expired', final['detail'])
        self.assertTrue(final['requiresStop'])
        self.assertLess(service.maximum_loop_gap, .1)
        self.assertEqual(.15, service.runtime.command_timeout)
        self.assertLess(service.generation.retired_at - observed, .25)
        self.assertEqual((0., 0., 0.), service.observer.latest)

    def test_pause_before_late_acceptance_retires_group_then_resume_gets_new_epoch(self):
        service = self.run_case('startup-block')
        service.building.result(timeout=2.)
        service.tick()
        epoch = service.runtime.ticket.epoch
        service.runtime.pause()
        self.assertFalse(service.state()['outputQuiescent'])
        paused = self.wait(service, lambda state: state['outputQuiescent'])
        self.assertEqual('paused', paused['phase'])
        self.assertEqual(0, service.command_count)
        service.runtime.resume(service.inputs())
        self.wait(service, lambda state: state['phase'] == 'running')
        self.assertNotEqual(epoch, service.runtime.ticket.epoch)
        self.assertFalse(service.runtime.requires_stop)
        self.assertEqual((.3, .15, 2., 5.), (service.runtime.input_timeout, service.runtime.command_timeout,
            service.runtime.retirement_timeout, service.runtime.startup_timeout))

    def test_unexpected_worker_exit_fails_and_still_retires_stubborn_descendants(self):
        service = self.run_case('worker-exit')
        final = self.wait(service, lambda state: state['outputQuiescent'])
        self.assertEqual('failed', final['phase'])
        self.assertEqual(0, service.command_count)
        self.assertIsNone(service.runtime.accepted_at)


if __name__ == '__main__':
    unittest.main()
