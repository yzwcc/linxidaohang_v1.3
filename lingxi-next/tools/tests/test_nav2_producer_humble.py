"""Real owned Nav2 nodes, synthetic sensors and in-memory AIMDK. No robot IO."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import struct
import sys
import time
import traceback
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
import test_physical_ros as fixtures
from test_nav2_producer import profile
import test_physical_evidence_humble as evidence_fixtures
from lingxi_navigation.nav2_producer import Nav2Generation, OwnedNav2Backend, spin_callbacks
from lingxi_navigation.physical_bridge import MapEvidence, PhysicalNavigationBridge
from lingxi_navigation.physical_ros import AimdkVelocityOutput, digest_footprint


@unittest.skipUnless(os.environ.get('LINGXI_VERIFY_NAV2_PRODUCER_ROS') == '1',
                     'explicit isolated Humble required')
class ActualOwnedNav2Tests(unittest.TestCase):
    def setUp(self):
        if (set(os.listdir('/sys/class/net')) != {'lo'} or os.environ.get('ROS_DISTRO') != 'humble'
                or os.environ.get('ROS_LOCALHOST_ONLY') != '1'
                or os.environ.get('RMW_IMPLEMENTATION') != 'rmw_fastrtps_cpp'):
            raise RuntimeError('isolated_loopback_humble_test_required')
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
        from nav_msgs.msg import OccupancyGrid, Odometry
        from sensor_msgs.msg import PointCloud2
        from tf2_msgs.msg import TFMessage
        self.root = Path(os.environ['LINGXI_NAV2_TEST_OUTPUT'])
        self.root.mkdir(mode=0o700)
        rclpy.init(args=[])
        self.addCleanup(rclpy.try_shutdown)
        self.node = rclpy.create_node('controlled_synthetic_nav2_producer_test', use_global_arguments=False)
        self.addCleanup(self.node.destroy_node)
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.addCleanup(lambda: self.executor.shutdown(timeout_sec=1.))
        footprint = self.root / 'footprint.json'
        footprint.write_text('{"testOnlyRadius":0.3}', encoding='utf-8')
        self.identity = MapEvidence('slam-' + 'a' * 32, 'a' * 64, 'b' * 64, digest_footprint(footprint))
        self.profile = profile(self.identity)
        sensor_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        tf_qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.RELIABLE)
        latched_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.publishers = {}
        for name, kind, topic, qos in (
            ('cloud', PointCloud2, self.profile.cloud_topic, sensor_qos),
            ('odom', Odometry, self.profile.odometry_topic, sensor_qos),
            ('terrain', OccupancyGrid, self.profile.terrain_topic, latched_qos),
            ('tf', TFMessage, '/tf', tf_qos),
            ('tf_static', TFMessage, '/tf_static', latched_qos)):
            self.publishers[name] = self.node.create_publisher(kind, topic, qos)
        self.publishers['tf_static'].publish(TFMessage(transforms=[
            evidence_fixtures.ActualPhysicalEvidenceTests.transform('base_link', 'lidar', 0)]))
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
        self.generations, self.trace = [], []
        def factory(ticket):
            generation = Nav2Generation(self.profile, ticket, self.root, footprint, self.executor)
            self.generations.append(generation)
            generation.connect(self.output, self.profile.limits)
            return generation
        self.backend = OwnedNav2Backend(self.identity, self.output, factory, self.spin)
        self.backend_errors = []
        command = self.output.command
        def recorded_command(value, limits, observation):
            prior = self.output.last_velocity
            origin = self.output.armed_at if self.output.last_command_at is None else self.output.last_command_at
            source_origin = (self.output.armed_at if self.output.last_command_generated is None
                             else self.output.last_command_generated)
            try:
                return command(value, limits, observation)
            except Exception:
                self.backend_errors.append(repr(dict(previous=prior, command=asdict(value),
                    elapsed=time.monotonic() - origin, sourceElapsed=value.generated_at - source_origin)))
                raise
        self.output.command = recorded_command
        tick = self.backend.tick
        def recorded_tick():
            try:
                tick()
            except Exception:
                self.backend_errors.append(traceback.format_exc())
                sensors = self.backend.generation.sensors
                if sensors is not None:
                    steady, ros = time.monotonic(), self.node.get_clock().now().nanoseconds
                    self.backend_errors.append(repr(dict(tfFailed=sensors.tf_failed,
                        clockFailed=sensors.clock_failed, tfReady=sensors._tf_ready(),
                        ages={key: (steady - value[1], (ros - value[0]) / 1e9)
                              for key, value in sensors.receipts.items()})))
                raise
        self.backend.tick = recorded_tick
        self.addCleanup(self.backend.close)
        self.bridge = PhysicalNavigationBridge(self.backend, self.identity)
        observe = self.bridge._observe
        def recorded_observe():
            ready = observe()
            if not ready and self.bridge.phase in ('requested', 'running'):
                try:
                    value = self.backend.observe()
                    self.backend_errors.append(repr(dict(checks=value.checks,
                        acquisitionAge=time.monotonic() - value.observed_at)))
                except Exception:
                    self.backend_errors.append(traceback.format_exc())
            return ready
        self.bridge._observe = recorded_observe
        self.number = 0
        self.last_publish = 0.
        self.sensors_enabled = True

    def publish(self):
        from nav_msgs.msg import OccupancyGrid, Odometry
        from sensor_msgs.msg import PointCloud2, PointField
        from tf2_msgs.msg import TFMessage
        now = self.node.get_clock().now().nanoseconds
        def stamp(message, frame, ns):
            message.header.frame_id = frame
            message.header.stamp.sec, message.header.stamp.nanosec = divmod(ns, 1_000_000_000)
        cloud = PointCloud2()
        stamp(cloud, 'lidar', now - 20_000_000)
        cloud.width = cloud.height = 1
        cloud.point_step = cloud.row_step = 12
        cloud.fields = [PointField(name=name, offset=index * 4, datatype=PointField.FLOAT32, count=1)
            for index, name in enumerate(('x', 'y', 'z'))]
        cloud.data = struct.pack('fff', 2., 0., .2)
        odom = Odometry()
        stamp(odom, 'map', now - 10_000_000)
        odom.child_frame_id = 'base_link'
        odom.pose.pose.orientation.w = 1.
        grid = OccupancyGrid()
        stamp(grid, 'map', now)
        grid.info.width, grid.info.height, grid.info.resolution = 160, 160, .05
        grid.info.origin.position.x = grid.info.origin.position.y = -4.
        grid.info.origin.orientation.w = 1.
        grid.data = [0] * (160 * 160)
        self.publishers['cloud'].publish(cloud)
        self.publishers['odom'].publish(odom)
        self.publishers['terrain'].publish(grid)
        for ns in (now - 40_000_000, now):
            self.publishers['tf'].publish(TFMessage(transforms=[
                evidence_fixtures.ActualPhysicalEvidenceTests.transform('map', 'base_link', ns)]))

    def spin(self):
        message = self.messages.McCommonState()
        message.input_source.name, message.fsm_state.current_state = 'lingxi-next', 2
        self.output._state_message(message)
        now = time.monotonic()
        if self.sensors_enabled and now - self.last_publish >= .025:
            self.last_publish = now
            self.publish()
        spin_callbacks(self.executor)

    def until(self, predicate, seconds=35.):
        deadline = time.monotonic() + seconds
        while True:
            self.bridge.tick()
            if predicate():
                return
            if (time.monotonic() >= deadline
                    or (self.bridge.phase == 'failed' and self.bridge.retiring is None)):
                self.fail('actual_owned_nav2_timeout: ' + str(self.bridge.state(poll=False))
                          + '\n'.join(self.backend_errors))
            time.sleep(.002)

    def command(self, action):
        self.number += 1
        ticket = self.bridge.ticket
        request = dict(id=f'{self.number:032x}', action=action, session=self.bridge.session,
            taskId=ticket.task_id if ticket else 'tour', expectedEpoch=ticket.epoch if ticket else None,
            mapId=self.identity.mapId, evidence=asdict(self.identity))
        if action == 'start':
            request.update(pointId='test-point', goal=dict(x=1., y=0., yaw=0.))
        elif self.bridge.point_id is not None:
            request['pointId'] = self.bridge.point_id
        return self.bridge.request(request)

    def record(self, stage):
        self.trace.append(dict(stage=stage, state=self.bridge.state(poll=False),
            attemptedOutputCount=len(self.output_node.publisher.messages)))

    def nonzero_count(self):
        return sum(any(getattr(message, name) != 0. for name in
                   ('forward_velocity', 'lateral_velocity', 'angular_velocity'))
                   for message in self.output_node.publisher.messages)

    def test_real_launch_action_velocity_pause_resume_sensor_loss_and_process_teardown(self):
        self.command('stop')
        self.until(lambda: self.bridge.state(poll=False)['navigationReady'])
        self.assertEqual(1, len(self.generations))
        self.assertFalse(self.output.armed)
        self.assertFalse(self.output.physical_motion_sent)
        self.record('real_nav2_prepared_without_motion')
        self.command('start')
        self.until(lambda: self.bridge.phase == 'running' and self.nonzero_count() >= 10)
        first = self.bridge.ticket
        self.record('real_action_and_twist_in_memory_only')
        self.command('pause')
        self.until(lambda: self.bridge.phase == 'paused' and len(self.generations) == 2)
        self.assertTrue(self.generations[0].quiescent())
        self.assertFalse(self.generations[0].process._live_group())
        self.assertFalse(self.output.armed)
        self.record('paused_after_owned_process_group_exit')
        self.until(lambda: self.bridge.state(poll=False)['navigationReady'])
        count = self.nonzero_count()
        self.command('resume')
        self.assertNotEqual(first.epoch, self.bridge.ticket.epoch)
        self.until(lambda: self.bridge.phase == 'running' and self.nonzero_count() >= count + 10)
        self.record('resumed_with_fresh_namespace')
        self.sensors_enabled = False
        self.until(lambda: self.bridge.phase == 'failed' and self.bridge.retiring is None, seconds=5.)
        self.assertTrue(self.bridge.requires_stop)
        self.assertFalse(self.output.armed)
        self.assertTrue(self.generations[1].quiescent())
        self.assertEqual(0., self.output_node.publisher.messages[-1].forward_velocity)
        for _ in range(10):
            self.bridge.tick()
        self.assertEqual(2, len(self.generations))
        self.record('sensor_expiry_no_automatic_restart')
        (self.root / 'result.json').write_text(json.dumps(dict(schema=1, testOnly=True,
            robotConnected=False, vendorPublisher=False, nav2ProcessesExecuted=True,
            syntheticSensors=True, trace=self.trace), indent=2), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
