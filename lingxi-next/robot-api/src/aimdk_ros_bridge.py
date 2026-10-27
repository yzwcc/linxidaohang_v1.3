#!/usr/bin/env python3
"""Narrow local HTTP adapter for the documented X2 AIMDK ROS 2 interfaces."""

from __future__ import annotations

import json
import hashlib
import hmac
import math
import os
import re
import sqlite3
import struct
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import rclpy
from nav_msgs.msg import OccupancyGrid, Odometry
from geometry_msgs.msg import Pose
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import LaserScan, PointCloud2
from std_msgs.msg import Header, String
from aimdk_msgs.srv import GetStoredMapByName
try:
    from aimdk_msgs.srv import (
        ExecuteActionResource,
        GetMcPresetMotionState,
        GetRobotResources,
        PlayEmoji,
        SetMcInputSource,
        SetMcPresetMotion,
    )

    ACTION_SERVICES_AVAILABLE = True
except ImportError:
    ExecuteActionResource = None
    GetMcPresetMotionState = None
    GetRobotResources = None
    PlayEmoji = None
    SetMcInputSource = None
    SetMcPresetMotion = None
    ACTION_SERVICES_AVAILABLE = False
try:
    from rclpy.duration import Duration
    from rclpy.time import Time
    from tf2_ros import Buffer, TransformListener
except ImportError:
    Buffer = TransformListener = None
try:
    from aimdk_msgs.msg import PncTaskRequest, PncTaskResponse, TaskMasterState

    NAV_TYPES_AVAILABLE = True
except ImportError:
    PncTaskRequest = None
    PncTaskResponse = None
    TaskMasterState = None
    NAV_TYPES_AVAILABLE = False


PORT = int(os.getenv("LINGXI_AIMDK_BRIDGE_PORT", "18082"))
TOKEN = os.getenv("LINGXI_AIMDK_BRIDGE_TOKEN", "")
MAP_DIR = Path(os.getenv("AIMDK_MAP_DIR", "/agibot/data/var/MapManagerModule"))
MAP_TOPIC = os.getenv("AIMDK_MAP_TOPIC", "").strip()
SCAN_TOPIC = os.getenv("AIMDK_SCAN_TOPIC", "").strip()
POINTCLOUD_TOPIC = os.getenv("AIMDK_POINTCLOUD_TOPIC", "").strip()
LIDAR_FIXED_FRAME = os.getenv("AIMDK_LIDAR_FIXED_FRAME", "map").strip()
BASE_FRAME = os.getenv("AIMDK_BASE_FRAME", "base_link").strip()
ODOM_TOPIC = os.getenv("AIMDK_ODOM_TOPIC", "/slam/lidar_odom").strip()
INTEGRATED_COMMAND_TOPIC = "/integrated_command"
MAP_SERVICE = "/aimdk_5Fmsgs/srv/GetStoredMapByName"
NAV_REQUEST_TOPIC = "/aima/te/pnc_task_request"
NAV_RESPONSE_TOPIC = "/aima/pnc/pnc_task_response"
TASK_MASTER_TOPIC = "/task_master/state"
MAX_CELLS = 160_000
MAX_MAPS = 100
MAX_LIDAR_POINTS = 8000
LIDAR_TF_TIMEOUT_SECONDS = 0.05
MAPPING_STATE = 2
LOCALIZATION_STATE = 3
MAX_SAFE_MAP_ID = 9_007_199_254_740_991
RELOCALIZATION_TIMEOUT = 30.0
RELOCALIZATION_POSE_DELAY = 1.0
SERVICE_TIMEOUT_SECONDS = 3.0
MOTION_STATE_TIMEOUT_SECONDS = 60.0
MOTION_STATE_POLL_SECONDS = 0.2
RESOURCE_REFRESH_SECONDS = 15.0
RESOURCE_STALE_SECONDS = 60.0
ACTION_INTERLOCK_FILE = Path(os.getenv(
    "LINGXI_ACTION_INTERLOCK_FILE",
    str(Path(os.getenv("LINGXI_DATA_DIR", "/var/lib/lingxi-next")) / "action-interlock.json"),
))
MOTION_INPUT_SOURCE = "node.preset_motion"
MOTION_INPUT_PRIORITY = 40
MOTION_INPUT_TIMEOUT_MS = 1000
PRESET_MOTIONS = {
    "举手": (1001, 2),
    "挥手": (1002, 2),
    "握手": (1003, 2),
    "飞吻": (1004, 2),
    "点头": (4001, 4),
    "摇头": (4002, 4),
}
EXPRESSIONS = {
    "眨眼": 1,
    "平静": 10,
    "卖萌": 30,
    "开心": 90,
    "悲伤": 110,
    "同情": 120,
    "疑惑": 130,
    "震惊": 140,
    "严肃": 160,
    "思考": 170,
}


def valid_map_name(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(
        r"[\w\u4e00-\u9fff][\w\u4e00-\u9fff .-]{0,99}", value, re.UNICODE
    ) is not None


def read_map_catalog(directory: Path) -> list[dict[str, Any]]:
    """Read the vendor catalog without creating or modifying a database."""
    database = directory / "map.db"
    if not database.exists():
        return [{"id": file.stem, "name": file.stem}
                for file in sorted(directory.glob("*.yaml"))[:MAX_MAPS] if valid_map_name(file.stem)]
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)) as connection:
            connection.execute("PRAGMA query_only=ON")
            rows = connection.execute("SELECT map_id, map_name FROM map ORDER BY map_id DESC LIMIT ?",
                                      (MAX_MAPS,)).fetchall()
    except sqlite3.Error as error:
        raise AdapterError("无法只读查询 AIMDK map.db，请检查权限和 map 表结构") from error
    result = []
    names = set()
    for map_id, name in rows:
        if (isinstance(map_id, bool) or not isinstance(map_id, int)
                or not 0 < map_id <= MAX_SAFE_MAP_ID or not valid_map_name(name) or name in names):
            raise AdapterError("AIMDK 地图登记含无效 ID、名称或重名，未展示不确定地图")
        names.add(name)
        result.append({"id": name, "name": name, "mapId": map_id})
    return result


