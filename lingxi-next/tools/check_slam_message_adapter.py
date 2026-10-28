"""Compare every recorded packet with frozen offline decoding; no ROS or SLAM."""
import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.messages import SensorProfile, cloud_snapshot, decode_cloud, imu_snapshot
from diagnose_slam_sensors import stamp_ns, vector
from run_kiss_offline_trial import cloud_arrays
from slam_bag_audit import CLOUD, IMU
from verify_slam_capture import sha256, verify


SOURCES = (
    "robot-slam/lingxi_slam/messages.py", "tools/check_slam_message_adapter.py",
    "tools/run_kiss_offline_trial.py", "tools/diagnose_slam_sensors.py",
    "tools/slam_bag_audit.py", "tools/verify_slam_capture.py",
)


def compare_packet(message, kind, profile):
    if kind == "cloud":
        packet = cloud_snapshot(message, profile, 2_000_000, 8 * 1024 * 1024)
        points, offsets = decode_cloud(packet)
        old_points, old_offsets, dropped = cloud_arrays(message)
        if (packet.stamp_ns != stamp_ns(message) or dropped
                or not np.array_equal(points, old_points) or not np.array_equal(offsets, old_offsets)):
            raise ValueError("complete_cloud_decoder_equivalence_required")
        return len(points), packet.payload_bytes
    if kind != "imu":
        raise ValueError("explicit_cloud_or_imu_required")
    packet = imu_snapshot(message, profile)
    if (packet.stamp_ns != stamp_ns(message)
            or not np.array_equal(packet.acceleration, vector(message.linear_acceleration))
            or not np.array_equal(packet.angular, vector(message.angular_velocity))):
        raise ValueError("complete_imu_snapshot_equivalence_required")
    return 0, packet.payload_bytes


def run(data_root, output):
    output = output.resolve()
    if output.exists() or not output.is_relative_to((ROOT / ".local").resolve()):
        raise ValueError("new_private_message_check_directory_required")
    if (platform.system() != "Linux" or os.geteuid() == 0 or sys.prefix == sys.base_prefix
            or metadata.version("rosbags") != "0.11.9" or metadata.version("numpy") != "2.5.4"):
        raise ValueError("reviewed_isolated_message_check_environment_required")
    from rosbags.highlevel import AnyReader
    from rosbags.typesys import Stores, get_typestore

    manifest_path = ROOT / "docs/slam-capture-evidence.json"
    manifest = json.loads(manifest_path.read_text())
    original = verify(manifest, data_root)
    sources = {name: sha256(ROOT / name) for name in SOURCES}
    report = {
        "schemaVersion": 1, "observedDate": "2026-10-28",
        "purpose": "complete_recorded_message_snapshot_decode_equivalence_only",
        "result": "running", "sourceSha256": sources, "captureManifestSha256": sha256(manifest_path),
        "datasets": {},
        "boundaries": {name: False for name in (
            "robotContacted", "workerExecuted", "rosNodeStarted", "nativeSlamExecuted", "motionCommandsSent",
            "pointTimesCalibrated", "mapGeometryValidated", "navigationValidated", "deploymentApproved",
            "rawDataPublished",
        )},
    }
    profile = SensorProfile("lidar_chest_front", "lidar_imu_chest_front",
                            "frame_relative_nanoseconds_hypothesis")
    output.mkdir(parents=True, mode=0o700)
    for source in SOURCES:
        target = output / "source-snapshot" / source
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        shutil.copyfile(ROOT / source, target)
    try:
        for name, expected in manifest["datasets"].items():
            counts = {"cloud": 0, "imu": 0}
            total_points = maximum_bytes = 0
            with AnyReader([data_root / expected["directoryName"]],
                           default_typestore=get_typestore(Stores.ROS2_HUMBLE)) as reader:
                connections = [item for item in reader.connections if item.topic in (CLOUD, IMU)]
                if (len(connections) != 2 or {item.topic: item.msgtype for item in connections}
                        != {CLOUD: "sensor_msgs/msg/PointCloud2", IMU: "sensor_msgs/msg/Imu"}):
                    raise ValueError("exact_cloud_imu_connections_required")
                for connection, _, payload in reader.messages(connections=connections):
                    kind = "cloud" if connection.topic == CLOUD else "imu"
                    points, size = compare_packet(reader.deserialize(payload, connection.msgtype), kind, profile)
                    counts[kind] += 1
                    total_points += points
                    maximum_bytes = max(maximum_bytes, size)
            if counts != {key: expected["topicCounts"][key] for key in counts}:
                raise ValueError("every_recorded_sensor_message_required")
            report["datasets"][name] = {
                "comparedMessages": counts, "completePointsCompared": total_points,
                "maximumSnapshotPayloadBytes": maximum_bytes,
                "allCoordinatesOffsetsAndImuValuesExactlyEqualToFrozenDecoder": True,
            }
        if (verify(manifest, data_root) != original
                or {name: sha256(ROOT / name) for name in SOURCES} != sources):
            raise ValueError("message_sources_or_original_recordings_changed")
        report["originalCapturesAndSourcesUnchanged"] = True
        report["result"] = "all_recorded_sensor_messages_match_frozen_decoder_not_live_acceptance"
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        with (output / "report.json").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        report = run(args.data_root.resolve(strict=True), args.output)
    finally:
        os.umask(previous)
    print(json.dumps({"result": report["result"], "datasets": report["datasets"]}, allow_nan=False))


if __name__ == "__main__":
    main()
