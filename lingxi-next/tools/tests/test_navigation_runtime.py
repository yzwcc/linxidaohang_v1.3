"""Lifecycle/epoch checks only. These test doubles do not execute Nav2."""
from dataclasses import replace
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
from lingxi_navigation.nav2_profile import OfflinePlatform
from lingxi_navigation.runtime import Goal, MapIdentity, OfflineNavigationRuntime, RuntimeInputs, Ticket


def platform():
    # Synthetic fixture, not a measured X2 profile.
    return OfflinePlatform(.3, .2, .1, .3, .2, .4, .1, 'a' * 64)


def identity():
    return MapIdentity('slam-' + 'a' * 32, 'b' * 64, 'c' * 64, 'd' * 64)


class Clock:
    now = 10.

    def __call__(self):
        return self.now


class Backend:
    offline_only = True

    def __init__(self):
        self.submitted, self.retiring, self.drained = [], [], set()
        self.fail_submit, self.fail_retire, self.fail_query = False, False, False

    def submit(self, ticket, goal):
        self.submitted.append((ticket, goal))
        if self.fail_submit:
            raise RuntimeError('unconfirmed_submit')

    def retire(self, ticket):
        self.retiring.append(ticket)
        if self.fail_retire:
            raise RuntimeError('unconfirmed_retire')

    def quiescent(self, ticket):
        if self.fail_query:
            raise RuntimeError('unconfirmed_query')
        return ticket in self.drained


