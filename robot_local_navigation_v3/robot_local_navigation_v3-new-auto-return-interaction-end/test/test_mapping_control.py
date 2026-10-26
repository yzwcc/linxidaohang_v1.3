from __future__ import annotations

import importlib
import math
import sys
import struct
import zlib
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

from robot_local_navigation_v3.config_store import UnifiedConfigStore
from robot_local_navigation_v3.map_label_server_v3 import V3LabelApp
from robot_local_navigation_v3.map_point_store import MapPointStore
from robot_local_navigation_v3.models import UnifiedNavConfig


def _install_stubs(monkeypatch):
    aimdk_msgs = ModuleType("aimdk_msgs")
    aimdk_srv = ModuleType("aimdk_msgs.srv")

    class GetStoredMapByName:
        class Request:
            pass

    aimdk_srv.GetStoredMapByName = GetStoredMapByName
    aimdk_msgs.srv = aimdk_srv
    monkeypatch.setitem(sys.modules, "aimdk_msgs", aimdk_msgs)
    monkeypatch.setitem(sys.modules, "aimdk_msgs.srv", aimdk_srv)

    qos = ModuleType("rclpy.qos")
    qos.QoSDurabilityPolicy = SimpleNamespace(TRANSIENT_LOCAL="transient")
    qos.QoSReliabilityPolicy = SimpleNamespace(RELIABLE="reliable", BEST_EFFORT="best_effort")
    qos.QoSDurabilityPolicy.VOLATILE = "volatile"
    qos.QoSProfile = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "rclpy.qos", qos)

    nav_msgs = ModuleType("nav_msgs")
    nav_msg = ModuleType("nav_msgs.msg")
    nav_msg.Odometry = type("Odometry", (), {})
    nav_msgs.msg = nav_msg
    monkeypatch.setitem(sys.modules, "nav_msgs", nav_msgs)
    monkeypatch.setitem(sys.modules, "nav_msgs.msg", nav_msg)

    sensor_msgs = ModuleType("sensor_msgs")
    sensor_msg = ModuleType("sensor_msgs.msg")
    sensor_msg.Imu = type("Imu", (), {})
    sensor_msg.PointCloud2 = type("PointCloud2", (), {})
    sensor_msgs.msg = sensor_msg
    monkeypatch.setitem(sys.modules, "sensor_msgs", sensor_msgs)
    monkeypatch.setitem(sys.modules, "sensor_msgs.msg", sensor_msg)
    sensor_msgs_py = ModuleType("sensor_msgs_py")
    point_cloud2 = ModuleType("sensor_msgs_py.point_cloud2")
    point_cloud2.read_points = lambda *_args, **_kwargs: []
    sensor_msgs_py.point_cloud2 = point_cloud2
    monkeypatch.setitem(sys.modules, "sensor_msgs_py", sensor_msgs_py)
    monkeypatch.setitem(sys.modules, "sensor_msgs_py.point_cloud2", point_cloud2)

    tf2_ros = ModuleType("tf2_ros")
    tf2_ros.Buffer = type("Buffer", (), {})
    tf2_ros.TransformListener = lambda *_args, **_kwargs: object()
    monkeypatch.setitem(sys.modules, "tf2_ros", tf2_ros)
    rclpy_time = ModuleType("rclpy.time")
    rclpy_time.Time = type("Time", (), {"from_msg": staticmethod(lambda value: value)})
    monkeypatch.setitem(sys.modules, "rclpy.time", rclpy_time)

    std_msgs = ModuleType("std_msgs")
    std_msg = ModuleType("std_msgs.msg")
    class Header:
        pass
    class String:
        def __init__(self):
            self.data = ""
    std_msg.Header = Header
    std_msg.String = String
    std_msgs.msg = std_msg
    monkeypatch.setitem(sys.modules, "std_msgs", std_msgs)
    monkeypatch.setitem(sys.modules, "std_msgs.msg", std_msg)
    return GetStoredMapByName, String


class FakePublisher:
    def __init__(self, subscribers=1):
        self.subscribers = subscribers
        self.messages = []
    def get_subscription_count(self):
        return self.subscribers
    def publish(self, message):
        self.messages.append(message.data)


class FakeFuture:
    def __init__(self, result):
        self.value = result
    def add_done_callback(self, callback):
        callback(self)
    def result(self):
        return self.value


