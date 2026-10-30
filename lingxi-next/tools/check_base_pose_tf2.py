"""Synthetic exact-time Humble tf2 compatibility check; no ROS context or node."""
import hashlib
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot-slam"))

SOURCES = (
    "tools/check_base_pose_tf2.py",
    "robot-slam/lingxi_slam/__init__.py",
    "robot-slam/lingxi_slam/base_pose.py",
    "robot-slam/lingxi_slam/base_pose_input.py",
    "robot-slam/lingxi_slam/messages.py",
    "robot-slam/lingxi_slam/seeded_localization.py",
)
RESULT = "synthetic_exact_time_tf2_verified_not_live_base_pose_acceptance"
CHECKS = ("integerExactTime", "midpointInterpolation", "noExtrapolation",
          "bothScanTimeHypotheses", "full3dPose", "translationScaleOnly", "readOnlyOutput",
          "oversizedGapRejected", "closedCoverageRejected", "noRosContext")
BOUNDARIES = ("rosContextStarted", "rosNodeStarted", "sensorsRead", "motionCommandsSent",
              "physicalScaleValidated", "liveBasePoseAccepted", "navigationReady")


def check():
    import numpy as np
    import rclpy
    import scipy
    from geometry_msgs.msg import TransformStamped
    from rclpy.duration import Duration
    from scipy.spatial.transform import Rotation
    from tf2_ros import Buffer
    from lingxi_slam.base_pose import BasePoseProjector, ScanTime, Tf2BaseLookup
    from lingxi_slam.base_pose_input import CHAIN, DYNAMIC_CHILDREN, TfCoverage

    if rclpy.ok():
        raise ValueError("uninitialized_ros_context_required")
    sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
    coverage, buffer = TfCoverage(), Buffer(cache_time=Duration(seconds=5.))
    lookup = Tf2BaseLookup(buffer, coverage=coverage.covers)
    epoch, interval = 1_700_000_000_000_000_000, 20_000_000
    query, expected = epoch + interval // 2, np.eye(4)

    def edge(index, fraction):
        matrix = np.eye(4)
        matrix[:3, :3] = Rotation.from_euler("z", .05 * index + .02 * fraction).as_matrix()
        matrix[:3, 3] = [.03 * index + .01 * fraction, -.02 * index, .04 * index]
        return matrix

    def insert(index, stamp, fraction, static):
        value = edge(index, fraction)
        message = TransformStamped()
        message.header.frame_id, message.child_frame_id = CHAIN[index - 1:index + 1]
        message.header.stamp.sec, message.header.stamp.nanosec = divmod(stamp, 10**9)
        for key, number in zip(("x", "y", "z"), value[:3, 3]):
            setattr(message.transform.translation, key, float(number))
        for key, number in zip(("x", "y", "z", "w"), Rotation.from_matrix(value[:3, :3]).as_quat()):
            setattr(message.transform.rotation, key, float(number))
        setter = buffer.set_transform_static if static else buffer.set_transform
        if coverage.observe(message, static=static, apply=lambda: setter(message, "synthetic_check")) is not True:
            raise ValueError("synthetic_tf_insertion_required")

    try:
        for index, child in enumerate(CHAIN[1:], 1):
            static = child not in DYNAMIC_CHILDREN
            if static:
                insert(index, 0, 0., True)
            else:
                insert(index, epoch, 0., False)
                insert(index, epoch + interval, 1., False)
            expected = expected @ edge(index, 0. if static else .5)
        transformed = lookup(query)
        if transformed is None or transformed.stamp_ns != query:
            raise ValueError("integer_exact_time_transform_required")
        errors = [float(np.max(np.abs(transformed.base_from_lidar - expected)))]
        if lookup(epoch - 1) is not None or lookup(epoch + interval + 1) is not None:
            raise ValueError("tf_extrapolation_must_be_unavailable")
        world_lidar = np.eye(4)
        world_lidar[:3, :3] = Rotation.from_euler("xyz", [.2, -.3, .4]).as_matrix()
        world_lidar[:3, 3] = [1.1, -.7, .9]
        scaled = world_lidar.copy()
        scaled[:3, 3] *= 2.
        reference = scaled @ np.linalg.inv(expected)
        projector = BasePoseProjector(lookup, metres_per_map_unit=2.)
        for hypothesis in ("header_is_scan_start", "header_is_scan_end"):
            header = query + (interval if hypothesis == "header_is_scan_end" else 0)
            timing = ScanTime(header, interval, epoch - 100_000_000, hypothesis)
            result = projector.project(world_lidar, timing, algorithm_stamp=timing.algorithm_stamp)
            errors.append(float(np.max(np.abs(result - reference))))
            if result.flags.writeable:
                raise ValueError("owned_read_only_base_pose_required")
        for index, child in enumerate(CHAIN[1:], 1):
            if child in DYNAMIC_CHILDREN:
                insert(index, epoch + 120_000_000, 6., False)
        # Real tf2 can interpolate this interval; our density contract rejects it.
        if lookup(epoch + 60_000_000) is not None:
            raise ValueError("oversized_dynamic_tf_gap_must_be_unavailable")
        coverage.close()
        if lookup(query) is not None or rclpy.ok() or max(errors) > 1e-10:
            raise ValueError("closed_coverage_precise_composition_and_no_ros_context_required")
        if sources != {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}:
            raise ValueError("unchanged_checked_sources_required")
        return {
            "schemaVersion": 1, "result": RESULT,
            "inputClassification": "synthetic_transforms_no_sensors",
            "machine": platform.machine(), "python": platform.python_version(),
            "versions": {"numpy": np.__version__, "scipy": scipy.__version__},
            "sourceSha256": sources, "maximumMatrixElementError": max(errors),
            "checks": dict.fromkeys(CHECKS, True), "boundaries": dict.fromkeys(BOUNDARIES, False),
        }
    finally:
        coverage.close()


if __name__ == "__main__":
    print(json.dumps(check(), allow_nan=False))
