"""ROS-free aggregate diagnostics, never automatic calibration or robot control."""
import argparse
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform

import numpy as np

from run_kiss_offline_trial import cloud_arrays
from slam_bag_audit import CLOUD, IMU, ODOMETRY, TYPES
from verify_slam_capture import sha256, verify


def stamp_ns(message):
    stamp = message.header.stamp
    if (type(stamp.sec) is not int or type(stamp.nanosec) is not int
            or stamp.sec < 0 or not 0 <= stamp.nanosec < 10**9):
        raise ValueError("invalid_sensor_stamp")
    result = stamp.sec * 10**9 + stamp.nanosec
    if result <= 0:
        raise ValueError("invalid_sensor_stamp")
    return result


def vector(value):
    result = np.asarray([value.x, value.y, value.z], dtype=np.float64)
    if not np.isfinite(result).all():
        raise ValueError("nonfinite_sensor_vector")
    return result


def quaternion(value):
    result = np.asarray([value.x, value.y, value.z, value.w], dtype=np.float64)
    if not np.isfinite(result).all() or abs(np.linalg.norm(result) - 1) > 0.001:
        raise ValueError("invalid_sensor_quaternion")
    return result / np.linalg.norm(result)


def quaternion_product(left, right):
    left, right = np.asarray(left), np.asarray(right)
    xyz = (left[..., 3, None] * right[..., :3]
           + right[..., 3, None] * left[..., :3]
           + np.cross(left[..., :3], right[..., :3]))
    w = left[..., 3] * right[..., 3] - np.sum(left[..., :3] * right[..., :3], axis=-1)
    return np.concatenate([xyz, w[..., None]], axis=-1)


def rotation_vectors(left, right):
    conjugate = np.asarray(left).copy()
    conjugate[..., :3] *= -1
    relative = quaternion_product(conjugate, right)
    relative = np.where(relative[..., 3, None] < 0, -relative, relative)
    length = np.linalg.norm(relative[..., :3], axis=-1)
    angle = 2 * np.arctan2(length, np.clip(relative[..., 3], 0, 1))
    factor = np.full_like(length, 2.0)
    np.divide(angle, length, out=factor, where=length > 1e-12)
    return relative[..., :3] * factor[..., None]


def series_times(stamps, origin_ns):
    values = np.asarray(stamps, dtype=np.int64)
    if len(values) < 2 or np.any(np.diff(values) <= 0):
        raise ValueError("strictly_increasing_sensor_stamps_required")
    return (values - origin_ns).astype(np.float64) / 1e9


def vector_summary(values):
    values = np.asarray(values, dtype=np.float64)
    return {
        "count": len(values), "mean": values.mean(axis=0).tolist(),
        "standardDeviation": values.std(axis=0).tolist(),
        "normMinP50P95Max": np.percentile(np.linalg.norm(values, axis=1), [0, 50, 95, 100]).tolist(),
    }


def orientation_summary(values):
    values = np.asarray(values, dtype=np.float64)
    angles = np.linalg.norm(rotation_vectors(values[0], values), axis=1)
    return {
        "count": len(values),
        "maximumRotationFromFirstDegrees": float(np.degrees(angles.max())),
        "identityRotationSamples": int(np.count_nonzero(np.linalg.norm(values[:, :3], axis=1) < 1e-12)),
        "normalizedQuaternionDoesNotProveValidAttitude": True,
    }


