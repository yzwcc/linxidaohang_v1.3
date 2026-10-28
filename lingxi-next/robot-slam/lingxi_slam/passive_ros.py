"""Opt-in Humble subscription session; no motion, TF or map publishers."""
from dataclasses import dataclass
import math
import os
import re
import threading
import time

from .worker import SensorWorker


@dataclass(frozen=True)
class Topics:
    cloud: str
    imu: str

    def __post_init__(self):
        for value in (self.cloud, self.imu):
            if (type(value) is not str or len(value) > 256
                    or re.fullmatch(r"(?:/[A-Za-z_][A-Za-z0-9_]*)+", value) is None):
                raise ValueError("explicit_absolute_sensor_topics_required")
        if self.cloud == self.imu:
            raise ValueError("distinct_cloud_and_imu_topics_required")


def run_passive(worker, topics, seconds, *, stop_event=None):
    """Run an isolated context/executor. Injected engine must not actuate."""
    if (not isinstance(worker, SensorWorker) or not isinstance(topics, Topics)
            or type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 300
            or worker.snapshot()["state"] != "new"
            or (stop_event is not None and not isinstance(stop_event, threading.Event))):
        raise ValueError("new_worker_and_bounded_passive_session_required")
    if os.environ.get("ROS_DISTRO") != "humble":
        raise RuntimeError("explicit_sourced_humble_environment_required")

    import rclpy
    from rclpy.context import Context
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
    from sensor_msgs.msg import Imu, PointCloud2

    context = Context()
    node = executor = None
    started = time.monotonic()
    reason = "duration_reached"
    try:
        rclpy.init(args=[], context=context)
        node = rclpy.create_node(
            "lingxi_passive_slam_input", context=context, use_global_arguments=False,
            enable_rosout=False, start_parameter_services=False,
        )
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(node)
        for message_type, topic, callback, depth in (
            (PointCloud2, topics.cloud, worker.offer_cloud, 8),
            (Imu, topics.imu, worker.offer_imu, 256),
        ):
            qos = QoSProfile(
                history=HistoryPolicy.KEEP_LAST, depth=depth,
                reliability=ReliabilityPolicy.BEST_EFFORT, durability=DurabilityPolicy.VOLATILE,
            )
            node.create_subscription(message_type, topic, callback, qos)
        if stop_event is None or not stop_event.is_set():
            worker.start()
        while time.monotonic() - started < seconds:
            if stop_event is not None and stop_event.is_set():
                reason = "requested_stop"
                break
            if not context.ok():
                reason = "ros_context_stopped"
                break
            state = worker.snapshot()["state"]
            if state not in ("starting", "running"):
                reason = "worker_failed" if state == "failed" else "worker_stopped"
                break
            executor.spin_once(timeout_sec=min(worker.limits.tick_seconds,
                                              max(0., seconds - (time.monotonic() - started))))
    finally:
        # Attempt every cleanup even if an earlier ROS teardown fails.
        cleanup_failed = False
        for cleanup in (
            worker.stop,
            lambda: executor.shutdown(timeout_sec=1.) if executor is not None else True,
            lambda: node.destroy_node() if node is not None else None,
            context.try_shutdown,
        ):
            try:
                if cleanup() is False:
                    cleanup_failed = True
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            raise RuntimeError("passive_ros_cleanup_failed")
    summary = worker.snapshot()
    return {
        "purpose": "passive_humble_sensor_session_not_mapping_or_navigation_acceptance",
        "stopReason": "worker_failed" if summary["state"] == "failed" else reason,
        "elapsedWallSecondsIncludingCleanup": time.monotonic() - started,
        "worker": summary,
        "boundaries": {name: False for name in (
            "motionCommandsSent", "tfPublished", "mapSaved", "mapActivated",
            "calibrationValidated", "relocalizationValidated", "navigationValidated", "deploymentApproved",
        )},
    }