class FakeClient:
    def __init__(self, result):
        self.response = result
        self.requests = []
    def service_is_ready(self):
        return True
    def wait_for_service(self, timeout_sec):
        return True
    def call_async(self, request):
        self.requests.append(request)
        return FakeFuture(self.response)


class FakeNode:
    def __init__(self, publisher, client, tmp_path):
        self.publisher = publisher
        self.client = client
        self.navigation_client = SimpleNamespace(get_status=lambda: {"status": "idle"})
        self.state_path = tmp_path / "mapping-session.json"
        self.logs = []
    def create_publisher(self, *_args, **_kwargs):
        return self.publisher
    def create_client(self, *_args, **_kwargs):
        return self.client
    def create_subscription(self, *_args, **_kwargs):
        return object()
    def get_publishers_info_by_topic(self, _topic):
        return [object()]
    def get_clock(self):
        return SimpleNamespace(now=lambda: SimpleNamespace(to_msg=lambda: "stamp"))
    def get_logger(self):
        return SimpleNamespace(info=self.logs.append, debug=self.logs.append)


def _controller(monkeypatch, tmp_path, response=None, subscribers=1):
    GetStoredMapByName, String = _install_stubs(monkeypatch)
    import os
    monkeypatch.setenv("ROBOT_LOCAL_NAV_MAPPING_STATE", str(tmp_path / "mapping-session.json"))
    module_name = "robot_local_navigation_v3.mapping_control"
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    publisher = FakePublisher(subscribers)
    map_path = tmp_path / "occupancy_map.png"
    map_path.write_bytes(b"png")
    map_root = tmp_path / "map-root"
    monkeypatch.setenv("ROBOT_LOCAL_NAV_MAP_ROOT", str(map_root))
    response = response or SimpleNamespace(
        code=0, map_id=123, map_path=str(map_path),
        data=[0, 25, 65, 100, -1, 0],
        map_info=SimpleNamespace(width=3, height=2, resolution=0.05, origin=SimpleNamespace(
            position=SimpleNamespace(x=-1.0, y=-2.0),
            orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
        ))
    )
    client = FakeClient(response)
    node = FakeNode(publisher, client, tmp_path)
    node.state_path = tmp_path / "mapping-session.json"
    controller = module.MappingController(node)
    controller._latest_lidar_at = __import__("time").monotonic()
    controller._latest_imu_at = __import__("time").monotonic()
    return controller, publisher, client, GetStoredMapByName, String


def test_start_and_save_publish_only_documented_mapping_commands(monkeypatch, tmp_path):
    controller, publisher, client, _, _ = _controller(monkeypatch, tmp_path)
    assert controller.start("showroom_1")["status"] == "mapping"
    result = controller.stop_and_save("showroom_1", timeout_sec=1)
    assert publisher.messages == ["start_mapping", "stop_mapping:showroom_1"]
    assert result["map_id"] == 123
    assert result["map_name"] == "123"
    assert result["source_map_name"] == "showroom_1"
    assert Path(result["map_path"]).is_file()
    assert Path(result["grid_map_info_path"]).read_text().splitlines() == ["20", "20 -38", "3 2"]
    assert client.requests[0].map_name == "showroom_1"
    assert result["status"] == "saved"


def test_preflight_allows_mapping_before_slam_odometry_is_published(monkeypatch, tmp_path):
    controller, publisher, _, _, _ = _controller(monkeypatch, tmp_path)
    controller._publisher_count = lambda topic: 0 if topic == "/slam/lidar_odom" else 1
    status = controller.status()
    assert status["mapping_health"]["can_start"] is True
    assert status["mapping_health"]["odom_data_available"] is False
    assert controller.start("before_odom") == {"ok": True, "status": "mapping", "map_name": "before_odom"}
    assert publisher.messages == ["start_mapping"]


def test_preflight_refuses_stale_sensor_stream_without_publishing(monkeypatch, tmp_path):
    controller, publisher, _, _, _ = _controller(monkeypatch, tmp_path)
    controller._latest_lidar_at = 0.0
    health = controller.status()["mapping_health"]
    assert health["can_start"] is False
    assert health["lidar_data_available"] is False
    try:
        controller.start("stale_sensor")
    except RuntimeError as exc:
        assert "尚未收到实时雷达点云和 IMU 数据" in str(exc)
    else:
        raise AssertionError("stale sensor stream was accepted")
    assert publisher.messages == []


