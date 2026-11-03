"""Opt-in isolated actual Humble DDS/tf2 evidence, with no output publisher."""
import os
from pathlib import Path
import struct
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-navigation"))
from lingxi_navigation.physical_bridge import MapEvidence
from lingxi_navigation.physical_ros import RosSensorEvidence, RosTopics, digest_footprint


@unittest.skipUnless(os.environ.get("LINGXI_VERIFY_PHYSICAL_EVIDENCE_ROS") == "1", "explicit isolated Humble required")
class ActualPhysicalEvidenceTests(unittest.TestCase):
    def setUp(self):
        if (set(os.listdir("/sys/class/net")) != {"lo"} or os.environ.get("ROS_DISTRO") != "humble"
                or os.environ.get("ROS_LOCALHOST_ONLY") != "1"
                or os.environ.get("RMW_IMPLEMENTATION") != "rmw_fastrtps_cpp"):
            raise RuntimeError("isolated_loopback_humble_test_required")
        import rclpy
        from rclpy.context import Context
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
        from nav_msgs.msg import OccupancyGrid, Odometry
        from sensor_msgs.msg import PointCloud2
        from tf2_msgs.msg import TFMessage
        self.context = Context()
        rclpy.init(args=[], context=self.context)
        self.addCleanup(self.context.try_shutdown)
        self.node = rclpy.create_node("synthetic_physical_evidence_test", context=self.context,
            use_global_arguments=False, enable_rosout=False, start_parameter_services=False)
        self.addCleanup(self.node.destroy_node)
        self.executor = SingleThreadedExecutor(context=self.context)
        self.executor.add_node(self.node)
        self.addCleanup(lambda: self.executor.shutdown(timeout_sec=1.))
        self.topics = RosTopics(cloud="/test/points", odometry="/test/odometry", tf="/test/tf",
            tf_static="/test/tf_static", global_costmap="/test/global", local_costmap="/test/local", terrain="/test/terrain")
        sensor_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        tf_qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.BEST_EFFORT)
        grid_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE)
        static_qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.publishers = {}
        for name, kind, qos in (("cloud", PointCloud2, sensor_qos), ("odometry", Odometry, sensor_qos),
            ("global_costmap", OccupancyGrid, grid_qos), ("local_costmap", OccupancyGrid, grid_qos),
            ("terrain", OccupancyGrid, grid_qos), ("tf", TFMessage, tf_qos), ("tf_static", TFMessage, static_qos)):
            self.publishers[name] = self.node.create_publisher(kind, getattr(self.topics, name), qos)
        # Publish before constructing the subscriber to exercise transient-local delivery.
        self.publishers["tf_static"].publish(TFMessage(transforms=[self.transform("base_link", "lidar", 0)]))
        self.identity = MapEvidence("slam-" + "a" * 32, "a" * 64, "b" * 64, digest_footprint({"radius": .3}))
        self.sensors = RosSensorEvidence(self.node, self.identity, self.topics, map_frame="map", sensor_frame="lidar",
            footprint={"radius": .3})
        self.addCleanup(self.sensors.close)
        self.until(lambda: all(pub.get_subscription_count() == 1 for pub in self.publishers.values())
            and "lidar" in self.sensors.tf_parents)

    def until(self, predicate, seconds=4.):
        deadline = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("bounded_actual_ros_evidence_timeout: receipts=" + str(tuple(self.sensors.receipts))
                    + ", frames=" + str(tuple(self.sensors.tf_parents))
                    + ", tf_failed=" + str(self.sensors.tf_failed) + ", clock_failed=" + str(self.sensors.clock_failed))
            self.executor.spin_once(timeout_sec=.005)

    @staticmethod
    def transform(parent, child, ns):
        from geometry_msgs.msg import TransformStamped
        item = TransformStamped()
        item.header.frame_id, item.child_frame_id = parent, child
        item.header.stamp.sec, item.header.stamp.nanosec = divmod(ns, 1_000_000_000)
        item.transform.rotation.w = 1.
        return item

    def publish(self, *, transforms=True, age_ns=0):
        from nav_msgs.msg import OccupancyGrid, Odometry
        from sensor_msgs.msg import PointCloud2, PointField
        from tf2_msgs.msg import TFMessage
        now = self.node.get_clock().now().nanoseconds - age_ns
        def stamp(message, frame, ns):
            message.header.frame_id = frame
            message.header.stamp.sec, message.header.stamp.nanosec = divmod(ns, 1_000_000_000)
        cloud = PointCloud2()
        stamp(cloud, "lidar", now - 20_000_000)
        cloud.width = cloud.height = 1
        cloud.point_step = cloud.row_step = 12
        cloud.fields = [PointField(name=name, offset=index * 4, datatype=PointField.FLOAT32, count=1)
            for index, name in enumerate(("x", "y", "z"))]
        cloud.data = struct.pack("fff", 0., 0., .2)
        odom = Odometry()
        stamp(odom, "map", now - 10_000_000)
        odom.child_frame_id = "base_link"
        odom.pose.pose.orientation.w = 1.
        self.publishers["cloud"].publish(cloud)
        self.publishers["odometry"].publish(odom)
        for name in ("global_costmap", "local_costmap", "terrain"):
            grid = OccupancyGrid()
            stamp(grid, "map", now)
            grid.info.width = grid.info.height = 1
            grid.info.resolution = .05
            grid.info.origin.orientation.w = 1.
            grid.data = [0]
            self.publishers[name].publish(grid)
        if transforms:
            for ns in (now - 40_000_000, now):
                for parent, child in (("map", "odom"), ("odom", "base_link")):
                    self.publishers["tf"].publish(TFMessage(transforms=[self.transform(parent, child, ns)]))
        return now

    def ready(self):
        try:
            return self.sensors.observe()
        except RuntimeError:
            return None

    def test_late_static_and_split_dynamic_chain_at_distinct_sample_times(self):
        from tf2_ros import Buffer
        self.assertIsInstance(self.sensors.tf_buffer, Buffer)
        self.publish()
        self.until(self.ready)
        value = self.sensors.observe()
        self.assertTrue(value.checks["tf"])
        self.assertTrue(value.checks["localization"])
        self.assertFalse(value.physical_motion_sent)
        self.assertGreater(time.monotonic() - value.observed_at, .019)
        self.assertEqual(3, len(self.sensors.tf_parents))

    def test_fresh_sensor_messages_cannot_hide_missing_new_tf(self):
        self.publish()
        self.until(self.ready)
        time.sleep(.06)
        stamp = self.publish(transforms=False)
        self.until(lambda: self.sensors.receipts.get("odom", (0,))[0] == stamp - 10_000_000)
        self.assertIsNone(self.ready())
        self.assertFalse(self.sensors.tf_failed)
        self.publish()
        self.until(self.ready)

    def test_delayed_dds_data_and_idle_expiry_revoke_evidence(self):
        self.publish()
        self.until(self.ready)
        self.until(lambda: self.ready() is None, seconds=1.)
        self.publish(transforms=False, age_ns=2_000_000_000)
        self.until(lambda: not self.sensors.receipts)
        self.assertIsNone(self.ready())
        self.publish()
        self.until(self.ready)

    def test_paused_ros_clock_latches_even_if_all_receive_times_are_recent(self):
        from rclpy.parameter import Parameter
        self.publish()
        self.until(self.ready)
        self.node.set_parameters([Parameter("use_sim_time", value=True)])
        self.assertIsNone(self.ready())
        self.assertTrue(self.sensors.clock_failed)
        self.node.set_parameters([Parameter("use_sim_time", value=False)])
        self.assertIsNone(self.ready())


if __name__ == "__main__":
    unittest.main()
