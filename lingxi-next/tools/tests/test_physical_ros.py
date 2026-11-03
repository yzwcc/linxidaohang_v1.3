"""Offline contracts for the ROS point-cloud and AIMDK boundary adapters."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-navigation"))

from lingxi_navigation.physical_bridge import MapEvidence, Ticket
from lingxi_navigation.physical_ros import (
    AimdkVelocityOutput,
    DEFAULT_MAXIMUM_Z_METRES,
    DEFAULT_MINIMUM_Z_METRES,
    FileMapActivator,
    RosSensorEvidence,
    RosTopics,
    digest_footprint,
    parse_odometry,
    parse_occupancy_grid,
    parse_point_cloud_xyz,
    validate_tf_message,
)


@dataclass
class Clock:
    value: float = 10.0

    def __call__(self):
        return self.value


def header(frame="map", sec=1):
    return types.SimpleNamespace(frame_id=frame, stamp=types.SimpleNamespace(sec=sec, nanosec=0))


def quaternion():
    return types.SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0)


def point_cloud(*, bigendian=False, z=0.2, row_padding=0):
    fields = [types.SimpleNamespace(name="x", offset=0, datatype=7, count=1),
              types.SimpleNamespace(name="y", offset=4, datatype=7, count=1),
              types.SimpleNamespace(name="z", offset=8, datatype=7, count=1)]
    point_step, width, height = 12, 2, 2
    row_step = width * point_step + row_padding
    endian = ">" if bigendian else "<"
    rows = []
    for row in range(height):
        for column in range(width):
            rows.append(struct.pack(endian + "fff", float(row), float(column), z))
        rows.append(b"x" * row_padding)
    return types.SimpleNamespace(header=header("lidar"), fields=fields, width=width, height=height,
                                point_step=point_step, row_step=row_step,
                                is_bigendian=bigendian, data=b"".join(rows))


class PhysicalRosTests(unittest.TestCase):
    def test_point_cloud_supports_endianness_and_row_padding(self):
        little = parse_point_cloud_xyz(point_cloud(row_padding=4))
        self.assertEqual(len(little.points), 4)
        self.assertEqual(little.points[3][:2], (1.0, 1.0))
        self.assertAlmostEqual(little.points[3][2], 0.2, places=6)
        big = point_cloud(bigendian=True)
        for field in big.fields:
            field.datatype = 8
            field.offset *= 2
        big.point_step = 24
        big.row_step = 48
        raw = []
        for row in range(2):
            for column in range(2):
                raw.append(struct.pack(">d", float(row)))
                raw.append(struct.pack(">d", float(column)))
                raw.append(struct.pack(">d", 0.2))
        big.data = b"".join(raw)
        self.assertEqual(parse_point_cloud_xyz(big).points[2], (1.0, 0.0, 0.2))

    def test_point_cloud_rejects_bad_layout_and_default_height_window(self):
        bad = point_cloud()
        bad.fields[0].datatype = 4
        with self.assertRaisesRegex(ValueError, "xyz_field"):
            parse_point_cloud_xyz(bad)
        bad = point_cloud(z=0.0)
        with self.assertRaisesRegex(ValueError, "finite_point_cloud"):
            parse_point_cloud_xyz(bad)
        self.assertEqual(DEFAULT_MINIMUM_Z_METRES, 0.1)
        self.assertEqual(DEFAULT_MAXIMUM_Z_METRES, 1.4)

    def test_occupancy_and_odometry_validation(self):
        origin = types.SimpleNamespace(position=types.SimpleNamespace(x=1.0, y=-2.0), orientation=quaternion())
        info = types.SimpleNamespace(width=2, height=2, resolution=0.05, origin=origin)
        grid = types.SimpleNamespace(header=header("map"), info=info, data=[0, 100, -1, 50])
        parsed = parse_occupancy_grid(grid, expected_frame="map")
        self.assertEqual(parsed.width, 2)
        self.assertEqual(parsed.data, bytes((0, 100, 255, 50)))
        grid.data = [101, 0, 0, 0]
        with self.assertRaisesRegex(ValueError, "occupancy_values"):
            parse_occupancy_grid(grid)

        odom = types.SimpleNamespace(header=header("map"), child_frame_id="base_link",
                                     pose=types.SimpleNamespace(pose=types.SimpleNamespace(
                                         position=types.SimpleNamespace(x=1.5, y=2.0),
                                         orientation=quaternion())))
        stamp, value = parse_odometry(odom, map_frame="map", base_frame="base_link")
        self.assertEqual(stamp, 1.0)
        self.assertEqual(value["x"], 1.5)

    def test_tf_accepts_realistic_chain_and_rejects_missing_translation(self):
        def transform(parent, child, translation=None):
            translation = translation or types.SimpleNamespace(x=0.0, y=0.0, z=0.0)
            return types.SimpleNamespace(header=header(parent), child_frame_id=child,
                                         transform=types.SimpleNamespace(rotation=quaternion(), translation=translation))

        message = types.SimpleNamespace(transforms=[transform("map", "odom"), transform("odom", "base_link"),
                                                     transform("base_link", "lidar_chest_front")])
        self.assertEqual(validate_tf_message(message, map_frame="map", base_frame="base_link",
                                              sensor_frame="lidar_chest_front"), 1.0)
        message.transforms[1].transform.translation.x = float("inf")
        with self.assertRaisesRegex(ValueError, "numeric_value"):
            validate_tf_message(message, map_frame="map", base_frame="base_link")

    def test_file_map_activator_binds_catalog_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "catalog-entry"
            directory.mkdir()
            session = "a" * 32
            (directory / "map-ready.json").write_text(json.dumps({"session": session}), encoding="utf-8")
            (directory / "calibration.json").write_bytes(b"calibration")
            (directory / "footprint.json").write_bytes(b"footprint")
            value = FileMapActivator(root).evidence("slam-" + session)
            self.assertEqual(value.receiptSha256, hashlib.sha256((directory / "map-ready.json").read_bytes()).hexdigest())
            with self.assertRaisesRegex(ValueError, "saved_map_directory_missing"):
                FileMapActivator(root).evidence("slam-" + "b" * 32)

    def test_sensor_freshness_uses_local_receive_clock(self):
        identity = MapEvidence("slam-" + "a" * 32, "a" * 64, "b" * 64, digest_footprint({"radius": 0.3}))
        clock = Clock()
        modules = {name: types.ModuleType(name) for name in ("nav_msgs", "nav_msgs.msg", "sensor_msgs", "sensor_msgs.msg", "tf2_msgs", "tf2_msgs.msg")}
        modules["nav_msgs.msg"].OccupancyGrid = type("OccupancyGrid", (), {})
        modules["nav_msgs.msg"].Odometry = type("Odometry", (), {})
        modules["sensor_msgs.msg"].PointCloud2 = type("PointCloud2", (), {})
        modules["tf2_msgs.msg"].TFMessage = type("TFMessage", (), {})
        old = {key: sys.modules.get(key) for key in modules}
        sys.modules.update(modules)
        try:
            class Node:
                def __init__(self):
                    self.callbacks = {}
                    self.subscriptions = []

                def create_subscription(self, kind, topic, callback, qos):
                    self.callbacks[topic] = callback
                    subscription = object()
                    self.subscriptions.append(subscription)
                    return subscription

                def destroy_subscription(self, subscription):
                    return None

            node = Node()
            sensor = RosSensorEvidence(node, identity, RosTopics(), clock=clock, footprint={"radius": 0.3})
            sensor._cloud(point_cloud())
            origin = types.SimpleNamespace(position=types.SimpleNamespace(x=0.0, y=0.0), orientation=quaternion())
            info = types.SimpleNamespace(width=1, height=1, resolution=0.05, origin=origin)
            sensor._global(types.SimpleNamespace(header=header("lingxi_saved_map"), info=info, data=[0]))
            sensor._local(types.SimpleNamespace(header=header("lingxi_saved_map"), info=info, data=[0]))
            sensor._terrain(types.SimpleNamespace(header=header("lingxi_saved_map"), info=info, data=[0]))
            sensor._odom(types.SimpleNamespace(header=header("lingxi_saved_map"), child_frame_id="base_link",
                                               pose=types.SimpleNamespace(pose=types.SimpleNamespace(
                                                   position=types.SimpleNamespace(x=0.0, y=0.0), orientation=quaternion()))))
            def tf(parent, child):
                return types.SimpleNamespace(header=header(parent), child_frame_id=child,
                                             transform=types.SimpleNamespace(rotation=quaternion(),
                                                                             translation=types.SimpleNamespace(x=0., y=0., z=0.)))
            sensor._tf(types.SimpleNamespace(transforms=[tf("lingxi_saved_map", "odom"), tf("odom", "base_link"),
                                                         tf("base_link", "lidar_chest_front")]))
            self.assertTrue(sensor.observe().checks["localization"])
            clock.value += 0.6
            with self.assertRaisesRegex(RuntimeError, "evidence_unavailable"):
                sensor.observe()
        finally:
            for key, value in old.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value

    def _aimdk_fake_modules(self, add_state=2, enable_state=1):
        module = types.ModuleType("aimdk_msgs")
        message_module = types.ModuleType("aimdk_msgs.msg")
        service_module = types.ModuleType("aimdk_msgs.srv")

        class Velocity:
            def __init__(self):
                self.header = types.SimpleNamespace(stamp=types.SimpleNamespace(sec=0, nanosec=0))
                self.source = ""
                self.forward_velocity = self.lateral_velocity = self.angular_velocity = 0.0

        class CommonState:
            STABLE, SAFE = 2, 4

            def __init__(self):
                self.input_source = types.SimpleNamespace(name="")
                self.fsm_state = types.SimpleNamespace(current_state=0)

        class SetMcInputSource:
            class Request:
                def __init__(self):
                    self.request = types.SimpleNamespace()
                    self.action = types.SimpleNamespace(value=0)
                    self.input_source = types.SimpleNamespace(name="", priority=0, timeout=0)

        message_module.McLocomotionVelocity = Velocity
        message_module.McCommonState = CommonState
        service_module.SetMcInputSource = SetMcInputSource
        old = {key: sys.modules.get(key) for key in ("aimdk_msgs", "aimdk_msgs.msg", "aimdk_msgs.srv")}
        sys.modules["aimdk_msgs"], sys.modules["aimdk_msgs.msg"] = module, message_module
        sys.modules["aimdk_msgs.srv"] = service_module

        class Future:
            def __init__(self, response):
                self.response = response
            def done(self):
                return True
            def result(self):
                return self.response
            def cancel(self):
                return None

        class Node:
            def __init__(self):
                self.publisher = None
                self.callbacks = {}
                self.requests = []
                self.responses = [add_state, enable_state]

            def create_publisher(self, kind, topic, qos):
                class Publisher:
                    def __init__(self):
                        self.messages = []
                    def publish(self, message):
                        self.messages.append(message)
                self.publisher = Publisher()
                return self.publisher

            def create_subscription(self, kind, topic, callback, qos):
                self.callbacks[topic] = callback
                return (kind, topic, callback, qos)

            def create_client(self, kind, service):
                node = self
                class Client:
                    def service_is_ready(self):
                        return True
                    def call_async(self, request):
                        node.requests.append(request)
                        value = node.responses.pop(0)
                        return Future(types.SimpleNamespace(response=types.SimpleNamespace(
                            state=types.SimpleNamespace(value=value))))
                self.client = Client()
                return self.client

            def destroy_subscription(self, subscription):
                return None
            def destroy_client(self, client):
                return None

        return old, module, message_module, service_module, Node

    def test_aimdk_registers_source_and_requires_fresh_state_for_stop(self):
        identity = MapEvidence("slam-" + "a" * 32, "a" * 64, "b" * 64, "c" * 64)
        old, module, message_module, service_module, Node = self._aimdk_fake_modules(add_state=2, enable_state=1)
        try:
            clock, node = Clock(), Node()
            output = AimdkVelocityOutput(node, identity, clock=clock)
            for _ in range(4):
                output.poll()
            self.assertTrue(output.input_source_ready)
            self.assertEqual([request.action.value for request in node.requests], [1001, 2001])
            self.assertEqual(node.requests[0].input_source.name, "lingxi-next")
            state = message_module.McCommonState()
            state.input_source.name, state.fsm_state.current_state = "lingxi-next", 2
            output._state_message(state)
            self.assertTrue(output.ready(identity))
            ticket = Ticket("task", "d" * 32)
            self.assertTrue(output.arm(ticket, identity))
            nonce = "e" * 32
            output.retire(ticket, nonce)
            self.assertGreater(node.publisher.messages[-1].header.stamp.sec, 0)
            self.assertIsNone(output.retirement(nonce))
            clock.value = 10.1
            output._state_message(state)
            self.assertTrue(output.retirement(nonce).output_quiescent)
        finally:
            for key, value in old.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value

    def test_aimdk_enable_rejection_keeps_readiness_false(self):
        identity = MapEvidence("slam-" + "a" * 32, "a" * 64, "b" * 64, "c" * 64)
        old, module, message_module, service_module, Node = self._aimdk_fake_modules(add_state=2, enable_state=2)
        try:
            output = AimdkVelocityOutput(Node(), identity, clock=Clock())
            for _ in range(4):
                output.poll()
            self.assertFalse(output.input_source_ready)
            self.assertEqual(output.registration_error, "aimdk_input_source_enable_rejected")
            self.assertFalse(output.ready(identity))
        finally:
            for key, value in old.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value


if __name__ == "__main__":
    unittest.main()