class Observer:
    offline_only = True

    def __init__(self):
        self.values, self.fail = [], False

    def write(self, value):
        if self.fail:
            raise RuntimeError('observer_failed')
        self.values.append(value)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.backend, self.output = Clock(), Backend(), Observer()
        self.runtime = OfflineNavigationRuntime(self.backend, self.output, platform(), clock=self.clock)
        self.goal = Goal(identity(), 2., 0., 0.)
        self.sequence = 0

    def inputs(self, **changes):
        self.sequence += 1
        return replace(RuntimeInputs(identity(), self.sequence, self.clock(), True, True, True, True, True), **changes)

    def start(self):
        return self.runtime.start('task1', self.goal, self.inputs())

    def running(self):
        ticket = self.start()
        self.assertTrue(self.runtime.accepted(ticket))
        return ticket

    def velocity(self, ticket, sequence=0, **changes):
        args = dict(generated_monotonic=self.clock(), forward=.1, lateral=0., yaw=0.)
        args.update(changes)
        return self.runtime.velocity(ticket, sequence, **args)

    def drain(self, ticket):
        self.backend.drained.add(ticket)
        return self.runtime.poll()

    def test_complete_action_waits_for_retired_source_and_zero_observation(self):
        ticket = self.running()
        self.assertTrue(self.velocity(ticket))
        self.assertTrue(self.runtime.terminal(ticket, 'succeeded'))
        self.assertEqual('stopping', self.runtime.poll()['phase'])
        self.assertEqual((0., 0., 0.), (self.output.values[-1].forward,
                         self.output.values[-1].lateral, self.output.values[-1].yaw))
        self.assertEqual('finished', self.drain(ticket)['phase'])
        self.assertTrue(all(not value.physical_motion_sent for value in self.output.values))
        self.assertFalse(self.runtime.state()['navigationReady'])

    def test_requested_has_independent_planning_deadline_not_command_deadline(self):
        ticket = self.start()
        for _ in range(20):
            self.clock.now += .2
            self.runtime.update_inputs(self.inputs())
            self.assertEqual('requested', self.runtime.poll()['phase'])
        self.assertTrue(self.runtime.accepted(ticket))

    def test_planning_timeout_retires_without_accepting_late_goal(self):
        ticket = self.start()
        self.clock.now += 5.01
        self.runtime.update_inputs(self.inputs())
        self.runtime.poll()
        self.assertEqual('goal_acceptance_timeout', self.runtime.detail)
        self.assertFalse(self.runtime.accepted(ticket))
        self.assertTrue(self.runtime.requires_stop)

    def test_late_acceptance_cannot_skip_startup_deadline_without_poll(self):
        ticket = self.start()
        self.clock.now += 5.01
        self.runtime.update_inputs(self.inputs())
        self.assertFalse(self.runtime.accepted(ticket))
        self.assertEqual([ticket], self.backend.retiring)

    def test_first_command_wait_is_separate_then_strict_watchdog_applies(self):
        ticket = self.running()
        self.clock.now += .5
        self.runtime.update_inputs(self.inputs())
        self.assertEqual('running', self.runtime.poll()['phase'])
        self.assertTrue(self.velocity(ticket))
        self.clock.now += .151
        self.runtime.update_inputs(self.inputs())
        self.runtime.poll()
        self.assertEqual('controller_output_expired', self.runtime.detail)

    def test_missing_first_command_fails_without_any_motion_output(self):
        self.running()
        self.clock.now += 1.01
        self.runtime.update_inputs(self.inputs())
        self.runtime.poll()
        self.assertEqual('first_controller_output_timeout', self.runtime.detail)
        self.assertTrue(all(value.forward == 0 for value in self.output.values))

    def test_pause_is_latched_until_explicit_resume_with_a_new_epoch(self):
        old = self.running()
        self.runtime.pause()
        with self.assertRaises(RuntimeError):
            self.runtime.resume(self.inputs())
        self.assertEqual('paused', self.drain(old)['phase'])
        self.clock.now += 30
        self.runtime.update_inputs(self.inputs())
        self.assertEqual('paused', self.runtime.poll()['phase'])
        new = self.runtime.resume(self.inputs())
        self.assertEqual(old.task_id, new.task_id)
        self.assertNotEqual(old.epoch, new.epoch)
        self.assertTrue(self.runtime.accepted(new))
        self.assertFalse(self.velocity(old, 100))
        self.assertFalse(self.runtime.terminal(old, 'succeeded'))
        self.assertTrue(self.velocity(new))

    def test_failed_resume_retains_manual_pause(self):
        old = self.running()
        self.runtime.pause()
        self.drain(old)
        with self.assertRaises(ValueError):
            self.runtime.resume(self.inputs(localization_valid=False))
        self.assertEqual('paused', self.runtime.phase)
        self.assertEqual(1, len(self.backend.submitted))

    def test_stop_during_pause_retirement_supersedes_the_pause(self):
        ticket = self.running()
        self.runtime.pause()
        self.runtime.stop()
        self.assertEqual('idle', self.drain(ticket)['phase'])
        self.assertEqual([ticket], self.backend.retiring)
        with self.assertRaises(RuntimeError):
            self.runtime.resume(self.inputs())

    def test_stop_during_unconfirmed_dispatch_still_requires_retirement(self):
        self.backend.fail_submit = True
        with self.assertRaises(RuntimeError):
            self.start()
        ticket = self.runtime.ticket
        self.runtime.stop()
        with self.assertRaises(RuntimeError):
            self.start()
        self.assertEqual('idle', self.drain(ticket)['phase'])
        self.assertFalse(self.runtime.requires_stop)

    def test_retirement_timeout_is_sticky_even_if_source_later_drains(self):
        ticket = self.running()
        self.runtime.pause()
        self.clock.now += 2.01
        self.runtime.poll()
        self.assertEqual('retirement_timeout', self.runtime.detail)
        self.assertEqual('failed', self.drain(ticket)['phase'])
        with self.assertRaises(RuntimeError):
            self.start()
        self.runtime.stop()
        self.assertEqual('idle', self.runtime.phase)

    def test_cancel_ack_or_query_error_is_not_retirement_evidence(self):
        self.running()
        self.runtime.stop()
        self.backend.fail_query = True
        self.assertFalse(self.runtime.poll()['outputQuiescent'])
        self.backend.fail_query = False
        self.assertFalse(self.runtime.poll()['outputQuiescent'])

    def test_no_new_job_before_source_and_queue_are_quiescent(self):
        ticket = self.running()
        self.runtime.terminal(ticket, 'succeeded')
        with self.assertRaises(RuntimeError):
            self.start()
        self.drain(ticket)
        self.assertNotEqual(ticket, self.start())

    def test_backend_retire_exception_latches_explicit_stop(self):
        ticket = self.running()
        self.backend.fail_retire = True
        self.runtime.stop()
        self.assertEqual('retirement_unconfirmed', self.runtime.detail)
        self.assertEqual('failed', self.drain(ticket)['phase'])
        self.assertTrue(self.runtime.requires_stop)

    def test_observer_failure_does_not_skip_retirement(self):
        ticket = self.running()
        self.output.fail = True
        with self.assertRaises(RuntimeError):
            self.velocity(ticket)
        self.assertEqual([ticket], self.backend.retiring)
        self.assertTrue(self.runtime.requires_stop)
        self.assertEqual('failed', self.drain(ticket)['phase'])

    def test_zero_observer_failure_is_not_a_successful_stop(self):
        ticket = self.running()
        self.output.fail = True
        self.runtime.stop()
        self.assertEqual('zero_output_unobserved', self.runtime.detail)
        self.assertEqual('failed', self.drain(ticket)['phase'])

    def test_no_successful_arrival_before_action_acceptance(self):
        ticket = self.start()
        self.assertFalse(self.runtime.terminal(ticket, 'succeeded'))
        self.assertEqual('arrival_inputs_invalid', self.runtime.detail)

    def test_mismatched_map_evidence_faults_before_replacing_current_inputs(self):
        self.running()
        original = self.runtime.inputs
        for key in ('map_id', 'receipt_sha256', 'metric_evidence_sha256', 'traversability_sha256'):
            value = 'slam-' + 'e' * 32 if key == 'map_id' else 'e' * 64
            self.assertFalse(self.runtime.update_inputs(self.inputs(identity=replace(identity(), **{key: value}))))
            self.assertIs(original, self.runtime.inputs)
        self.assertTrue(self.runtime.requires_stop)

    def test_stale_and_reordered_inputs_never_refresh_the_watchdog(self):
        self.running()
        original = self.runtime.inputs
        self.assertFalse(self.runtime.update_inputs(original))
        self.clock.now += .31
        self.assertFalse(self.runtime.update_inputs(original))
        self.runtime.poll()
        self.assertEqual('navigation_inputs_expired', self.runtime.detail)

    def test_each_failed_input_gate_faults_and_prevents_late_arrival(self):
        for key in ('localization_valid', 'transforms_current', 'costmap_current',
                    'traversable_support', 'actions_idle'):
            with self.subTest(key=key):
                self.setUp()
                ticket = self.running()
                self.runtime.update_inputs(self.inputs(**{key: False}))
                self.assertFalse(self.runtime.terminal(ticket, 'succeeded'))
                self.assertEqual('failed', self.drain(ticket)['phase'])

    def test_invalid_inputs_are_never_dispatched(self):
        for changes in ({'localization_valid': False}, {'traversable_support': False},
                        {'received_monotonic': 9.}, {'received_monotonic': 11.}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.runtime.start('task1', self.goal, self.inputs(**changes))
        self.assertEqual([], self.backend.submitted)

    def test_invalid_velocity_never_reaches_observer_and_retires_action(self):
        for changes in ({'forward': .21}, {'forward': -.01}, {'lateral': .11}, {'yaw': .31},
                        {'forward': True}, {'forward': float('nan')}, {'yaw': float('inf')},
                        {'generated_monotonic': 9.}, {'generated_monotonic': 11.}):
            with self.subTest(changes=changes):
                self.setUp()
                ticket = self.running()
                self.assertFalse(self.velocity(ticket, **changes))
                self.assertEqual([ticket], self.backend.retiring)
                self.assertTrue(all(value.forward == 0 and value.lateral == 0 and value.yaw == 0
                                    for value in self.output.values))

    def test_duplicate_or_invalid_velocity_sequence_faults_not_replays(self):
        for sequence in (0, -1, True, 2**53):
            with self.subTest(sequence=sequence):
                self.setUp()
                ticket = self.running()
                self.velocity(ticket)
                self.assertFalse(self.velocity(ticket, sequence))
                self.assertEqual(2, len(self.output.values))
                self.assertEqual(0., self.output.values[-1].forward)

    def test_controller_roundoff_is_clamped_but_real_overlimit_is_rejected(self):
        ticket = self.running()
        self.assertTrue(self.velocity(ticket, forward=.20000000000000004,
                                      lateral=-.10000000000000002, yaw=.30000000000000004))
        value = self.output.values[-1]
        self.assertEqual((.2, -.1, .3), (value.forward, value.lateral, value.yaw))
        self.assertFalse(self.velocity(ticket, 1, forward=.2 + 1e-10))
        self.assertTrue(self.runtime.requires_stop)

    def test_old_ticket_events_are_ignored_not_relabelled_to_new_job(self):
        old = self.running()
        self.runtime.stop()
        self.drain(old)
        new = self.running()
        before = len(self.output.values)
        self.assertFalse(self.runtime.accepted(old))
        self.assertFalse(self.velocity(old, 20))
        self.assertFalse(self.runtime.terminal(old, 'failed'))
        self.assertEqual(before, len(self.output.values))
        self.assertEqual(new, self.runtime.ticket)

    def test_backwards_clock_does_not_keep_velocity_alive(self):
        ticket = self.running()
        self.velocity(ticket)
        self.clock.now -= .01
        self.runtime.poll()
        self.assertTrue(self.runtime.requires_stop)

    def test_callbacks_cannot_revive_expired_controller_when_poll_is_delayed(self):
        for first_command in (False, True):
            for event in ('velocity', 'arrival'):
                with self.subTest(first_command=first_command, event=event):
                    self.setUp()
                    ticket = self.running()
                    if not first_command:
                        self.assertTrue(self.velocity(ticket))
                    self.clock.now += 1.01 if first_command else .151
                    self.runtime.update_inputs(self.inputs())
                    observed = len(self.output.values)
                    result = (self.velocity(ticket, 1) if event == 'velocity'
                              else self.runtime.terminal(ticket, 'succeeded'))
                    self.assertFalse(result)
                    self.assertTrue(self.runtime.requires_stop)
                    self.assertEqual(observed + 1, len(self.output.values))
                    self.assertEqual(0., self.output.values[-1].forward)
                    self.assertEqual([ticket], self.backend.retiring)

    def test_only_single_event_owner_may_change_state(self):
        errors = []

        def wrong_owner():
            try:
                self.runtime.stop()
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=wrong_owner)
        thread.start()
        thread.join()
        self.assertEqual(1, len(errors))
        self.assertIsInstance(errors[0], RuntimeError)
        self.assertEqual('idle', self.runtime.phase)

    def test_no_physical_backend_or_output_can_be_attached(self):
        for target in (self.backend, self.output):
            for value in (False, 1, None):
                target.offline_only = value
                with self.assertRaises(ValueError):
                    OfflineNavigationRuntime(self.backend, self.output, platform())
            target.offline_only = True

    def test_typed_identities_goals_tickets_and_timeouts(self):
        for changes in ({'receipt_sha256': 'bad'}, {'map_id': '../hall'}):
            with self.assertRaises(ValueError):
                replace(identity(), **changes)
        for changes in ({'x_metres': True}, {'yaw_radians': 4.}, {'x_metres': 501.}):
            with self.assertRaises(ValueError):
                replace(self.goal, **changes)
        for changes in ({'sequence': True}, {'actions_idle': 1}, {'received_monotonic': float('nan')}):
            with self.assertRaises(ValueError):
                self.inputs(**changes)
        with self.assertRaises(ValueError):
            Ticket('task', 'reused')
        for key in ('input_timeout', 'command_timeout', 'startup_timeout',
                    'first_command_timeout', 'retirement_timeout'):
            with self.assertRaises(ValueError):
                OfflineNavigationRuntime(self.backend, self.output, platform(), **{key: True})


if __name__ == '__main__':
    unittest.main()
