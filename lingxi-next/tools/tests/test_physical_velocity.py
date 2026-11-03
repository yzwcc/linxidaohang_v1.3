"""Physical output contracts; the AIMDK publisher is an in-memory test double."""
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
import test_physical_ros as fixtures
from lingxi_navigation.nav2_action import Nav2ActionDriver, RosNavigationBackend
from lingxi_navigation.physical_bridge import CHECKS, MapEvidence, Observation, Ticket
from lingxi_navigation.physical_ros import AimdkVelocityOutput
from lingxi_navigation.physical_velocity import (
    ControllerCommand, ControllerVelocityChannel, PhysicalTwistSource, VelocityLimits, controller_namespace,
)


class FakeReader:
    def __init__(self, namespace, **kwargs):
        self.namespace, self.samples, self.closed = namespace, [], False
        self.publishers = [NS(node_name='velocity_smoother', node_namespace=namespace,
            topic_type='geometry_msgs/msg/Twist', endpoint_gid=list(range(24)))]
        self.node = NS(get_publishers_info_by_topic=lambda topic: self.publishers)

    def take(self):
        return self.samples.pop(0) if self.samples else None

    def close(self):
        self.closed = True


class PhysicalVelocityTests(unittest.TestCase):
    def setUp(self):
        self.identity = MapEvidence('slam-' + 'a' * 32, 'a' * 64, 'b' * 64, 'c' * 64)
        self.ticket = Ticket('tour', '1' * 32)
        self.limits = VelocityLimits(self.identity, .3, .1, .5, .5, .5, 1.)
        old, _, self.messages, _, Node = fixtures.PhysicalRosTests()._aimdk_fake_modules()
        def restore():
            for key, value in old.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value
        self.addCleanup(restore)
        self.clock, self.node = fixtures.Clock(), Node()
        self.output = AimdkVelocityOutput(self.node, self.identity, clock=self.clock)
        self.addCleanup(self.output.close)
        for _ in range(4):
            self.output.poll()
        self.refresh()
        self.observation = lambda: Observation(self.identity, self.clock(), dict(x=0., y=0., yaw=0.),
                                               dict.fromkeys(CHECKS, True), False)

    def refresh(self):
        message = self.messages.McCommonState()
        message.input_source.name, message.fsm_state.current_state = 'lingxi-next', 2
        self.output._state_message(message)

    def arm(self):
        self.assertTrue(self.output.arm(self.ticket, self.identity))

    def command(self, *, speed=.02, sequence=1, **changes):
        value = ControllerCommand(self.ticket, self.identity, controller_namespace(self.ticket), sequence,
                                  self.clock(), speed, 0., 0.)
        return replace(value, **changes)

    def advance(self, seconds=.1):
        self.clock.value += seconds
        self.refresh()

    def assert_zero(self):
        last = self.node.publisher.messages[-1]
        self.assertEqual((0., 0., 0.), (last.forward_velocity, last.lateral_velocity, last.angular_velocity))

    def channel(self):
        context = patch('lingxi_navigation.velocity_source.HumbleTwistSource', FakeReader)
        context.start()
        self.addCleanup(context.stop)
        channel = ControllerVelocityChannel(self.output, self.limits, clock=self.clock)
        self.addCleanup(channel.retire)
        channel.arm(self.ticket, self.identity, controller_namespace(self.ticket))
        return channel

    def test_explicit_limits_and_immutable_command_identity(self):
        for name, value in (('forward', True), ('forward', 0.), ('lateral', -.1), ('angular', 1.1),
                            ('command_age', .2), ('acceleration', float('nan')), ('deceleration', 0.)):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                replace(self.limits, **{name: value})
        for changes in ({'namespace': '/cmd_vel'}, {'sequence': True}, {'generated_at': float('inf')},
                        {'forward': True}, {'sequence': 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.command(**changes)

    def test_normal_planar_output_and_nonzero_attempt_receipt(self):
        self.arm()
        self.advance()
        self.assertTrue(self.output.command(self.command(), self.limits, self.observation()))
        self.assertEqual(.02, self.node.publisher.messages[-1].forward_velocity)
        self.assertTrue(self.output.physical_motion_sent)

    def test_old_epoch_and_retired_callbacks_are_inert(self):
        self.arm()
        self.advance()
        old = self.command(ticket=Ticket('tour', '2' * 32), namespace='/lingxi_nav_' + '2' * 32)
        self.assertFalse(self.output.command(old, self.limits, self.observation()))
        self.assertEqual([], self.node.publisher.messages)
        self.output.retire(self.ticket, 'a' * 32)
        size = len(self.node.publisher.messages)
        self.assertFalse(self.output.command(self.command(), self.limits, self.observation()))
        self.assertEqual(size, len(self.node.publisher.messages))

    def test_reverse_out_of_range_and_acceleration_violation_fence_before_zero(self):
        for speed in (-.001, .31, .1):
            with self.subTest(speed=speed):
                self.setUp()
                self.arm()
                self.advance()
                with self.assertRaisesRegex(ValueError, 'out_of_bounds'):
                    self.output.command(self.command(speed=speed), self.limits, self.observation())
                self.assertFalse(self.output.armed)
                self.assertTrue(self.output.controller_fault)
                self.assert_zero()

    def test_sensor_and_map_evidence_are_checked_at_publication(self):
        for kind in ('expired', 'future', 'terrain', 'map', 'state', 'source'):
            with self.subTest(kind=kind):
                self.setUp()
                self.arm()
                self.advance()
                observation = self.observation()
                if kind == 'expired':
                    observation = replace(observation, observed_at=self.clock() - .301)
                elif kind == 'future':
                    observation = replace(observation, observed_at=self.clock() + .01)
                elif kind == 'terrain':
                    observation = replace(observation, checks=dict(observation.checks, terrain=False))
                elif kind == 'map':
                    observation = replace(observation, identity=replace(self.identity, calibrationSha256='f' * 64))
                elif kind == 'state':
                    self.clock.value += .301
                else:
                    foreign = self.messages.McCommonState()
                    foreign.input_source.name, foreign.fsm_state.current_state = 'remote-control', 2
                    self.output._state_message(foreign)
                with self.assertRaisesRegex(ValueError, 'controller_evidence'):
                    self.output.command(self.command(), self.limits, observation)
                self.assert_zero()
                self.assertFalse(self.output.ready(self.identity))

    def test_stale_future_prearm_replay_and_delayed_owner_cannot_revive(self):
        for kind in ('stale', 'future', 'prearm', 'sequence', 'timestamp', 'watchdog'):
            with self.subTest(kind=kind):
                self.setUp()
                self.arm()
                self.advance()
                first = self.command()
                self.output.command(first, self.limits, self.observation())
                self.advance(.2 if kind == 'watchdog' else .05)
                changes = dict(sequence=2)
                if kind == 'stale':
                    changes['generated_at'] = self.clock() - .151
                elif kind == 'future':
                    changes['generated_at'] = self.clock() + .01
                elif kind == 'prearm':
                    changes['generated_at'] = 9.99
                elif kind == 'sequence':
                    changes['sequence'] = 1
                elif kind == 'timestamp':
                    changes['generated_at'] = first.generated_at
                with self.assertRaisesRegex(ValueError, 'controller_evidence'):
                    self.output.command(self.command(**changes), self.limits, self.observation())
                self.assert_zero()

    def test_publisher_failure_fences_and_records_attempt_without_retrying_motion(self):
        self.arm()
        self.advance()
        publisher = self.node.publisher.publish
        def fail_nonzero(message):
            if message.forward_velocity:
                raise OSError('fake_output_failed')
            publisher(message)
        self.node.publisher.publish = fail_nonzero
        with self.assertRaisesRegex(OSError, 'fake_output_failed'):
            self.output.command(self.command(), self.limits, self.observation())
        self.assertTrue(self.output.physical_motion_sent)
        self.assertFalse(self.output.armed)
        self.assert_zero()

    def test_only_explicit_confirmed_retirement_releases_fault(self):
        self.arm()
        self.output.fence_controller()
        self.assertFalse(self.output.arm(self.ticket, self.identity))
        self.output.retire(self.ticket, 'a' * 32)
        self.advance()
        self.assertIsNotNone(self.output.complete_retirement('a' * 32))
        self.assertFalse(self.output.controller_fault)
        self.assertTrue(self.output.arm(Ticket('tour', '2' * 32), self.identity))

    def test_source_binds_ticket_map_namespace_and_rejects_nonplanar_twist(self):
        channel = self.channel()
        self.advance()
        source = channel.source
        message = NS(linear=NS(x=.02, y=0., z=0.), angular=NS(x=0., y=0., z=0.))
        source.reader.samples.append((message, self.clock()))
        command = source.take()
        self.assertEqual(self.ticket, command.ticket)
        self.assertEqual(self.identity, command.identity)
        self.assertEqual(controller_namespace(self.ticket), command.namespace)
        self.assertEqual(bytes(range(24)), source.publisher_gid)
        message.linear.z = .01
        source.reader.samples.append((message, self.clock()))
        with self.assertRaisesRegex(ValueError, 'planar'):
            source.take()

    def test_shared_namespace_epoch_reuse_and_publisher_replacement_rejected(self):
        channel = self.channel()
        source = channel.source
        source.take()
        source.reader.publishers[0].endpoint_gid = [1] * 24
        with self.assertRaisesRegex(ValueError, 'stable_controller'):
            source.take()
        channel.retire()
        self.output.retire(self.ticket, 'a' * 32)
        self.advance()
        self.output.complete_retirement('a' * 32)
        with self.assertRaisesRegex(RuntimeError, 'fresh_generation'):
            channel.arm(self.ticket, self.identity, controller_namespace(self.ticket))
        with self.assertRaisesRegex(RuntimeError, 'fresh_generation'):
            channel.arm(Ticket('tour', '2' * 32), self.identity, '/')

    def test_multiple_publishers_wrong_node_and_wrong_namespace_rejected(self):
        channel = self.channel()
        source = channel.source
        original = source.reader.publishers[0]
        for publishers in ([original, original], [NS(**dict(vars(original), node_name='controller_server'))],
                           [NS(**dict(vars(original), node_namespace='/'))]):
            source.reader.publishers = publishers
            with self.assertRaisesRegex(ValueError, 'exclusive_generation'):
                source.take()

    def test_first_command_and_stream_watchdog_fence_before_late_sample(self):
        for first in (True, False):
            with self.subTest(first=first):
                self.setUp()
                channel = self.channel()
                generation = NS(ticket=self.ticket, accepted_at=self.clock(), retiring=False, terminal=False)
                if not first:
                    self.advance(.05)
                    message = NS(linear=NS(x=.01, y=0., z=0.), angular=NS(x=0., y=0., z=0.))
                    channel.source.reader.samples.append((message, self.clock()))
                    channel.poll(generation, self.observation())
                self.advance(1.01 if first else .151)
                with self.assertRaisesRegex(ValueError, 'output_expired'):
                    channel.poll(generation, self.observation())
                self.assert_zero()
                self.assertFalse(self.output.armed)

    def test_requested_terminal_and_retiring_actions_never_consume_velocity(self):
        channel = self.channel()
        source = channel.source
        for accepted, retiring, terminal in ((None, False, False), (10., True, False), (10., False, True)):
            channel.poll(NS(ticket=self.ticket, accepted_at=accepted, retiring=retiring, terminal=terminal),
                         self.observation())
        self.assertEqual(0, source.sequence)
        self.assertEqual([], self.node.publisher.messages)
        channel.retire()
        self.assertTrue(source.quiescent)
        self.assertIsNone(source.take())

    def test_failed_source_close_does_not_grant_retirement(self):
        channel = self.channel()
        source = channel.source
        with patch.object(source.reader, 'close', side_effect=RuntimeError('close_failed')):
            with self.assertRaisesRegex(RuntimeError, 'close_failed'):
                channel.retire()
        self.assertFalse(channel.quiescent())
        self.assertFalse(source.quiescent)
        channel.retire()
        self.assertTrue(channel.quiescent())

    def test_real_output_without_controller_never_reports_navigation_ready(self):
        driver = object.__new__(Nav2ActionDriver)
        driver.ready = lambda: True
        sensors = NS(observe=self.observation)
        backend = RosNavigationBackend(driver, sensors, self.output, lambda *_: self.identity, lambda: None)
        self.assertFalse(backend.observe().checks['velocityOutput'])
        with self.assertRaisesRegex(RuntimeError, 'exclusive_output'):
            driver.generation = None
            backend.start(self.ticket, dict(x=1., y=0., yaw=0.))


if __name__ == '__main__':
    unittest.main()
