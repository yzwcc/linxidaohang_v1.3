#!/usr/bin/env python3
"""Narrow local HTTP adapter for the documented X2 AIMDK ROS 2 interfaces."""

from __future__ import annotations

import json
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
MAPPING_STATE = 2
LOCALIZATION_STATE = 3
MAX_SAFE_MAP_ID = 9_007_199_254_740_991
RELOCALIZATION_TIMEOUT = 30.0
RELOCALIZATION_POSE_DELAY = 1.0


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
    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


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
        self.publisher = self.create_publisher(String, INTEGRATED_COMMAND_TOPIC, reliable_transient)
        self.relocalization_publisher = self.create_publisher(Pose, "/relocalization_pose", best_effort_volatile)
        self.map_client = self.create_client(GetStoredMapByName, MAP_SERVICE)
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
        return self.tf_buffer.lookup_transform(target, source, Time.from_msg(stamp)).transform

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
            map_fresh = bool(grid and now - float(grid["stamp"]) <= 2.0)
        integrated_ready = self.publisher.get_subscription_count() > 0
        map_service_ready = self.map_client.service_is_ready()
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
            "mapGrid": grid if map_fresh else None,
            "motionPresets": [],
            "expressions": [],
            "linkcraftResources": [],
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "taskMasterState": task_state if task_state_fresh else None,
        }

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
            if path.startswith("/v1/maps/") and path.endswith("/grid"):
                map_key = unquote(path[len("/v1/maps/") : -len("/grid")]).strip("/")
                if not re.fullmatch(r"[\w\u4e00-\u9fff][\w\u4e00-\u9fff .-]{0,99}", map_key, re.UNICODE):
                    raise AdapterError("无效地图名称", 400)
                return self.send_json(200, self.node.stored_grid(map_key))
            raise AdapterError("接口不存在", 404)
        except AdapterError as error:
            return self.send_json(error.status, {"message": str(error)})
        except Exception:
            return self.send_json(500, {"message": "AIMDK 桥接内部错误"})

    def do_POST(self) -> None:
        try:
            self.authorize()
            body = self.read_json()
            path = urlparse(self.path).path
            if path == "/v1/mapping/start":
                state = self.node.snapshot()
                required = ("aimdk", "slam", "lidar")
                not_ready = [item["label"] for item in state["checks"] if item["id"] in required and not item["ready"]]
                if not_ready:
                    raise AdapterError("建图预检未通过: " + "、".join(not_ready), 409)
                with self.node.lock:
                    if self.node.mapping_status != "idle":
                        raise AdapterError("已有建图请求尚未完成", 409)
                    if self.node.relocalization_pending:
                        raise AdapterError("地图切换尚未完成确认", 409)
                    if self.node.last_navigation.get("status") in ("requested", "running", "paused"):
                        raise AdapterError("请先停止导航", 409)
                    self.node.publish_command("start_mapping")
                    self.node.mapping_status = "requested"
                    self.node.lidar_epoch = str(time.time_ns())
                    self.node.last_lidar = None
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
                raise AdapterError("AIMDK 预设动作资源尚未完成目标机器人白名单验证", 501)
            raise AdapterError("接口不存在", 404)
        except AdapterError as error:
            return self.send_json(error.status, {"message": str(error)})
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