def compare_angular_rates(imu_times, gyro, odometry_times, orientations):
    # Finite rotations over 0.1 s reduce derivative noise; neither stream is truth.
    ends = np.searchsorted(odometry_times, odometry_times + 0.1)
    starts = np.arange(len(odometry_times))
    available = ends < len(odometry_times)
    starts, ends = starts[available], ends[available]
    dt = odometry_times[ends] - odometry_times[starts]
    midpoints = (odometry_times[ends] + odometry_times[starts]) / 2
    covered = ((dt <= 0.15) & (midpoints >= imu_times[0]) & (midpoints <= imu_times[-1]))
    starts, ends, dt, midpoints = starts[covered], ends[covered], dt[covered], midpoints[covered]
    if not len(starts):
        raise ValueError("no_overlapping_angular_comparison_intervals")
    rates = rotation_vectors(orientations[starts], orientations[ends]) / dt[:, None]
    measured = np.column_stack([np.interp(midpoints, imu_times, gyro[:, axis]) for axis in range(3)])
    left, right = measured - measured.mean(axis=0), rates - rates.mean(axis=0)
    denominator = float(np.sum(left**2))
    right_energy = float(np.sum(right**2))
    result = {
        "intervals": len(starts), "nominalWindowSeconds": 0.1,
        "midpointInterpolationOnlyNoTimeOffsetFit": True,
        "bodyFramesMustMatch": True, "neitherStreamIsGroundTruth": True,
        "odometryDerivedRateNormMinP50P95Max": np.percentile(
            np.linalg.norm(rates, axis=1), [0, 50, 95, 100],
        ).tolist(),
        "centeredLeastSquaresScaleDiagnosticOnly": (
            float(np.sum(left * right) / denominator) if denominator > 1e-12 else None
        ),
        "centeredVectorCorrelation": (
            float(np.sum(left * right) / math.sqrt(denominator * right_energy))
            if denominator > 1e-12 and right_energy > 1e-12 else None
        ),
        "fixedScaleHypotheses": {},
    }
    for name, scale in (("radiansPerSecond", 1.0), ("degreesPerSecond", math.pi / 180)):
        difference = measured * scale - rates
        centered = difference - difference.mean(axis=0)
        result["fixedScaleHypotheses"][name] = {
            "scale": scale,
            "vectorRmse": float(np.sqrt(np.mean(np.sum(difference**2, axis=1)))),
            "demeanedVectorRmseDiagnosticOnly": float(np.sqrt(np.mean(np.sum(centered**2, axis=1)))),
            "meanDifferenceNotAppliedAsBias": difference.mean(axis=0).tolist(),
        }
    return result


def scan_coverage(cloud_stamps, time_limits, imu_stamps, origin_ns):
    cloud_times = series_times(cloud_stamps, origin_ns)
    imu_times = series_times(imu_stamps, origin_ns)
    limits = np.asarray(time_limits, dtype=np.float64)
    result = {}
    for name, starts, ends in (
        ("headerIsScanStartHypothesis", cloud_times + limits[:, 0], cloud_times + limits[:, 1]),
        ("headerIsScanEndHypothesis", cloud_times + limits[:, 0] - limits[:, 1], cloud_times),
    ):
        covered = (starts >= imu_times[0]) & (ends <= imu_times[-1])
        result[name] = {
            "fullyBracketedScans": int(covered.sum()),
            "scansBeginningBeforeImu": int(np.count_nonzero(starts < imu_times[0])),
            "scansEndingAfterImu": int(np.count_nonzero(ends > imu_times[-1])),
        }
    result["neitherHypothesisIsCalibrationApproval"] = True
    return result


