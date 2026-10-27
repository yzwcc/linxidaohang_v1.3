"""Exercise actual bridge callbacks with minimal ROS type stubs, without a robot."""
import importlib.util
import math
import sqlite3
import struct
import tempfile
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from contextlib import closing
from unittest.mock import patch


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
    ("geometry_msgs.msg", ["Pose"]),
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
        self.node.relocalization_seen = False
        self.node.last_task_state = None
        self.node.last_task_state_at = 0
        self.node.last_odom = None
        self.node.last_odom_at = 0
        self.node.last_odom_stamp_ns = 0
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

    def test_navigation_accepts_vendor_timestamp_map_id_above_uint32(self):
        published = []
        map_id = 1_773_113_429_735
        self.node.mapping_status = "idle"
        self.node.last_navigation = {"status": "idle"}
        self.node.navigation_ready = True
        self.node.nav_publisher = obj(get_subscription_count=lambda: 1, publish=published.append)
        self.node.snapshot = lambda: {"taskMasterState": {"mapId": map_id, "slamState": 3}, "pose": {"x": 0}}
        self.node.get_clock = lambda: obj(now=lambda: obj(to_msg=lambda: None))
        with patch.object(bridge, "PncTaskRequest", lambda: obj(
                header=obj(), target_pose=obj(pose=obj(position=obj(), orientation=obj())))):
            self.node.send_navigation(task_id=102, map_id=map_id, target={"x": 1, "y": 2, "yaw": 0}, action=1)
        self.assertEqual(map_id, published[0].map_id)
        self.assertEqual("map", published[0].header.frame_id)

    def odom(self, stamp=10, frame="map"):
        return obj(header=obj(stamp=obj(sec=stamp, nanosec=0), frame_id=frame),
                   pose=obj(pose=obj(position=obj(x=1, y=2),
                                     orientation=obj(x=0, y=0, z=0, w=1))))

    def test_odom_requires_fresh_map_frame_and_normalized_finite_pose(self):
        for message in (self.odom(1), self.odom(frame="odom")):
            self.node.on_odom(message)
            self.assertIsNone(self.node.last_odom)
        self.node.on_odom(self.odom())
        self.assertEqual({"x": 1.0, "y": 2.0, "yaw": 0.0}, self.node.last_odom)
        received = self.node.last_odom_at
        self.node.on_odom(self.odom())
        self.assertEqual(received, self.node.last_odom_at)
        invalid = self.odom(11)
        invalid.pose.pose.orientation.w = 0
        self.node.on_odom(invalid)
        self.assertEqual(10_000_000_000, self.node.last_odom_stamp_ns)

    def setup_map(self, directory):
        map_id = 1_773_113_429_735
        root = directory / str(map_id)
        root.mkdir()
        (root / "grid_map_info.txt").write_text("20\n100 150\n400 500\n")
        self.node.mapping_status = "idle"
        self.node.last_navigation = {"status": "idle"}
        self.node.query_map = lambda name: {"mapId": map_id, "name": name}
        self.node.publisher = obj(get_subscription_count=lambda: 1)
        commands, poses = [], []
        def command(value):
            commands.append(value)
            self.node.on_task_state(obj(main_state=1, sub_state=0, slam_state=1,
                                        loc_map_id=0, loc_map_name=""))
        self.node.publish_command = command
        def pose(value):
            poses.append(value)
            self.node.on_task_state(obj(main_state=0, sub_state=0, slam_state=3,
                                        loc_map_id=map_id, loc_map_name="大厅"))
            self.node.on_odom(self.odom())
        self.node.relocalization_publisher = obj(get_subscription_count=lambda: 1, publish=pose)
        return map_id, commands, poses

    def test_relocalization_publishes_pixels_then_confirms_new_map_pose(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            map_id, commands, poses = self.setup_map(directory)
            self.node.last_lidar = {"old": True}
            self.node.last_map = {"old": True}
            with patch.object(bridge, "MAP_DIR", directory), \
                    patch.object(bridge, "RELOCALIZATION_POSE_DELAY", 0), \
                    patch.object(bridge, "Pose", lambda: obj(position=obj(), orientation=obj())):
                result = self.node.activate_map("大厅", {"x": 2, "y": 3, "yaw": math.pi / 2})
            self.assertEqual({"accepted": True, "confirmed": True, "mapId": map_id}, result)
            self.assertEqual([f"start_relocalization:{map_id}"], commands)
            self.assertEqual((140.0, 90.0), (poses[0].position.x, poses[0].position.y))
            self.assertAlmostEqual(math.sqrt(0.5), poses[0].orientation.z)
            self.assertFalse(self.node.relocalization_pending)
            self.assertIsNone(self.node.last_lidar)
            self.assertIsNone(self.node.last_map)

    def test_relocalization_without_pose_uses_mapping_origin(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, _, poses = self.setup_map(directory)
            with patch.object(bridge, "MAP_DIR", directory), \
                    patch.object(bridge, "RELOCALIZATION_POSE_DELAY", 0), \
                    patch.object(bridge, "Pose", lambda: obj(position=obj(), orientation=obj())):
                self.node.activate_map("大厅")
            self.assertEqual((100.0, 150.0), (poses[0].position.x, poses[0].position.y))

    def test_relocalization_timeout_keeps_barrier_and_does_not_republish(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, commands, _ = self.setup_map(directory)
            self.node.publish_command = commands.append  # No observed transition.
            with patch.object(bridge, "MAP_DIR", directory), \
                    patch.object(bridge, "RELOCALIZATION_POSE_DELAY", 0), \
                    patch.object(bridge, "RELOCALIZATION_TIMEOUT", 0.005), \
                    patch.object(bridge, "Pose", lambda: obj(position=obj(), orientation=obj())):
                with self.assertRaises(bridge.AdapterError) as failure:
                    self.node.activate_map("大厅")
                self.assertEqual(504, failure.exception.status)
                with self.assertRaises(bridge.AdapterError) as failure:
                    self.node.activate_map("大厅")
                self.assertEqual(409, failure.exception.status)
            self.assertTrue(self.node.relocalization_pending)
            self.assertEqual(1, len(commands))

    def test_relocalization_preflight_failure_sends_no_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, commands, _ = self.setup_map(directory)
            self.node.relocalization_publisher.get_subscription_count = lambda: 0
            with patch.object(bridge, "MAP_DIR", directory):
                with self.assertRaises(bridge.AdapterError):
                    self.node.activate_map("大厅")
            self.assertFalse(self.node.relocalization_pending)
            self.assertEqual([], commands)

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


class MapCatalog(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def database(self, rows):
        database = self.directory / "map.db"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE map (map_id INTEGER, map_name TEXT, vendor_data TEXT)")
            connection.executemany("INSERT INTO map VALUES (?, ?, 'preserved')", rows)
        return database

    def test_reads_vendor_catalog_read_only_including_timestamp_ids(self):
        database = self.database([(1_773_113_429_735, "大厅"), (9, "展厅")])
        before = database.read_bytes()
        maps = bridge.read_map_catalog(self.directory)
        self.assertEqual([1_773_113_429_735, 9], [item["mapId"] for item in maps])
        self.assertEqual(["大厅", "展厅"], [item["name"] for item in maps])
        self.assertEqual(before, database.read_bytes())
        self.assertEqual(["map.db"], [file.name for file in self.directory.iterdir()])

    def test_missing_database_is_not_created_and_yaml_variants_still_list(self):
        (self.directory / "旧场馆.yaml").touch()
        self.assertEqual([{"id": "旧场馆", "name": "旧场馆"}], bridge.read_map_catalog(self.directory))
        self.assertFalse((self.directory / "map.db").exists())

    def test_uncommitted_catalog_entry_is_not_exposed(self):
        database = self.database([(9, "展厅")])
        with closing(sqlite3.connect(database)) as writer, writer:
            writer.execute("INSERT INTO map VALUES (10, '未提交', '')")
            self.assertEqual(["展厅"], [item["name"] for item in bridge.read_map_catalog(self.directory)])
            writer.rollback()

    def test_duplicate_or_unsafe_names_are_rejected(self):
        database = self.database([(9, "展厅"), (10, "展厅")])
        with self.assertRaises(bridge.AdapterError):
            bridge.read_map_catalog(self.directory)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("UPDATE map SET map_name='../other' WHERE map_id=10")
        with self.assertRaises(bridge.AdapterError):
            bridge.read_map_catalog(self.directory)

    def test_unknown_schema_reports_unavailable_instead_of_empty_maps(self):
        with closing(sqlite3.connect(self.directory / "map.db")) as connection, connection:
            connection.execute("CREATE TABLE map (unknown TEXT)")
        with self.assertRaises(bridge.AdapterError):
            bridge.read_map_catalog(self.directory)

    def test_pixels_use_vendor_top_left_origin_and_pixels_per_metre(self):
        root = self.directory / "9"
        root.mkdir()
        (root / "grid_map_info.txt").write_text("resolution: 20\norigin: 100 150\nsize: 400 500\n")
        self.assertEqual((140.0, 90.0, 1.0), bridge.relocalization_pixels(
            self.directory, 9, {"x": 2, "y": 3, "yaw": 1}))
        for pose in ({"x": 100, "y": 0, "yaw": 0}, {"x": 0, "y": math.nan, "yaw": 0},
                     {"x": 0, "y": 0, "yaw": 8}, {"x": True, "y": 0, "yaw": 0}):
            with self.assertRaises(bridge.AdapterError):
                bridge.relocalization_pixels(self.directory, 9, pose)
        (root / "grid_map_info.txt").write_text("0\n100 150\n400 500\n")
        with self.assertRaises(bridge.AdapterError):
            bridge.relocalization_pixels(self.directory, 9, None)


if __name__ == "__main__":
    unittest.main()
