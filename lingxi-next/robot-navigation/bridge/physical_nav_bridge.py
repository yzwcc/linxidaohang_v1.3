#!/usr/bin/env python3
"""Production entrypoint for the independent physical navigation bridge.

The process owns one ROS 2 executor and one private Unix socket.  It is
deliberately fail-closed: the service refuses to start unless the independent
product flag, a verified saved map, an explicit footprint, ROS setup and the
vendor AIMDK message type are all available.  It never falls back to the
vendor navigation service or to a synthetic sensor source.
"""

from __future__ import annotations

import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lingxi_navigation.nav2_action import Nav2ActionDriver, RosNavigationBackend
from lingxi_navigation.physical_bridge import PhysicalNavigationBridge
from lingxi_navigation.physical_ros import AimdkVelocityOutput, FileMapActivator, RosSensorEvidence, RosTopics
from lingxi_navigation.physical_socket import PhysicalNavigationSocket


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name.lower()}_required")
    return value


def _bool_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _topics() -> RosTopics:
    def value(name: str, default: str | None):
        result = os.environ.get(name, default)
        return result.strip() if isinstance(result, str) and result.strip() else None

    return RosTopics(
        cloud=_required_env("LINGXI_POINTCLOUD_TOPIC"),
        scan=value("LINGXI_LASERSCAN_TOPIC", None),
        odometry=_required_env("LINGXI_ODOMETRY_TOPIC"),
        tf=value("LINGXI_TF_TOPIC", "/tf"),
        tf_static=value("LINGXI_TF_STATIC_TOPIC", "/tf_static"),
        global_costmap=_required_env("LINGXI_GLOBAL_COSTMAP_TOPIC"),
        local_costmap=_required_env("LINGXI_LOCAL_COSTMAP_TOPIC"),
        terrain=_required_env("LINGXI_TERRAIN_TOPIC"),
        # AIMDK v1.1.0 has no vendor stop-ack topic. A velocity echo may be
        # enabled for diagnostics, but it is never used for readiness.
        velocity_feedback=value("LINGXI_AIMDK_VELOCITY_FEEDBACK_TOPIC", None),
    )


def build_runtime():
    if not _bool_env("LINGXI_INDEPENDENT_PRODUCT"):
        raise RuntimeError("independent_product_flag_required")
    socket_path = _required_env("LINGXI_NAVIGATION_SOCKET")
    map_root = Path(_required_env("LINGXI_MAP_ROOT"))
    map_id = _required_env("LINGXI_ACTIVE_MAP_ID")
    footprint = Path(_required_env("LINGXI_FOOTPRINT_FILE"))
    if not footprint.is_absolute() or footprint.is_symlink() or not footprint.is_file():
        raise RuntimeError("direct_footprint_file_required")
    map_activator = FileMapActivator(map_root)
    identity = map_activator.evidence(map_id)

    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node

    rclpy.init(args=None)
    node = Node("lingxi_next_physical_navigation")
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    topics = _topics()
    map_frame = _required_env("LINGXI_MAP_FRAME")
    base_frame = _required_env("LINGXI_BASE_FRAME")
    sensor_frame = _required_env("LINGXI_SENSOR_FRAME")
    sensors = RosSensorEvidence(node, identity, topics, map_frame=map_frame, base_frame=base_frame,
                                sensor_frame=sensor_frame, footprint=footprint)
    output = AimdkVelocityOutput(node, identity)
    driver = Nav2ActionDriver(node, map_frame)

    def spin():
        executor.spin_once(timeout_sec=.01)

    backend = RosNavigationBackend(driver, sensors, output, map_activator.activate_map, spin)
    bridge = PhysicalNavigationBridge(backend, identity)
    socket = PhysicalNavigationSocket(socket_path, bridge)
    return rclpy, node, executor, sensors, output, driver, socket


def main() -> int:
    runtime = None
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        runtime = build_runtime()
        while not stopping:
            runtime[-1].step(.02)
        return 0
    except Exception as error:
        print(f"physical navigation bridge refused to start: {error}", file=sys.stderr)
        return 78
    finally:
        if runtime is not None:
            rclpy, node, executor, sensors, output, driver, socket = runtime
            try:
                socket.close()
            except Exception as error:
                print(f"physical navigation bridge close incomplete: {error}", file=sys.stderr)
            for value in (sensors, output, driver):
                try:
                    value.close()
                except Exception:
                    pass
            executor.shutdown()
            node.destroy_node()
            rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
