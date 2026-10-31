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


def wait_for_publishers(node, topics, deadline, stop_event):
    """Allow bounded DDS discovery before starting the sensor-silence watchdog."""
    while stop_event is None or not stop_event.is_set():
        if all(node.count_publishers(topic) > 0 for topic in (topics.cloud, topics.imu)):
            return True
        if time.monotonic() >= deadline:
            raise TimeoutError("sensor_publisher_discovery_deadline_exceeded")
        if stop_event is None:
            time.sleep(.02)
        else:
            stop_event.wait(.02)
    return False


def run_passive(worker, topics, seconds, *, stop_event=None, admission=None, on_input_closed=None,
                setup=None, on_sensor=None):
    """Run isolated subscriptions; optional finalization follows clean ROS teardown.

    A mapping caller provides its own admission and completion callback. Failure
    is reported to that callback as ``input_failed`` so it can discard, not save.
    The worker is still stopped here even when the callback fails.
    """
    if admission is not None:
        # Minimal passive-only diagnostic bundles do not use the mapping lifecycle.
        from .sensor_admission import SensorAdmission
    if (not isinstance(worker, SensorWorker) or not isinstance(topics, Topics)
            or type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 300
            or worker.snapshot()["state"] != "new"
            or (stop_event is not None and not isinstance(stop_event, threading.Event))
            or setup is not None and not callable(setup)
            or on_sensor is not None and not callable(on_sensor)
            or (admission is None) != (on_input_closed is None)
            or admission is not None and (
                not isinstance(admission, SensorAdmission) or admission.worker is not worker
                or admission.started or admission.closed or not callable(on_input_closed))):
        raise ValueError("new_worker_and_bounded_passive_session_required")
    if os.environ.get("ROS_DISTRO") != "humble":
        raise RuntimeError("explicit_sourced_humble_environment_required")

    import rclpy
    from rclpy.context import Context
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
    from sensor_msgs.msg import Imu, PointCloud2

    context = Context()
    node = executor = extra_cleanup = None
    started = time.monotonic()
    reason, input_completed, finalization = "duration_reached", False, None
    discovery_seconds = 0.
    receiver = worker if admission is None else admission
    try:
        rclpy.init(args=[], context=context)
        node = rclpy.create_node(
            "lingxi_passive_slam_input", context=context, use_global_arguments=False,
            enable_rosout=False, start_parameter_services=False,
        )
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(node)
        def receive(kind, callback, message):
            # Discard cold-start callbacks, not a queue of stale work behind native loading.
            if worker.snapshot()["state"] != "running":
                return False
            if on_sensor is not None:
                on_sensor(kind, message)
            return callback(message)

        for message_type, topic, callback, depth, kind in (
            (PointCloud2, topics.cloud, receiver.offer_cloud, 8, "cloud"),
            (Imu, topics.imu, receiver.offer_imu, 256, "imu"),
        ):
            qos = QoSProfile(
                history=HistoryPolicy.KEEP_LAST, depth=depth,
                reliability=ReliabilityPolicy.BEST_EFFORT, durability=DurabilityPolicy.VOLATILE,
            )
            node.create_subscription(message_type, topic,
                                     lambda message, k=kind, cb=callback: receive(k, cb, message), qos)
        if setup is not None:
            extra_cleanup = setup(node)
            if not callable(extra_cleanup):
                raise ValueError("owned_ros_setup_cleanup_required")
        if stop_event is None or not stop_event.is_set():
            discovery_started = time.monotonic()
            if wait_for_publishers(node, topics, min(started + seconds, discovery_started + 10.), stop_event):
                discovery_seconds = time.monotonic() - discovery_started
                receiver.start()
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
        input_completed = True
    finally:
        # Attempt every cleanup even if an earlier ROS teardown fails.
        cleanup_failed = False
        if extra_cleanup is not None:
            try:
                extra_cleanup()
            except BaseException:
                cleanup_failed = True
        if admission is not None:
            admission.close()
        cleanups = (() if admission is not None else (worker.stop,)) + (
            lambda: executor.shutdown(timeout_sec=1.) if executor is not None else True,
            lambda: node.destroy_node() if node is not None else None,
            context.try_shutdown,
        )
        for cleanup in cleanups:
            try:
                if cleanup() is False:
                    cleanup_failed = True
            except BaseException:
                cleanup_failed = True
        if admission is not None:
            try:
                finalization = on_input_closed(
                    reason if input_completed and not cleanup_failed else "input_failed")
            except BaseException:
                cleanup_failed = True
            finally:
                try:
                    terminal = worker.stop()
                    if terminal["threadAlive"] or terminal["failure"]:
                        cleanup_failed = True
                except BaseException:
                    cleanup_failed = True
        if cleanup_failed:
            raise RuntimeError("passive_ros_cleanup_failed")
    summary = worker.snapshot()
    return {
        "purpose": ("passive_humble_sensor_session_not_mapping_or_navigation_acceptance"
                    if admission is None else "passive_humble_mapping_input_not_navigation_acceptance"),
        "stopReason": "worker_failed" if summary["state"] == "failed" else reason,
        "publisherDiscoverySeconds": discovery_seconds,
        "elapsedWallSecondsIncludingCleanup": time.monotonic() - started,
        "worker": summary,
        **({} if admission is None else {"inputFinalization": finalization}),
        "boundaries": {**{name: False for name in (
            "motionCommandsSent", "tfPublished", "mapSaved", "mapActivated",
            "calibrationValidated", "relocalizationValidated", "navigationValidated", "deploymentApproved",
        )}, "mapSaved": bool(admission is not None and isinstance(finalization, dict)
                            and finalization.get("saved") is True)},
    }
