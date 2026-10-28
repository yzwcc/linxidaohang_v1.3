"""Read existing Humble bags on ARM64; no ROS node, DDS, live input, or motion."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import platform
import resource
import time

import numpy as np

from build_slam_cpu_trial import PINS
from diagnose_slam_sensors import quaternion, stamp_ns, vector
from run_glim_offline_trial import HYPOTHESES, Native, explicit_diagnostic_profile
from run_kiss_offline_trial import cloud_arrays
from slam_bag_audit import CLOUD, IMU
from slam_target_probe import verify_bundle
from verify_slam_capture import sha256, verify


def verify_replay_bundle(root, purpose="arm64_recorded_glim_odometry_trial"):
    manifest = json.loads((root / "replay-manifest.json").read_text())
    if (manifest.get("purpose") != purpose
            or manifest.get("sourceCommits") != PINS
            or manifest.get("timingHypothesesOnlyNotCalibration") is not True
            or manifest.get("navigationValidated") is not False):
        raise ValueError("explicit_private_recorded_replay_bundle_required")
    for name, entry in manifest["files"].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise ValueError("direct_relative_replay_member_required")
        path = root / name
        if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve())
                or path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]):
            raise ValueError("replay_member_integrity_mismatch")
    return manifest


def recorded_messages(bag, topic):
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from sensor_msgs.msg import Imu, PointCloud2

    if topic not in (CLOUD, IMU):
        raise ValueError("only_recorded_cloud_and_imu_supported")
    reader = rosbag2_py.SequentialReader()
    reader.open(rosbag2_py.StorageOptions(uri=str(bag), storage_id="sqlite3"),
                rosbag2_py.ConverterOptions("cdr", "cdr"))
    reader.set_filter(rosbag2_py.StorageFilter(topics=[topic]))
    message_type = Imu if topic == IMU else PointCloud2
    while reader.has_next():
        name, payload, _ = reader.read_next()
        if name != topic:
            raise ValueError("unexpected_filtered_recorded_topic")
        yield deserialize_message(payload, message_type)


def replay_recording(bag, expected, hypothesis, native, messages=recorded_messages, checkpoint=None):
    if hypothesis not in HYPOTHESES:
        raise ValueError("explicit_timing_hypothesis_required")
    imu_stamps, accelerations, angular = [], [], []
    for message in messages(bag, IMU):
        if message.header.frame_id != "lidar_imu_chest_front":
            raise ValueError("unexpected_recorded_imu_frame")
        quaternion(message.orientation)  # Never used as an attitude measurement.
        imu_stamps.append(stamp_ns(message))
        accelerations.append(vector(message.linear_acceleration))
        angular.append(vector(message.angular_velocity))
    if len(imu_stamps) != expected["topicCounts"]["imu"] or np.any(np.diff(imu_stamps) <= 0):
        raise ValueError("ordered_complete_recorded_imu_required")
    imu_stamps = np.asarray(imu_stamps, dtype=np.int64)
    origin = int(imu_stamps[0])
    imu_times = (imu_stamps - origin).astype(np.float64) / 1e9 + 1000
    poses, stamps, algorithm_stamps, measurements = [], [], [], []
    cursor, seen, skipped, initialized, last_stamp = 0, 0, 0, 0, None
    started, cpu_started = time.monotonic(), time.process_time()
    for message in messages(bag, CLOUD):
        seen += 1
        stamp = stamp_ns(message)
        if (message.header.frame_id != "lidar_chest_front"
                or (last_stamp is not None and stamp <= last_stamp)):
            raise ValueError("ordered_expected_recorded_cloud_required")
        last_stamp = stamp
        points, offsets, dropped = cloud_arrays(message)
        if dropped:
            raise ValueError("finite_recorded_points_required")
        frame_stamp = (stamp - origin) / 1e9 + 1000
        if hypothesis == "header_is_scan_end":
            frame_stamp -= offsets.max()
        scan_end = frame_stamp + offsets.max()
        if frame_stamp < imu_times[0] or scan_end > imu_times[-1]:
            skipped += 1
            continue
        while cursor < len(imu_times) and (cursor == 0 or imu_times[cursor - 1] < scan_end):
            native.imu(imu_times[cursor], accelerations[cursor], angular[cursor])
            cursor += 1
        pose, statistics = native.frame(frame_stamp, points, offsets)
        measurements.append(statistics)
        if pose is None:
            initialized += 1
        else:
            poses.append(pose)
            stamps.append(stamp)
            algorithm_stamps.append(frame_stamp)
        if checkpoint and len(measurements) % 100 == 0:
            checkpoint({"recordedScansSeen": seen, "processedScans": len(measurements),
                        "skippedUnbracketedScans": skipped, "estimatedPoses": len(poses)})
    if seen != expected["topicCounts"]["cloud"] or len(poses) < 2:
        raise ValueError("complete_capture_and_initialized_trajectory_required")
    return {
        "recordedScans": seen, "processedScans": len(measurements),
        "skippedUnbracketedScans": skipped, "initializationScans": initialized,
        "estimatedPoses": len(poses), "imuSamplesInserted": cursor,
        "processingWallSeconds": time.monotonic() - started,
        "processCpuSeconds": time.process_time() - cpu_started,
    }, np.stack(poses), np.asarray(stamps, dtype=np.int64), np.asarray(algorithm_stamps), np.stack(measurements)


def validate_environment(root, core_root):
    expected_library_path = ":".join((
        str(root / "lib"), str(core_root / "lib"),
        "/opt/ros/humble/lib/aarch64-linux-gnu", "/opt/ros/humble/lib",
    ))
    if (platform.system() != "Linux" or platform.machine() != "aarch64" or os.geteuid() == 0
            or os.environ.get("LD_LIBRARY_PATH") != expected_library_path
            or os.environ.get("AMENT_PREFIX_PATH") != "/opt/ros/humble"
            or os.environ.get("OMP_NUM_THREADS") != "2"
            or os.environ.get("OPENBLAS_NUM_THREADS") != "2"
            or any(key in os.environ for key in ("LD_PRELOAD", "ROS_DOMAIN_ID", "ROS_DISTRO"))):
        raise ValueError("ordinary_user_explicit_arm64_offline_environment_required")


def run(args, native_factory=Native, on_native_closed=None,
        bundle_purpose="arm64_recorded_glim_odometry_trial"):
    validate_environment(args.root, args.core_root)
    if args.output.exists() or args.output.parent != args.root:
        raise ValueError("new_private_output_inside_trial_required")
    manifest = verify_replay_bundle(args.root, bundle_purpose)
    verify_bundle(args.core_root)
    if sha256(args.core_root / "bundle-manifest.json") != manifest["coreBundleManifestSha256"]:
        raise ValueError("isolated_core_bundle_mismatch")
    capture_path = args.root / "capture-manifest.json"
    capture = json.loads(capture_path.read_text())
    profile = json.loads((args.root / "private-profile.json").read_text())
    diagnostic = json.loads((args.root / "private-sensor-diagnostic.json").read_text())
    explicit_diagnostic_profile(profile, diagnostic, args.hypothesis)
    if sha256(capture_path) != manifest["captureManifestSha256"] or diagnostic["captureManifestSha256"] != sha256(capture_path):
        raise ValueError("recorded_data_provenance_mismatch")
    integrity = verify(capture, args.data_root)
    args.output.mkdir(mode=0o700)
    report = {
        "purpose": "actual_arm64_recorded_glim_odometry_not_navigation",
        "result": "running", "dataset": args.dataset, "timingHypothesis": args.hypothesis,
        "runnerSha256": sha256(__file__),
        "replayManifestSha256": sha256(args.root / "replay-manifest.json"),
        "captureIntegrity": integrity, "sourceCommits": PINS,
        "target": {"machine": platform.machine(), "python": platform.python_version(), "numpy": np.__version__},
        "reader": "rosbag2_py_humble_sequential_reader_no_ros_init",
        "boundaries": {key: False for key in (
            "rosNodeStarted", "liveSensorsSubscribed", "motionCommandsSent",
            "timingHypothesisValidated", "robotCalibrationValidated", "globalMappingExecuted",
            "mapPersistenceValidatedOnTarget", "relocalizationValidated", "navigationValidated",
            "targetRealtimePerformanceValidated", "voiceConcurrencyValidated", "deploymentApproved",
            "rawDataPublished",
        )},
    }

    def checkpoint(update=None):
        if update:
            report.update(update)
        (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")

    native = None
    stdout_copy, stderr_copy = os.dup(1), os.dup(2)
    try:
        checkpoint()
        with (args.output / "private-native.log").open("x") as log:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            native = native_factory(args.root / "lib/liblingxi_glim_offline.so", args.root / "config",
                                    args.core_root / "lib/libodometry_estimation_cpu.so")
            expected = capture["datasets"][args.dataset]
            summary, poses, stamps, algorithm_stamps, measurements = replay_recording(
                args.data_root / expected["directoryName"], expected, args.hypothesis, native,
                checkpoint=checkpoint,
            )
            report["remainingFixedLagFramesAtEnd"] = native.close()
            native = None
        positions = poses[:, :3, 3]
        steps = np.linalg.norm(np.diff(positions, axis=0), axis=1)
        np.savez_compressed(args.output / "private-trajectory.npz", poses=poses,
                            header_stamps_ns=stamps, algorithm_stamps_relative_seconds=algorithm_stamps,
                            preprocess_odometry_pointcount_marginalized=measurements)
        report.update(summary)
        if on_native_closed is not None:
            on_native_closed(report)
        report.update({
            "result": "all_recorded_scans_accounted_for_on_arm64_not_quality_acceptance",
            "preprocessMillisecondsP50P95P99": np.percentile(measurements[:, 0], [50, 95, 99]).tolist(),
            "odometryMillisecondsP50P95P99": np.percentile(measurements[:, 1], [50, 95, 99]).tolist(),
            "combinedMillisecondsMaximum": float(measurements[:, :2].sum(axis=1).max()),
            "peakResidentMiB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "estimatedMotionNotGroundTruth": {
                "accumulatedPositionStepsRawUnits": float(steps.sum()),
                "endToStartDistanceRawUnits": float(np.linalg.norm(positions[-1] - positions[0])),
                "maximumConsecutivePositionStepRawUnits": float(steps.max()),
            },
            "privateTrajectorySha256": sha256(args.output / "private-trajectory.npz"),
            "privateNativeLogSha256": sha256(args.output / "private-native.log"),
            "captureIntegrityAfterReplay": verify(capture, args.data_root),
            "checks": {"recordedDataProcessedOnArm64": True, "allScansAccountedFor": True,
                       "originalCapturesUnchanged": True},
        })
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        try:
            if native is not None:
                native.close()
        finally:
            os.dup2(stdout_copy, 1)
            os.dup2(stderr_copy, 2)
            os.close(stdout_copy)
            os.close(stderr_copy)
            checkpoint()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "core-root", "data-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--dataset", choices=("static", "dynamic"), required=True)
    parser.add_argument("--hypothesis", choices=HYPOTHESES, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    old_umask = os.umask(0o077)
    try:
        print(json.dumps(run(args), indent=2))
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
