"""Bounded ROS 2 evidence and AIMDK output adapters.

This module is intentionally boring at the ROS boundary.  It decodes messages,
checks their timestamps/frames and exposes a single-owner snapshot to
``RosNavigationBackend``.  It does not create a velocity command from a web
request and it never treats a DDS publish as proof that the base stopped.
ROS imports are lazy so all parsers and the map activator remain testable on a
normal development machine.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import threading
import time
from typing import Any, Iterable

from .navfn import CostmapSnapshot, finite
from .physical_bridge import CHECKS, MapEvidence, Observation, Retirement, Ticket, pose


DEFAULT_MINIMUM_Z_METRES = 0.1
DEFAULT_MAXIMUM_Z_METRES = 1.4
MAX_POINT_CLOUD_POINTS = 262_144
MAX_COSTMAP_CELLS = 1_048_576


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def digest_footprint(value: Any) -> str:
    """Return a stable digest for the active base footprint declaration.

    Files are hashed byte-for-byte.  JSON-like declarations are canonicalised
    first; this avoids accepting a different footprint merely because object
    key order changed.
    """
    if isinstance(value, (str, os.PathLike)):
        path = Path(value)
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 64 * 1024:
            raise ValueError("direct_bounded_footprint_file_required")
        return _sha256_bytes(path.read_bytes())
    try:
        return _sha256_bytes(_canonical(value))
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("canonical_footprint_required") from error


def _stamp_parts(value: Any) -> tuple[int, int]:
    if value is None:
        raise ValueError("ros_timestamp_required")
    sec, nano = getattr(value, "sec", None), getattr(value, "nanosec", None)
    if isinstance(value, dict):
        sec, nano = value.get("sec"), value.get("nanosec")
    if type(sec) is not int or type(nano) is not int or sec < 0 or not 0 <= nano < 1_000_000_000:
        raise ValueError("bounded_ros_timestamp_required")
    return sec, nano


def stamp_seconds(value: Any) -> float:
    sec, nano = _stamp_parts(value)
    result = sec + nano / 1_000_000_000
    if not finite(result):
        raise ValueError("finite_ros_timestamp_required")
    return result


def stamp_nanoseconds(value: Any) -> int:
    sec, nano = _stamp_parts(value)
    return sec * 1_000_000_000 + nano


def message_stamp(message: Any) -> float:
    return stamp_seconds(getattr(getattr(message, "header", None), "stamp", None))


def _frame(value: Any) -> str:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_/]{0,119}", value):
        raise ValueError("bounded_ros_frame_required")
    return value


def validate_quaternion(value: Any) -> tuple[float, float, float, float]:
    fields = tuple(getattr(value, name, None) for name in ("x", "y", "z", "w"))
    if any(not finite(item) for item in fields):
        raise ValueError("finite_ros_quaternion_required")
    norm = math.sqrt(sum(item * item for item in fields))
    if not .98 <= norm <= 1.02:
        raise ValueError("normalised_ros_quaternion_required")
    return tuple(item / norm for item in fields)


def yaw_from_quaternion(value: Any) -> float:
    x, y, z, w = validate_quaternion(value)
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def _bounded_float(value: Any, limit: float = 1e6) -> float:
    if not finite(value) or abs(float(value)) > limit:
        raise ValueError("bounded_ros_numeric_value_required")
    return float(value)


@dataclass(frozen=True)
class RosTopics:
    """Topic names are explicit deployment inputs, never inferred by probing."""

    cloud: str = "/lidar/points"
    scan: str | None = None
    odometry: str = "/localization/odometry"
    tf: str = "/tf"
    tf_static: str = "/tf_static"
    global_costmap: str = "/global_costmap/costmap"
    local_costmap: str = "/local_costmap/costmap"
    terrain: str = "/lingxi/terrain/traversable_map"
    # A vendor velocity echo is not a stop acknowledgement in AIMDK v1.1.0.
    # Keep the field only for optional diagnostics; production readiness uses
    # McCommonState below.
    velocity_feedback: str | None = None

    def __post_init__(self):
        for value in self.__dict__.values():
            if value is not None and (type(value) is not str or not value.startswith("/") or len(value) > 160):
                raise ValueError("absolute_ros_topic_names_required")


@dataclass(frozen=True)
class PointCloudXYZ:
    stamp: float
    frame: str
    points: tuple[tuple[float, float, float], ...]


def _field_info(message: Any) -> dict[str, tuple[int, int]]:
    fields = getattr(message, "fields", None)
    if not isinstance(fields, (list, tuple)):
        raise ValueError("point_cloud_fields_required")
    result = {}
    for field in fields:
        name, offset, datatype, count = (getattr(field, key, None)
                                         for key in ("name", "offset", "datatype", "count"))
        if type(name) is not str or type(offset) is not int or type(datatype) is not int or type(count) is not int:
            raise ValueError("bounded_point_cloud_field_required")
        # PointCloud2 commonly carries unrelated fields such as intensity or
        # ring. They are allowed, but XYZ must be scalar float32/float64 fields.
        if name not in ("x", "y", "z"):
            continue
        if name in result or offset < 0 or count != 1 or datatype not in (7, 8):
            raise ValueError("bounded_point_cloud_xyz_field_required")
        result[name] = (offset, datatype)
    if set(("x", "y", "z")) - set(result):
        raise ValueError("xyz_point_cloud_fields_required")
    return result


def parse_point_cloud_xyz(message: Any, *, minimum_z: float = DEFAULT_MINIMUM_Z_METRES,
                          maximum_z: float = DEFAULT_MAXIMUM_Z_METRES,
                          maximum_points: int = MAX_POINT_CLOUD_POINTS) -> PointCloudXYZ:
    """Decode finite XYZ returns from a ROS ``PointCloud2`` without numpy."""
    if not .0 <= minimum_z < maximum_z <= 100 or type(maximum_points) is not int or not 1 <= maximum_points <= MAX_POINT_CLOUD_POINTS:
        raise ValueError("bounded_point_cloud_height_window_required")
    width, height = getattr(message, "width", None), getattr(message, "height", None)
    step, data = getattr(message, "point_step", None), getattr(message, "data", None)
    row_step = getattr(message, "row_step", width * step if type(width) is int and type(step) is int else None)
    if (type(width) is not int or type(height) is not int or width < 1 or height < 1
            or width * height > maximum_points or type(step) is not int or not 12 <= step <= 256
            or type(row_step) is not int or row_step < width * step or row_step > 64 * 1024 * 1024
            or not (isinstance(data, (bytes, bytearray)) or isinstance(data, array) and data.typecode == "B"
                    or isinstance(data, memoryview) and data.ndim == 1 and data.itemsize == 1 and data.c_contiguous)
            or len(data) < height * row_step):
        raise ValueError("bounded_point_cloud_layout_required")
    fields = _field_info(message)
    endian = ">" if bool(getattr(message, "is_bigendian", False)) else "<"
    points = []
    raw = memoryview(data)
    for index in range(width * height):
        row, column = divmod(index, width)
        base = row * row_step + column * step
        xyz = []
        for name in ("x", "y", "z"):
            offset, datatype = fields[name]
            size, fmt = (8, "d") if datatype == 8 else (4, "f")
            if offset + size > step:
                raise ValueError("point_cloud_field_outside_point_step")
            xyz.append(struct.unpack_from(endian + fmt, raw, base + offset)[0])
        if all(finite(item) for item in xyz) and all(abs(item) <= 500 for item in xyz) and minimum_z <= xyz[2] <= maximum_z:
            points.append(tuple(float(item) for item in xyz))
    if not points:
        raise ValueError("fresh_finite_point_cloud_required")
    return PointCloudXYZ(message_stamp(message), _frame(getattr(getattr(message, "header", None), "frame_id", None)), tuple(points))


@dataclass(frozen=True)
class RosCostmap:
    stamp: float
    frame: str
    width: int
    height: int
    resolution: float
    origin_x: float
    origin_y: float
    data: bytes

    @property
    def sha256(self) -> str:
        return _sha256_bytes(_canonical({"frame": self.frame, "width": self.width, "height": self.height,
                                         "resolution": self.resolution, "origin": [self.origin_x, self.origin_y]}) + b"\n" + self.data)


def parse_occupancy_grid(message: Any, *, expected_frame: str | None = None) -> RosCostmap:
    info = getattr(message, "info", None)
    width, height = getattr(info, "width", None), getattr(info, "height", None)
    resolution = getattr(info, "resolution", None)
    data = getattr(message, "data", None)
    if (type(width) is not int or type(height) is not int or not 1 <= width <= 2048 or not 1 <= height <= 2048
            or width * height > MAX_COSTMAP_CELLS or not finite(resolution) or not .005 <= resolution <= 1
            or not (isinstance(data, (bytes, bytearray, tuple, list)) or isinstance(data, array) and data.typecode == "b")
            or len(data) != width * height):
        raise ValueError("bounded_occupancy_grid_required")
    frame = _frame(getattr(getattr(message, "header", None), "frame_id", None))
    if expected_frame is not None and frame != expected_frame:
        raise ValueError("occupancy_grid_frame_mismatch")
    if any(type(value) is not int or not -1 <= value <= 100 for value in data):
        raise ValueError("bounded_occupancy_values_required")
    values = bytes((value & 0xff for value in data))
    origin = getattr(info, "origin", None)
    position = getattr(origin, "position", None)
    orientation = getattr(origin, "orientation", None)
    validate_quaternion(orientation)
    return RosCostmap(message_stamp(message), frame, width, height, float(resolution),
                      _bounded_float(getattr(position, "x", None)), _bounded_float(getattr(position, "y", None)), values)


def parse_odometry(message: Any, *, map_frame: str, base_frame: str) -> tuple[float, dict]:
    if _frame(getattr(getattr(message, "header", None), "frame_id", None)) != map_frame:
        raise ValueError("odometry_map_frame_mismatch")
    if _frame(getattr(message, "child_frame_id", None)) != base_frame:
        raise ValueError("odometry_base_frame_mismatch")
    value = getattr(getattr(message, "pose", None), "pose", None)
    position, orientation = getattr(value, "position", None), getattr(value, "orientation", None)
    result = pose({"x": _bounded_float(getattr(position, "x", None), 1000),
                   "y": _bounded_float(getattr(position, "y", None), 1000),
                   "yaw": yaw_from_quaternion(orientation)})
    return message_stamp(message), result


def _validated_transforms(message: Any) -> tuple:
    transforms = getattr(message, "transforms", None)
    if not isinstance(transforms, (list, tuple)) or not 1 <= len(transforms) <= 128:
        raise ValueError("bounded_tf_transforms_required")
    for item in transforms:
        header, child = getattr(item, "header", None), getattr(item, "child_frame_id", None)
        parent = _frame(getattr(header, "frame_id", None))
        child = _frame(child)
        if parent == child:
            raise ValueError("distinct_tf_parent_and_child_required")
        stamp_nanoseconds(getattr(header, "stamp", None))
        transform = getattr(item, "transform", None)
        validate_quaternion(getattr(transform, "rotation", None))
        translation = getattr(transform, "translation", None)
        for name in ("x", "y", "z"):
            _bounded_float(getattr(translation, name, None), 1000)
    return tuple(transforms)


def validate_tf_message(message: Any, *, map_frame: str, base_frame: str, sensor_frame: str | None = None) -> float:
    transforms = _validated_transforms(message)
    newest = max(message_stamp(item) for item in transforms)
    edges = {(item.header.frame_id, item.child_frame_id) for item in transforms}

    def connected(source: str, target: str) -> bool:
        pending, seen = [source], {source}
        while pending:
            current = pending.pop()
            if current == target:
                return True
            for parent, child in edges:
                if parent == current and child not in seen:
                    seen.add(child)
                    pending.append(child)
        return False

    if not connected(map_frame, base_frame) or sensor_frame is not None and not connected(base_frame, sensor_frame):
        raise ValueError("required_tf_chain_missing")
    return newest


class FileMapActivator:
    """Load a map evidence record without trusting request-supplied hashes."""

    def __init__(self, root: str | os.PathLike, *, calibration_name="calibration.json", footprint_name="footprint.json"):
        self.root = Path(root)
        if not self.root.is_absolute() or self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("direct_saved_map_directory_required")
        self.calibration_name, self.footprint_name = calibration_name, footprint_name

    def _file(self, directory: Path, name: str) -> Path:
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("direct_saved_map_artifact_required")
        return path

    def _receipt(self, directory: Path) -> Path | None:
        for name in ("receipt.json", "map-ready.json", "receipt"):
            path = directory / name
            if path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_size > 64 * 1024:
                raise ValueError("bounded_saved_map_receipt_required")
            return path
        return None

    def _directory_for(self, map_id: str) -> Path:
        direct = self.root / map_id
        if direct.is_dir() and not direct.is_symlink() and self._receipt(direct) is not None:
            return direct

        # The catalog stores the map id in map-ready.json's session field, not
        # necessarily in the directory name. Only inspect direct child folders.
        root_receipt = self._receipt(self.root)
        if root_receipt is not None:
            return self.root
        try:
            children = sorted(self.root.iterdir(), key=lambda item: item.name)
        except OSError as error:
            raise ValueError("saved_map_directory_missing") from error
        for child in children[:32]:
            if child.is_symlink() or not child.is_dir():
                continue
            receipt = self._receipt(child)
            if receipt is None:
                continue
            try:
                value = json.loads(receipt.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and value.get("session") == map_id[5:]:
                return child
        raise ValueError("saved_map_directory_missing")

    def evidence(self, map_id: str) -> MapEvidence:
        if type(map_id) is not str or re.fullmatch(r"slam-[a-f0-9]{32}", map_id) is None:
            raise ValueError("saved_map_id_required")
        directory = self._directory_for(map_id)
        receipt = self._receipt(directory)
        if receipt is None:
            raise ValueError("saved_map_receipt_required")
        calibration = self._file(directory, self.calibration_name)
        footprint = self._file(directory, self.footprint_name)
        return MapEvidence(map_id, _sha256_bytes(receipt.read_bytes()),
                           _sha256_bytes(calibration.read_bytes()), _sha256_bytes(footprint.read_bytes()))

    def activate_map(self, map_id: str, initial_pose: dict | None = None) -> MapEvidence:
        evidence = self.evidence(map_id)
        if initial_pose is not None:
            pose(initial_pose)
        return evidence


class RosSensorEvidence:
    """Single-executor evidence with acquisition age and exact-time tf2 queries.

    Receive time cannot renew an old sample. A ROS/steady clock discontinuity or
    changed TF topology/calibration latches this owner closed to admission.
    """

    def __init__(self, node: Any, identity: MapEvidence, topics: RosTopics, *, map_frame="lingxi_saved_map",
                 base_frame="base_link", sensor_frame="lidar_chest_front", footprint: Any,
                 clock=time.monotonic, maximum_age=.5):
        if type(identity) is not MapEvidence or type(topics) is not RosTopics or not callable(clock) or not finite(maximum_age) or not .05 <= maximum_age <= 2:
            raise ValueError("typed_ros_sensor_evidence_configuration_required")
        self.owner, self.node, self.identity, self.topics = threading.get_ident(), node, identity, topics
        self.map_frame, self.base_frame, self.sensor_frame = map(_frame, (map_frame, base_frame, sensor_frame))
        if len({self.map_frame, self.base_frame, self.sensor_frame}) != 3:
            raise ValueError("distinct_map_base_and_sensor_frames_required")
        self.clock, self.maximum_age, self.footprint_sha256 = clock, maximum_age, digest_footprint(footprint)
        self.latest_cloud = self.latest_odom = None
        self.latest_global = self.latest_local = self.latest_terrain = None
        self.receipts, self.previous_stamps, self.tf_parents = {}, {}, {}
        self.closed = self.clock_failed = self.tf_failed = False
        self.anchor = None
        self.last_clock = None
        self.subscriptions = []
        try:
            from rclpy.duration import Duration
            from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
            from rclpy.time import Time
            from nav_msgs.msg import OccupancyGrid, Odometry
            from sensor_msgs.msg import PointCloud2
            from tf2_msgs.msg import TFMessage
            from tf2_ros import Buffer
            self.ros_time_type = Time
            self.tf_buffer = Buffer(cache_time=Duration(seconds=2.))
            sensor_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
            grid_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE)
            tf_qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.BEST_EFFORT)
            static_qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.RELIABLE,
                                    durability=DurabilityPolicy.TRANSIENT_LOCAL)
            self._sample_clock()
            bindings = ((PointCloud2, topics.cloud, self._cloud, sensor_qos),
                        (Odometry, topics.odometry, self._odom, sensor_qos),
                        (TFMessage, topics.tf, self._tf, tf_qos),
                        (TFMessage, topics.tf_static, self._tf_static, static_qos),
                        (OccupancyGrid, topics.global_costmap, self._global, grid_qos),
                        (OccupancyGrid, topics.local_costmap, self._local, grid_qos),
                        (OccupancyGrid, topics.terrain, self._terrain, grid_qos))
            for kind, name, callback, qos in bindings:
                self.subscriptions.append(node.create_subscription(kind, name, callback, qos))
        except BaseException:
            self.close()
            raise

    def _owned(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("single_ros_sensor_owner_required")

    def _sample_clock(self):
        self._owned()
        if self.closed or self.clock_failed:
            raise RuntimeError("ros_sensor_clock_unavailable")
        now = self.clock()
        ros = self.node.get_clock().now().nanoseconds
        if not finite(now) or now < 0 or type(ros) is not int or ros <= 0:
            self.clock_failed = True
            raise RuntimeError("initialised_ros_and_steady_clock_required")
        if self.anchor is None:
            self.anchor = (now, ros)
        previous, source = self.anchor
        if ((self.last_clock is not None and (now < self.last_clock[0] or ros < self.last_clock[1]))
                or abs((ros - source) / 1e9 - (now - previous)) > .05):
            self.clock_failed = True
            raise RuntimeError("ros_sensor_clock_discontinuity")
        self.last_clock = now, ros
        return now, ros

    def _receipt(self, stamp, now, ros):
        age = (ros - stamp) / 1e9
        if stamp <= 0 or not 0 <= age <= self.maximum_age:
            raise ValueError("current_sensor_acquisition_stamp_required")
        return (stamp, min(now - age, self.anchor[0] + (stamp - self.anchor[1]) / 1e9))

    def _accept(self, name, message, parser):
        self._owned()
        if self.closed:
            return False
        try:
            value = parser(message)
            stamp = stamp_nanoseconds(message.header.stamp)
            now, ros = self._sample_clock()
            receipt = self._receipt(stamp, now, ros)
            if stamp <= self.previous_stamps.get(name, -1):
                return False
            self.previous_stamps[name] = stamp
            self.receipts[name] = receipt
            setattr(self, "latest_" + name, value)
            return True
        except (ValueError, RuntimeError, AttributeError, TypeError, OverflowError):
            self.receipts.pop(name, None)
            setattr(self, "latest_" + name, None)
            return False

    def _cloud(self, message):
        def parse(value):
            cloud = parse_point_cloud_xyz(value)
            if cloud.frame != self.sensor_frame:
                raise ValueError("point_cloud_sensor_frame_mismatch")
            return cloud
        return self._accept("cloud", message, parse)

    def _odom(self, message):
        return self._accept("odom", message, lambda value: parse_odometry(
            value, map_frame=self.map_frame, base_frame=self.base_frame))

    def _tf(self, message):
        return self._transforms(message, static=False)

    def _tf_static(self, message):
        return self._transforms(message, static=True)

    def _transforms(self, message, *, static):
        self._owned()
        if self.closed or self.tf_failed or self.clock_failed:
            return False
        try:
            now, ros = self._sample_clock()
            transforms = _validated_transforms(message)
            parents = dict(self.tf_parents)
            for item in transforms:
                child, parent = item.child_frame_id, item.header.frame_id
                transform = item.transform
                geometry = None
                if static:
                    geometry = tuple(getattr(transform.translation, key) for key in ("x", "y", "z")) + tuple(
                        getattr(transform.rotation, key) for key in ("x", "y", "z", "w"))
                declaration = (parent, static, geometry)
                if child in parents and parents[child] != declaration:
                    raise ValueError("unchanged_tf_topology_and_static_calibration_required")
                parents[child] = declaration
                if len(parents) > 256:
                    raise ValueError("bounded_tf_frame_inventory_required")
                ancestor, seen = parent, {child}
                while ancestor in parents:
                    if ancestor in seen:
                        raise ValueError("acyclic_tf_tree_required")
                    seen.add(ancestor)
                    ancestor = parents[ancestor][0]
                if not static:
                    self._receipt(stamp_nanoseconds(item.header.stamp), now, ros)
            for item in transforms:
                setter = self.tf_buffer.set_transform_static if static else self.tf_buffer.set_transform
                setter(item, "lingxi_next_validated_tf")
            self.tf_parents = parents
            return True
        except Exception:
            self.tf_failed = True
            self.tf_buffer.clear()
            return False

    def _global(self, message):
        return self._accept("global", message, lambda value: parse_occupancy_grid(value, expected_frame=self.map_frame))

    def _local(self, message):
        return self._accept("local", message, lambda value: parse_occupancy_grid(value, expected_frame=self.map_frame))

    def _terrain(self, message):
        return self._accept("terrain", message, lambda value: parse_occupancy_grid(value, expected_frame=self.map_frame))

    def _tf_ready(self):
        if self.tf_failed:
            return False
        for name in ("cloud", "odom"):
            receipt = self.receipts.get(name)
            if receipt is None:
                return False
            when = self.ros_time_type(nanoseconds=receipt[0])
            for target, source in ((self.map_frame, self.base_frame), (self.base_frame, self.sensor_frame)):
                if not self.tf_buffer.can_transform(target, source, when):
                    return False
        return True

    def observe(self) -> Observation:
        self._owned()
        now, ros = self._sample_clock()
        def fresh(name):
            receipt = self.receipts.get(name)
            return (receipt is not None and 0 <= now - receipt[1] <= self.maximum_age
                    and 0 <= (ros - receipt[0]) / 1e9 <= self.maximum_age)
        cloud_ok = fresh("cloud")
        localization_ok = fresh("odom")
        tf_ok = self._tf_ready()
        global_ok = fresh("global")
        local_ok = fresh("local")
        terrain_ok = fresh("terrain")
        costmap_ok = global_ok and local_ok
        checks = {"nav2Lifecycle": False, "localization": localization_ok and cloud_ok,
                  "tf": tf_ok, "costmap": costmap_ok, "terrain": terrain_ok,
                  "footprint": self.footprint_sha256 == self.identity.footprintSha256,
                  "velocityOutput": False}
        if not all(checks[key] for key in ("localization", "tf", "costmap", "terrain", "footprint")):
            raise RuntimeError("current_ros_sensor_evidence_unavailable")
        acquired_at = min(receipt[1] for receipt in self.receipts.values())
        return Observation(self.identity, acquired_at, self.latest_odom[1], checks, False)

    def close(self):
        self.closed = True
        for subscription in getattr(self, "subscriptions", ()):
            try:
                self.node.destroy_subscription(subscription)
            except Exception:
                pass
        self.subscriptions = []
        self.receipts.clear()
        if hasattr(self, "tf_buffer"):
            self.tf_buffer.clear()


class AimdkVelocityOutput:
    """Exclusive AIMDK owner with a state-based, nonce-fenced stop receipt.

    AIMDK v1.1.0 does not echo a command nonce and does not expose a velocity
    feedback message with a stop bit. The adapter therefore registers an
    independent input source, publishes the zero command, and accepts a stop
    only after a *new* ``McCommonState`` reports this source in STABLE or SAFE.
    """

    ADD = 1001
    ENABLE = 2001
    STABLE = 2
    SAFE = 4

    def __init__(self, node: Any, identity: MapEvidence, topic="/aima/mc/locomotion/velocity", *, source="lingxi-next",
                 priority=60, timeout_ms=500, state_topic=None, input_source_service=None,
                 clock=time.monotonic, maximum_age=.3):
        if (type(identity) is not MapEvidence or type(topic) is not str or not topic.startswith("/")
                or type(source) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", source)
                or type(priority) is not int or not 0 <= priority <= 100
                or type(timeout_ms) is not int or not 100 <= timeout_ms <= 60_000
                or not callable(clock) or not finite(maximum_age) or not .05 <= maximum_age <= 2):
            raise ValueError("typed_aimdk_velocity_output_required")
        self.owner, self.node, self.identity, self.topic, self.source = threading.get_ident(), node, identity, topic, source
        self.priority, self.timeout_ms = priority, timeout_ms
        self.clock, self.maximum_age = clock, maximum_age
        state_topic = state_topic or os.getenv("LINGXI_AIMDK_STATE_TOPIC", "/aima/mc/common/state")
        input_source_service = input_source_service or os.getenv(
            "LINGXI_AIMDK_INPUT_SOURCE_SERVICE", "/aimdk_5Fmsgs/srv/SetMcInputSource")
        if (type(state_topic) is not str or not state_topic.startswith("/")
                or type(input_source_service) is not str or not input_source_service.startswith("/")):
            raise ValueError("absolute_aimdk_state_and_service_names_required")
        try:
            from aimdk_msgs.msg import McCommonState, McLocomotionVelocity
            from aimdk_msgs.srv import SetMcInputSource
        except ImportError as error:
            raise RuntimeError("aimdk_velocity_and_state_interfaces_unavailable") from error
        self.message_type, self.state_type, self.service_type = McLocomotionVelocity, McCommonState, SetMcInputSource
        self.publisher = node.create_publisher(McLocomotionVelocity, topic, 1)
        self.state_subscription = node.create_subscription(McCommonState, state_topic, self._state_message, 1)
        self.input_source_client = node.create_client(SetMcInputSource, input_source_service)
        self.diagnostic_feedback_subscription = None
        feedback_topic = os.getenv("LINGXI_AIMDK_VELOCITY_FEEDBACK_TOPIC", "").strip()
        if feedback_topic:
            if not feedback_topic.startswith("/"):
                raise ValueError("absolute_aimdk_feedback_topic_required")
            self.diagnostic_feedback_subscription = node.create_subscription(
                McLocomotionVelocity, feedback_topic, self.on_feedback, 1)
        self.registration_phase = "add"
        self.registration_future = None
        self.registration_error = None
        self.input_source_ready = False
        self.state = None
        self.ticket = None
        self.armed = False
        self.retiring = None
        self.completed_retirement = None
        self.zero_published_at = None
        self.physical_motion_sent = False
        self.feedback = None
        self.closed = False
        self.controller_fault = False
        self.armed_at = self.last_command_at = self.last_command_generated = None
        self.command_sequence = 0
        self.last_velocity = (0., 0., 0.)

    def _owned(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("single_aimdk_output_owner_required")

    def _state_message(self, message):
        self._owned()
        input_source = getattr(message, "input_source", None)
        fsm_state = getattr(message, "fsm_state", None)
        name = getattr(input_source, "name", None)
        current = getattr(fsm_state, "current_state", None)
        if name != self.source or type(current) is not int:
            self.state = None
            return False
        received = self.clock()
        if not finite(received):
            return False
        self.state = (received, current)
        return True

    def _request(self, action_value):
        request = self.service_type.Request()
        action = getattr(request, "action", None)
        input_source = getattr(request, "input_source", None)
        if action is None or input_source is None:
            raise RuntimeError("aimdk_input_source_request_shape_unavailable")
        action.value = action_value
        input_source.name = self.source
        input_source.priority = self.priority
        input_source.timeout = self.timeout_ms
        return request

    @staticmethod
    def _service_succeeded(response):
        response = getattr(response, "response", response)
        state = getattr(response, "state", None)
        value = getattr(state, "value", None)
        return value == 1 or getattr(response, "success", False) is True

    def poll(self):
        """Advance registration without blocking the ROS executor."""
        self._owned()
        if self.closed or self.registration_phase in ("ready", "failed"):
            return
        if self.registration_future is not None:
            if not self.registration_future.done():
                return
            try:
                response = self.registration_future.result()
            except Exception:
                self.registration_error = "aimdk_input_source_request_failed"
                self.registration_phase = "failed"
                self.registration_future = None
                return
            self.registration_future = None
            if self.registration_phase == "add":
                # ADD can legitimately report an existing source. ENABLE is
                # still required before the source is considered ours.
                self.registration_phase = "enable"
                return
            if not self._service_succeeded(response):
                self.registration_error = "aimdk_input_source_enable_rejected"
                self.registration_phase = "failed"
                return
            self.input_source_ready = True
            self.registration_phase = "ready"
            return
        if not self.input_source_client.service_is_ready():
            return
        action = self.ADD if self.registration_phase == "add" else self.ENABLE
        try:
            self.registration_future = self.input_source_client.call_async(self._request(action))
        except Exception:
            self.registration_error = "aimdk_input_source_call_failed"
            self.registration_phase = "failed"

    def _message(self, forward=0., lateral=0., angular=0.):
        msg = self.message_type()
        header = getattr(msg, "header", None)
        if header is not None:
            stamp = getattr(header, "stamp", None)
            try:
                header.stamp = self.node.get_clock().now().to_msg()
            except Exception:
                now = time.time()
                if stamp is not None:
                    stamp.sec = int(now)
                    stamp.nanosec = int((now - int(now)) * 1_000_000_000)
        msg.source = self.source
        msg.forward_velocity, msg.lateral_velocity, msg.angular_velocity = float(forward), float(lateral), float(angular)
        return msg

    def on_feedback(self, message: Any, **_ignored):
        """Record an optional vendor echo for diagnostics only."""
        self._owned()
        stamp = self.clock()
        if not finite(stamp):
            raise ValueError("finite_velocity_feedback_clock_required")
        source = getattr(message, "source", None)
        if source != self.source:
            return False
        velocities = tuple(getattr(message, name, None) for name in ("forward_velocity", "lateral_velocity", "angular_velocity"))
        if any(not finite(value) for value in velocities):
            return False
        self.feedback = (stamp, tuple(float(value) for value in velocities))
        return True

    def ready(self, identity: MapEvidence) -> bool:
        self._owned()
        if (self.closed or identity != self.identity or not self.input_source_ready
                or self.retiring is not None or self.controller_fault):
            return False
        if self.state is None:
            return False
        stamp, current = self.state
        return (0 <= self.clock() - stamp <= self.maximum_age
                and current in (self.STABLE, self.SAFE))

    def arm(self, ticket: Ticket, identity: MapEvidence) -> bool:
        self._owned()
        if type(ticket) is not Ticket or self.armed or not self.ready(identity):
            return False
        self.ticket = ticket
        self.armed = True
        self.completed_retirement = None
        self.armed_at = self.clock()
        self.last_command_at = self.last_command_generated = None
        self.command_sequence = 0
        self.last_velocity = (0., 0., 0.)
        return True

    def fence_controller(self):
        """Invalidate before zero publication; an explicit stop must recover it."""
        self._owned()
        if not self.closed and not self.controller_fault:
            self.armed, self.controller_fault = False, True
            self.publisher.publish(self._message())

    def command(self, command, limits, observation):
        from .physical_bridge import Observation
        from .physical_velocity import ControllerCommand, VelocityLimits
        self._owned()
        # Stale generations are inert, including after a different task is armed.
        if type(command) is ControllerCommand and command.ticket != self.ticket:
            return False
        if type(command) is ControllerCommand and (self.closed or not self.armed or self.retiring is not None):
            return False
        try:
            now = self.clock()
            watchdog_origin = self.armed_at if self.last_command_generated is None else self.last_command_generated
            watchdog_timeout = limits.first_command_timeout if self.last_command_generated is None else limits.command_age
            if (type(command) is not ControllerCommand or type(limits) is not VelocityLimits
                    or not self.armed or not self.ready(command.identity)
                    or limits.identity != self.identity or command.identity != self.identity
                    or type(observation) is not Observation or observation.identity != self.identity
                    or not all(observation.checks.values())
                    or not 0 <= now - observation.observed_at <= .3
                    or not self.armed_at <= command.generated_at <= now
                    or not 0 <= now - watchdog_origin <= watchdog_timeout
                    or not 0 <= now - command.generated_at <= limits.command_age
                    or command.sequence <= self.command_sequence
                    or (self.last_command_generated is not None
                        and command.generated_at <= self.last_command_generated)):
                raise ValueError('current_generation_controller_evidence_required')
            origin = self.armed_at if self.last_command_at is None else self.last_command_at
            generated_origin = self.armed_at if self.last_command_generated is None else self.last_command_generated
            values = limits.bounded((command.forward, command.lateral, command.angular), self.last_velocity,
                                    min(now - origin, command.generated_at - generated_origin))
            self.command_sequence, self.last_command_at = command.sequence, now
            self.last_command_generated, self.last_velocity = command.generated_at, values
            # This records an attempted nonzero dispatch, not measured robot motion.
            self.physical_motion_sent = self.physical_motion_sent or any(value != 0. for value in values)
            self.publisher.publish(self._message(*values))
            return True
        except Exception:
            self.fence_controller()
            raise

    def retire(self, ticket: Ticket | None, nonce: str):
        self._owned()
        if (self.closed or type(nonce) is not str or re.fullmatch(r"[a-f0-9]{32}", nonce) is None
                or ticket != self.ticket or self.retiring is not None):
            raise ValueError("velocity_retirement_ticket_required")
        # Fence this generation before the external zero publication, even if it fails.
        self.armed = False
        self.completed_retirement = None
        self.retiring = nonce
        self.zero_published_at = self.clock()
        try:
            self.publisher.publish(self._message())
        except Exception:
            self.zero_published_at = None
            raise

    def retirement(self, nonce: str) -> Retirement | None:
        self._owned()
        if self.closed:
            return None
        proof = self.completed_retirement
        if (self.retiring is None and proof is not None and proof.nonce == nonce
                and proof.ticket == self.ticket and 0 <= self.clock() - proof.observed_at <= self.maximum_age):
            return proof
        if self.retiring != nonce or self.state is None or self.zero_published_at is None:
            return None
        stamp, current = self.state
        if (stamp <= self.zero_published_at or current not in (self.STABLE, self.SAFE)
                or self.clock() - stamp < 0 or self.clock() - stamp > self.maximum_age):
            return None
        return Retirement(nonce, self.identity, self.ticket, stamp, True)

    def complete_retirement(self, nonce: str) -> Retirement | None:
        """Release only after the backend has also observed action termination.

        Keep the original receipt briefly available for the bridge's validation;
        an explicit new arm invalidates it instead of relabeling an old nonce.
        """
        self._owned()
        proof = self.retirement(nonce)
        if proof is not None:
            self.completed_retirement = proof
            self.retiring = self.zero_published_at = None
            self.controller_fault = False
        return proof

    def close(self):
        self._owned()
        self.closed = True
        try:
            self.publisher.publish(self._message())
        finally:
            try:
                self.node.destroy_subscription(self.state_subscription)
            except Exception:
                pass
            if self.diagnostic_feedback_subscription is not None:
                try:
                    self.node.destroy_subscription(self.diagnostic_feedback_subscription)
                except Exception:
                    pass
            try:
                self.node.destroy_client(self.input_source_client)
            except Exception:
                pass
            if self.registration_future is not None:
                try:
                    self.registration_future.cancel()
                except Exception:
                    pass
            self.ticket = self.retiring = self.completed_retirement = None
            self.armed = False
