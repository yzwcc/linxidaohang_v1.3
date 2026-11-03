"""Offline contracts for the ROS point-cloud and AIMDK boundary adapters."""

from array import array
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

    def test_ros_generated_byte_arrays_and_nonbyte_layout_rejection(self):
        message = point_cloud()
        message.data = array("B", message.data)
        self.assertEqual(4, len(parse_point_cloud_xyz(message).points))
        message.data = array("I", [0] * 48)
        with self.assertRaisesRegex(ValueError, "point_cloud_layout"):
            parse_point_cloud_xyz(message)
        info = types.SimpleNamespace(width=2, height=2, resolution=.05,
            origin=types.SimpleNamespace(position=types.SimpleNamespace(x=0., y=0.), orientation=quaternion()))
        grid = types.SimpleNamespace(header=header("map"), info=info, data=array("b", [0, 100, -1, 50]))
        self.assertEqual(bytes((0, 100, 255, 50)), parse_occupancy_grid(grid).data)
        grid.data = array("i", [0, 100, -1, 50])
        with self.assertRaisesRegex(ValueError, "occupancy_grid"):
            parse_occupancy_grid(grid)

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

    def test_map_named_directory_and_root_still_require_matching_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            map_id = 'slam-' + 'a' * 32
            child = root / map_id
            child.mkdir()
            for folder in (root, child):
                (folder / 'map-ready.json').write_text(json.dumps(dict(session='b' * 32)))
                (folder / 'calibration.json').write_bytes(b'calibration')
                (folder / 'footprint.json').write_bytes(b'footprint')
            with self.assertRaisesRegex(ValueError, 'directory_missing'):
                FileMapActivator(root).evidence(map_id)

    def test_catalog_after_32_entries_is_found_but_duplicate_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(40):
                (root / ('empty-%02d' % index)).mkdir()
            child = root / 'z-map'
            child.mkdir()
            (child / 'map-ready.json').write_text(json.dumps(dict(session='a' * 32)))
            (child / 'calibration.json').write_bytes(b'calibration')
            (child / 'footprint.json').write_bytes(b'footprint')
            self.assertEqual('slam-' + 'a' * 32, FileMapActivator(root).evidence('slam-' + 'a' * 32).mapId)
            duplicate = root / 'duplicate'
            duplicate.mkdir()
            (duplicate / 'map-ready.json').write_bytes((child / 'map-ready.json').read_bytes())
            with self.assertRaisesRegex(ValueError, 'unambiguous'):
                FileMapActivator(root).evidence('slam-' + 'a' * 32)

    def test_localization_binding_requires_reloaded_map_profile_and_points(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'calibration.json').write_bytes(b'calibration')
            (root / 'footprint.json').write_bytes(b'footprint')
            value = dict(schemaVersion=3, mappingEngine='glim-full-async-v1', session='a' * 32,
                inputMode='live', freshProcessReloadPassed=True, savedPointsSha256='d' * 64,
                sensorProvenance=dict(profileSha256='e' * 64))
            receipt = root / 'map-ready.json'
            receipt.write_text(json.dumps(value))
            self.assertEqual(dict(points_sha256='d' * 64, profile_sha256='e' * 64),
                FileMapActivator(root).localization_binding('slam-' + 'a' * 32))
            for changed in (dict(value, schemaVersion=True), dict(value, freshProcessReloadPassed=False),
                            dict(value, savedPointsSha256='invalid'), dict(value, sensorProvenance={})):
                receipt.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    FileMapActivator(root).localization_binding('slam-' + 'a' * 32)
            receipt.write_text('{"session":"' + 'b' * 32 + '",' + json.dumps(value)[1:])
            with self.assertRaises(ValueError):
                FileMapActivator(root).evidence('slam-' + 'a' * 32)

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
            self.assertFalse(output.arm(Ticket("other", "f" * 32), identity))
            nonce = "e" * 32
            output.retire(ticket, nonce)
            self.assertGreater(node.publisher.messages[-1].header.stamp.sec, 0)
            self.assertIsNone(output.retirement(nonce))
            clock.value = 10.1
            output._state_message(state)
            self.assertTrue(output.retirement(nonce).output_quiescent)
            # Reading a physical proof alone must not release the output owner.
            self.assertFalse(output.ready(identity))
            proof = output.complete_retirement(nonce)
            self.assertEqual(proof, output.retirement(nonce))
            self.assertTrue(output.ready(identity))
            self.assertTrue(output.arm(Ticket("resumed", "f" * 32), identity))
            self.assertIsNone(output.retirement(nonce))
            with self.assertRaisesRegex(ValueError, "retirement_ticket"):
                output.retire(ticket, "f" * 32)
        finally:
            for key, value in old.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value

    def test_aimdk_startup_stop_and_repeated_confirmed_stop_keep_original_ticket(self):
        identity = MapEvidence("slam-" + "a" * 32, "a" * 64, "b" * 64, "c" * 64)
        old, _, messages, _, Node = self._aimdk_fake_modules()
        try:
            clock, node = Clock(), Node()
            output = AimdkVelocityOutput(node, identity, clock=clock)
            for _ in range(4):
                output.poll()
            state = messages.McCommonState()
            state.input_source.name, state.fsm_state.current_state = "lingxi-next", 2
            output.retire(None, "1" * 32)
            with self.assertRaisesRegex(ValueError, "retirement_ticket"):
                output.retire(None, "2" * 32)
            clock.value += .01
            output._state_message(state)
            self.assertIsNone(output.complete_retirement("2" * 32))
            self.assertIsNotNone(output.complete_retirement("1" * 32))
            self.assertIsNone(output.retiring)
            self.assertTrue(output.ready(identity))
            ticket = Ticket("first", "2" * 32)
            self.assertTrue(output.arm(ticket, identity))
            for nonce in ("3" * 32, "4" * 32):
                output.retire(ticket, nonce)
                clock.value += .01
                output._state_message(state)
                proof = output.complete_retirement(nonce)
                self.assertEqual(ticket, proof.ticket)
            clock.value += .31
            self.assertIsNone(output.retirement("4" * 32))
            self.assertFalse(output.ready(identity))
            self.assertFalse(output.arm(Ticket("expired", "5" * 32), identity))
            output.close()
            self.assertIsNone(output.retirement("4" * 32))
        finally:
            for key, value in old.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value

    def test_aimdk_failed_zero_publication_cannot_complete_retirement(self):
        identity = MapEvidence("slam-" + "a" * 32, "a" * 64, "b" * 64, "c" * 64)
        old, _, messages, _, Node = self._aimdk_fake_modules()
        try:
            clock, node = Clock(), Node()
            output = AimdkVelocityOutput(node, identity, clock=clock)
            for _ in range(4):
                output.poll()
            def failed_publish(message):
                raise RuntimeError("synthetic_publish_failure")
            node.publisher.publish = failed_publish
            with self.assertRaisesRegex(RuntimeError, "synthetic_publish_failure"):
                output.retire(None, "1" * 32)
            state = messages.McCommonState()
            state.input_source.name, state.fsm_state.current_state = "lingxi-next", 2
            clock.value += .01
            output._state_message(state)
            self.assertIsNone(output.complete_retirement("1" * 32))
            self.assertFalse(output.ready(identity))
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
