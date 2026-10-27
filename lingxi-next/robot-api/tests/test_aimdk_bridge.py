"""Exercise actual bridge callbacks with minimal ROS type stubs, without a robot."""
import importlib.util
import math
import struct
from pathlib import Path
import sys
import threading
import time
import types
import unittest


def module(name, **members):
    result = types.ModuleType(name)
    result.__dict__.update(members)
    sys.modules[name] = result
    return result


module("rclpy")
module("rclpy.node", Node=type("Node", (), {}))
module("rclpy.executors", MultiThreadedExecutor=object)
module("rclpy.qos", DurabilityPolicy=object, QoSProfile=object, ReliabilityPolicy=object)
for name, members in [
    ("nav_msgs.msg", ["OccupancyGrid", "Odometry"]),
    ("sensor_msgs.msg", ["LaserScan", "PointCloud2"]),
    ("std_msgs.msg", ["Header", "String"]),
    ("aimdk_msgs.msg", ["PncTaskRequest", "PncTaskResponse", "TaskMasterState"]),
    ("aimdk_msgs.srv", ["GetStoredMapByName"]),
]:
    module(name.split(".")[0])
    module(name, **{member: type(member, (), {}) for member in members})
spec = importlib.util.spec_from_file_location("bridge", Path(__file__).parents[1] / "src" / "aimdk_ros_bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
obj = types.SimpleNamespace


class BridgeCallbacks(unittest.TestCase):
    def setUp(self):
        self.node = bridge.X2Node.__new__(bridge.X2Node)
        self.node.lock = threading.RLock()
        self.node.last_map = None
        self.node.last_navigation = {"taskId": "101", "mapId": 9, "status": "requested"}
        self.node.mapping_status = "requested"
        self.node.relocalization_pending = False
        self.node.last_lidar = None
        self.node.last_scan_at = 0
        self.node.lidar_sequence = 0
        self.node.lidar_epoch = "test-epoch"
        self.node.tf_buffer = None
        self.node.get_clock = lambda: obj(now=lambda: obj(nanoseconds=10_000_000_000))

    def test_exact_task_and_map_ids_required(self):
        self.node.on_navigation_response(obj(task_id=100, map_id=9, task_status=4))
        self.assertEqual("requested", self.node.last_navigation["status"])
        self.node.on_navigation_response(obj(task_id=101, map_id=8, task_status=4))
        self.assertEqual("requested", self.node.last_navigation["status"])
        self.node.on_navigation_response(obj(task_id=101, map_id=9, task_status=4))
        self.assertEqual("finished", self.node.last_navigation["status"])

    def test_unknown_pnc_status_is_never_success(self):
        self.node.on_navigation_response(obj(task_id=101, map_id=9, task_status=6))
        self.assertEqual("requested", self.node.last_navigation["status"])

    def test_mapping_requires_task_master_confirmation(self):
        self.node.on_task_state(obj(main_state=1, sub_state=1, slam_state=3, loc_map_id=9, loc_map_name="map"))
        self.assertEqual("requested", self.node.mapping_status)
        self.node.on_task_state(obj(main_state=1, sub_state=1, slam_state=2, loc_map_id=0, loc_map_name=""))
        self.assertEqual("running", self.node.mapping_status)

    def test_grid_retains_orientation_and_ros_row_order(self):
        q = obj(x=0, y=0, z=math.sqrt(0.5), w=math.sqrt(0.5))
        info = obj(width=2, height=2, resolution=0.05, origin=obj(position=obj(x=-2, y=-3), orientation=q))
        self.node.on_grid(obj(info=info, data=[0, 100, -1, 0]))
        self.assertEqual([0, 100, -1, 0], self.node.last_map["cells"])
        self.assertAlmostEqual(math.pi / 2, self.node.last_map["originYaw"])
        self.node.on_grid(obj(info=info, data=[]))
        self.assertEqual([0, 100, -1, 0], self.node.last_map["cells"])

    def test_navigation_rejects_mapping_relocalization_and_duplicate_dispatch(self):
        published = []
        self.node.navigation_ready = True
        self.node.nav_publisher = obj(get_subscription_count=lambda: 1, publish=published.append)
        self.node.snapshot = lambda: {"taskMasterState": {"mapId": 9, "slamState": 3}, "pose": {"x": 0}}
        self.node.get_clock = lambda: obj(now=lambda: obj(to_msg=lambda: None))
        original = bridge.PncTaskRequest
        bridge.PncTaskRequest = lambda: obj(header=obj(), target_pose=obj(pose=obj(position=obj(), orientation=obj())))
        try:
            self.node.last_navigation = {"status": "idle"}
            with self.assertRaises(bridge.AdapterError):
                self.node.send_navigation(task_id=102, map_id=9, target={"x": 1, "y": 2, "yaw": 0}, action=1)
            self.node.mapping_status = "idle"
            self.node.relocalization_pending = True
            with self.assertRaises(bridge.AdapterError):
                self.node.send_navigation(task_id=102, map_id=9, target={"x": 1, "y": 2, "yaw": 0}, action=1)
            self.node.relocalization_pending = False
            self.node.send_navigation(task_id=102, map_id=9, target={"x": 1, "y": 2, "yaw": 0}, action=1)
            with self.assertRaises(bridge.AdapterError):
                self.node.send_navigation(task_id=103, map_id=9, target={"x": 1, "y": 2, "yaw": 0}, action=1)
            self.assertEqual(1, len(published))
        finally:
            bridge.PncTaskRequest = original

    def scan(self):
        return obj(header=obj(stamp=obj(sec=10, nanosec=0), frame_id="laser"),
                   angle_min=0.0, angle_increment=math.pi / 2, range_min=0.1, range_max=10.0,
                   ranges=[1.0, 2.0, math.inf, math.nan, -1.0, 20.0])

    def test_scan_filters_invalid_ranges_without_using_grid(self):
        points = bridge.scan_xyz(self.scan())
        self.assertEqual(2, len(points))
        self.assertAlmostEqual(1.0, points[0][0])
        self.assertAlmostEqual(2.0, points[1][1])
        self.node.on_scan(self.scan())
        frame = self.node.lidar_frame()
        self.assertEqual("laser-scan", frame["source"])
        self.assertEqual([1.0, 0.0, 0.0, 0.0, 2.0, 0.0], frame["points"])
        self.assertFalse(frame["fixedFrame"])
        self.assertNotIn("robotPose", frame)
        self.assertIsNone(self.node.last_map)

    def test_scan_has_bounded_payload(self):
        scan = self.scan()
        scan.ranges = [1.0] * 100_000
        self.assertLessEqual(len(bridge.scan_xyz(scan)), bridge.MAX_LIDAR_POINTS)

    def test_cloud_supports_endian_and_organized_row_padding(self):
        for endian, big in (("<", False), (">", True)):
            row = lambda a, b: struct.pack(endian + "fff", *a) + struct.pack(endian + "fff", *b) + b"padding!"
            data = row((1, 2, 3), (4, 5, 6)) + row((7, 8, 9), (math.nan, 0, 0))
            message = obj(width=2, height=2, point_step=12, row_step=32, data=data, is_bigendian=big,
                          fields=[obj(name=name, offset=i*4, datatype=7, count=1) for i, name in enumerate("xyz")])
            self.assertEqual([(1, 2, 3), (4, 5, 6), (7, 8, 9)], bridge.cloud_xyz(message))
            message.fields[0].offset = 99
            with self.assertRaises(ValueError):
                bridge.cloud_xyz(message)

    def test_transform_rotates_three_dimensions(self):
        q = obj(x=math.sqrt(0.5), y=0, z=0, w=math.sqrt(0.5))
        tf = obj(translation=obj(x=1, y=2, z=3), rotation=q)
        self.assertEqual([1, 2, 4], bridge.transform_xyz([(0, 1, 0)], tf))

    def test_scan_uses_timestamp_tf_for_points_and_robot(self):
        calls = []
        q = obj(x=0, y=0, z=math.sqrt(0.5), w=math.sqrt(0.5))
        def lookup(target, source, stamp):
            calls.append((target, source, stamp.sec))
            return obj(translation=obj(x=1, y=2, z=0), rotation=q)
        self.node.lidar_transform = lookup
        self.node.on_scan(self.scan())
        frame = self.node.lidar_frame()
        self.assertTrue(frame["fixedFrame"])
        self.assertEqual("map", frame["frameId"])
        self.assertEqual([1.0, 3.0, 0.0], frame["points"][:3])
        self.assertAlmostEqual(math.pi / 2, frame["robotPose"]["yaw"])
        self.assertEqual([("map", "laser", 10), ("map", "base_link", 10)], calls)

    def test_stale_scans_and_expired_frames_are_unavailable(self):
        scan = self.scan()
        scan.header.stamp.sec = 1
        self.node.on_scan(scan)
        with self.assertRaises(bridge.AdapterError):
            self.node.lidar_frame()
        self.node.on_scan(self.scan())
        self.node.last_scan_at = time.time() - 3
        with self.assertRaises(bridge.AdapterError):
            self.node.lidar_frame()


if __name__ == "__main__":
    unittest.main()