def inspect_bag(bag, expected):
    from rosbags.highlevel import AnyReader
    from rosbags.typesys import Stores, get_typestore

    counts = {topic: 0 for topic in TYPES}
    imu_stamps, gyro, accel, imu_q = [], [], [], []
    odometry_stamps, odometry_q, twist = [], [], []
    cloud_stamps, limits, static_edges = [], [], []
    frames, covariance_first = {}, {}
    with AnyReader([bag], default_typestore=get_typestore(Stores.ROS2_HUMBLE)) as reader:
        for topic, expected_type in TYPES.items():
            connections = [connection for connection in reader.connections if connection.topic == topic]
            if not connections or any(connection.msgtype != expected_type for connection in connections):
                raise ValueError("missing_or_wrong_sensor_connection")
        for connection, _, payload in reader.messages():
            topic = connection.topic
            if topic not in TYPES:
                raise ValueError("unexpected_recorded_topic")
            message = reader.deserialize(payload, connection.msgtype)
            counts[topic] += 1
            if topic in (CLOUD, IMU, ODOMETRY):
                frames.setdefault(topic, set()).add(message.header.frame_id)
            if topic == CLOUD:
                _, offsets, dropped = cloud_arrays(message)
                if dropped:
                    raise ValueError("diagnostic_requires_all_finite_recorded_cloud_points")
                cloud_stamps.append(stamp_ns(message))
                limits.append([float(offsets.min()), float(offsets.max())])
            elif topic == IMU:
                imu_stamps.append(stamp_ns(message))
                gyro.append(vector(message.angular_velocity))
                accel.append(vector(message.linear_acceleration))
                imu_q.append(quaternion(message.orientation))
                covariance_first.setdefault("orientation", []).append(float(message.orientation_covariance[0]))
            elif topic == ODOMETRY:
                frames.setdefault("odometryChild", set()).add(message.child_frame_id)
                odometry_stamps.append(stamp_ns(message))
                odometry_q.append(quaternion(message.pose.pose.orientation))
                twist.append(vector(message.twist.twist.angular))
            elif topic == "/tf_static":
                for transform in message.transforms:
                    if (transform.header.frame_id == "lidar_chest_front"
                            and transform.child_frame_id == "lidar_imu_chest_front"):
                        static_edges.append(np.concatenate([
                            vector(transform.transform.translation), quaternion(transform.transform.rotation),
                        ]))
    aliases = {"cloud": CLOUD, "imu": IMU, "odometry": ODOMETRY, "tf": "/tf", "tf_static": "/tf_static"}
    if {alias: counts[topic] for alias, topic in aliases.items()} != expected["topicCounts"]:
        raise ValueError("not_all_sensor_messages_inspected")
    if frames != {
        CLOUD: {"lidar_chest_front"}, IMU: {"lidar_imu_chest_front"},
        ODOMETRY: {"leg_odom"}, "odometryChild": {"lidar_imu_chest_front"},
    }:
        raise ValueError("unexpected_or_mixed_sensor_frames")
    if not static_edges or not np.allclose(static_edges, static_edges[0], rtol=0, atol=1e-12):
        raise ValueError("missing_or_inconsistent_lidar_imu_static_transform")
    origin = min(imu_stamps[0], odometry_stamps[0], cloud_stamps[0])
    imu_times = series_times(imu_stamps, origin)
    odometry_times = series_times(odometry_stamps, origin)
    gyro, odometry_q = np.asarray(gyro), np.asarray(odometry_q)
    return {
        "messageCounts": counts, "frames": {name: sorted(value) for name, value in frames.items()},
        "imuAngularVelocityRaw": vector_summary(gyro),
        "imuAccelerationRaw": vector_summary(accel), "imuOrientation": orientation_summary(imu_q),
        "imuOrientationCovarianceFirstValues": sorted(set(covariance_first["orientation"])),
        "odometryOrientation": orientation_summary(odometry_q),
        "odometryAngularTwistRaw": vector_summary(twist),
        "angularRateComparison": compare_angular_rates(imu_times, gyro, odometry_times, odometry_q),
        "scanImuCoverage": scan_coverage(cloud_stamps, limits, imu_stamps, origin),
        "imuHeaderGapSecondsP50P95Max": np.percentile(np.diff(imu_times), [50, 95, 100]).tolist(),
        "pointOffsetSecondsMinMax": np.asarray(limits).min(axis=0).tolist()
            + np.asarray(limits).max(axis=0).tolist(),
        "staticExtrinsicObservationNotCalibration": {
            "parent": "lidar_chest_front", "child": "lidar_imu_chest_front",
            "transformDirection": "imu_coordinates_to_lidar_coordinates",
            "observations": len(static_edges), "T_lidar_imu": static_edges[0].tolist(),
        },
    }


def run(manifest_path, data_root, output):
    if platform.system() != "Linux" or os.geteuid() == 0 or os.sys.prefix == os.sys.base_prefix:
        raise ValueError("ordinary_user_isolated_linux_environment_required")
    if metadata.version("rosbags") != "0.11.9":
        raise ValueError("reviewed_rosbags_version_required")
    output, data_root = Path(output).resolve(), Path(data_root).resolve(strict=True)
    if output.exists():
        raise ValueError("existing_diagnostic_is_preserved")
    if output == data_root or data_root in output.parents:
        raise ValueError("output_must_not_be_inside_recordings")
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    integrity = verify(manifest, data_root)
    report = {
        "schemaVersion": 1, "purpose": "recorded_sensor_semantics_diagnostic_not_calibration",
        "runnerSha256": sha256(__file__), "captureManifestSha256": sha256(manifest_path),
        "captureIntegrityResult": integrity["result"], "datasets": {},
        "boundaries": {
            "containsRosNode": False, "containsMotionAdapter": False,
            "automaticallySelectsUnitScale": False, "automaticallySubtractsImuBias": False,
            "automaticallyFitsTimeOffset": False, "robotCalibrationValidated": False,
            "groundTruthAvailable": False, "navigationValidated": False, "deploymentApproved": False,
            "containsRawMessageOrTrajectorySequence": False,
        },
    }
    for name, expected in manifest["datasets"].items():
        report["datasets"][name] = inspect_bag(data_root / expected["directoryName"], expected)
    old_umask = os.umask(0o077)
    try:
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        with (output / "report.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    finally:
        os.umask(old_umask)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "data-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    report = run(args.manifest, args.data_root, args.output)
    print(json.dumps({
        "result": "aggregate_diagnostic_completed_not_calibration",
        "reportSha256": sha256(args.output / "report.json"),
        "datasets": {name: {"imuOrientation": value["imuOrientation"],
                            "angularRateComparison": value["angularRateComparison"],
                            "scanImuCoverage": value["scanImuCoverage"]}
                     for name, value in report["datasets"].items()},
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
