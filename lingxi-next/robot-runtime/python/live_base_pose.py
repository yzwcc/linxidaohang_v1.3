"""Own passive TF subscriptions and exact-time metadata inside the existing ROS node."""
import threading

from lingxi_slam.base_pose import BasePoseProjector, Tf2BaseLookup
from lingxi_slam.base_pose_input import ScanTimeIndex, TfCoverage, MAXIMUM_TF_GAP_NS


class LiveBasePose:
    def __init__(self, session, profile, hypothesis):
        if session.base_scale is None or session.binding.input_mode != "live":
            raise ValueError("explicit_live_base_pose_session_required")
        self.session = session
        self.times, self.coverage = ScanTimeIndex(profile, hypothesis), TfCoverage()
        self.closed = threading.Event()
        self.buffer = self.projector = None
        self.metrics_lock = threading.Lock()
        self.observations = dict.fromkeys((
            "imuMessages", "cloudMessages", "staticTransforms", "dynamicTransforms",
            "timedFrames", "tfLookupsAvailable", "tfLookupsUnavailable",
            "scanTimeUnavailable", "invalidScanTimes", "invalidTransforms", "lookupErrors"), 0)

    def count(self, name):
        with self.metrics_lock:
            self.observations[name] += 1

    def setup(self, node):
        from rclpy.duration import Duration
        from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
        from tf2_msgs.msg import TFMessage
        from tf2_ros import Buffer
        if self.closed.is_set() or self.buffer is not None:
            raise RuntimeError("live_base_pose_setup_cannot_restart")
        self.buffer = Buffer(cache_time=Duration(seconds=5.))
        self.projector = BasePoseProjector(Tf2BaseLookup(self.buffer, coverage=self.coverage.covers),
                                          metres_per_map_unit=self.session.base_scale)
        for topic, static in (("/tf", False), ("/tf_static", True)):
            qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=100,
                             reliability=ReliabilityPolicy.RELIABLE if static else ReliabilityPolicy.BEST_EFFORT,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL if static else DurabilityPolicy.VOLATILE)
            node.create_subscription(TFMessage, topic, lambda message, s=static: self.transforms(message, s), qos)
        return self.close

    def transforms(self, message, static):
        if self.closed.is_set():
            return
        try:
            if len(message.transforms) > 256:
                raise ValueError("bounded_kinematic_tf_message_required")
            for transform in message.transforms:
                setter = self.buffer.set_transform_static if static else self.buffer.set_transform
                if self.coverage.observe(transform, static=static,
                                         apply=lambda value=transform: setter(value, "lingxi_passive_base_pose")):
                    self.count("staticTransforms" if static else "dynamicTransforms")
        except Exception:
            self.count("invalidTransforms")
            self.coverage.invalidate()
            self.session.revoke_base("invalid_kinematic_tf")

    def sensor(self, kind, message):
        if self.closed.is_set():
            return
        try:
            if self.times.observe(kind, message):
                self.count("imuMessages" if kind == "imu" else "cloudMessages")
        except Exception:
            self.count("invalidScanTimes")
            self.times.invalidate()
            self.session.revoke_base("invalid_scan_time")

    def frame(self, header, algorithm, pose, statistics):
        if self.closed.is_set():
            return
        try:
            timing = self.times.consume(header, algorithm)
        except Exception:
            self.count("scanTimeUnavailable")
            self.session.revoke_base("invalid_scan_time")
            return
        self.count("timedFrames")
        # Unseeded input can verify timed TF without fabricating a map pose.
        if self.projector is not None:
            try:
                value = self.projector.lookup(timing.effective_ns)
                self.count("tfLookupsAvailable" if value is not None else "tfLookupsUnavailable")
            except Exception:
                self.count("lookupErrors")
                self.session.revoke_base("timestamped_projection_unavailable")
        if pose is not None:
            if self.projector is None:
                self.session.revoke_base("timestamped_projection_unavailable")
            else:
                self.session.project_base(timing, self.projector, algorithm_stamp=algorithm)

    def close(self):
        self.closed.set()
        self.times.close()
        self.coverage.close()
        self.session.revoke_base("input_closed")

    def summary(self):
        with self.times.lock, self.coverage.lock, self.metrics_lock:
            return {"closed": self.closed.is_set(), "pendingScanTimes": len(self.times.frames),
                    "retainedDynamicTransforms": sum(map(len, self.coverage.dynamic.values())),
                    "maximumTfGapNs": MAXIMUM_TF_GAP_NS, "metresPerMapUnit": self.session.base_scale,
                    "physicalScaleValidated": False, "navigationReady": False,
                    "observations": dict(self.observations)}