def test_preflight_refuses_stale_imu_stream_without_publishing(monkeypatch, tmp_path):
    controller, publisher, _, _, _ = _controller(monkeypatch, tmp_path)
    controller._latest_imu_at = 0.0
    health = controller.status()["mapping_health"]
    assert health["can_start"] is False
    assert health["imu_data_available"] is False
    try:
        controller.start("stale_imu")
    except RuntimeError as exc:
        assert "尚未收到实时雷达点云和 IMU 数据" in str(exc)
    else:
        raise AssertionError("stale IMU stream was accepted")
    assert publisher.messages == []


def test_live_preview_returns_accumulated_scan_and_robot_path(monkeypatch, tmp_path):
    controller, _, _, _, _ = _controller(monkeypatch, tmp_path)
    controller._active_map_name = "preview_room"
    controller._preview_points[(12, 24)] = (1.0, 2.0)
    controller._robot_pose = {"x": 1.0, "y": 2.0, "yaw": 0.5, "frame_id": "map"}
    controller._robot_path = [{"x": 0.0, "y": 1.0}]

    preview = controller.live_preview()

    assert preview["ok"] is True
    assert preview["points"] == [[1.0, 2.0]]
    assert preview["robot"] == {"x": 1.0, "y": 2.0, "yaw": 0.5}
    assert preview["robot_path"] == [{"x": 0.0, "y": 1.0}]
    assert preview["frame_id"] == "map"


def test_start_refuses_to_publish_without_slam_subscriber(monkeypatch, tmp_path):
    controller, publisher, _, _, _ = _controller(monkeypatch, tmp_path, subscribers=0)
    try:
        controller.start("showroom")
    except RuntimeError as exc:
        assert "未发送建图命令" in str(exc)
    else:
        raise AssertionError("start should require the SDK SLAM subscriber")
    assert publisher.messages == []


def test_invalid_name_never_publishes_a_command(monkeypatch, tmp_path):
    controller, publisher, _, _, _ = _controller(monkeypatch, tmp_path)
    try:
        controller.start("../existing")
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe map name was accepted")
    assert publisher.messages == []


def test_saved_state_recovers_active_mapping_after_restart(monkeypatch, tmp_path):
    controller, _, _, _, _ = _controller(monkeypatch, tmp_path)
    controller.start("restart_safe")
    controller2, _, _, _, _ = _controller(monkeypatch, tmp_path)
    assert controller2.status()["active"] is True
    assert controller2.status()["map_name"] == "restart_safe"


def test_saving_state_blocks_new_mapping_and_retries_only_map_lookup(monkeypatch, tmp_path):
    controller, publisher, client, _, _ = _controller(monkeypatch, tmp_path)
    controller.start("save_in_progress")
    original_wait = client.wait_for_service
    client.wait_for_service = lambda timeout_sec: False

    try:
        controller.stop_and_save("save_in_progress", timeout_sec=1)
    except TimeoutError:
        pass
    else:
        raise AssertionError("a missing saved-map response should remain in the saving state")

    status = controller.status()
    assert status["active"] is False
    assert status["saving"] is True
    assert status["map_name"] == "save_in_progress"
    assert publisher.messages == ["start_mapping", "stop_mapping:save_in_progress"]

    try:
        controller.start("overlap")
    except RuntimeError as exc:
        assert "建图正在进行" in str(exc)
    else:
        raise AssertionError("a new mapping was allowed while save confirmation was pending")

    client.wait_for_service = original_wait
    saved = controller.stop_and_save("save_in_progress", timeout_sec=1)
    assert saved["status"] == "saved"
    assert publisher.messages == ["start_mapping", "stop_mapping:save_in_progress"]
    assert controller.status()["saving"] is False


def test_mapping_start_never_constructs_navigation_or_velocity_messages(monkeypatch, tmp_path):
    controller, publisher, _, _, _ = _controller(monkeypatch, tmp_path)
    controller.start("stationary_robot")
    assert publisher.messages == ["start_mapping"]
    assert all("PncTaskRequest" not in value and "velocity" not in value for value in publisher.messages)


