"""Run a pinned, ROS-free KISS-ICP diagnostic on a verified private recording."""
import argparse
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform
import resource
import time

import numpy as np

from slam_bag_audit import CLOUD
from verify_slam_capture import sha256, verify


VERSIONS = {"kiss-icp": "1.3.0", "rosbags": "0.11.9"}
WHEELS = {
    "kiss_icp-1.3.0-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl":
        "762d319c26870af2ba190edf61e22390244305b27ba958fdb3b84a73a9916a5b",
    "rosbags-0.11.9-py3-none-any.whl":
        "010c5a48b326e4dad6a79183db243c7b59188121d37b629d8c166ecaed3fc890",
}


def validate_profile(profile):
    if (type(profile.get("schemaVersion")) is not int or profile["schemaVersion"] != 1
            or profile.get("scope") != "offline_real_recording_diagnostic"
            or profile.get("robotCalibrationValidated") is not False
            or profile.get("navigationValidated") is not False
            or profile.get("pointTimeInterpretation") != "frame_relative_nanoseconds_hypothesis"):
        raise ValueError("unapproved_offline_diagnostic_profile")
    parameters = profile["parameters"]
    fields = {
        "data": {"max_range", "min_range", "deskew"},
        "mapping": {"voxel_size", "max_points_per_voxel"},
        "registration": {"max_num_iterations", "convergence_criterion", "max_num_threads"},
        "adaptive_threshold": {"fixed_threshold", "initial_threshold", "min_motion_th"},
    }
    if set(parameters) != set(fields):
        raise ValueError("explicit_kiss_parameters_required")
    for section, names in fields.items():
        if set(parameters[section]) != names:
            raise ValueError("explicit_kiss_parameters_required")
        for name, value in parameters[section].items():
            if name == "deskew":
                if type(value) is not bool:
                    raise ValueError("invalid_kiss_parameter")
            elif name == "fixed_threshold" and value is None:
                continue
            elif type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("invalid_kiss_parameter")
    if parameters["data"]["max_range"] <= parameters["data"]["min_range"]:
        raise ValueError("invalid_range_limits")
    for section, name in (
        ("registration", "max_num_threads"), ("registration", "max_num_iterations"),
        ("mapping", "max_points_per_voxel"),
    ):
        if type(parameters[section][name]) is not int:
            raise ValueError("integer_kiss_parameter_required")
    if not 1 <= parameters["registration"]["max_num_threads"] <= 4:
        raise ValueError("bounded_thread_count_required")
    return parameters


def cloud_arrays(message):
    width, height = message.width, message.height
    step, row = message.point_step, message.row_step
    if (any(type(value) is not int or value <= 0 for value in (width, height, step, row))
            or width * height > 2_000_000 or row < width * step):
        raise ValueError("invalid_cloud_dimensions")
    payload = memoryview(message.data)
    if payload.nbytes != height * row:
        raise ValueError("invalid_cloud_payload_size")
    fields = {}
    for field in message.fields:
        if field.name in fields:
            raise ValueError("duplicate_cloud_field")
        fields[field.name] = field
    sizes = {"x": (7, 4), "y": (7, 4), "z": (7, 4), "timestamp": (8, 8)}
    for name, (datatype, size) in sizes.items():
        field = fields.get(name)
        if (field is None or field.datatype != datatype or field.count != 1
                or type(field.offset) is not int or not 0 <= field.offset <= step - size):
            raise ValueError("unsupported_recorded_cloud_schema")
    intervals = sorted((fields[name].offset, fields[name].offset + size)
                       for name, (_, size) in sizes.items())
    if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:])):
        raise ValueError("overlapping_cloud_fields")
    order = ">" if message.is_bigendian else "<"
    dtype = np.dtype({
        "names": list(sizes), "formats": [order + "f4"] * 3 + [order + "f8"],
        "offsets": [fields[name].offset for name in sizes], "itemsize": step,
    })
    structured = np.ndarray(
        shape=(height, width), dtype=dtype, buffer=payload, strides=(row, step),
    )
    points = np.column_stack([structured[name].ravel() for name in ("x", "y", "z")]).astype(np.float64)
    offsets = structured["timestamp"].ravel().astype(np.float64) * 1e-9
    if not np.isfinite(offsets).all() or offsets.min() < 0 or offsets.max() > 0.2:
        raise ValueError("invalid_relative_nanosecond_times")
    valid = np.isfinite(points).all(axis=1)
    dropped = int((~valid).sum())
    points, offsets = points[valid], offsets[valid]
    if len(points) < 10 or np.ptp(offsets) <= 0:
        raise ValueError("insufficient_points_or_time_span")
    return np.ascontiguousarray(points), np.ascontiguousarray(offsets), dropped


