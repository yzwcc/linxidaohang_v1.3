from __future__ import annotations

import importlib
import os
import sys
import threading
from types import ModuleType, SimpleNamespace


def _install_ros_stubs() -> dict[str, object]:
    if "aimdk_msgs.msg" not in sys.modules:
        aimdk_msgs = ModuleType("aimdk_msgs")
        aimdk_msg = ModuleType("aimdk_msgs.msg")
        aimdk_msg.MessageHeader = type("MessageHeader", (), {})
        class PncTaskRequest:
            def __init__(self):
                self.reference_line = []

        aimdk_msg.PncTaskRequest = PncTaskRequest
        aimdk_msgs.msg = aimdk_msg
        sys.modules["aimdk_msgs"] = aimdk_msgs
        sys.modules["aimdk_msgs.msg"] = aimdk_msg

    if "geometry_msgs.msg" not in sys.modules:
        geometry_msgs = ModuleType("geometry_msgs")
        geometry_msg = ModuleType("geometry_msgs.msg")
        class Pose2D:
            def __init__(self):
                self.x = 0.0
                self.y = 0.0
                self.theta = 0.0

        class PoseStamped:
            def __init__(self):
                self.header = SimpleNamespace(stamp=None, frame_id="")
                self.pose = SimpleNamespace(
                    position=SimpleNamespace(x=0.0, y=0.0, z=0.0),
                    orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
                )

        geometry_msg.Pose2D = Pose2D
        geometry_msg.PoseStamped = PoseStamped
        geometry_msgs.msg = geometry_msg
        sys.modules["geometry_msgs"] = geometry_msgs
        sys.modules["geometry_msgs.msg"] = geometry_msg

    if "nav_msgs.msg" not in sys.modules:
        nav_msgs = ModuleType("nav_msgs")
        nav_msg = ModuleType("nav_msgs.msg")
        nav_msg.Odometry = type("Odometry", (), {})
        nav_msgs.msg = nav_msg
        sys.modules["nav_msgs"] = nav_msgs
        sys.modules["nav_msgs.msg"] = nav_msg

    if "rclpy.qos" not in sys.modules:
        rclpy = ModuleType("rclpy")
        qos = ModuleType("rclpy.qos")

        class QoSProfile:
            def __init__(self, **kwargs):
                self.options = kwargs

        qos.QoSProfile = QoSProfile
        qos.QoSReliabilityPolicy = SimpleNamespace(BEST_EFFORT="best_effort")
        qos.QoSDurabilityPolicy = SimpleNamespace(
            TRANSIENT_LOCAL="transient_local",
            VOLATILE="volatile",
        )
        rclpy.qos = qos
        sys.modules["rclpy"] = rclpy
        sys.modules["rclpy.qos"] = qos

    get_message_state: dict[str, object] = {"calls": 0, "value": type("PncResponse", (), {})}
    utilities = ModuleType("rosidl_runtime_py.utilities")

    def get_message(_type_name: str):
        get_message_state["calls"] = int(get_message_state["calls"]) + 1
        value = get_message_state["value"]
        if isinstance(value, BaseException):
            raise value
        return value

    utilities.get_message = get_message
    runtime = ModuleType("rosidl_runtime_py")
    runtime.utilities = utilities
    sys.modules["rosidl_runtime_py"] = runtime
    sys.modules["rosidl_runtime_py.utilities"] = utilities
    return get_message_state


GET_MESSAGE_STATE = _install_ros_stubs()
navigation_client = importlib.import_module("robot_local_navigation_v3.navigation_client")


class FakeLogger:
    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.warning_messages: list[str] = []

    def info(self, message: str) -> None:
        self.info_messages.append(message)

    def warning(self, message: str) -> None:
        self.warning_messages.append(message)


class FakeTimer:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeNode:
    def __init__(self, *, publishers: list[object] | None = None) -> None:
        self.publishers = list(publishers or [])
        self.logger = FakeLogger()
        self.subscriptions: list[str] = []
        self.timers: list[FakeTimer] = []
        self.published_messages: list[object] = []
        self.publisher_qos: list[object] = []
        self.subscription_qos: list[object] = []

    def create_publisher(self, _message_type, _topic, qos):
        self.publisher_qos.append(qos)
        return SimpleNamespace(publish=self.published_messages.append)

    def create_subscription(self, _message_type, topic: str, _callback, **kwargs):
        self.subscriptions.append(topic)
        self.subscription_qos.append(kwargs.get("qos_profile"))
        return SimpleNamespace(topic=topic)

    def create_timer(self, _period_sec: float, callback):
        timer = FakeTimer(callback)
        self.timers.append(timer)
        return timer

    def get_topic_names_and_types(self):
        return [
            (
                navigation_client.PNC_RESPONSE_TOPIC,
                ["aimdk_msgs/msg/PncTaskResponse"],
            )
        ]

    def get_publishers_info_by_topic(self, _topic: str):
        return list(self.publishers)

    def get_logger(self):
        return self.logger

    def get_clock(self):
        return SimpleNamespace(
            now=lambda: SimpleNamespace(to_msg=lambda: "test-stamp")
        )