def test_mapping_start_refuses_while_navigation_is_active(monkeypatch, tmp_path):
    controller, publisher, _, _, _ = _controller(monkeypatch, tmp_path)
    controller.node.navigation_client.get_status = lambda: {"status": "navigating"}
    assert controller.status()["mapping_health"]["can_start"] is False
    try:
        controller.start("stationary_robot")
    except RuntimeError as exc:
        assert "请先结束导航" in str(exc)
    else:
        raise AssertionError("mapping started while navigation was active")
    assert publisher.messages == []


def test_stop_generates_compatible_png_from_occupancy_data(monkeypatch, tmp_path):
    controller, publisher, client, _, _ = _controller(monkeypatch, tmp_path)
    missing_path = tmp_path / "late-map.png"
    client.response.map_path = str(missing_path)
    client.response.code = 0
    client.response.map_id = 456
    assert controller.start("late_map")["status"] == "mapping"

    result = controller.stop_and_save("late_map", timeout_sec=1)
    assert Path(result["map_path"]) == controller._map_root / "456" / "occupancy_map.png"
    assert Path(result["map_path"]).is_file()
    assert Path(result["source_map_path"]) == missing_path
    assert publisher.messages == ["start_mapping", "stop_mapping:late_map"]


def test_saved_aimdk_map_is_discovered_and_served_by_existing_map_web_api(monkeypatch, tmp_path):
    controller, _, _, _, _ = _controller(monkeypatch, tmp_path)
    controller.start("inspection_room")
    saved = controller.stop_and_save("inspection_room", timeout_sec=1)

    app_home = tmp_path / "web-app"
    config_store = UnifiedConfigStore(app_home / "unified_nav_config.json")
    config_store.save(UnifiedNavConfig())
    app = V3LabelApp(config_store, MapPointStore(app_home / "maps"), Mock(), Mock())
    maps = app.discover_maps(str(controller._map_root))
    payload = app.get_map_payload(str(controller._map_root), saved["map_name"])
    image, content_type = app.get_map_image(str(controller._map_root), saved["map_name"])

    assert [item["map_id"] for item in maps] == [saved["map_id"]]
    assert payload["map"]["width"] == 3
    assert payload["map"]["height"] == 2
    assert payload["map"]["resolution_pixels_per_meter"] == 20.0
    assert payload["map"]["origin_u"] == 20.0
    assert payload["map"]["origin_v"] == -38.0
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert content_type == "image/png"


def test_live_lidar_callback_transforms_and_accumulates_map_points(monkeypatch, tmp_path):
    controller, _, _, _, _ = _controller(monkeypatch, tmp_path)
    controller._active_map_name = "callback_preview"
    controller._robot_pose = {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame_id": "map"}
    translation = SimpleNamespace(x=2.0, y=3.0, z=0.0)
    rotation = SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0)
    controller._tf_buffer = SimpleNamespace(lookup_transform=lambda *_args: SimpleNamespace(
        transform=SimpleNamespace(translation=translation, rotation=rotation)
    ))
    module = importlib.import_module("robot_local_navigation_v3.mapping_control")
    monkeypatch.setattr(module.point_cloud2, "read_points", lambda *_args, **_kwargs: [(1.0, 2.0, 0.0), (1.01, 2.0, 0.0)])
    stamp = SimpleNamespace(sec=100, nanosec=1)
    message = SimpleNamespace(header=SimpleNamespace(frame_id="lidar_chest_front", stamp=stamp))

    controller._on_lidar(message)
    preview = controller.live_preview()

    assert preview["ok"] is True
    assert preview["points"] == [[3.01, 5.0]]
    assert preview["updated_at"] == 100.000000001


def test_sensor_callbacks_skip_preview_work_when_mapping_is_idle(monkeypatch, tmp_path):
    controller, _, _, _, _ = _controller(monkeypatch, tmp_path)
    lookup_transform = Mock(side_effect=AssertionError("idle preview must not request TF"))
    controller._tf_buffer = SimpleNamespace(lookup_transform=lookup_transform)
    module = importlib.import_module("robot_local_navigation_v3.mapping_control")
    read_points = Mock(side_effect=AssertionError("idle preview must not decode point clouds"))
    monkeypatch.setattr(module.point_cloud2, "read_points", read_points)
    message = SimpleNamespace(
        header=SimpleNamespace(
            frame_id="lidar_chest_front",
            stamp=SimpleNamespace(sec=100, nanosec=0),
        )
    )

    controller._on_lidar(message)

    assert controller._latest_lidar_at > 0
    lookup_transform.assert_not_called()
    read_points.assert_not_called()