def valid_pose(pose):
    pose = np.asarray(pose, dtype=np.float64)
    if (pose.shape != (4, 4) or not np.isfinite(pose).all()
            or not np.allclose(pose[3], [0, 0, 0, 1], atol=1e-7, rtol=0)
            or not np.allclose(pose[:3, :3].T @ pose[:3, :3], np.eye(3), atol=1e-5, rtol=0)
            or not np.isclose(np.linalg.det(pose[:3, :3]), 1, atol=1e-5, rtol=0)):
        raise ValueError("invalid_estimated_pose")
    return pose.copy()


def wheel_inventory(directory):
    root = Path(directory).resolve(strict=True)
    for name, expected in WHEELS.items():
        path = root / name
        if path.is_symlink() or sha256(path) != expected:
            raise ValueError("unexpected_diagnostic_wheel")
    wheels = sorted(root.glob("*.whl"))
    if any(path.is_symlink() or not path.is_file() for path in wheels):
        raise ValueError("direct_wheel_files_required")
    return [{"file": path.name, "sha256": sha256(path)} for path in wheels]


def run(manifest_path, data_root, dataset, profile_path, wheel_cache, output):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("linux_x86_64_diagnostic_host_required")
    if os.geteuid() == 0 or os.sys.prefix == os.sys.base_prefix:
        raise ValueError("ordinary_user_isolated_environment_required")
    for name, version in VERSIONS.items():
        if metadata.version(name) != version:
            raise ValueError("unexpected_diagnostic_package_version")
    if any(name.lower().startswith("kiss_icp_") for name in os.environ):
        raise ValueError("ambient_kiss_settings_not_allowed")
    output, data_root = Path(output).resolve(), Path(data_root).resolve(strict=True)
    if output.exists():
        raise ValueError("existing_trial_results_are_preserved")
    if output == data_root or data_root in output.parents:
        raise ValueError("output_must_not_be_inside_recordings")
    manifest_path, profile_path = Path(manifest_path), Path(profile_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    parameters = validate_profile(profile)
    if dataset not in ("static", "dynamic"):
        raise ValueError("explicit_captured_dataset_required")
    if not manifest.get("localCopySha256Verified"):
        raise ValueError("verified_capture_manifest_required")
    verified = verify(manifest, data_root)
    wheels = wheel_inventory(wheel_cache)
    from kiss_icp.config import KISSConfig
    from kiss_icp.kiss_icp import KissICP
    from rosbags.highlevel import AnyReader
    from rosbags.typesys import Stores, get_typestore

    configuration = KISSConfig(out_dir=str(output), **parameters)
    if configuration.model_dump(exclude={"out_dir"}) != parameters:
        raise ValueError("effective_kiss_config_differs_from_profile")
    expected = manifest["datasets"][dataset]
    bag = data_root / expected["directoryName"]
    output.mkdir(mode=0o700, parents=True)
    report = {
        "schemaVersion": 1, "purpose": "real_cloud_lidar_odometry_diagnostic_not_navigation",
        "result": "running", "dataset": dataset,
        "host": {"system": platform.system(), "machine": platform.machine(),
                 "python": platform.python_version()},
        "versions": {name: metadata.version(name) for name in (*VERSIONS, "numpy")},
        "installedPackages": sorted(
            ({"name": dist.metadata["Name"], "version": dist.version}
             for dist in metadata.distributions()), key=lambda item: item["name"].lower(),
        ),
        "wheelInventory": wheels, "profile": profile,
        "runnerSha256": sha256(Path(__file__)),
        "captureManifestSha256": sha256(manifest_path), "profileSha256": sha256(profile_path),
        "captureIntegrityResult": verified["result"], "processedFrames": 0,
        "boundaries": {
            "usesImu": False, "containsRosNode": False, "containsMotionAdapter": False,
            "sourceBinaryMatchesReviewedGitCommitProven": False,
            "physicalUnitsCalibrated": False, "robotCalibrationValidated": False,
            "loopClosureAlgorithmEnabled": False, "groundTruthAvailable": False,
            "mapPersistenceValidated": False, "relocalizationValidated": False,
            "targetArm64RuntimeValidated": False, "robotPerformanceBenchmark": False,
            "navigationValidated": False, "deploymentApproved": False,
            "rawDataPublished": False,
        },
    }

    def checkpoint():
        (output / "report.json").write_text(
            json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8",
        )

    old_umask = os.umask(0o077)
    try:
        checkpoint()
        algorithm = KissICP(configuration)
        poses, stamps, registration_ms, decode_ms = [], [], [], []
        total_points = dropped_points = 0
        source_sizes, span_seconds = [], []
        wall_started, cpu_started = time.monotonic(), time.process_time()
        with AnyReader([bag], default_typestore=get_typestore(Stores.ROS2_HUMBLE)) as reader:
            connections = [connection for connection in reader.connections if connection.topic == CLOUD]
            if (len(connections) != 1
                    or connections[0].msgtype != "sensor_msgs/msg/PointCloud2"):
                raise ValueError("one_expected_cloud_connection_required")
            for connection, _, payload in reader.messages(connections=connections):
                started = time.monotonic()
                message = reader.deserialize(payload, connection.msgtype)
                stamp = message.header.stamp.sec * 10**9 + message.header.stamp.nanosec
                if stamp <= 0 or (stamps and stamp <= stamps[-1]):
                    raise ValueError("non_increasing_cloud_header")
                points, offsets, dropped = cloud_arrays(message)
                decode_ms.append((time.monotonic() - started) * 1000)
                started = time.monotonic()
                _, source = algorithm.register_frame(points, offsets)
                registration_ms.append((time.monotonic() - started) * 1000)
                if len(source) < 10:
                    raise ValueError("insufficient_registration_points")
                poses.append(valid_pose(algorithm.last_pose))
                stamps.append(stamp)
                total_points += len(points)
                dropped_points += dropped
                source_sizes.append(len(source))
                span_seconds.append(float(np.ptp(offsets)))
                report["processedFrames"] = len(poses)
                if len(poses) % 100 == 0:
                    checkpoint()
                    print(json.dumps({"dataset": dataset, "frames": len(poses)}), flush=True)
        wall_seconds = time.monotonic() - wall_started
        cpu_seconds = time.process_time() - cpu_started
        if len(poses) != expected["topicCounts"]["cloud"] or len(poses) < 2:
            raise ValueError("not_all_recorded_clouds_processed")
        poses = np.stack(poses)
        positions = poses[:, :3, 3]
        relative_positions = positions - positions[0]
        recorded_seconds = (stamps[-1] - stamps[0]) / 1e9
        np.savez_compressed(
            output / "private-trajectory.npz", stamps_ns=stamps, poses=poses,
            registration_ms=registration_ms, decode_ms=decode_ms,
            registration_point_counts=source_sizes, point_time_spans_seconds=span_seconds,
        )
        steps = np.linalg.norm(np.diff(positions, axis=0), axis=1)
        rotations = poses[:-1, :3, :3].transpose(0, 2, 1) @ poses[1:, :3, :3]
        angles = np.degrees(np.arccos(np.clip((np.trace(rotations, axis1=1, axis2=2) - 1) / 2, -1, 1)))
        report.update({
            "result": "all_recorded_clouds_processed_not_quality_acceptance",
            "pointsPassedToAlgorithm": total_points, "nonfiniteCoordinatesDropped": dropped_points,
            "recordedCloudHeaderSpanSeconds": recorded_seconds,
            "processingWallSeconds": wall_seconds, "processCpuSeconds": cpu_seconds,
            "peakResidentMiB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "offlineRecordedSpanToProcessingTimeRatio": recorded_seconds / wall_seconds,
            "decodeMillisecondsP50P95P99": np.percentile(decode_ms, [50, 95, 99]).tolist(),
            "registrationMillisecondsP50P95P99": np.percentile(registration_ms, [50, 95, 99]).tolist(),
            "registrationMillisecondsMaximum": max(registration_ms),
            "registrationOver100MillisecondsFrames": sum(value > 100 for value in registration_ms),
            "latencySamplesIncludeFirstFrame": True,
            "perFrameRegistrationPointCountMinMax": [min(source_sizes), max(source_sizes)],
            "perFramePointTimeSpanSecondsMinMax": [min(span_seconds), max(span_seconds)],
            "estimatedMotionNotGroundTruth": {
                "accumulatedPositionStepsRawUnits": float(steps.sum()),
                "endToStartDistanceRawUnits": float(np.linalg.norm(relative_positions[-1])),
                "maximumDistanceFromStartRawUnits": float(np.linalg.norm(relative_positions, axis=1).max()),
                "maximumConsecutivePositionStepRawUnits": float(steps.max()),
                "maximumConsecutiveRotationDegrees": float(angles.max()),
            },
            "privateTrajectorySha256": sha256(output / "private-trajectory.npz"),
        })
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        try:
            checkpoint()
        finally:
            os.umask(old_umask)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "data-root", "profile", "wheel-cache", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--dataset", choices=("static", "dynamic"), required=True)
    args = parser.parse_args()
    report = run(args.manifest, args.data_root, args.dataset, args.profile, args.wheel_cache, args.output)
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ("wheelInventory", "installedPackages")}, indent=2))


if __name__ == "__main__":
    main()
