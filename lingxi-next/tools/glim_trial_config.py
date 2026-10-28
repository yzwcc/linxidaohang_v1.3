"""Generate an offline-only CPU GLIM trial configuration from reviewed inputs.

No ROS process, container, robot connection or motion interface is started.
Unresolved sensor values are errors, not identity transforms or scale defaults.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_CONFIGS = (
    "config_logging.json", "config_preprocess.json", "config_odometry_cpu.json",
    "config_sub_mapping_passthrough.json", "config_global_mapping_pose_graph.json",
)


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("unresolved_or_invalid_" + name)
    return value


def settings(profile):
    if (not isinstance(profile, dict) or type(profile.get("schemaVersion")) is not int
            or profile["schemaVersion"] != 1 or profile.get("purpose") != "offline_glim_trial"):
        raise ValueError("invalid_profile_scope")
    if profile.get("inputClassification") not in ("synthetic_fixture", "target_sensor_profile"):
        raise ValueError("missing_or_invalid_input_classification")
    if profile.get("reviewedForOfflineTrial") is not True:
        raise ValueError("sensor_profile_not_reviewed")
    for key in ("timeEvidence", "imuUnitEvidence", "extrinsicEvidence"):
        if not isinstance(profile.get(key), str) or not profile[key].strip():
            raise ValueError("missing_" + key)
    if profile.get("pointTime") != {
        "field": "timestamp", "datatype": "FLOAT64",
        "unit": "nanoseconds", "reference": "frame_relative",
    }:
        raise ValueError("unsupported_or_unresolved_point_time_contract")
    if profile.get("lidarFrame") != "lidar_chest_front" or profile.get("imuFrame") != "lidar_imu_chest_front":
        raise ValueError("unexpected_sensor_frames")
    transform = profile.get("T_lidar_imu")
    if not isinstance(transform, list) or len(transform) != 7:
        raise ValueError("unresolved_T_lidar_imu")
    transform = [number(value, "extrinsic") for value in transform]
    if abs(math.hypot(*transform[3:]) - 1) > 0.001:
        raise ValueError("extrinsic_quaternion_not_normalized")
    acceleration_scale = number(profile.get("accelerationScale"), "acceleration_scale")
    angular_scale = number(profile.get("angularVelocityScale"), "angular_velocity_scale")
    # The reviewed wrapper auto-detects acceleration units below this threshold.
    if acceleration_scale < 1e-6 or angular_scale <= 0:
        raise ValueError("invalid_imu_scale")
    imu_offset = number(profile.get("imuTimeOffsetSeconds"), "imu_time_offset")
    cloud_offset = number(profile.get("cloudTimeOffsetSeconds"), "cloud_time_offset")
    return {
        "config.json": {"global": {
            "config_path": "",
            "config_ros": "config_ros.json", "config_logging": "config_logging.json",
            "config_sensors": "config_sensors.json", "config_preprocess": "config_preprocess.json",
            "config_odometry": "config_odometry_cpu.json",
            "config_sub_mapping": "config_sub_mapping_passthrough.json",
            "config_global_mapping": "config_global_mapping_pose_graph.json",
        }},
        "config_sensors.json": {"sensors": {
            "global_shutter_lidar": False, "T_lidar_imu": transform,
            "intensity_field": "intensity", "ring_field": "ring",
            "autoconf_perpoint_times": False, "autoconf_prefer_frame_time": True,
            "perpoint_relative_time": True, "perpoint_time_scale": 1e-9,
        }},
        "config_ros.json": {"glim_ros": {
            "enable_local_mapping": True, "enable_global_mapping": True,
            "keep_raw_points": False, "extension_modules": [],
            "imu_time_offset": imu_offset, "points_time_offset": cloud_offset,
            "acc_scale": acceleration_scale, "ang_scale": angular_scale,
            "imu_frame_id": profile["imuFrame"], "lidar_frame_id": profile["lidarFrame"],
            "base_frame_id": profile["imuFrame"], "publish_imu2lidar": False,
            "odom_frame_id": "lingxi_trial_odom", "map_frame_id": "lingxi_trial_map",
            "imu_topic": "/aima/hal/sensor/lidar_chest_front/imu",
            "points_topic": "/aima/hal/sensor/lidar_chest_front/lidar_pointcloud",
            "image_topic": "/lingxi_trial/unused_image", "playback_speed": 1.0,
            "imu_qos": {"profile": "sensor_data", "depth": 1000},
            "points_qos": {"profile": "sensor_data"},
        }},
    }


def pinned_commit():
    manifest = json.loads((ROOT / "docs" / "slam-candidates.json").read_text(encoding="utf-8"))
    return next(item["commit"] for item in manifest["repositories"] if item["repository"] == "koide3/glim")


def verify_source(source):
    expected = pinned_commit()
    result = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=10,
    )
    if result.stdout.strip() != expected:
        raise ValueError("upstream_commit_does_not_match_review_baseline")
    dirty = subprocess.run(
        ["git", "-C", str(source), "-c", "core.fsmonitor=false",
         "status", "--porcelain", "--untracked-files=all", "--", "config", "LICENSE"],
        check=True, capture_output=True, text=True, timeout=10,
    )
    if dirty.stdout.strip():
        raise ValueError("upstream_configuration_is_not_clean")
    return expected


def generate(source, output, profile):
    source, output = Path(source).resolve(), Path(output).resolve()
    documents = settings(profile)
    if output.exists():
        raise ValueError("output_already_exists")
    if output == source or source in output.parents:
        raise ValueError("output_must_be_outside_upstream_source")
    revision = verify_source(source)
    inputs = [source / "config" / name for name in REQUIRED_CONFIGS] + [source / "LICENSE"]
    for path in inputs:
        if not path.is_file() or path.is_symlink() or path.resolve().parent not in (source, source / "config"):
            raise ValueError("missing_or_indirect_upstream_configuration")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for path in inputs:
        target = output / (path.name if path.name != "LICENSE" else "UPSTREAM_LICENSE.txt")
        shutil.copyfile(path, target)
    for name, value in documents.items():
        (output / name).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.iterdir()) if path.is_file()
    }
    manifest = {
        "schemaVersion": 1, "purpose": "offline_glim_trial",
        "inputClassification": profile["inputClassification"],
        "upstreamCommit": revision,
        "profileSha256": hashlib.sha256(
            json.dumps(profile, sort_keys=True, allow_nan=False).encode("utf-8")
        ).hexdigest(),
        "files": hashes, "deploymentApproved": False,
        "robotCalibrationValidated": False, "navigationValidated": False,
        "algorithmParameters": "copied_upstream_defaults_not_target_tuned",
        "imuNoiseParameters": "upstream_library_defaults_not_target_calibrated",
        "requiresNetworkIsolatedExecution": True,
        "startsProcesses": False, "containsMotionAdapter": False,
    }
    (output / "trial-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-root", type=Path, required=True)
    parser.add_argument("--sensor-profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads(args.sensor_profile.read_text(encoding="utf-8"))
    print(json.dumps(generate(args.upstream_root, args.output, profile), indent=2))


if __name__ == "__main__":
    main()