def relocalization_pixels(directory: Path, map_id: int, pose: Any) -> tuple[float, float, float]:
    """X2 uses image pixels for position and a ROS quaternion for orientation."""
    if isinstance(map_id, bool) or not isinstance(map_id, int) or not 0 < map_id <= MAX_SAFE_MAP_ID:
        raise AdapterError("地图 ID 无效", 400)
    target = (directory / str(map_id) / "grid_map_info.txt").resolve()
    if not target.is_relative_to(directory.resolve()):
        raise AdapterError("地图信息路径越界", 400)
    try:
        if target.stat().st_size > 4096:
            raise ValueError("Oversized grid metadata")
        lines = target.read_text(encoding="utf-8").strip().splitlines()
        # Accept the vendor's three numeric lines, including optional field labels.
        values = [re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", line) for line in lines]
        if len(values) != 3 or [len(line) for line in values] != [1, 2, 2]:
            raise ValueError("Unsupported grid metadata")
        scale, origin_u, origin_v, width, height = [float(value) for line in values for value in line]
        if (not all(math.isfinite(value) for value in (scale, origin_u, origin_v, width, height))
                or not 0 < scale <= 10_000 or not 1 <= width <= 100_000 or not 1 <= height <= 100_000
                or not width.is_integer() or not height.is_integer()
                or not 0 <= origin_u < width or not 0 <= origin_v < height):
            raise ValueError("Invalid grid metadata")
    except (OSError, ValueError) as error:
        raise AdapterError("无法读取三行 grid_map_info.txt，请核对地图 ID、权限和格式") from error
    if pose is None:
        return origin_u, origin_v, 0.0
    if not isinstance(pose, dict) or set(pose) != {"x", "y", "yaw"}:
        raise AdapterError("初始位姿格式无效", 400)
    if any(isinstance(pose[key], bool) or not isinstance(pose[key], (int, float))
           or not math.isfinite(pose[key]) for key in pose):
        raise AdapterError("初始位姿必须为有限数值", 400)
    if abs(pose["yaw"]) > 2 * math.pi or any(abs(pose[key]) > 1000 for key in ("x", "y")):
        raise AdapterError("初始位姿超出支持范围", 400)
    u, v = origin_u + pose["x"] * scale, origin_v - pose["y"] * scale
    if not 0 <= u < width or not 0 <= v < height:
        raise AdapterError("初始位姿位于地图范围之外", 400)
    return u, v, float(pose["yaw"])


class AdapterError(Exception):
    def __init__(self, message: str, status: int = 503, uncertain: bool = False):
        super().__init__(message)
        self.status = status
        self.uncertain = uncertain


def scan_xyz(message: LaserScan) -> list[tuple[float, float, float]]:
    if (len(message.ranges) > 2_000_000 or not math.isfinite(message.angle_min)
            or not math.isfinite(message.angle_increment) or message.angle_increment == 0
            or not 0 <= message.range_min < message.range_max <= 500):
        raise ValueError("Invalid LaserScan layout")
    stride = max(1, math.ceil(len(message.ranges) / MAX_LIDAR_POINTS))
    points = []
    for index in range(0, len(message.ranges), stride):
        distance = float(message.ranges[index])
        if not math.isfinite(distance) or not message.range_min <= distance <= message.range_max:
            continue
        angle = message.angle_min + index * message.angle_increment
        points.append((distance * math.cos(angle), distance * math.sin(angle), 0.0))
    return points


def cloud_xyz(message: PointCloud2) -> list[tuple[float, float, float]]:
    """Read the standard XYZ float fields with explicit endian and row padding."""
    width, height, step, row = int(message.width), int(message.height), int(message.point_step), int(message.row_step)
    if (width < 1 or height < 1 or width * height > 2_000_000 or step < 12 or row < width * step
            or len(message.data) < row * height or len(message.data) > 32 * 1024 * 1024):
        raise ValueError("Invalid PointCloud2 layout")
    fields = {field.name: field for field in message.fields}
    readers = []
    for name in ("x", "y", "z"):
        field = fields.get(name)
        if field is None or field.count != 1 or field.datatype not in (7, 8):
            raise ValueError("PointCloud2 requires FLOAT32 or FLOAT64 XYZ")
        reader = struct.Struct((">" if message.is_bigendian else "<") + ("f" if field.datatype == 7 else "d"))
        if field.offset < 0 or field.offset + reader.size > step:
            raise ValueError("PointCloud2 field outside point_step")
        readers.append((int(field.offset), reader))
    data = memoryview(message.data)
    stride = max(1, math.ceil(width * height / MAX_LIDAR_POINTS))
    points = []
    for index in range(0, width * height, stride):
        offset = (index // width) * row + (index % width) * step
        point = tuple(reader.unpack_from(data, offset + field_offset)[0] for field_offset, reader in readers)
        if all(math.isfinite(value) for value in point):
            points.append(point)
    return points


def transform_xyz(points, transform):
    t, q = transform.translation, transform.rotation
    norm = math.sqrt(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w)
    if norm < 1e-8 or not all(math.isfinite(v) for v in (norm, t.x, t.y, t.z)):
        raise ValueError("Invalid TF transform")
    x, y, z, w = q.x / norm, q.y / norm, q.z / norm, q.w / norm
    result = []
    for px, py, pz in points:
        result.extend((
            round((1 - 2 * (y*y + z*z)) * px + 2 * (x*y - z*w) * py + 2 * (x*z + y*w) * pz + t.x, 4),
            round(2 * (x*y + z*w) * px + (1 - 2 * (x*x + z*z)) * py + 2 * (y*z - x*w) * pz + t.y, 4),
            round(2 * (x*z - y*w) * px + 2 * (y*z + x*w) * py + (1 - 2 * (x*x + y*y)) * pz + t.z, 4),
        ))
    return result


class X2Node(Node):
    def __init__(self) -> None:
        super().__init__("lingxi_next_adapter")
        reliable_transient = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        best_effort_volatile = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        self.lock = threading.RLock()
        self.last_map: dict[str, Any] | None = None
        self.last_scan_at = 0.0
        self.last_lidar = None
        self.lidar_sequence = 0
        self.lidar_epoch = str(time.time_ns())
        self.tf_buffer = Buffer(cache_time=Duration(seconds=10)) if Buffer else None
        self.tf_listener = TransformListener(self.tf_buffer, self) if self.tf_buffer else None
        self.last_odom: dict[str, float] | None = None
        self.last_odom_at = 0.0
        self.last_odom_stamp_ns = 0
        self.last_task_state: dict[str, Any] | None = None
        self.last_task_state_at = 0.0
        self.last_navigation: dict[str, Any] = {"status": "idle"}
        self.mapping_status = "idle"
        self.relocalization_pending = False
        self.relocalization_seen = False
        self.motion_action_lock = threading.Lock()
        self.motion_source_ready = False
        self.active_motion_task_id: int | None = None
        self.motion_dispatch_uncertain = False
        self.linkcraft_pending_confirmation = False
        self._load_action_interlock()
        self.linkcraft_resource_records: dict[str, dict[str, str]] = {}
        self.linkcraft_resource_cache_at = 0.0
        self.linkcraft_resource_refresh_at = 0.0
        self.linkcraft_resource_refresh_pending = False
        self.publisher = self.create_publisher(String, INTEGRATED_COMMAND_TOPIC, reliable_transient)
        self.relocalization_publisher = self.create_publisher(Pose, "/relocalization_pose", best_effort_volatile)
        self.map_client = self.create_client(GetStoredMapByName, MAP_SERVICE)
        self.motion_input_source_client = None
        self.preset_motion_client = None
        self.preset_motion_state_client = None
        self.expression_client = None
        self.linkcraft_resource_client = None
        self.linkcraft_execute_client = None
        if ACTION_SERVICES_AVAILABLE:
            self.motion_input_source_client = self._optional_client(
                SetMcInputSource, "/aimdk_5Fmsgs/srv/SetMcInputSource"
            )
            self.preset_motion_client = self._optional_client(
                SetMcPresetMotion, "/aimdk_5Fmsgs/srv/SetMcPresetMotion"
            )
            self.preset_motion_state_client = self._optional_client(
                GetMcPresetMotionState, "/aimdk_5Fmsgs/srv/GetMcPresetMotionState"
            )
            self.expression_client = self._optional_client(
                PlayEmoji, "/aimdk_5Fmsgs/srv/PlayEmoji"
            )
            self.linkcraft_resource_client = self._optional_client(
                GetRobotResources, "/aimdk_5Fmsgs/srv/GetRobotResources"
            )
            self.linkcraft_execute_client = self._optional_client(
                ExecuteActionResource, "/aimdk_5Fmsgs/srv/ExecuteActionResource"
            )
        else:
            self.get_logger().warning("Optional AIMDK action services are unavailable")
        self.grid_subscription = (
            self.create_subscription(OccupancyGrid, MAP_TOPIC, self.on_grid, best_effort_volatile)
            if MAP_TOPIC
            else None
        )
        self.scan_subscription = (
            self.create_subscription(LaserScan, SCAN_TOPIC, self.on_scan, best_effort_volatile)
            if SCAN_TOPIC and not POINTCLOUD_TOPIC
            else None
        )
        self.cloud_subscription = (
            self.create_subscription(PointCloud2, POINTCLOUD_TOPIC, self.on_cloud, best_effort_volatile)
            if POINTCLOUD_TOPIC else None
        )
        self.odom_subscription = self.create_subscription(Odometry, ODOM_TOPIC, self.on_odom, best_effort_volatile)
        self.navigation_ready = False
        if NAV_TYPES_AVAILABLE:
            try:
                self.nav_publisher = self.create_publisher(PncTaskRequest, NAV_REQUEST_TOPIC, best_effort_volatile)
                self.nav_response_subscription = self.create_subscription(
                    PncTaskResponse, NAV_RESPONSE_TOPIC, self.on_navigation_response, reliable_transient
                )
                self.task_state_subscription = self.create_subscription(
                    TaskMasterState, TASK_MASTER_TOPIC, self.on_task_state, reliable_transient
                )
                self.navigation_ready = True
            except Exception as error:
                self.get_logger().warning(f"Optional X2 navigation interface unavailable: {error}")
                self.nav_publisher = None
        else:
            self.nav_publisher = None
            self.get_logger().warning("Optional AIMDK navigation messages are unavailable; map services remain available")

    def _optional_client(self, service_type, service_name: str):
        try:
            return self.create_client(service_type, service_name)
        except Exception as error:
            self.get_logger().warning(f"Optional AIMDK service unavailable ({service_name}): {error}")
            return None

    def _load_action_interlock(self) -> None:
        try:
            if not ACTION_INTERLOCK_FILE.exists():
                return
            if ACTION_INTERLOCK_FILE.stat().st_size > 4096:
                raise ValueError("Oversized interlock record")
            record = json.loads(ACTION_INTERLOCK_FILE.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or record.get("version") != 1:
                raise ValueError("Unsupported interlock record")
            status = record.get("status")
            if status == "idle":
                return
            if status == "preset-motion":
                task_id = record.get("taskId")
                if isinstance(task_id, int) and not isinstance(task_id, bool) and task_id > 0:
                    self.active_motion_task_id = task_id
                    return
            if status == "linkcraft-awaiting-operator":
                self.linkcraft_pending_confirmation = True
                return
            if status in ("dispatching", "uncertain"):
                self.motion_dispatch_uncertain = True
                return
            raise ValueError("Invalid interlock state")
        except Exception as error:
            self.motion_dispatch_uncertain = True
            self.get_logger().error(f"Action interlock record is unreadable; actions remain locked: {error}")

    def _write_action_interlock(self, status: str, task_id: int | None = None) -> None:
        if status not in ("idle", "dispatching", "preset-motion", "linkcraft-awaiting-operator", "uncertain"):
            raise ValueError("Invalid action interlock state")
        if status == "preset-motion" and (
            isinstance(task_id, bool) or not isinstance(task_id, int) or task_id <= 0
        ):
            raise ValueError("Preset motion interlock requires a task ID")
        payload = json.dumps(
            {"version": 1, "status": status, "taskId": task_id if status == "preset-motion" else None},
            separators=(",", ":"),
        )
        temporary = ACTION_INTERLOCK_FILE.with_name(
            f".{ACTION_INTERLOCK_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            ACTION_INTERLOCK_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, ACTION_INTERLOCK_FILE)
            try:
                os.chmod(ACTION_INTERLOCK_FILE, 0o600)
            except OSError:
                pass
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _set_action_interlock(self, status: str, task_id: int | None = None) -> None:
        with self.lock:
            try:
                self._write_action_interlock(status, task_id)
            except Exception as error:
                self.motion_dispatch_uncertain = True
                self.linkcraft_pending_confirmation = False
                self.get_logger().error(f"Unable to persist action interlock; actions remain locked: {error}")
                raise AdapterError("无法持久化动作互锁，机器人动作已锁定", 503, uncertain=True) from error
            self.active_motion_task_id = task_id if status == "preset-motion" else None
            self.linkcraft_pending_confirmation = status == "linkcraft-awaiting-operator"
            self.motion_dispatch_uncertain = status in ("dispatching", "uncertain")

    def resume_action_interlock(self) -> None:
        with self.lock:
            task_id = self.active_motion_task_id
        if task_id is not None:
            threading.Thread(target=self._monitor_preset_motion, args=(task_id,), daemon=True).start()

    @staticmethod
    def quaternion_yaw(orientation: Any) -> float:
        return math.atan2(
            2.0 * (orientation.w * orientation.z + orientation.x * orientation.y),
            1.0 - 2.0 * (orientation.y * orientation.y + orientation.z * orientation.z),
        )

    def on_grid(self, message: OccupancyGrid) -> None:
        width = int(message.info.width)
        height = int(message.info.height)
        if width < 1 or height < 1 or width * height > 4_000_000 or len(message.data) != width * height:
            return
        if not math.isfinite(message.info.resolution) or message.info.resolution <= 0:
            return
        stride = max(1, math.ceil(math.sqrt(max(1, width * height) / MAX_CELLS)))
        sample_width = math.ceil(width / stride)
        sample_height = math.ceil(height / stride)
        cells = []
        source = message.data
        for row in range(0, height, stride):
            start = row * width
            cells.extend(int(value) for value in source[start : start + width : stride])
        stamp = time.time()
        with self.lock:
            self.last_map = {
                "width": sample_width,
                "height": sample_height,
                "resolution": float(message.info.resolution) * stride,
                "originX": float(message.info.origin.position.x),
                "originY": float(message.info.origin.position.y),
                "originYaw": self.quaternion_yaw(message.info.origin.orientation),
                "cells": cells,
                "stamp": stamp,
            }

    def lidar_transform(self, target: str, source: str, stamp):
        if not self.tf_buffer:
            raise ValueError("TF unavailable")
        return self.tf_buffer.lookup_transform(
            target, source, Time.from_msg(stamp),
            timeout=Duration(seconds=LIDAR_TF_TIMEOUT_SECONDS),
        ).transform

    def record_lidar(self, message, points, source):
        with self.lock:
            epoch = self.lidar_epoch
        stamp_ns = int(message.header.stamp.sec) * 1_000_000_000 + int(message.header.stamp.nanosec)
        if stamp_ns <= 0 or abs(self.get_clock().now().nanoseconds - stamp_ns) > 2_000_000_000:
            return
        sensor_frame = str(message.header.frame_id).strip()
        if not sensor_frame or len(sensor_frame) > 200:
            return
        frame = sensor_frame
        transformed = [round(value, 4) for point in points for value in point]
        detail = None
        if LIDAR_FIXED_FRAME and sensor_frame != LIDAR_FIXED_FRAME:
            try:
                transformed = transform_xyz(points, self.lidar_transform(LIDAR_FIXED_FRAME, sensor_frame, message.header.stamp))
                frame = LIDAR_FIXED_FRAME
            except Exception:
                detail = "缺少采样时刻的 TF，显示雷达原始坐标，不累计点迹"
        fixed = bool(LIDAR_FIXED_FRAME and frame == LIDAR_FIXED_FRAME)
        pose = None
        try:
            transform = self.lidar_transform(frame, BASE_FRAME, message.header.stamp)
            pose = {"x": float(transform.translation.x), "y": float(transform.translation.y),
                    "yaw": self.quaternion_yaw(transform.rotation)}
            if not all(math.isfinite(value) for value in pose.values()):
                pose = None
        except Exception:
            if frame == BASE_FRAME:
                pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        now = time.time()
        with self.lock:
            if epoch != self.lidar_epoch:
                return
            self.lidar_sequence += 1
            self.last_scan_at = now
            self.last_lidar = {
                "sequence": self.lidar_sequence, "epoch": self.lidar_epoch,
                "source": source, "frameId": frame, "sensorFrame": sensor_frame, "fixedFrame": fixed,
                "stampMs": stamp_ns / 1_000_000, "receivedAtMs": now * 1000,
                "points": transformed,
            }
            if pose is not None:
                self.last_lidar["robotPose"] = pose
            if detail:
                self.last_lidar["detail"] = detail

    def on_scan(self, message: LaserScan) -> None:
        try:
            if len(message.ranges) == 0:
                return
            self.record_lidar(message, scan_xyz(message), "laser-scan")
        except (ValueError, TypeError, OverflowError):
            return

    def on_cloud(self, message: PointCloud2) -> None:
        try:
            self.record_lidar(message, cloud_xyz(message), "point-cloud")
        except (ValueError, TypeError, OverflowError, struct.error):
            return

    def lidar_frame(self):
        with self.lock:
            if not self.last_lidar or time.time() - self.last_scan_at > 2:
                raise AdapterError("雷达无新数据，请检查 LaserScan/PointCloud2 话题", 503)
            return self.last_lidar

    def on_odom(self, message: Odometry) -> None:
        stamp_ns = int(message.header.stamp.sec) * 1_000_000_000 + int(message.header.stamp.nanosec)
        if (message.header.frame_id != "map" or stamp_ns <= 0
                or abs(self.get_clock().now().nanoseconds - stamp_ns) > 2_000_000_000):
            return
        pose = message.pose.pose
        orientation = pose.orientation
        if not all(math.isfinite(value) for value in
                   (pose.position.x, pose.position.y, orientation.x, orientation.y, orientation.z, orientation.w)):
            return
        norm = sum(value * value for value in (orientation.x, orientation.y, orientation.z, orientation.w))
        if abs(norm - 1.0) > 0.01:
            return
        yaw = self.quaternion_yaw(orientation)
        with self.lock:
            if stamp_ns <= self.last_odom_stamp_ns:
                return
            self.last_odom = {"x": float(pose.position.x), "y": float(pose.position.y), "yaw": yaw}
            self.last_odom_at = time.time()
            self.last_odom_stamp_ns = stamp_ns

    def on_task_state(self, message: TaskMasterState) -> None:
        with self.lock:
            slam_state = int(message.slam_state)
            self.last_task_state = {
                "mainState": int(message.main_state),
                "subState": int(message.sub_state),
                "slamState": slam_state,
                "mapId": int(message.loc_map_id),
                "mapName": str(message.loc_map_name),
            }
            self.last_task_state_at = time.time()
            if self.relocalization_pending and slam_state == 1:
                self.relocalization_seen = True
            if self.mapping_status == "requested" and slam_state == MAPPING_STATE:
                self.mapping_status = "running"

    def on_navigation_response(self, message: Any) -> None:
        status = int(message.task_status)
        with self.lock:
            current = self.last_navigation
            if current.get("taskId") != str(message.task_id) or current.get("mapId") != int(message.map_id):
                return
            mapped = {1: "running", 2: "failed", 3: "paused", 4: "finished"}.get(status, "requested")
            self.last_navigation = {
                "status": mapped,
                "taskId": str(message.task_id),
                "mapId": int(message.map_id),
                "rawStatus": status,
                "updatedAt": time.time(),
            }

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        self.refresh_linkcraft_resources(now)
        with self.lock:
            grid = self.last_map
            odom = self.last_odom
            scan_fresh = bool(self.last_scan_at and now - self.last_scan_at <= 2.0)
            odom_fresh = bool(self.last_odom_at and now - self.last_odom_at <= 2.0)
            task_state = self.last_task_state
            task_state_fresh = bool(self.last_task_state_at and now - self.last_task_state_at <= 3.0)
            nav = dict(self.last_navigation)
            mapping_status = self.mapping_status
            relocalization_pending = self.relocalization_pending
            action_uncertain = self.motion_dispatch_uncertain
            linkcraft_pending = self.linkcraft_pending_confirmation
            active_motion = self.active_motion_task_id is not None
            dispatching = self.motion_action_lock.locked()
            action_busy = action_uncertain or linkcraft_pending or active_motion or dispatching
            map_fresh = bool(grid and now - float(grid["stamp"]) <= 2.0)
            resources_fresh = now - self.linkcraft_resource_cache_at <= RESOURCE_STALE_SECONDS
            resources = [
                {"id": item["id"], "name": item["name"]}
                for item in self.linkcraft_resource_records.values()
            ] if resources_fresh else []
        integrated_ready = self.publisher.get_subscription_count() > 0
        map_service_ready = self.map_client.service_is_ready()
        preset_ready = not action_uncertain and all(self._client_ready(client) for client in (
            self.motion_input_source_client, self.preset_motion_client, self.preset_motion_state_client
        ))
        expression_ready = not action_uncertain and self._client_ready(self.expression_client)
        resource_ready = not action_uncertain and self._client_ready(self.linkcraft_resource_client) and resources_fresh
        execute_resource_ready = not action_uncertain and self._client_ready(self.linkcraft_execute_client)
        checks = [
            {"id": "aimdk", "label": "AIMDK", "ready": map_service_ready, "detail": "地图服务在线" if map_service_ready else "GetStoredMapByName 不可用"},
            {
                "id": "slam",
                "label": "SLAM",
                "ready": integrated_ready and (task_state_fresh or odom_fresh),
                "detail": "SLAM 与命令链路在线" if integrated_ready and (task_state_fresh or odom_fresh) else "需确认 /integrated_command 与 SLAM 状态",
            },
            {"id": "lidar", "label": "激光雷达", "ready": scan_fresh, "detail": "实时扫描" if scan_fresh else f"{POINTCLOUD_TOPIC or SCAN_TOPIC or '雷达话题未配置'} 无新数据"},
            {"id": "odom", "label": "里程计", "ready": odom_fresh, "detail": "实时定位" if odom_fresh else "等待 /slam/lidar_odom"},
            {
                "id": "navigation",
                "label": "原生导航",
                "ready": self.navigation_ready and task_state_fresh and not relocalization_pending,
                "detail": "地图切换待确认" if relocalization_pending else "X2 PNC 状态在线" if self.navigation_ready and task_state_fresh else "可选导航接口/状态不可用",
            },
            {
                "id": "actions",
                "label": "动作服务",
                "ready": preset_ready or expression_ready or (resource_ready and execute_resource_ready),
                "detail": "可用：" + "、".join(
                    label for ready, label in (
                        (preset_ready, "预设动作"),
                        (expression_ready, "表情"),
                        (resource_ready and execute_resource_ready, "灵创资源"),
                    ) if ready
                ) if preset_ready or expression_ready or (resource_ready and execute_resource_ready)
                else "等待 AIMDK 动作服务或灵创资源清单",
            },
            {"id": "linksoul", "label": "灵心", "ready": False, "detail": "AgentSDK v1.5 适配器未安装"},
        ]
        pose = odom if odom_fresh else None
        return {
            "connected": True,
            "checks": checks,
            "mapping": mapping_status != "idle",
            "mappingStatus": mapping_status,
            "pose": pose,
            "navigation": nav,
            "actionBusy": action_busy,
            "actionStatus": "uncertain" if action_uncertain else
                "linkcraft-awaiting-operator" if linkcraft_pending else
                "preset-motion" if active_motion else
                "dispatching" if dispatching else None,
            "mapGrid": grid if map_fresh else None,
            "motionPresets": list(PRESET_MOTIONS) if preset_ready else [],
            "expressions": list(EXPRESSIONS) if expression_ready else [],
            "linkcraftResources": resources if resource_ready and execute_resource_ready else [],
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "taskMasterState": task_state if task_state_fresh else None,
        }

    @staticmethod
    def _client_ready(client: Any) -> bool:
        try:
            return client is not None and bool(client.service_is_ready())
        except Exception:
            return False

    def start_mapping(self) -> None:
        if not self.motion_action_lock.acquire(blocking=False):
            raise AdapterError("已有动作服务请求正在处理", 409)
        try:
            self._require_action_window()
            self.ensure_no_active_preset_motion()
            state = self.snapshot()
            required = ("aimdk", "slam", "lidar")
            not_ready = [item["label"] for item in state["checks"]
                         if item["id"] in required and not item["ready"]]
            if not_ready:
                raise AdapterError("建图预检未通过: " + "、".join(not_ready), 409)
            with self.lock:
                if self.mapping_status != "idle":
                    raise AdapterError("已有建图请求尚未完成", 409)
                if self.relocalization_pending:
                    raise AdapterError("地图切换尚未完成确认", 409)
                if self.last_navigation.get("status") in ("requested", "running", "paused"):
                    raise AdapterError("请先停止导航", 409)
                self.publish_command("start_mapping")
                self.mapping_status = "requested"
                self.lidar_epoch = str(time.time_ns())
                self.last_lidar = None
        finally:
            self.motion_action_lock.release()

    def _call_service(self, client: Any, request: Any, label: str, *, uncertain: bool = False,
                      timeout: float | None = None) -> Any:
        if timeout is None:
            timeout = SERVICE_TIMEOUT_SECONDS
        if client is None or not client.wait_for_service(timeout_sec=min(timeout, 0.5)):
            raise AdapterError(f"AIMDK {label} 服务不可用")
        completed = threading.Event()
        result: dict[str, Any] = {}

        def on_complete(future: Any) -> None:
            try:
                result["response"] = future.result()
            except Exception:
                result["error"] = True
            finally:
                completed.set()

        try:
            future = client.call_async(request)
            future.add_done_callback(on_complete)
        except Exception as error:
            raise AdapterError(f"AIMDK {label} 请求未发送", 503) from error
        if not completed.wait(timeout):
            raise AdapterError(f"AIMDK {label} 响应超时，未自动重试", 504, uncertain=uncertain)
        if "error" in result or result.get("response") is None:
            raise AdapterError(f"AIMDK {label} 请求失败", 502, uncertain=uncertain)
        return result["response"]

    @staticmethod
    def _resource_id(resource_key: str, version: str) -> str:
        digest = hashlib.sha256(f"{resource_key}\0{version}".encode("utf-8")).hexdigest()
        return digest[:32]

    def _parse_linkcraft_resources(self, response: Any) -> dict[str, dict[str, str]]:
        try:
            header = response.header
            code = int(header.header.code)
            state = int(header.status.value)
            resources = response.robot_resources
        except (AttributeError, TypeError, ValueError) as error:
            raise AdapterError("AIMDK 灵创资源清单格式无效") from error
        if code != 0 or state != 1 or len(resources) > 500:
            raise AdapterError("AIMDK 未确认灵创资源清单")

        parsed: dict[str, dict[str, str]] = {}
        for item in resources:
            try:
                key = item.resource_key
                current = item.current_version
                version = current.version
                raw_name = current.name
                files = current.files
            except AttributeError:
                continue
            if (not isinstance(key, str) or not key or len(key) > 1024
                    or not isinstance(version, str) or not version or len(version) > 256
                    or any(ord(character) < 32 for character in key + version)):
                continue
            identifier = self._resource_id(key, version)
            name = raw_name.strip() if isinstance(raw_name, str) else ""
            if (not name or len(name) > 160 or any(ord(character) < 32 for character in name)):
                name = f"灵创资源 {identifier[:8]}"
            # The vendor sample uses the resource key to distinguish ONNX body actions.
            body_action = "onnx" in key.lower()
            meta = {"resource_type": "BODY_MONTION" if body_action else "ARM_MONTION"}
            parsed[identifier] = {
                "id": identifier,
                "name": name,
                "resourceKey": key,
                "version": version,
                "meta": json.dumps(meta, separators=(",", ":")),
            }
        return parsed

    def refresh_linkcraft_resources(self, now: float | None = None) -> None:
        client = self.linkcraft_resource_client
        if client is None:
            return
        now = time.time() if now is None else now
        with self.lock:
            if (self.linkcraft_resource_refresh_pending
                    or now - self.linkcraft_resource_refresh_at < RESOURCE_REFRESH_SECONDS):
                return
            self.linkcraft_resource_refresh_at = now
            self.linkcraft_resource_refresh_pending = True
        if not self._client_ready(client):
            with self.lock:
                self.linkcraft_resource_refresh_pending = False
            return
        try:
            request = GetRobotResources.Request()
            request.header.header.stamp = self.get_clock().now().to_msg()
            future = client.call_async(request)
        except Exception:
            with self.lock:
                self.linkcraft_resource_refresh_pending = False
            return

        def on_complete(done: Any) -> None:
            try:
                records = self._parse_linkcraft_resources(done.result())
            except Exception:
                records = None
            with self.lock:
                self.linkcraft_resource_refresh_pending = False
                if records is not None:
                    self.linkcraft_resource_records = records
                    self.linkcraft_resource_cache_at = time.time()

        future.add_done_callback(on_complete)

    def _require_action_window(self) -> None:
        with self.lock:
            if self.motion_dispatch_uncertain:
                raise AdapterError("动作请求结果不确定，请现场检查机器人后重启桥接服务", 409, uncertain=True)
            if self.linkcraft_pending_confirmation:
                raise AdapterError("灵创动作播放结束状态不可见，请现场确认动作已停止", 409, uncertain=True)
            if self.mapping_status != "idle" or self.relocalization_pending:
                raise AdapterError("建图或地图切换期间不接受动作请求", 409)
            if self.last_navigation.get("status") in ("requested", "running", "paused"):
                raise AdapterError("导航期间不接受动作请求", 409)

    def ensure_no_active_preset_motion(self) -> None:
        with self.lock:
            if self.motion_dispatch_uncertain:
                raise AdapterError("预设动作状态不确定，请现场检查机器人后重启桥接服务", 409, uncertain=True)
            task_id = self.active_motion_task_id
        if task_id is None:
            return
        try:
            status = self.preset_motion_status(task_id)["status"]
        except AdapterError as error:
            self._set_action_interlock("uncertain")
            raise AdapterError("预设动作结束状态无法确认，已阻止后续机器人动作", 409, uncertain=True) from error
        if status in ("running", "pending"):
            raise AdapterError("机器人仍在执行预设动作", 409)
        if status != "finished":
            self._set_action_interlock("uncertain")
            raise AdapterError("预设动作结束状态无法确认，已阻止后续机器人动作", 409, uncertain=True)

    def _ensure_motion_input_source(self) -> None:
        with self.lock:
            if self.motion_source_ready:
                return
        client = self.motion_input_source_client
        if client is None:
            raise AdapterError("预设动作输入源服务不可用")
        for action in (1001, 2001):  # AIMDK ADD, then ENABLE for an existing registration.
            request = SetMcInputSource.Request()
            request.request.header.stamp = self.get_clock().now().to_msg()
            request.action.value = action
            request.input_source.name = MOTION_INPUT_SOURCE
            request.input_source.priority = MOTION_INPUT_PRIORITY
            request.input_source.timeout = MOTION_INPUT_TIMEOUT_MS
            try:
                response = self._call_service(
                    client, request, "SetMcInputSource"
                )
            except AdapterError:
                raise
            if int(response.response.header.code) == 0:
                with self.lock:
                    self.motion_source_ready = True
                return
        raise AdapterError("AIMDK 未确认预设动作输入源", 409)

    def start_preset_motion(self, name: Any, *, monitor_completion: bool = True) -> dict[str, Any]:
        if not isinstance(name, str) or name not in PRESET_MOTIONS:
            raise AdapterError("预设动作不在 X2 白名单中", 400)
        if not self.motion_action_lock.acquire(blocking=False):
            raise AdapterError("已有动作服务请求正在处理", 409)
        try:
            self._require_action_window()
            self.ensure_no_active_preset_motion()
            client = self.preset_motion_client
            if client is None or self.preset_motion_state_client is None:
                raise AdapterError("预设动作或状态查询服务不可用")
            self._ensure_motion_input_source()
            motion_id, area_id = PRESET_MOTIONS[name]
            request = SetMcPresetMotion.Request()
            request.header.stamp = self.get_clock().now().to_msg()
            request.input_source.name = MOTION_INPUT_SOURCE
            request.input_source.priority = MOTION_INPUT_PRIORITY
            request.input_source.timeout = MOTION_INPUT_TIMEOUT_MS
            request.motion.value = motion_id
            request.area.value = area_id
            request.interrupt = False
            request.ani_path = ""
            request.play_timestamp = 0
            self._set_action_interlock("dispatching")
            try:
                response = self._call_service(
                    client, request, "SetMcPresetMotion", uncertain=True
                )
            except AdapterError as error:
                if error.uncertain:
                    self._set_action_interlock("uncertain")
                else:
                    self._set_action_interlock("idle")
                raise
            result = response.response
            task_id = int(result.task_id)
            state = int(result.state.value)
            if task_id <= 0:
                self._set_action_interlock("uncertain")
                raise AdapterError("AIMDK 已响应预设动作请求，但未返回可查询任务 ID", 502, uncertain=True)
            if int(result.header.code) != 0 and state != 400:
                self._set_action_interlock("idle")
                raise AdapterError("AIMDK 未确认预设动作请求", 502)
            self._set_action_interlock("preset-motion", task_id)
            if state not in (1, 200, 300, 400):
                self._set_action_interlock("uncertain")
                raise AdapterError("AIMDK 已响应预设动作请求，但状态无法确认", 409, uncertain=True)
            if monitor_completion:
                threading.Thread(
                    target=self._monitor_preset_motion, args=(task_id,), daemon=True
                ).start()
            status = "pending" if state in (1, 200, 300) else "running"
            return {"accepted": True, "taskId": str(task_id), "status": status}
        finally:
            self.motion_action_lock.release()

    def preset_motion_status(self, task_id: int) -> dict[str, Any]:
        client = self.preset_motion_state_client
        if client is None:
            raise AdapterError("预设动作状态查询服务不可用")
        request = GetMcPresetMotionState.Request()
        request.request.header.stamp = self.get_clock().now().to_msg()
        request.request.task_id = task_id
        response = self._call_service(client, request, "GetMcPresetMotionState")
        result = response.response
        if int(result.header.code) != 0 or int(result.task_id) != task_id:
            raise AdapterError("AIMDK 未返回匹配的预设动作状态")
        state = int(result.state.value)
        status = "running" if state == 400 else "finished" if state == 1 else "pending" if state in (200, 300) else "unknown"
        if status == "finished":
            with self.lock:
                if self.active_motion_task_id == task_id:
                    self._set_action_interlock("idle")
        elif status == "unknown":
            with self.lock:
                if self.active_motion_task_id == task_id:
                    self._set_action_interlock("uncertain")
        return {"taskId": str(task_id), "status": status}

    def wait_preset_motion(self, task_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + MOTION_STATE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                status = self.preset_motion_status(task_id)
            except AdapterError as error:
                with self.lock:
                    if self.active_motion_task_id == task_id:
                        self._set_action_interlock("uncertain")
                raise AdapterError("预设动作结束状态无法确认，已阻止后续机器人动作", 502, uncertain=True) from error
            if status["status"] == "finished":
                return {"confirmed": True, **status}
            if status["status"] not in ("running", "pending"):
                self._set_action_interlock("uncertain")
                raise AdapterError("AIMDK 未确认预设动作结束状态", 502, uncertain=True)
            time.sleep(MOTION_STATE_POLL_SECONDS)
        self._set_action_interlock("uncertain")
        raise AdapterError("预设动作在期限内未确认结束，已阻止后续机器人动作", 504, uncertain=True)

    def _monitor_preset_motion(self, task_id: int) -> None:
        try:
            self.wait_preset_motion(task_id)
        except AdapterError:
            return

    def play_expression(self, name: Any) -> dict[str, Any]:
        if not isinstance(name, str) or name not in EXPRESSIONS:
            raise AdapterError("表情不在 X2 白名单中", 400)
        if not self.motion_action_lock.acquire(blocking=False):
            raise AdapterError("已有动作服务请求正在处理", 409)
        try:
            self._require_action_window()
            self.ensure_no_active_preset_motion()
            client = self.expression_client
            if client is None:
                raise AdapterError("AIMDK 表情服务不可用")
            request = PlayEmoji.Request()
            request.header.header.stamp = self.get_clock().now().to_msg()
            request.emotion_id = EXPRESSIONS[name]
            request.mode = 1
            request.priority = 10
            self._set_action_interlock("dispatching")
            try:
                response = self._call_service(client, request, "PlayEmoji", uncertain=True)
            except AdapterError as error:
                if error.uncertain:
                    self._set_action_interlock("uncertain")
                else:
                    self._set_action_interlock("idle")
                raise
            if response.success is not True:
                self._set_action_interlock("idle")
                raise AdapterError("AIMDK 未确认表情请求", 502)
            self._set_action_interlock("idle")
            return {"accepted": True, "completed": False}
        finally:
            self.motion_action_lock.release()

    def execute_linkcraft_resource(self, identifier: Any) -> dict[str, Any]:
        if not isinstance(identifier, str) or re.fullmatch(r"[0-9a-f]{32}", identifier) is None:
            raise AdapterError("灵创资源 ID 无效", 400)
        if not self.motion_action_lock.acquire(blocking=False):
            raise AdapterError("已有动作服务请求正在处理", 409)
        try:
            self._require_action_window()
            self.ensure_no_active_preset_motion()
            with self.lock:
                record = self.linkcraft_resource_records.get(identifier)
                fresh = time.time() - self.linkcraft_resource_cache_at <= RESOURCE_STALE_SECONDS
            if not fresh or record is None:
                raise AdapterError("灵创资源未在近期 AIMDK 清单中确认", 409)
            client = self.linkcraft_execute_client
            if client is None:
                raise AdapterError("AIMDK 灵创资源执行服务不可用")
            request = ExecuteActionResource.Request()
            request.header.header.stamp = self.get_clock().now().to_msg()
            request.resource_key = record["resourceKey"]
            request.resource_version = record["version"]
            request.slaves = []
            request.meta = record["meta"]
            self._set_action_interlock("dispatching")
            try:
                response = self._call_service(
                    client, request, "ExecuteActionResource", uncertain=True
                )
            except AdapterError as error:
                if error.uncertain:
                    self._set_action_interlock("uncertain")
                else:
                    self._set_action_interlock("idle")
                raise
            result = response.header
            message = str(result.message).strip()
            if int(result.header.code) != 0 or message != "Play success":
                known_rejections = {
                    "The robot is playing": "机器人正在播放其他动作，未接受灵创资源",
                    "No valid resource files found": "灵创资源没有可播放文件",
                    "The robot is in a faulty state": "机器人状态异常，未接受灵创资源",
                    "The robot is not in ready posture": "机器人姿态未就绪，未接受灵创资源",
                    "MC ROS service not ready": "机器人运控服务未就绪",
                }
                if message in known_rejections:
                    self._set_action_interlock(
                        "linkcraft-awaiting-operator"
                        if message == "The robot is playing" else "idle"
                    )
                else:
                    self._set_action_interlock("uncertain")
                user_message = known_rejections.get(message, "AIMDK 未确认灵创资源执行请求")
                raise AdapterError(user_message, 409 if message else 502)
            self._set_action_interlock("linkcraft-awaiting-operator")
            return {"accepted": True, "completed": False, "message": "Play success"}
        finally:
            self.motion_action_lock.release()

    def confirm_actions_idle(self, confirmed: Any) -> dict[str, Any]:
        if confirmed is not True:
            raise AdapterError("请先现场确认灵创动作已停止", 400)
        if not self.motion_action_lock.acquire(blocking=False):
            raise AdapterError("动作服务请求正在处理，请稍后重试", 409)
        try:
            with self.lock:
                if self.active_motion_task_id is not None:
                    raise AdapterError("预设动作仍需等待 AIMDK 状态查询", 409)
                if not self.linkcraft_pending_confirmation and not self.motion_dispatch_uncertain:
                    raise AdapterError("没有等待现场确认的灵创动作", 409)
            self._set_action_interlock("idle")
            return {"confirmed": True, "actionBusy": False}
        finally:
            self.motion_action_lock.release()

    def publish_command(self, value: str) -> None:
        if self.publisher.get_subscription_count() < 1:
            raise AdapterError("AIMDK 未订阅 /integrated_command，未发送命令")
        message = String()
        message.data = value
        self.publisher.publish(message)

    def query_map(self, name: str, timeout: float = 4.0, include_grid: bool = False) -> dict[str, Any]:
        if not self.map_client.wait_for_service(timeout_sec=timeout):
            raise AdapterError("AIMDK 地图查询服务不可用")
        request = GetStoredMapByName.Request()
        request.header = Header()
        request.header.stamp = self.get_clock().now().to_msg()
        request.map_name = name
        done = threading.Event()
        result: dict[str, Any] = {}

        def completed(future: Any) -> None:
            try:
                result["response"] = future.result()
            except Exception as error:  # surfaced below without logging map contents
                result["error"] = error
            finally:
                done.set()

        self.map_client.call_async(request).add_done_callback(completed)
        if not done.wait(timeout):
            raise AdapterError(f"查询地图超时: {name}", 504)
        if "error" in result:
            raise AdapterError("AIMDK 地图查询失败")
        response = result["response"]
        if int(response.code) != 0:
            raise AdapterError(f"AIMDK 未确认地图可用: {name}", 404)
        info = response.map_info
        if not 0 < int(response.map_id) <= MAX_SAFE_MAP_ID or not math.isfinite(info.resolution) or info.resolution <= 0:
            raise AdapterError("AIMDK 返回的地图 ID 或分辨率无效")
        metadata = {
            "id": name,
            "name": name,
            "mapId": int(response.map_id),
            "mapPath": str(response.map_path),
            "resolution": float(info.resolution),
            "width": int(info.width),
            "height": int(info.height),
            "origin": [
                float(info.origin.position.x),
                float(info.origin.position.y),
                self.quaternion_yaw(info.origin.orientation),
            ],
            "active": False,
            "createdAt": "",
        }
        if include_grid:
            expected = int(info.width) * int(info.height)
            if expected < 1 or expected > 4_000_000 or len(response.data) != expected:
                raise AdapterError("AIMDK 返回的地图栅格尺寸无效")
            metadata["cells"] = [int(value) for value in response.data]
        return metadata

    def stored_grid(self, name: str) -> dict[str, Any]:
        result = self.query_map(name, include_grid=True)
        origin = result["origin"]
        return {
            "width": result["width"],
            "height": result["height"],
            "resolution": result["resolution"],
            "originX": origin[0],
            "originY": origin[1],
            "originYaw": origin[2],
            "cells": result["cells"],
            "stamp": time.time(),
        }

    def send_navigation(self, *, task_id: int, map_id: int, target: dict[str, float], action: int) -> None:
        if action != 1:
            return self._send_navigation(task_id=task_id, map_id=map_id, target=target, action=action)
        if not self.motion_action_lock.acquire(blocking=False):
            raise AdapterError("已有动作服务请求正在处理", 409)
        try:
            return self._send_navigation(task_id=task_id, map_id=map_id, target=target, action=action)
        finally:
            self.motion_action_lock.release()

    def _send_navigation(self, *, task_id: int, map_id: int, target: dict[str, float], action: int) -> None:
        if not self.navigation_ready or self.nav_publisher is None:
            raise AdapterError("X2 原生导航组件未启用")
        if self.nav_publisher.get_subscription_count() < 1:
            raise AdapterError("X2 原生导航请求接口没有接收者")
        state = self.snapshot().get("taskMasterState")
        if action == 1:
            if isinstance(map_id, bool) or not isinstance(map_id, int) or not 0 < map_id <= MAX_SAFE_MAP_ID:
                raise AdapterError("地图 ID 无效", 400)
            if any(not math.isfinite(target.get(key, math.nan)) for key in ("x", "y", "yaw")):
                raise AdapterError("导航坐标必须为有限数值", 400)
            if any(abs(target[key]) > 1000 for key in ("x", "y")) or abs(target["yaw"]) > 2 * math.pi:
                raise AdapterError("导航坐标超出支持范围", 400)
            if not state or int(state["mapId"]) != map_id or state["slamState"] != LOCALIZATION_STATE:
                raise AdapterError("当前定位地图与目标地图不一致，请先在地图管理中完成重定位", 409)
            if not self.snapshot()["pose"]:
                raise AdapterError("定位里程计已过期，导航请求已拒绝", 409)
            self._require_action_window()
            self.ensure_no_active_preset_motion()
        else:
            with self.lock:
                current = self.last_navigation
                if current.get("taskId") != str(task_id) or not current.get("mapId"):
                    raise AdapterError("导航任务 ID 不匹配，控制请求已拒绝", 409)
                map_id = int(current["mapId"])
        request = PncTaskRequest()
        request.header.stamp = self.get_clock().now().to_msg()
        request.header.meas_stamp = request.header.stamp
        request.header.frame_id = "map"
        request.header.sequence = task_id % (2**32)
        request.task_type = 2
        request.task_request = action
        request.pnc_mode = 0
        request.task_id = task_id
        request.map_id = map_id
        if action == 1:
            request.target_pose.header = Header()
            request.target_pose.header.frame_id = "map"
            request.target_pose.header.stamp = self.get_clock().now().to_msg()
            request.target_pose.pose.position.x = float(target["x"])
            request.target_pose.pose.position.y = float(target["y"])
            request.target_pose.pose.position.z = 0.0
            request.target_pose.pose.orientation.z = math.sin(float(target["yaw"]) / 2.0)
            request.target_pose.pose.orientation.w = math.cos(float(target["yaw"]) / 2.0)
            request.target_pose_radius = 0.25
            request.max_forward_speed = 0.3
        with self.lock:
            if action == 1:
                if self.mapping_status != "idle" or self.relocalization_pending:
                    raise AdapterError("建图或地图切换尚未完成确认", 409)
                if self.last_navigation.get("status") in ("requested", "running", "paused"):
                    raise AdapterError("已有导航请求尚未结束", 409)
                self.last_navigation = {"status": "requested", "taskId": str(task_id), "mapId": map_id}
            elif self.last_navigation.get("taskId") != str(task_id):
                raise AdapterError("导航任务 ID 不匹配，控制请求已拒绝", 409)
        self.nav_publisher.publish(request)

    def list_maps(self) -> list[dict[str, Any]]:
        if not MAP_DIR.is_dir():
            return []
        state = self.snapshot().get("taskMasterState") or {}
        active_name = str(state.get("mapName", ""))
        active_id = state.get("mapId")
        result = []
        for item in read_map_catalog(MAP_DIR):
            active = (state.get("slamState") == LOCALIZATION_STATE and item["name"] == active_name
                      and (item.get("mapId") is None or item["mapId"] == active_id))
            result.append({**item, "mapId": item.get("mapId", int(active_id) if active else None),
                           "active": active, "createdAt": ""})
        return result

    def activate_map(self, name: str, initial: Any = None) -> dict[str, Any]:
        if not self.motion_action_lock.acquire(blocking=False):
            raise AdapterError("已有动作服务请求正在处理", 409)
        try:
            self._require_action_window()
            self.ensure_no_active_preset_motion()
            return self._activate_map(name, initial)
        finally:
            self.motion_action_lock.release()

    def _activate_map(self, name: str, initial: Any = None) -> dict[str, Any]:
        if not valid_map_name(name):
            raise AdapterError("无效地图名称", 400)
        info = self.query_map(name)
        u, v, yaw = relocalization_pixels(MAP_DIR, int(info["mapId"]), initial)
        if (self.publisher.get_subscription_count() < 1
                or self.relocalization_publisher.get_subscription_count() < 1):
            raise AdapterError("AIMDK 重定位指令或初始位姿接口没有接收者")
        with self.lock:
            if self.relocalization_pending:
                raise AdapterError("地图切换待确认，请现场检查后重启本项目桥接服务", 409)
            if self.mapping_status != "idle" or self.last_navigation.get("status") in ("requested", "running", "paused"):
                raise AdapterError("请先停止建图或导航", 409)
            self.relocalization_pending = True
            self.relocalization_seen = False
            self.lidar_epoch = str(time.time_ns())
            self.last_lidar = None
            self.last_scan_at = 0
            self.last_map = None
            self.last_odom = None
            self.last_odom_at = 0
            self.publish_command(f"start_relocalization:{int(info['mapId'])}")
        time.sleep(RELOCALIZATION_POSE_DELAY)
        # No retry: an uncertain publish can already have reached the robot.
        if self.relocalization_publisher.get_subscription_count() < 1:
            raise AdapterError("初始位姿接收者已离线，地图切换待确认")
        pose = Pose()
        pose.position.x, pose.position.y, pose.position.z = float(u), float(v), 0.0
        pose.orientation.z = math.sin(yaw / 2)
        pose.orientation.w = math.cos(yaw / 2)
        requested_at = time.time()
        stamp_ns = self.get_clock().now().nanoseconds
        self.relocalization_publisher.publish(pose)
        deadline = time.monotonic() + RELOCALIZATION_TIMEOUT
        while time.monotonic() < deadline:
            with self.lock:
                state = self.last_task_state
                confirmed = bool(
                    self.relocalization_seen and state and self.last_task_state_at >= requested_at
                    and state["slamState"] == LOCALIZATION_STATE
                    and int(state["mapId"]) == int(info["mapId"])
                    and self.last_odom_at >= requested_at and self.last_odom_stamp_ns >= stamp_ns
                    and time.time() - self.last_odom_at <= 2.0
                )
                if confirmed:
                    self.relocalization_pending = False
                    return {"accepted": True, "confirmed": True, "mapId": info["mapId"]}
            time.sleep(0.05)
        raise AdapterError("AIMDK 未确认目标地图和初始位姿下发后的新定位数据", 504)


class BridgeHandler(BaseHTTPRequestHandler):
    node: X2Node

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            return self.send_json(200, {"ok": rclpy.ok()})
        try:
            self.authorize()
            if path == "/v1/state":
                return self.send_json(200, self.node.snapshot())
            if path == "/v1/lidar":
                return self.send_json(200, self.node.lidar_frame())
            if path == "/v1/maps":
                return self.send_json(200, {"maps": self.node.list_maps()})
            motion_status_match = re.fullmatch(r"/v1/motions/preset/(\d+)", path)
            if motion_status_match:
                task_id = self.parse_task_id(motion_status_match.group(1))
                return self.send_json(200, self.node.preset_motion_status(task_id))
            if path.startswith("/v1/maps/") and path.endswith("/grid"):
                map_key = unquote(path[len("/v1/maps/") : -len("/grid")]).strip("/")
                if not re.fullmatch(r"[\w\u4e00-\u9fff][\w\u4e00-\u9fff .-]{0,99}", map_key, re.UNICODE):
                    raise AdapterError("无效地图名称", 400)
                return self.send_json(200, self.node.stored_grid(map_key))
            raise AdapterError("接口不存在", 404)
        except AdapterError as error:
            return self.send_json(error.status, {"message": str(error), "uncertain": error.uncertain})
        except Exception:
            return self.send_json(500, {"message": "AIMDK 桥接内部错误"})

    def do_POST(self) -> None:
        try:
            self.authorize()
            body = self.read_json()
            path = urlparse(self.path).path
            if path == "/v1/mapping/start":
                self.node.start_mapping()
                return self.send_json(202, {"accepted": True, "status": "requested"})
            if path == "/v1/mapping/stop-save":
                name = str(body.get("name", "")).strip()
                if not re.fullmatch(r"[\w\u4e00-\u9fff][\w\u4e00-\u9fff .-]{0,63}", name, re.UNICODE):
                    raise AdapterError("地图名称格式无效", 400)
                if any(item["name"] == name for item in read_map_catalog(MAP_DIR)):
                    raise AdapterError("同名地图已存在，请使用新地图名称", 409)
                with self.node.lock:
                    if self.node.mapping_status not in ("requested", "running", "failed"):
                        raise AdapterError("没有待停止的建图任务", 409)
                    self.node.mapping_status = "saving"
                self.node.publish_command(f"stop_mapping:{name}")
                deadline = time.monotonic() + 120
                last_error = "正在等待地图保存"
                while time.monotonic() < deadline:
                    try:
                        result = self.node.query_map(name, timeout=min(3.0, max(0.5, deadline - time.monotonic())))
                        with self.node.lock:
                            self.node.mapping_status = "idle"
                        return self.send_json(200, {"map": result, "confirmed": True})
                    except AdapterError as error:
                        last_error = str(error)
                        time.sleep(1.0)
                with self.node.lock:
                    self.node.mapping_status = "failed"
                raise AdapterError(f"AIMDK 未在 120 秒内确认地图保存: {last_error}", 504)
            if path.startswith("/v1/maps/") and path.endswith("/activate"):
                map_key = unquote(path[len("/v1/maps/") : -len("/activate")]).strip("/")
                return self.send_json(200, self.node.activate_map(map_key, body.get("initialPose")))
            if path == "/v1/navigation/start":
                task_id = self.parse_task_id(body.get("taskId"))
                target = body.get("target")
                if not isinstance(target, dict):
                    raise AdapterError("导航目标无效", 400)
                self.node.send_navigation(
                    task_id=task_id,
                    map_id=int(body["mapId"]),
                    target={key: float(target[key]) for key in ("x", "y", "yaw")},
                    action=1,
                )
                return self.send_json(202, {"accepted": True, "taskId": str(task_id)})
            if path == "/v1/navigation/control":
                action = {"stop": 2, "pause": 3, "resume": 4}.get(str(body.get("action")))
                if action is None:
                    raise AdapterError("不支持的导航控制命令", 400)
                task_id = self.parse_task_id(body.get("taskId"))
                requested_at = time.time()
                self.node.send_navigation(task_id=task_id, map_id=int(body.get("mapId", 0)), target={}, action=action)
                expected = {2: "finished", 3: "paused", 4: "running"}[action]
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    with self.node.lock:
                        current = self.node.last_navigation
                        confirmed = (
                            current.get("taskId") == str(task_id)
                            and current.get("updatedAt", 0) >= requested_at
                            and current.get("status") == expected
                        )
                    if confirmed:
                        return self.send_json(200, {"accepted": True, "confirmed": True})
                    time.sleep(0.05)
                raise AdapterError("PNC 未确认控制请求，请检查机器人状态", 504)
            if path == "/v1/motions/preset":
                name = body.get("name")
                monitor = body.get("monitorCompletion", True)
                if not isinstance(monitor, bool):
                    raise AdapterError("动作状态监控参数无效", 400)
                return self.send_json(202, self.node.start_preset_motion(name, monitor_completion=monitor))
            if path == "/v1/expression":
                result = self.node.play_expression(body.get("name"))
                return self.send_json(202, result)
            if path == "/v1/linkcraft/resource":
                result = self.node.execute_linkcraft_resource(body.get("id"))
                return self.send_json(202, result)
            if path == "/v1/actions/confirm-idle":
                result = self.node.confirm_actions_idle(body.get("confirmed"))
                return self.send_json(200, result)
            raise AdapterError("接口不存在", 404)
        except AdapterError as error:
            return self.send_json(error.status, {"message": str(error), "uncertain": error.uncertain})
        except (KeyError, TypeError, ValueError):
            return self.send_json(400, {"message": "请求格式无效"})
        except Exception:
            return self.send_json(500, {"message": "AIMDK 桥接内部错误"})

    def authorize(self) -> None:
        if not TOKEN:
            raise AdapterError("AIMDK 桥接服务未配置内部令牌", 503)
        supplied = self.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode("utf-8"), f"Bearer {TOKEN}".encode("utf-8")):
            raise AdapterError("未授权", 401)

    def read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > 64_000:
                raise AdapterError("请求体长度无效", 400)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise AdapterError("请求体必须为对象", 400)
            return body
        except json.JSONDecodeError as error:
            raise AdapterError("请求 JSON 无效", 400) from error

    @staticmethod
    def parse_task_id(value: Any) -> int:
        try:
            task_id = int(value)
        except (TypeError, ValueError) as error:
            raise AdapterError("导航任务 ID 无效", 400) from error
        if task_id < 1 or task_id > 18_446_744_073_709_551_615:
            raise AdapterError("导航任务 ID 超出范围", 400)
        return task_id

    def send_json(self, status: int, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    if not TOKEN:
        raise SystemExit("LINGXI_AIMDK_BRIDGE_TOKEN is required")
    rclpy.init()
    node = X2Node()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    node.resume_action_interlock()
    BridgeHandler.node = node
    server = ThreadingHTTPServer(("127.0.0.1", PORT), BridgeHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
