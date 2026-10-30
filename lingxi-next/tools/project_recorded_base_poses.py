"""Project preserved native LiDAR trajectories through same-recording timed TF."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.base_pose import BaseLidarTransform, BasePoseProjector, ScanTime
from audit_slam_calibration import MAXIMUM_TF_GAP_NS, TransformHistory, matrix
from diagnose_slam_sensors import quaternion, stamp_ns, vector
from run_kiss_offline_trial import cloud_arrays
from slam_bag_audit import CLOUD, IMU, TYPES
from verify_slam_capture import sha256, verify

SOURCES = (
    "robot-slam/lingxi_slam/base_pose.py", "robot-slam/lingxi_slam/seeded_localization.py",
    "tools/project_recorded_base_poses.py", "tools/audit_slam_calibration.py",
    "tools/diagnose_slam_sensors.py", "tools/run_kiss_offline_trial.py",
    "tools/verify_slam_capture.py", "tools/slam_bag_audit.py",
)


def source_hashes():
    return {name: sha256(ROOT / name) for name in SOURCES}


def recorded_context(bag, expected):
    from rosbags.highlevel import AnyReader
    from rosbags.typesys import Stores, get_typestore
    history, scans, first_imu = TransformHistory(), {}, None
    counts = dict.fromkeys((CLOUD, IMU, "/tf", "/tf_static"), 0)
    with AnyReader([bag], default_typestore=get_typestore(Stores.ROS2_HUMBLE)) as reader:
        selected = [connection for connection in reader.connections if connection.topic in counts]
        if (len(selected) != len(counts) or {row.topic: row.msgtype for row in selected}
                != {name: TYPES[name] for name in counts}):
            raise ValueError("same_recording_typed_cloud_imu_and_tf_required")
        for connection, _, payload in reader.messages(connections=selected):
            counts[connection.topic] += 1
            message = reader.deserialize(payload, connection.msgtype)
            if connection.topic == IMU:
                if message.header.frame_id != "lidar_imu_chest_front":
                    raise ValueError("matching_recorded_imu_frame_required")
                first_imu = stamp_ns(message) if first_imu is None else first_imu
            elif connection.topic == CLOUD:
                if message.header.frame_id != "lidar_chest_front":
                    raise ValueError("matching_recorded_lidar_frame_required")
                _, offsets, dropped = cloud_arrays(message)
                offset = float(offsets.max()) * 1e9
                if dropped or abs(offset - round(offset)) > 1e-4 or stamp_ns(message) in scans:
                    raise ValueError("complete_unique_recorded_scan_time_required")
                scans[stamp_ns(message)] = round(offset)
            else:
                for transform in message.transforms:
                    stamp = transform.header.stamp
                    history.add(transform.header.frame_id, transform.child_frame_id,
                                int(stamp.sec) * 10**9 + int(stamp.nanosec),
                                matrix(vector(transform.transform.translation), quaternion(transform.transform.rotation)),
                                static=connection.topic == "/tf_static")
    aliases = ((CLOUD, "cloud"), (IMU, "imu"), ("/tf", "tf"), ("/tf_static", "tf_static"))
    if first_imu is None or any(counts[name] != expected["topicCounts"][alias] for name, alias in aliases):
        raise ValueError("all_declared_recorded_context_messages_required")
    return history, scans, first_imu


def project_arrays(poses, headers, algorithms, context, hypothesis, scale):
    history, scans, first_imu = context
    if (not isinstance(poses, np.ndarray) or poses.shape != (len(headers), 4, 4)
            or not 1 <= len(headers) <= 20_000 or headers.dtype.kind != "i" or np.any(headers <= 0)
            or headers.ndim != 1 or np.any(np.diff(headers) <= 0)
            or algorithms.shape != headers.shape or not np.isfinite(algorithms).all()
            or np.any(np.diff(algorithms) <= 0)):
        raise ValueError("complete_ordered_native_pose_time_arrays_required")
    path = history.path("base_link", "lidar_chest_front")
    gaps, missing, base_poses, valid_rows, effective_times = [], Counter(), [], [], []

    def lookup(stamp):
        transform, detail = history.query(path, stamp, MAXIMUM_TF_GAP_NS)
        if transform is None:
            missing[detail] += 1
            return None
        gaps.append(detail)
        return BaseLidarTransform(stamp, transform)

    projector = BasePoseProjector(lookup, metres_per_map_unit=scale)
    from lingxi_slam.base_pose import BasePoseUnavailable
    for row, (pose, header, algorithm) in enumerate(zip(poses, headers, algorithms)):
        header = int(header)
        if header not in scans:
            raise ValueError("native_pose_header_missing_from_same_recording")
        timing = ScanTime(header, scans[header], first_imu, hypothesis)
        try:
            projected = projector.project(pose, timing, algorithm_stamp=float(algorithm))
        except BasePoseUnavailable:
            continue
        base_poses.append(projected)
        valid_rows.append(row)
        effective_times.append(timing.effective_ns)
    if not base_poses or len(base_poses) + sum(missing.values()) != len(poses):
        raise ValueError("complete_base_projection_accounting_required")
    base_poses = np.stack(base_poses)
    indices = np.asarray(valid_rows, dtype=np.int64)
    # A reconstruction check detects inversion, order and scale mistakes only.
    errors = []
    for projected, row, stamp in zip(base_poses, indices, effective_times):
        transform, _ = history.query(path, stamp, MAXIMUM_TF_GAP_NS)
        expected = poses[row].copy()
        expected[:3, 3] *= scale
        errors.append(float(np.max(np.abs(projected @ transform - expected))))
    summary = {
        "nativePoses": len(poses), "projectedBasePoses": len(base_poses),
        "unavailablePoses": sum(missing.values()), "unavailableReasons": dict(missing),
        "baseToLidarPath": path, "largestUsedInterpolationGapNs": max(gaps),
        "maximumReconstructionElementError": max(errors),
        "maximumLidarBaseOriginDistanceMetresAtDeclaredScale": float(np.linalg.norm(
            poses[indices, :3, 3] * scale - base_poses[:, :3, 3], axis=1).max()),
        "maximumLidarBaseRotationDegrees": float(np.degrees(Rotation.from_matrix(
            np.swapaxes(poses[indices, :3, :3], 1, 2) @ base_poses[:, :3, :3]).magnitude()).max()),
    }
    if max(errors) > 1e-8:
        raise ValueError("same_time_transform_reconstruction_required")
    return summary, {"odom_from_base_metres_at_declared_scale": base_poses,
                     "native_pose_rows": indices,
                     "effective_sensor_stamps_ns": np.asarray(effective_times, dtype=np.int64)}


def run(args):
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to((ROOT / ".local").resolve()):
        raise ValueError("new_private_base_projection_output_required")
    capture_path, reference_path = (ROOT / "docs" / name for name in (
        "slam-capture-evidence.json", "slam-stream-input-evidence.json"))
    capture, reference = (json.loads(path.read_text()) for path in (capture_path, reference_path))
    if sha256(capture_path) != reference["captureManifestSha256"]:
        raise ValueError("same_capture_and_accepted_stream_evidence_required")
    integrity, sources = verify(capture, args.data_root), source_hashes()
    inputs, summaries = {}, {}
    output.mkdir(mode=0o700, parents=True)
    for dataset in ("static", "dynamic"):
        context = recorded_context(args.data_root / capture["datasets"][dataset]["directoryName"],
                                   capture["datasets"][dataset])
        for suffix, hypothesis in (("start", "header_is_scan_start"), ("end", "header_is_scan_end")):
            label = dataset + "-" + suffix
            directory, expected = args.stream_results / label, reference["runs"][label]
            files = {name: sha256(directory / name) for name in ("report.json", "private-trajectory.npz")}
            if (files["report.json"] != expected["reportSha256"]
                    or files["private-trajectory.npz"] != expected["privateTrajectorySha256"]):
                raise ValueError("unchanged_accepted_native_stream_trajectory_required")
            report = json.loads((directory / "report.json").read_text())
            if (report["dataset"] != dataset or report["timingHypothesis"] != hypothesis
                    or report["captureManifestSha256"] != sha256(capture_path)):
                raise ValueError("same_native_stream_dataset_and_time_hypothesis_required")
            with np.load(directory / "private-trajectory.npz", allow_pickle=False) as arrays:
                summary, projected = project_arrays(
                    arrays["poses"], arrays["header_stamps_ns"], arrays["algorithm_stamps_relative_seconds"],
                    context, hypothesis, args.metres_per_map_unit)
            if summary["nativePoses"] != expected["counts"]["estimatedPoses"]:
                raise ValueError("all_accepted_native_poses_required")
            np.savez_compressed(output / (label + "-private-base-poses.npz"), **projected)
            summaries[label], inputs[label] = summary, files
            print(json.dumps({"condition": label, "projected": summary["projectedBasePoses"],
                              "unavailable": summary["unavailablePoses"]}), flush=True)
    if (verify(capture, args.data_root) != integrity or source_hashes() != sources
            or any(sha256(args.stream_results / label / name) != digest
                   for label, files in inputs.items() for name, digest in files.items())):
        raise ValueError("unchanged_original_inputs_and_projection_sources_required")
    result = {
        "schemaVersion": 1, "result": "same_recorded_native_lidar_to_base_projection_not_calibration_acceptance",
        "sourceSha256": sources, "captureManifestSha256": sha256(capture_path),
        "streamEvidenceSha256": sha256(reference_path), "nativeInputSha256": inputs,
        "maximumInterpolationGapNs": MAXIMUM_TF_GAP_NS,
        "outputCoordinateFrame": "lingxi_glim_odom", "outputBodyFrame": "base_link",
        "explicitMetresPerMapUnitNotMeasured": args.metres_per_map_unit, "runs": summaries,
        "boundaries": {name: False for name in (
            "robotContacted", "liveSensorsSubscribed", "motionCommandsSent", "nativeEngineRerun",
            "tfPublished", "physicalScaleValidated", "timingHypothesisValidated",
            "calibrationValidated", "siteAssociationVerified", "mapGravityAlignmentValidated",
            "localizationReady", "navigationReady", "tf2TargetValidated", "deploymentApproved")},
    }
    with (output / "aggregate.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("data-root", "stream-results", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--metres-per-map-unit", type=float, required=True)
    previous = os.umask(0o077)
    try:
        print(json.dumps({"result": run(parser.parse_args())["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
