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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lingxi_navigation.nav2_producer import Nav2Generation, OwnedNav2Backend, load_profile, spin_callbacks
from lingxi_navigation.physical_bridge import PhysicalNavigationBridge
from lingxi_navigation.physical_ros import AimdkVelocityOutput, FileMapActivator, digest_footprint
from lingxi_navigation.physical_socket import PhysicalNavigationSocket


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name.lower()}_required")
    return value


def _bool_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _close_runtime(rclpy, node, executor, output, backend, socket=None):
    errors = []
    closers = [(value.close, {}) for value in (socket, backend, output) if value is not None]
    if executor is not None:
        closers.append((executor.shutdown, dict(timeout_sec=1.)))
    if node is not None:
        closers.append((node.destroy_node, {}))
    closers.append((rclpy.try_shutdown, {}))
    for close, kwargs in closers:
        try:
            if close(**kwargs) is False:
                raise RuntimeError('resource_cleanup_not_confirmed')
        except Exception as error:
            errors.append(error)
            print(f"physical navigation resource cleanup incomplete: {error}", file=sys.stderr)
    return errors


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
    if digest_footprint(footprint) != identity.footprintSha256:
        raise RuntimeError("active_map_footprint_mismatch")
    profile = load_profile(Path(_required_env("LINGXI_NAV2_PROFILE_FILE")), identity,
        map_frame=_required_env("LINGXI_MAP_FRAME"), base_frame=_required_env("LINGXI_BASE_FRAME"),
        sensor_frame=_required_env("LINGXI_SENSOR_FRAME"), cloud_topic=_required_env("LINGXI_POINTCLOUD_TOPIC"),
        odometry_topic=_required_env("LINGXI_ODOMETRY_TOPIC"), terrain_topic=_required_env("LINGXI_TERRAIN_TOPIC"),
        tf_topic=os.environ.get("LINGXI_TF_TOPIC", "/tf"),
        tf_static_topic=os.environ.get("LINGXI_TF_STATIC_TOPIC", "/tf_static"))
    run_root = Path(_required_env("LINGXI_NAV2_RUN_ROOT"))
    if (not run_root.is_absolute() or run_root.resolve(strict=True) != run_root or not run_root.is_dir()
            or run_root.stat().st_uid != os.geteuid() or run_root.stat().st_mode & 0o077):
        raise RuntimeError("private_owned_nav2_run_directory_required")

    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node

    rclpy.init(args=None)
    node = executor = output = backend = None
    try:
        node = Node("lingxi_next_physical_navigation", use_global_arguments=False)
        executor = SingleThreadedExecutor()
        executor.add_node(node)
        output = AimdkVelocityOutput(node, identity)

        def factory(ticket):
            if map_activator.evidence(map_id) != identity or digest_footprint(footprint) != identity.footprintSha256:
                raise RuntimeError("saved_map_evidence_changed_before_nav2_preparation")
            generation = Nav2Generation(profile, ticket, run_root, footprint, executor)
            try:
                generation.connect(output, profile.limits)
                return generation
            except BaseException:
                generation.close()
                raise

        backend = OwnedNav2Backend(identity, output, factory, lambda: spin_callbacks(executor))
        bridge = PhysicalNavigationBridge(backend, identity)
        socket = PhysicalNavigationSocket(socket_path, bridge)
        return rclpy, node, executor, output, backend, socket
    except BaseException:
        _close_runtime(rclpy, node, executor, output, backend)
        raise


def main() -> int:
    runtime = None
    stopping = False
    result = 0

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        runtime = build_runtime()
        while not stopping:
            runtime[-1].step(.02)
    except Exception as error:
        print(f"physical navigation bridge refused to start: {error}", file=sys.stderr)
        result = 78
    finally:
        if runtime is not None and _close_runtime(*runtime):
            result = 78
    return result


if __name__ == "__main__":
    raise SystemExit(main())