def _make_client(node: FakeNode, retries: int = 15):
    old_value = os.environ.get("NAV_PNC_RESPONSE_DISCOVERY_RETRIES")
    os.environ["NAV_PNC_RESPONSE_DISCOVERY_RETRIES"] = str(retries)
    try:
        return navigation_client.NavigationClient(node, SimpleNamespace())
    finally:
        if old_value is None:
            os.environ.pop("NAV_PNC_RESPONSE_DISCOVERY_RETRIES", None)
        else:
            os.environ["NAV_PNC_RESPONSE_DISCOVERY_RETRIES"] = old_value


def test_zero_publishers_never_loads_the_missing_message_type():
    GET_MESSAGE_STATE["calls"] = 0
    GET_MESSAGE_STATE["value"] = AssertionError("get_message must not be called")
    node = FakeNode(publishers=[])
    client = _make_client(node)

    assert client._pnc_response_status == "no_publisher"
    assert client._pnc_response_publisher_count == 0
    assert client.pnc_response_subscription is None
    assert GET_MESSAGE_STATE["calls"] == 0
    assert node.subscriptions == ["/slam/lidar_odom"]


def test_discovery_stops_after_the_configured_retry_budget():
    GET_MESSAGE_STATE["calls"] = 0
    GET_MESSAGE_STATE["value"] = AssertionError("get_message must not be called")
    node = FakeNode(publishers=[])
    client = _make_client(node, retries=2)
    timer = client._pnc_response_retry_timer

    client._retry_pnc_response_subscription()
    client._retry_pnc_response_subscription()
    client._retry_pnc_response_subscription()

    assert client._pnc_response_retry_count == 2
    assert client._pnc_response_status == "unavailable"
    assert client._pnc_response_retry_timer is None
    assert timer.cancelled
    assert len(node.logger.warning_messages) == 1


def test_missing_generated_message_type_is_permanently_disabled():
    GET_MESSAGE_STATE["calls"] = 0
    GET_MESSAGE_STATE["value"] = AttributeError(
        "module 'aimdk_msgs.msg' has no attribute 'PncTaskResponse'"
    )
    node = FakeNode(publishers=[object()])
    client = _make_client(node)

    assert GET_MESSAGE_STATE["calls"] == 1
    assert client._pnc_response_status == "unsupported"
    assert client._pnc_response_retry_timer is None
    assert client.pnc_response_subscription is None


def test_publisher_appearing_during_retry_enables_the_subscription():
    GET_MESSAGE_STATE["calls"] = 0
    GET_MESSAGE_STATE["value"] = type("PncResponse", (), {})
    node = FakeNode(publishers=[])
    client = _make_client(node)
    timer = client._pnc_response_retry_timer
    node.publishers.append(object())

    client._retry_pnc_response_subscription()

    assert GET_MESSAGE_STATE["calls"] == 1
    assert client._pnc_response_status == "enabled"
    assert client.pnc_response_subscription is not None
    assert client._pnc_response_retry_timer is None
    assert timer.cancelled
    assert node.subscriptions == ["/slam/lidar_odom", navigation_client.PNC_RESPONSE_TOPIC]


def test_r18_pnc_endpoints_use_compatible_durability_qos():
    GET_MESSAGE_STATE["calls"] = 0
    GET_MESSAGE_STATE["value"] = type("PncResponse", (), {})
    node = FakeNode(publishers=[object()])
    client = _make_client(node)

    assert node.publisher_qos[0].options["durability"] == "volatile"
    assert node.subscription_qos[0].options["reliability"] == "best_effort"
    assert node.subscription_qos[1].options["durability"] == "transient_local"
    assert client._pnc_response_status == "enabled"


def test_task_type_two_normal_mode_requires_the_safe_reference_line():
    point = SimpleNamespace(navigation=SimpleNamespace(task_type=2, pnc_mode=0))
    assert navigation_client.NavigationClient._requires_global_path_safety(point)

    point.navigation.pnc_mode = 1
    assert not navigation_client.NavigationClient._requires_global_path_safety(point)


def test_start_request_contains_the_planned_reference_line():
    node = FakeNode()
    session = navigation_client.NavigationSession.__new__(
        navigation_client.NavigationSession
    )
    session.node = node
    session.publisher = SimpleNamespace(publish=node.published_messages.append)
    session.point = SimpleNamespace(
        id="point-1",
        name="test-point",
        map_id=7,
        navigation=SimpleNamespace(
            task_type=2,
            pnc_mode=0,
            max_forward_speed=0.3,
            target_pose_radius=0.15,
        ),
        pose=SimpleNamespace(
            position=SimpleNamespace(x=2.0, y=3.0, z=0.0),
            orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
        ),
    )
    session.safe_reference_line = [(1.0, 2.0), (2.0, 3.0)]
    session.task_id = 123
    session.republish_period_sec = 1.0
    session._lock = threading.RLock()
    session._current_msg = None
    session._republish_timer = None

    session._send_start()

    message = node.published_messages[0]
    assert [(pose.x, pose.y, pose.theta) for pose in message.reference_line] == [
        (1.0, 2.0, 0.0),
        (2.0, 3.0, 0.0),
    ]
