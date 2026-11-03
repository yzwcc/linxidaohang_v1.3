"""Actual isolated Humble DDS, with in-memory AIMDK output and sensor evidence.

These tests have no robot, vendor publisher, Nav2 controller, or physical IO.
"""
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
import test_physical_ros as fixtures
from lingxi_navigation.physical_bridge import CHECKS, MapEvidence, Observation, Ticket
from lingxi_navigation.physical_ros import AimdkVelocityOutput
from lingxi_navigation.physical_velocity import (
    ControllerVelocityChannel, PhysicalTwistSource, VelocityLimits, controller_namespace,
)
from lingxi_navigation.velocity_source import SourceTimeError


@unittest.skipUnless(os.environ.get('LINGXI_VERIFY_PHYSICAL_VELOCITY_ROS') == '1',
                     'explicit isolated Humble required')
class ActualPhysicalVelocityTests(unittest.TestCase):
    def setUp(self):
        if (set(os.listdir('/sys/class/net')) != {'lo'} or os.environ.get('ROS_DISTRO') != 'humble'
                or os.environ.get('ROS_LOCALHOST_ONLY') != '1'
                or os.environ.get('RMW_IMPLEMENTATION') != 'rmw_fastrtps_cpp'):
            raise RuntimeError('isolated_loopback_humble_test_required')
        import rclpy
        from rclpy.context import Context
        from geometry_msgs.msg import Twist
        self.rclpy, self.Twist = rclpy, Twist
        self.context = Context()
        rclpy.init(args=[], context=self.context)
        self.addCleanup(self.context.try_shutdown)
        self.ticket = Ticket('tour', '1' * 32)
        self.identity = MapEvidence('slam-' + 'a' * 32, 'a' * 64, 'b' * 64, 'c' * 64)
        self.limits = VelocityLimits(self.identity, .3, .1, .5, .5, .5, 1.)
        self.producer = rclpy.create_node('velocity_smoother', namespace=controller_namespace(self.ticket),
            context=self.context, enable_rosout=False, start_parameter_services=False)
        self.addCleanup(self.producer.destroy_node)
        self.publisher = self.producer.create_publisher(Twist, 'observed_cmd_vel', 1)
        old, _, self.messages, _, Node = fixtures.PhysicalRosTests()._aimdk_fake_modules()
        def restore():
            for key, value in old.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value
        self.addCleanup(restore)
        self.output_node = Node()
        self.output = AimdkVelocityOutput(self.output_node, self.identity)
        self.addCleanup(self.output.close)
        for _ in range(4):
            self.output.poll()
        self.refresh()
        self.channel = ControllerVelocityChannel(self.output, self.limits,
            source_factory=lambda ticket, identity: PhysicalTwistSource(ticket, identity, context=self.context))
        self.addCleanup(self.channel.retire)
        self.channel.arm(self.ticket, self.identity, controller_namespace(self.ticket))
        self.generation = NS(ticket=self.ticket, accepted_at=time.monotonic(), retiring=False, terminal=False)
        self.until(lambda: self.publisher.get_subscription_count() == 1
            and len(self.channel.source.reader.node.get_publishers_info_by_topic(
                controller_namespace(self.ticket) + '/observed_cmd_vel')) == 1)
        time.sleep(.05)

    def refresh(self):
        message = self.messages.McCommonState()
        message.input_source.name, message.fsm_state.current_state = 'lingxi-next', 2
        self.output._state_message(message)

    def observation(self):
        return Observation(self.identity, time.monotonic(), dict(x=0., y=0., yaw=0.),
                           dict.fromkeys(CHECKS, True), False)

    def until(self, predicate, seconds=2.):
        deadline = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() > deadline:
                self.fail('bounded_actual_velocity_dds_timeout')
            time.sleep(.005)

    def publish(self, speed=.01):
        value = self.Twist()
        value.linear.x = speed
        self.publisher.publish(value)

    def take(self, source=None):
        values = []
        source = source or self.channel.source
        def received():
            value = source.take()
            if value is not None:
                values.append(value)
            return bool(values)
        self.until(received)
        return values[0]

    def test_real_dds_identity_and_acquisition_time_reach_in_memory_aimdk_output(self):
        published_before = time.monotonic()
        self.publish()
        command = self.take()
        self.assertGreaterEqual(command.generated_at, published_before - .005)
        self.assertLessEqual(command.generated_at, time.monotonic())
        self.assertEqual(self.ticket, command.ticket)
        self.assertEqual(controller_namespace(self.ticket), command.namespace)
        self.assertEqual(24, len(self.channel.source.publisher_gid))
        self.refresh()
        self.assertTrue(self.output.command(command, self.limits, self.observation()))
        self.assertEqual(.01, self.output_node.publisher.messages[-1].forward_velocity)
        self.assertTrue(self.output.physical_motion_sent)

    def test_real_queued_sample_expires_and_fresh_packet_cannot_revive_source(self):
        self.publish()
        time.sleep(.18)
        with self.assertRaisesRegex(SourceTimeError, 'expired_or_future'):
            self.take()
        self.publish()
        with self.assertRaisesRegex(SourceTimeError, 'expired_or_future'):
            self.take()

    def test_second_real_publisher_is_fenced_before_nonzero_dispatch(self):
        extra = self.producer.create_publisher(self.Twist, 'observed_cmd_vel', 1)
        self.addCleanup(lambda: self.producer.destroy_publisher(extra))
        source = self.channel.source
        topic = source.namespace + '/observed_cmd_vel'
        self.until(lambda: len(source.reader.node.get_publishers_info_by_topic(topic)) == 2)
        self.publish()
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'exclusive_generation'):
            self.channel.poll(self.generation, self.observation())
        self.assertFalse(self.output.armed)
        self.assertTrue(self.output.controller_fault)
        self.assertEqual([0.], [message.forward_velocity for message in self.output_node.publisher.messages])

    def test_retired_namespace_cannot_feed_next_generation_even_if_old_publisher_continues(self):
        old_source = self.channel.source
        self.channel.retire()
        self.output.retire(self.ticket, 'a' * 32)
        time.sleep(.01)
        self.refresh()
        self.assertIsNotNone(self.output.complete_retirement('a' * 32))
        new = Ticket('tour', '2' * 32)
        self.channel.arm(new, self.identity, controller_namespace(new))
        for _ in range(5):
            self.publish()
            time.sleep(.01)
        self.assertIsNone(old_source.take())
        self.assertIsNone(self.channel.source.take())
        self.assertEqual(0, self.channel.source.sequence)
        producer = self.rclpy.create_node('velocity_smoother', namespace=controller_namespace(new),
            context=self.context, enable_rosout=False, start_parameter_services=False)
        self.addCleanup(producer.destroy_node)
        publisher = producer.create_publisher(self.Twist, 'observed_cmd_vel', 1)
        self.until(lambda: publisher.get_subscription_count() == 1)
        message = self.Twist()
        message.linear.x = .01
        publisher.publish(message)
        command = self.take()
        self.assertEqual(new, command.ticket)
        self.refresh()
        self.assertTrue(self.output.command(command, self.limits, self.observation()))

    def test_actual_action_velocity_and_confirmed_retirement_compose(self):
        from rclpy.action import ActionServer, CancelResponse
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.task import Future
        from lifecycle_msgs.srv import GetState
        from nav2_msgs.action import NavigateToPose
        from lingxi_navigation.nav2_action import Nav2ActionDriver, RosNavigationBackend

        self.channel.retire()
        self.output.retire(self.ticket, 'a' * 32)
        time.sleep(.001)
        self.refresh()
        self.assertIsNotNone(self.output.complete_retirement('a' * 32))
        ticket = Ticket('tour', '2' * 32)
        producer = self.rclpy.create_node('velocity_smoother', namespace=controller_namespace(ticket),
            context=self.context, enable_rosout=False, start_parameter_services=False)
        self.addCleanup(producer.destroy_node)
        publisher = producer.create_publisher(self.Twist, 'observed_cmd_vel', 1)
        executor = SingleThreadedExecutor(context=self.context)
        executor.add_node(producer)
        self.addCleanup(lambda: executor.shutdown(timeout_sec=1.))
        gate = Future(executor=executor)
        async def execute(handle):
            await gate
            handle.canceled()
            return NavigateToPose.Result()
        server = ActionServer(producer, NavigateToPose, 'navigate_to_pose', execute_callback=execute,
            cancel_callback=lambda handle: CancelResponse.ACCEPT)
        self.addCleanup(server.destroy)
        def state(request, response):
            response.current_state.id = 3
            return response
        services = [producer.create_service(GetState, name + '/get_state', state)
            for name in ('planner_server', 'controller_server', 'bt_navigator', 'velocity_smoother')]
        driver = Nav2ActionDriver(producer, 'map')
        self.addCleanup(driver.close)
        def spin():
            self.refresh()
            executor.spin_once(timeout_sec=.005)
        backend = RosNavigationBackend(driver, NS(observe=self.observation), self.output,
            lambda *_: self.identity, spin, controller=self.channel)
        def run_until(predicate):
            def step():
                backend.tick()
                return predicate()
            self.until(step)
        run_until(driver.ready)
        backend.start(ticket, dict(x=1., y=0., yaw=0.))
        run_until(lambda: driver.generation.accepted_at is not None and publisher.get_subscription_count() == 1)
        time.sleep(.03)
        message = self.Twist()
        message.linear.x = .005
        publisher.publish(message)
        run_until(lambda: self.output.physical_motion_sent)
        self.assertEqual(.005, self.output_node.publisher.messages[-1].forward_velocity)
        source = self.channel.source
        backend.retire(ticket, 'b' * 32)
        self.assertTrue(source.quiescent)
        self.assertIsNone(source.take())
        self.assertEqual(0., self.output_node.publisher.messages[-1].forward_velocity)
        run_until(lambda: driver.generation.cancel.done())
        self.assertIsNone(backend.retirement('b' * 32))
        gate.set_result(None)
        run_until(lambda: driver.action_retired(ticket))
        self.assertIsNotNone(backend.retirement('b' * 32))
        self.assertTrue(self.output.ready(self.identity))
        self.assertTrue(backend.observe().physical_motion_sent)


if __name__ == '__main__':
    unittest.main()
