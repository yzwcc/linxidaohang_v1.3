"""Opt-in actual ROS test of the deployed output owner with synthetic grid bytes."""
from dataclasses import replace
import os
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'robot-runtime/python'), str(ROOT / 'robot-navigation')]
from live_terrain_ros import LiveTerrainRosOutput
from lingxi_navigation.occupancy import ClearanceProjection
from lingxi_navigation.terrain_lane import LaneSnapshot
from lingxi_navigation.terrain_process import TerrainGrid


@unittest.skipUnless(os.environ.get('LINGXI_VERIFY_LIVE_TERRAIN_ROS') == '1', 'explicit isolated Humble required')
class ActualLiveTerrainRosTests(unittest.TestCase):
    def setUp(self):
        if (set(os.listdir('/sys/class/net')) != {'lo'} or os.environ.get('ROS_DISTRO') != 'humble'
                or os.environ.get('ROS_LOCALHOST_ONLY') != '1'
                or os.environ.get('RMW_IMPLEMENTATION') != 'rmw_fastrtps_cpp'):
            raise RuntimeError('isolated_loopback_humble_test_required')
        import rclpy
        from rclpy.context import Context
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
        from nav_msgs.msg import OccupancyGrid
        self.context = Context()
        rclpy.init(args=[], context=self.context)
        self.addCleanup(self.context.try_shutdown)
        self.node = rclpy.create_node('synthetic_terrain_output_test', context=self.context,
            use_global_arguments=False, enable_rosout=False, start_parameter_services=False)
        self.addCleanup(self.node.destroy_node)
        self.executor = SingleThreadedExecutor(context=self.context)
        self.executor.add_node(self.node)
        self.addCleanup(lambda: self.executor.shutdown(timeout_sec=1.))
        self.projection = ClearanceProjection(8, 8, .1, 0., 0.)
        self.state = LaneSnapshot(0, 'starting')
        self.owner = LiveTerrainRosOutput(self.projection, lambda: self.state, self.revoke)
        self.owner.setup(self.node)
        self.addCleanup(self.owner.close)
        self.samples = []
        self.qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.topic = '/' + self.owner.namespace + '/traversable_map'
        self.node.create_subscription(OccupancyGrid, self.topic, lambda msg: self.samples.append(msg), self.qos)

    def revoke(self):
        self.state = replace(self.state, state='retired', grid=None, failure='test_revoked')

    def until(self, predicate, seconds=4.):
        deadline = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail('bounded_actual_ros_publication_timeout')
            self.executor.spin_once(timeout_sec=.01)

    def offer(self):
        now = time.monotonic()
        grid = TerrainGrid('a' * 32, 1, 'b' * 64, 1, 'c' * 64, self.projection, bytes(64), now, now + .5)
        self.state = LaneSnapshot(1, 'ready', session=grid.session, grid=grid)
        self.until(lambda: bool(self.samples) and set(self.samples[-1].data) == {0})

    def test_idle_expiry_uses_steady_timer_when_ros_clock_is_paused(self):
        from rclpy.parameter import Parameter
        self.owner.node.set_parameters([Parameter('use_sim_time', value=True)])
        self.until(lambda: bool(self.samples) and set(self.samples[-1].data) == {-1})
        self.offer()
        count = self.owner.publisher.publications
        self.until(lambda: set(self.samples[-1].data) == {-1})
        self.assertGreater(self.owner.publisher.publications, count + 2)
        self.assertEqual(0, self.owner.node.get_clock().now().nanoseconds)
        self.assertEqual(self.owner.namespace + '_map', self.samples[-1].header.frame_id)
        self.assertEqual(64, len(self.samples[-1].data))

    def test_revocation_reaches_current_and_late_subscriber_before_owned_close(self):
        from nav_msgs.msg import OccupancyGrid
        self.offer()
        self.revoke()
        self.until(lambda: set(self.samples[-1].data) == {-1})
        late = []
        self.node.create_subscription(OccupancyGrid, self.topic, lambda msg: late.append(msg), self.qos)
        self.until(lambda: bool(late))
        self.assertEqual({-1}, set(late[-1].data))
        self.owner.close()
        self.owner.close()
        self.assertEqual([self.node], self.executor.get_nodes())
        self.assertTrue(self.owner.summary()['closed'])
        self.assertFalse(self.owner.summary()['failed'])
        self.assertFalse(self.owner.summary()['navigationReady'])

    def test_mismatched_session_grid_retires_owner_and_publishes_unknown(self):
        self.offer()
        now = time.monotonic()
        grid = TerrainGrid('d' * 32, 1, 'b' * 64, 1, 'c' * 64,
            self.projection, bytes(64), now, now + .5)
        self.state = LaneSnapshot(1, 'ready', session=grid.session, grid=grid)
        self.until(lambda: self.owner.revoked and set(self.samples[-1].data) == {-1})
        self.assertTrue(self.owner.failed)
        self.state = LaneSnapshot(2, 'ready', session=grid.session, grid=grid)
        count = self.owner.publisher.publications
        self.until(lambda: self.owner.publisher.publications > count + 2)
        self.assertIsNone(self.owner.snapshot().grid)
        self.assertEqual({-1}, set(self.samples[-1].data))


if __name__ == '__main__':
    unittest.main()