def test_materialized_png_encodes_known_free_and_occupied_cells(monkeypatch, tmp_path):
    controller, _, _, _, _ = _controller(monkeypatch, tmp_path)
    result = controller._materialize_map("room", SimpleNamespace(
        map_id=124,
        map_path="/vendor/maps/124.png",
        data=[0, 100],
        map_info=SimpleNamespace(width=2, height=1, resolution=0.1, origin=SimpleNamespace(
            position=SimpleNamespace(x=0.0, y=0.0),
            orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
        )),
    ))
    encoded = Path(result["map_path"]).read_bytes()
    assert encoded.startswith(b"\x89PNG\r\n\x1a\n")
    pos = 8
    chunks = []
    while pos < len(encoded):
        length = struct.unpack(">I", encoded[pos:pos + 4])[0]
        kind = encoded[pos + 4:pos + 8]
        data = encoded[pos + 8:pos + 8 + length]
        checksum = struct.unpack(">I", encoded[pos + 8 + length:pos + 12 + length])[0]
        assert checksum == zlib.crc32(kind + data) & 0xFFFFFFFF
        chunks.append((kind, data))
        pos += 12 + length
    ihdr = next(data for kind, data in chunks if kind == b"IHDR")
    assert struct.unpack(">IIBBBBB", ihdr) == (2, 1, 8, 0, 0, 0, 0)
    pixels = zlib.decompress(b"".join(data for kind, data in chunks if kind == b"IDAT"))
    assert pixels == bytes((0, 254, 0))


def test_materialize_refuses_rotated_origin_and_does_not_create_map_dir(monkeypatch, tmp_path):
    controller, _, _, _, _ = _controller(monkeypatch, tmp_path)
    response = SimpleNamespace(
        map_id=125,
        map_path="/vendor/maps/125.png",
        data=[0],
        map_info=SimpleNamespace(width=1, height=1, resolution=0.05, origin=SimpleNamespace(
            position=SimpleNamespace(x=0.0, y=0.0),
            orientation=SimpleNamespace(x=0.0, y=0.0, z=math.sin(0.2), w=math.cos(0.2)),
        )),
    )
    try:
        controller._materialize_map("room", response)
    except ValueError as exc:
        assert "不支持旋转地图" in str(exc)
    else:
        raise AssertionError("rotated map was imported with axis-aligned pixel coordinates")
    assert not (controller._map_root / "125").exists()


def test_invalid_saved_map_fails_immediately_and_clears_stopped_session(monkeypatch, tmp_path):
    controller, publisher, client, _, _ = _controller(monkeypatch, tmp_path)
    client.response.map_info.origin.orientation.z = math.sin(0.2)
    client.response.map_info.origin.orientation.w = math.cos(0.2)
    controller.start("rotated_room")

    try:
        controller.stop_and_save("rotated_room", timeout_sec=1)
    except RuntimeError as exc:
        assert "不支持旋转地图" in str(exc)
    else:
        raise AssertionError("unsupported map origin should fail instead of timing out")

    status = controller.status()
    assert publisher.messages == ["start_mapping", "stop_mapping:rotated_room"]
    assert status["active"] is False
    assert status["last_result"]["status"] == "import_failed"


def test_materialize_refuses_to_overwrite_existing_map_directory(monkeypatch, tmp_path):
    controller, _, _, _, _ = _controller(monkeypatch, tmp_path)
    existing = controller._map_root / "126"
    existing.mkdir(parents=True)
    sentinel = existing / "keep.txt"
    sentinel.write_text("user map data")
    response = SimpleNamespace(
        map_id=126,
        map_path="/vendor/maps/126.png",
        data=[0],
        map_info=SimpleNamespace(width=1, height=1, resolution=0.05, origin=SimpleNamespace(
            position=SimpleNamespace(x=0.0, y=0.0),
            orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
        )),
    )
    try:
        controller._materialize_map("room", response)
    except ValueError as exc:
        assert "拒绝覆盖" in str(exc)
    else:
        raise AssertionError("existing map directory was overwritten")
    assert sentinel.read_text() == "user map data"
