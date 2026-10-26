from __future__ import annotations

import re
import threading
import time
import json
import os
import math
import struct
import zlib
from pathlib import Path
from typing import Any

from aimdk_msgs.srv import GetStoredMapByName
from nav_msgs.msg import Odometry
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy
from sensor_msgs.msg import Imu, PointCloud2
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header, String
from tf2_ros import Buffer, TransformListener
from rclpy.time import Time


MAP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,48}$")
MAPPING_COMMAND_TOPIC = "/integrated_command"
STORED_MAP_SERVICE = "/aimdk_5Fmsgs/srv/GetStoredMapByName"
DEFAULT_MAP_ROOT = "/agibot/data/var/MapManagerModule"
MAX_MAP_CELLS = 12_000_000
MAX_PREVIEW_POINTS = 50_000
SENSOR_STALE_SEC = 3.0


class MappingController:
    """AIMDK v1.1.0 SLAM mapping commands; this controller never commands motion."""

    def __init__(self, node) -> None:
        self.node = node
        command_qos = QoSProfile(
            depth=10,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            reliability=QoSReliabilityPolicy.RELIABLE,
        )
        self.publisher = node.create_publisher(String, MAPPING_COMMAND_TOPIC, command_qos)
        self.map_client = node.create_client(GetStoredMapByName, STORED_MAP_SERVICE)
        sensor_qos = QoSProfile(
            depth=1,
            durability=QoSDurabilityPolicy.VOLATILE,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
        )
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, node, spin_thread=False)
        self._latest_lidar_at = 0.0
        self._latest_imu_at = 0.0
        self._latest_odom_at = 0.0
        self._last_cloud_stamp = 0.0
        self._preview_points: dict[tuple[int, int], tuple[float, float]] = {}
        self._robot_path: list[dict[str, float]] = []
        self._robot_pose: dict[str, float] = {}
        node.create_subscription(PointCloud2, "/aima/hal/sensor/lidar_chest_front/lidar_pointcloud", self._on_lidar, sensor_qos)
        node.create_subscription(Imu, "/aima/hal/sensor/lidar_chest_front/imu", self._on_imu, sensor_qos)
        node.create_subscription(Odometry, "/slam/lidar_odom", self._on_odom, sensor_qos)
        self._lock = threading.RLock()
        self._state_path = Path(os.environ.get(
            "ROBOT_LOCAL_NAV_MAPPING_STATE",
            str(Path(os.environ.get("ROBOT_LOCAL_NAVIGATION_V3_HOME", Path.home() / "robot_local_navigation_v3")) / "mapping-session.json"),
        )).expanduser()
        self._map_root = Path(os.environ.get("ROBOT_LOCAL_NAV_MAP_ROOT", DEFAULT_MAP_ROOT)).expanduser()
        self._active_map_name = ""
        self._last_result: dict[str, Any] = {}
        self._load_state()

    def status(self) -> dict[str, Any]:
        with self._lock:
            result_status = str(self._last_result.get("status", ""))
            saving = result_status in {"saving", "save_timeout"}
            now = time.monotonic()
            lidar_publishers = self._publisher_count("/aima/hal/sensor/lidar_chest_front/lidar_pointcloud")
            imu_publishers = self._publisher_count("/aima/hal/sensor/lidar_chest_front/imu")
            odom_publishers = self._publisher_count("/slam/lidar_odom")
            command_subscribers = self.publisher.get_subscription_count()
            map_service_ready = self.map_client.service_is_ready()
            aimdk_ready = command_subscribers > 0 and map_service_ready
            navigation_idle = self.node.navigation_client.get_status().get("status", "idle") == "idle"
            lidar_data = now - self._latest_lidar_at <= SENSOR_STALE_SEC
            imu_data = now - self._latest_imu_at <= SENSOR_STALE_SEC
            odom_data = now - self._latest_odom_at <= SENSOR_STALE_SEC
            can_start = (
                lidar_publishers > 0
                and imu_publishers > 0
                and lidar_data
                and imu_data
                and aimdk_ready
                and navigation_idle
            )
            active = bool(self._active_map_name)
            diagnosis = (
                "雷达或 IMU 尚未发现发布者，检查传感器服务。" if lidar_publishers < 1 or imu_publishers < 1 else
                "尚未收到新的雷达/IMU 数据，请检查传感器数据流。" if not (lidar_data and imu_data) else
                "导航任务仍在运行；请先停止导航，再使用遥控器建图。" if not navigation_idle else
                "AIMDK 尚未订阅建图命令或地图存储服务未就绪。" if not aimdk_ready else
                "建图条件已就绪；开始后等待 SLAM 里程计和坐标变换，再显示累计地图。"
            )
            return {
                "active": active,
                "saving": saving,
                "map_name": self._active_map_name or (str(self._last_result.get("map_name", "")) if saving else ""),
                "status": "saving" if saving else "mapping" if self._active_map_name else str(self._last_result.get("status", "idle")),
                "mapping_health": {
                    "ok": can_start,
                    "can_start": can_start,
                    "agibot_software": "unknown",
                    "hal_sensor_running": lidar_publishers > 0 and imu_publishers > 0,
                    "slam_running": odom_publishers > 0,
                    "lidar_publishers": lidar_publishers,
                    "imu_publishers": imu_publishers,
                    "odom_publishers": odom_publishers,
                    "mapping_command_subscribers": command_subscribers,
                    "lidar_data_available": lidar_data,
                    "imu_data_available": imu_data,
                    "odom_data_available": odom_data,
                    "mapping_related_topics": [
                        topic for topic, count in (("/aima/hal/sensor/lidar_chest_front/lidar_pointcloud", lidar_publishers), ("/aima/hal/sensor/lidar_chest_front/imu", imu_publishers), ("/slam/lidar_odom", odom_publishers)) if count
                    ],
                    "mapping_related_services": [STORED_MAP_SERVICE] if map_service_ready else [],
                    "aimdk_v1_1_0_ready": aimdk_ready,
                    "mapping_prerequisites": [
                        {"name": "雷达点云发布者", "available": lidar_publishers > 0},
                        {"name": "雷达点云实时数据", "available": lidar_data},
                        {"name": "雷达 IMU 发布者", "available": imu_publishers > 0},
                        {"name": "雷达 IMU 实时数据", "available": imu_data},
                        {"name": "导航任务已停止", "available": navigation_idle},
                        {"name": "AIMDK 建图命令订阅端", "available": command_subscribers > 0},
                        {"name": "AIMDK GetStoredMapByName 服务", "available": map_service_ready},
                        {"name": "SLAM 实时里程计（开始后出现）", "available": odom_data},
                    ],
                    "mapping_backend_available": aimdk_ready,
                    "mapping_backend_reason": "" if aimdk_ready else "AIMDK 建图命令订阅端或地图存储服务未就绪",
                    "checked_at": time.time(),
                    "diagnosis": diagnosis,
                },
                "last_result": dict(self._last_result),
            }

    def live_preview(self) -> dict[str, Any]:
        with self._lock:
            if not self._active_map_name:
                return {"ok": False, "message": "建图会话未运行"}
            if not self._preview_points or not self._robot_pose:
                return {"ok": False, "message": "等待 SLAM 里程计与雷达坐标变换，收到后将显示累计地图"}
            return {
                "ok": True,
                "topic": "/aima/hal/sensor/lidar_chest_front/lidar_pointcloud",
                "source_frame": "lidar_chest_front",
                "frame_id": self._robot_pose.get("frame_id", "map"),
                "point_count": len(self._preview_points),
                "points": [[x, y] for x, y in self._preview_points.values()],
                "robot": {key: value for key, value in self._robot_pose.items() if key != "frame_id"},
                "robot_path": list(self._robot_path),
                "updated_at": self._last_cloud_stamp,
            }

    def _publisher_count(self, topic: str) -> int:
        try:
            return len(self.node.get_publishers_info_by_topic(topic))
        except Exception:
            return 0

    def _on_imu(self, _message: Imu) -> None:
        self._latest_imu_at = time.monotonic()

    def _on_odom(self, message: Odometry) -> None:
        now = time.monotonic()
        self._latest_odom_at = now
        with self._lock:
            if not self._active_map_name:
                return
        pose = message.pose.pose
        orientation = pose.orientation
        yaw = math.atan2(
            2.0 * (orientation.w * orientation.z + orientation.x * orientation.y),
            1.0 - 2.0 * (orientation.y * orientation.y + orientation.z * orientation.z),
        )
        current = {"x": float(pose.position.x), "y": float(pose.position.y), "yaw": yaw, "frame_id": message.header.frame_id or "map"}
        with self._lock:
            self._robot_pose = current
            if not self._robot_path or math.hypot(current["x"] - self._robot_path[-1]["x"], current["y"] - self._robot_path[-1]["y"]) >= 0.1:
                self._robot_path.append({"x": current["x"], "y": current["y"]})
                self._robot_path = self._robot_path[-3000:]

    def _on_lidar(self, message: PointCloud2) -> None:
        self._latest_lidar_at = time.monotonic()
        with self._lock:
            if not self._active_map_name:
                return
        stamp = message.header.stamp.sec + message.header.stamp.nanosec / 1e9
        if stamp <= self._last_cloud_stamp:
            return
        self._last_cloud_stamp = stamp
        odom_frame = ""
        try:
            with self._lock:
                odom_frame = str(self._robot_pose.get("frame_id", ""))
            if not odom_frame:
                return
            source_frame = str(message.header.frame_id).lstrip("/")
            transform = self._tf_buffer.lookup_transform(odom_frame, source_frame, Time.from_msg(message.header.stamp))
            translation = transform.transform.translation
            rotation = transform.transform.rotation
            qx, qy, qz, qw = rotation.x, rotation.y, rotation.z, rotation.w
            frame_points: dict[tuple[int, int], tuple[float, float]] = {}
            for point in point_cloud2.read_points(message, field_names=("x", "y", "z"), skip_nans=True):
                x, y, z = float(point[0]), float(point[1]), float(point[2])
                if not math.isfinite(x + y + z) or x * x + y * y > 225 or abs(z) > 2.5:
                    continue
                tx = 2.0 * (qy * z - qz * y)
                ty = 2.0 * (qz * x - qx * z)
                tz = 2.0 * (qx * y - qy * x)
                wx = x + qw * tx + qy * tz - qz * ty + translation.x
                wy = y + qw * ty + qz * tx - qx * tz + translation.y
                key = (round(wx * 12), round(wy * 12))
                frame_points[key] = (round(wx, 3), round(wy, 3))
                if len(frame_points) >= 4000:
                    break
            with self._lock:
                self._preview_points.update(frame_points)
                while len(self._preview_points) > MAX_PREVIEW_POINTS:
                    self._preview_points.pop(next(iter(self._preview_points)))
        except Exception as exc:
            self.node.get_logger().debug(f"Live mapping preview unavailable: {exc}")

    def start(self, map_name: str) -> dict[str, Any]:
        self._validate_name(map_name)
        nav_status = self.node.navigation_client.get_status().get("status", "idle")
        if nav_status != "idle":
            raise RuntimeError("导航任务尚未停止；请先结束导航，再由外置遥控器移动机器人建图")
        with self._lock:
            if self._active_map_name or self._last_result.get("status") in {"saving", "save_timeout"}:
                raise RuntimeError(f"建图正在进行：{self._active_map_name}")
            subscribers = self.publisher.get_subscription_count()
            if subscribers < 1:
                raise RuntimeError("AIMDK SLAM 尚未订阅 /integrated_command，未发送建图命令")
            if not self.map_client.service_is_ready():
                raise RuntimeError("AIMDK GetStoredMapByName 服务尚未就绪，未发送建图命令")
            if self._publisher_count("/aima/hal/sensor/lidar_chest_front/lidar_pointcloud") < 1 or self._publisher_count("/aima/hal/sensor/lidar_chest_front/imu") < 1:
                raise RuntimeError("AIMDK 雷达点云或 IMU 尚无发布者，未发送建图命令")
            if time.monotonic() - self._latest_lidar_at > SENSOR_STALE_SEC or time.monotonic() - self._latest_imu_at > SENSOR_STALE_SEC:
                raise RuntimeError("尚未收到实时雷达点云和 IMU 数据，请先检查传感器链路")
            msg = String()
            msg.data = "start_mapping"
            self.publisher.publish(msg)
            self._preview_points.clear()
            self._robot_path.clear()
            self._robot_pose.clear()
            self._active_map_name = map_name
            self._last_result = {"status": "mapping", "map_name": map_name}
            self._persist_state()
        self.node.get_logger().info(f"AIMDK mapping started: map_name={map_name}")
        return {"ok": True, "status": "mapping", "map_name": map_name}

    def stop_and_save(self, map_name: str, timeout_sec: float = 120.0) -> dict[str, Any]:
        self._validate_name(map_name)
        with self._lock:
            already_saving = self._last_result.get("status") in {"saving", "save_timeout"}
            saved_map_name = str(self._last_result.get("map_name", ""))
            if self._active_map_name != map_name and not (already_saving and saved_map_name == map_name):
                raise RuntimeError("当前建图名称与停止请求不一致；未发送停止命令")
            if not already_saving:
                if not self.publisher.get_subscription_count():
                    raise RuntimeError("AIMDK SLAM 已断开 /integrated_command；未发送停止命令")
                msg = String()
                msg.data = f"stop_mapping:{map_name}"
                self.publisher.publish(msg)
                self._active_map_name = ""
                self._last_result = {"status": "saving", "map_name": map_name}
                self._persist_state()

        deadline = time.monotonic() + max(1.0, min(float(timeout_sec), 120.0))
        last_error = "地图保存尚未完成"
        while time.monotonic() < deadline:
            if not self.map_client.wait_for_service(timeout_sec=1.0):
                last_error = "AIMDK GetStoredMapByName 服务暂不可用"
                continue
            request = GetStoredMapByName.Request()
            request.header = Header()
            request.header.stamp = self.node.get_clock().now().to_msg()
            request.map_name = map_name
            future = self.map_client.call_async(request)
            completed = threading.Event()
            future.add_done_callback(lambda _future: completed.set())
            if not completed.wait(min(5.0, max(0.1, deadline - time.monotonic()))):
                last_error = "查询已保存地图超时，正在重试"
                continue
            try:
                result = future.result()
            except Exception as exc:
                last_error = f"查询已保存地图失败：{exc}"
                continue
            if result is None or int(result.code) != 0 or int(result.map_id) <= 0:
                last_error = f"AIMDK 尚未确认地图保存成功（code={getattr(result, 'code', '无响应')}）"
                continue
            try:
                payload = self._materialize_map(map_name, result)
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                with self._lock:
                    self._active_map_name = ""
                    self._last_result = {
                        "status": "import_failed",
                        "map_name": map_name,
                        "map_id": int(result.map_id),
                        "message": str(exc),
                    }
                    self._persist_state()
                raise RuntimeError(f"AIMDK 已保存地图，但导入现有地图目录失败：{exc}") from exc
            with self._lock:
                self._active_map_name = ""
                self._last_result = payload
                self._persist_state()
            self.node.get_logger().info(
                f"AIMDK mapping saved: map_name={map_name}, map_id={payload['map_id']}, "
                f"map_path={payload['map_path']}"
            )
            return payload

        with self._lock:
            self._last_result = {"status": "save_timeout", "map_name": map_name, "message": last_error}
            self._persist_state()
        raise TimeoutError(f"{last_error}；已发送停止命令，请在机器人上确认地图状态")

    def _materialize_map(self, map_name: str, result: Any) -> dict[str, Any]:
        map_id = int(result.map_id)
        info = result.map_info
        width, height = int(info.width), int(info.height)
        resolution = float(info.resolution)
        if map_id <= 0 or width <= 0 or height <= 0 or width * height > MAX_MAP_CELLS:
            raise ValueError("AIMDK 返回的地图 ID 或栅格尺寸无效/超过导入上限")
        if not math.isfinite(resolution) or resolution <= 0:
            raise ValueError("AIMDK 返回的地图 ID、栅格尺寸或分辨率无效")
        origin = info.origin
        q = origin.orientation
        if not _is_axis_aligned(q):
            raise ValueError("该地图原点包含旋转；当前标点坐标模型不支持旋转地图，未导入以避免点位坐标错位")
        try:
            values = [int(value) for value in result.data]
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("AIMDK 地图 occupancy data 包含非整数值") from exc
        if len(values) != width * height or any(int(value) < -1 or int(value) > 100 for value in values):
            raise ValueError("AIMDK 地图 occupancy data 长度或取值无效")

        target = self._map_root / str(map_id)
        self._map_root.mkdir(parents=True, exist_ok=True)
        try:
            target.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise ValueError(f"地图 ID {map_id} 已存在；拒绝覆盖现有地图目录") from exc
        image_path = target / "occupancy_map.png"
        info_path = target / "grid_map_info.txt"
        metadata_path = target / "lingxi_map_import.json"
        pixels_per_meter = 1.0 / resolution
        origin_u = -float(origin.position.x) * pixels_per_meter
        origin_v = height + float(origin.position.y) * pixels_per_meter
        try:
            _write_new_file(image_path, _occupancy_png(values, width, height))
            _write_new_file(info_path, (
                f"{pixels_per_meter:.12g}\n{origin_u:.12g} {origin_v:.12g}\n{width} {height}\n"
            ).encode("ascii"))
            _write_new_file(metadata_path, json.dumps({
                "map_name": str(map_id),
                "map_id": map_id,
                "source_map_name": map_name,
                "map_info": {
                    "resolution_pixels_per_meter": pixels_per_meter,
                    "origin_u": origin_u,
                    "origin_v": origin_v,
                    "width": width,
                    "height": height,
                },
            }, ensure_ascii=False, indent=2).encode("utf-8"))
        except Exception:
            for path in (image_path, info_path, metadata_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            try:
                target.rmdir()
            except OSError:
                pass
            raise

        return {
            "ok": True,
            "status": "saved",
            "map_name": str(map_id),
            "source_map_name": map_name,
            "map_id": map_id,
            "map_path": str(image_path),
            "source_map_path": str(result.map_path),
            "grid_map_info_path": str(info_path),
            "map_metadata_path": str(metadata_path),
            "width": width,
            "height": height,
            "resolution_m_per_cell": resolution,
            "imported_for_point_editing": True,
        }

    @staticmethod
    def _validate_name(map_name: str) -> None:
        if not MAP_NAME_PATTERN.fullmatch(map_name):
            raise ValueError("地图名称仅支持 1-48 位字母、数字、下划线和连字符")

    def _load_state(self) -> None:
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._active_map_name = str(state.get("active_map_name", ""))
        self._last_result = state.get("last_result", {}) if isinstance(state.get("last_result"), dict) else {}

    def _persist_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._state_path.with_suffix(".tmp")
        temp.write_text(json.dumps({
            "active_map_name": self._active_map_name,
            "last_result": self._last_result,
        }, ensure_ascii=False), encoding="utf-8")
        temp.replace(self._state_path)


def _is_axis_aligned(q: Any, tolerance: float = 1e-5) -> bool:
    x, y, z, w = float(q.x), float(q.y), float(q.z), float(q.w)
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if not math.isfinite(norm) or norm < 1e-9:
        return False
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    # q and -q encode the same rotation; require roll, pitch and yaw to be zero.
    return abs(x) <= tolerance and abs(y) <= tolerance and abs(z) <= tolerance and abs(abs(w) - 1.0) <= tolerance


def _occupancy_png(values: list[int], width: int, height: int) -> bytes:
    rows = bytearray()
    for row in range(height):
        rows.append(0)
        source_row = height - 1 - row
        start = source_row * width
        for value in values[start:start + width]:
            value = int(value)
            shade = 205 if value < 0 or value < 65 else 0
            if 0 <= value < 25:
                shade = 254
            rows.append(shade)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), level=6))
        + chunk(b"IEND", b"")
    )


def _write_new_file(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())

