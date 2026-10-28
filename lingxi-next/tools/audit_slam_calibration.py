"""Offline frame/time and warning audit, not a fitted calibration or navigation gate."""
import argparse
from bisect import bisect_left
import hashlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import re
import subprocess
import zipfile

import numpy as np
from ruamel.yaml import YAML
from scipy.spatial.transform import Rotation, Slerp

from build_slam_cpu_trial import PINS
from diagnose_slam_sensors import quaternion, stamp_ns, vector
from export_glim_trial_evidence import Text
from read_robot_calibration import FACTORY_PATH, MAXIMUM_BYTES
from run_kiss_offline_trial import cloud_arrays
from slam_bag_audit import CLOUD, TYPES
from verify_slam_capture import sha256, verify


ROOT = Path(__file__).resolve().parents[1]
MAXIMUM_TF_GAP_NS = 50_000_000
BOUNDARIES = (
    "robotCalibrationValidated", "timingHypothesisSelected", "factoryValuesApplied",
    "timeOffsetFitted", "imuBiasSubtracted", "groundTruthAvailable", "navigationValidated",
    "deploymentApproved", "rosNodeStarted", "motionCommandsSent", "rawCalibrationPublished",
)


def matrix(translation, rotation_xyzw):
    transform = np.eye(4)
    transform[:3, :3] = Rotation.from_quat(rotation_xyzw).as_matrix()
    transform[:3, 3] = translation
    return transform


class TransformHistory:
    """Recorded TF tree with bounded interpolation; never latest-value/extrapolation."""

    def __init__(self):
        self.parents, self.static, self.dynamic, self.ordered = {}, {}, {}, {}

    def add(self, parent, child, stamp, transform, *, static):
        transform = np.asarray(transform, dtype=np.float64)
        if (not parent or not child or parent == child or type(stamp) is not int or stamp < 0
                or transform.shape != (4, 4) or not np.isfinite(transform).all()
                or not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-12, rtol=0)
                or not np.allclose(transform[:3, :3].T @ transform[:3, :3], np.eye(3), atol=1e-8, rtol=0)
                or not np.isclose(np.linalg.det(transform[:3, :3]), 1, atol=1e-8, rtol=0)):
            raise ValueError("finite_valid_recorded_tf_required")
        if child in self.parents and self.parents[child] != parent:
            raise ValueError("unique_tf_parent_required")
        self.parents[child] = parent
        key = parent, child
        if static:
            if key in self.dynamic or (key in self.static
                                      and not np.allclose(self.static[key], transform, rtol=0, atol=1e-12)):
                raise ValueError("unchanged_static_tf_required")
            self.static[key] = transform.copy()
        else:
            if key in self.static:
                raise ValueError("static_and_dynamic_tf_cannot_overlap")
            rows = self.dynamic.setdefault(key, {})
            if stamp in rows and not np.allclose(rows[stamp], transform, rtol=0, atol=1e-12):
                raise ValueError("conflicting_tf_at_same_stamp")
            rows[stamp] = transform.copy()
            self.ordered.pop(key, None)

    def path(self, parent, child):
        path, seen = [child], {child}
        while path[-1] != parent:
            current = self.parents.get(path[-1])
            if current is None:
                raise ValueError("complete_ancestor_tf_path_required")
            if current in seen:
                raise ValueError("acyclic_tf_path_required")
            path.append(current)
            seen.add(current)
        return list(reversed(path))

    def query(self, path, stamp, maximum_gap_ns=MAXIMUM_TF_GAP_NS):
        if type(stamp) is not int or type(maximum_gap_ns) is not int or maximum_gap_ns <= 0:
            raise ValueError("integer_query_and_positive_gap_required")
        result, largest_gap = np.eye(4), 0
        for key in zip(path, path[1:]):
            if key in self.static:
                value = self.static[key]
            else:
                samples = self.dynamic[key]
                if key not in self.ordered:
                    self.ordered[key] = sorted(samples)
                stamps = self.ordered[key]
                index = bisect_left(stamps, stamp)
                if index < len(stamps) and stamps[index] == stamp:
                    value = samples[stamp]
                else:
                    if index == 0 or index == len(stamps):
                        return None, "outside_dynamic_tf_coverage"
                    left, right = stamps[index - 1], stamps[index]
                    gap = right - left
                    if gap > maximum_gap_ns:
                        return None, "dynamic_tf_interpolation_gap_exceeded"
                    fraction = (stamp - left) / gap
                    a, b = samples[left], samples[right]
                    value = np.eye(4)
                    value[:3, :3] = Slerp(
                        [0., 1.], Rotation.from_matrix(np.stack((a[:3, :3], b[:3, :3]))),
                    )([fraction]).as_matrix()[0]
                    value[:3, 3] = a[:3, 3] * (1 - fraction) + b[:3, 3] * fraction
                    largest_gap = max(largest_gap, gap)
            result = result @ value
        return result, largest_gap


def factory_file(path):
    if path.is_symlink() or not 0 < path.stat().st_size <= MAXIMUM_BYTES:
        raise ValueError("bounded_direct_private_factory_file_required")
    parser = YAML(typ="safe")
    parser.allow_duplicate_keys = False
    data = parser.load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("chest_lidar"), dict):
        raise ValueError("factory_chest_lidar_section_required")
    values = {}
    for field, length in (("R_baselink_sensor", 9), ("t_baselink_sensor", 3)):
        value = data["chest_lidar"].get(field)
        if (not isinstance(value, list) or len(value) != length
                or any(type(item) not in (int, float) or not math.isfinite(item) for item in value)):
            raise ValueError("explicit_finite_factory_lidar_arrays_required")
        values[field] = np.asarray(value, dtype=np.float64)
    rotation = values["R_baselink_sensor"].reshape(3, 3)
    orthogonality = float(np.max(np.abs(rotation.T @ rotation - np.eye(3))))
    determinant = float(np.linalg.det(rotation))
    if orthogonality > 1e-3 or abs(determinant - 1) > 1e-3:
        raise ValueError("valid_factory_lidar_rotation_required")
    transform = np.eye(4)
    transform[:3, :3], transform[:3, 3] = rotation, values["t_baselink_sensor"]
    return transform, {
        "sections": sorted(data),
        "lidarImuNamedSectionPresent": "lidar_imu_chest_front" in data,
        "chestImuSectionPresent": "chest_imu" in data,
        "waistImuSectionPresent": "waist_imu" in data,
        "chestLidarRotationDeterminant": determinant,
        "chestLidarRotationOrthogonalityMaximumError": orthogonality,
        "factoryBaseLidarCannotSupplyLidarImuExtrinsic": True,
    }


def sdk_document(archive):
    suffix = "/docs/cn/_build/html/en/dev/about_agibot_X2/coordinate_system.html"
    with zipfile.ZipFile(archive) as source:
        names = [name for name in source.namelist() if name.endswith(suffix)]
        if len(names) != 1:
            raise ValueError("unique_supplied_sdk_coordinate_document_required")
        payload = source.read(names[0])
    parser = Text()
    parser.feed(payload.decode("utf-8"))
    text = "".join("".join(parser.parts).split())
    if FACTORY_PATH not in text or any(token not in text for token in (
            "R_baselink_sensor", "t_baselink_sensor", "URDF", "FactoryCalibration")):
        raise ValueError("sdk_factory_file_declaration_not_verified")
    return {"member": names[0], "memberSha256": hashlib.sha256(payload).hexdigest(),
            "archiveSha256": sha256(archive), "factoryFileDeclaredAuthoritative": True,
            "urdfAndPerDeviceFactoryCalibrationAreDistinct": True}


def recorded_frames(bag, expected, profile, factory):
    from rosbags.highlevel import AnyReader
    from rosbags.typesys import Stores, get_typestore

    history, scans = TransformHistory(), []
    counts = dict.fromkeys((CLOUD, "/tf", "/tf_static"), 0)
    with AnyReader([bag], default_typestore=get_typestore(Stores.ROS2_HUMBLE)) as reader:
        selected = [connection for connection in reader.connections if connection.topic in counts]
        if ({connection.topic for connection in selected} != set(counts)
                or any(connection.msgtype != TYPES[connection.topic] for connection in selected)):
            raise ValueError("complete_typed_cloud_and_tf_connections_required")
        for connection, _, payload in reader.messages(connections=selected):
            counts[connection.topic] += 1
            message = reader.deserialize(payload, connection.msgtype)
            if connection.topic == CLOUD:
                _, offsets, dropped = cloud_arrays(message)
                if dropped:
                    raise ValueError("complete_finite_recorded_cloud_required")
                # Relative nanosecond field values were decoded as seconds; retain
                # the observed max offset, not a fitted or rounded 0.1 s shift.
                offset_ns = int(round(float(offsets.max()) * 1e9))
                if abs(offset_ns - float(offsets.max()) * 1e9) > 1e-4:
                    raise ValueError("integer_recorded_point_nanoseconds_required")
                scans.append((stamp_ns(message), offset_ns))
            else:
                for transform in message.transforms:
                    stamp = transform.header.stamp
                    history.add(
                        transform.header.frame_id, transform.child_frame_id,
                        int(stamp.sec) * 10**9 + int(stamp.nanosec),
                        matrix(vector(transform.transform.translation), quaternion(transform.transform.rotation)),
                        static=connection.topic == "/tf_static",
                    )
    for topic, alias in ((CLOUD, "cloud"), ("/tf", "tf"), ("/tf_static", "tf_static")):
        if counts[topic] != expected["topicCounts"][alias]:
            raise ValueError("all_selected_recorded_messages_required")
    observed = history.static[("lidar_chest_front", "lidar_imu_chest_front")]
    configured = matrix(profile["T_lidar_imu"][:3], profile["T_lidar_imu"][3:])
    if not np.allclose(observed, configured, rtol=0, atol=1e-12):
        raise ValueError("diagnostic_profile_differs_from_recorded_lidar_imu_tf")
    path = history.path("base_link", "lidar_chest_front")
    dynamic = [list(key) for key in zip(path, path[1:]) if key in history.dynamic]
    result = {
        "selectedMessageCounts": {key: counts[topic] for topic, key in
                                  ((CLOUD, "cloud"), ("/tf", "tf"), ("/tf_static", "tf_static"))},
        "profileMatchesRecordedLidarImuTf": True, "baseToLidarPath": path,
        "dynamicEdgesOnBaseToLidarPath": dynamic, "staticOnlyBaseToLidarPath": not bool(dynamic),
        "maximumInterpolationGapMilliseconds": MAXIMUM_TF_GAP_NS / 1e6, "queries": {},
    }
    for hypothesis in ("header_is_scan_start", "header_is_scan_end"):
        matrices, gaps, missed = [], [], {}
        for header, offset in scans:
            query_stamp = header if hypothesis == "header_is_scan_start" else header - offset
            value, gap = history.query(path, query_stamp)
            if value is None:
                missed[gap] = missed.get(gap, 0) + 1
            else:
                matrices.append(value)
                gaps.append(gap)
        if not matrices:
            raise ValueError("no_recorded_timestamped_base_lidar_tf_coverage")
        transforms = np.asarray(matrices)
        rotations = transforms[:, :3, :3]
        query = {
            "bracketedQueries": len(matrices), "uncoveredQueries": sum(missed.values()),
            "uncoveredReasons": missed,
            "largestUsedInterpolationGapMilliseconds": max(gaps) / 1e6,
            "maximumRotationFromFirstTfDegrees": float(np.degrees(
                Rotation.from_matrix(rotations[0].T @ rotations).magnitude()).max()),
            "maximumTranslationFromFirstTfMetres": float(np.linalg.norm(
                transforms[:, :3, 3] - transforms[0, :3, 3], axis=1).max()),
            "factoryConventionComparisonsNotCalibration": {},
        }
        for convention, candidate in (("literal_matrix", factory), ("inverse_matrix", np.linalg.inv(factory))):
            differences = candidate[:3, :3].T @ rotations
            query["factoryConventionComparisonsNotCalibration"][convention] = {
                "rotationDifferenceDegreesMinP50Max": np.percentile(
                    np.degrees(Rotation.from_matrix(differences).magnitude()), [0, 50, 100]).tolist(),
                "translationDifferenceMetresMinP50Max": np.percentile(
                    np.linalg.norm(transforms[:, :3, 3] - candidate[:3, 3], axis=1), [0, 50, 100]).tolist(),
            }
        result["queries"][hypothesis] = query
    return result


def warning_summary(text):
    rows, current = [], None
    for line in text.splitlines():
        match = re.search(r"\bnum_validations=(\d+)$", line)
        if match:
            current = {"cumulativeValidations": int(match[1])}
        match = re.search(r"IMU better ratios rot=([0-9.]+), trans=([0-9.]+), vel=([0-9.]+)$", line)
        if match:
            if (current is None or current["cumulativeValidations"] < 64
                    or current["cumulativeValidations"] % 64
                    or (rows and current["cumulativeValidations"] <= rows[-1]["cumulativeValidations"])):
                raise ValueError("complete_pinned_imu_warning_blocks_required")
            current["roundedBetterRatiosRotationTranslationVelocity"] = [float(value) for value in match.groups()]
            if any(not 0 <= value <= 1 for value in current["roundedBetterRatiosRotationTranslationVelocity"]):
                raise ValueError("finite_unit_interval_warning_ratios_required")
            rows.append(current)
            current = None
    warning_count = text.count("IMU prediction is not good.")
    if warning_count != len(rows):
        raise ValueError("every_logged_prediction_warning_must_be_accounted_for")
    return {
        "predictionWarningCount": warning_count, "loggedCumulativeWindows": rows,
        "biasMagnitudeWarningCount": text.count("IMU bias estimation seems inaccurate."),
        "roundedRatiosAreNotExactThresholdOrCalibrationTests": True,
    }


def warning_evidence(upstream, worker_results, live_results):
    version = subprocess.run(["git", "-C", str(upstream), "rev-parse", "HEAD"],
                             check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    relative = "src/glim/common/imu_validation.cpp"
    dirty = subprocess.run(["git", "-C", str(upstream), "diff", "HEAD", "--", relative],
                           check=True, capture_output=True, timeout=10).stdout
    source = (upstream / relative).read_text()
    if version != PINS["glim"] or dirty or any(token not in source for token in (
            "corrected_v_world_imu.norm() < 0.1", "num_validations % 64",
            "imu_better_ratios[0] > 0.7", "imu_better_ratios[1] > 0.4", "imu_better_ratios[2] > 0.5")):
        raise ValueError("clean_pinned_warning_semantics_required")
    result = {"upstreamCommit": version, "sourceSha256": sha256(upstream / relative),
              "minimumCorrectedSpeedMetresPerSecond": .1,
              "cumulativeReportInterval": 64, "strictBetterRatioThresholds": [.7, .4, .5],
              "comparisonReference": "optimized_state_not_external_ground_truth", "runs": {}}
    for kind, results, evidence_name in (
            ("paced", worker_results, "slam-arm64-worker-evidence.json"),
            ("live", live_results, "slam-humble-live-evidence.json")):
        public_path = ROOT / "docs" / evidence_name
        published = json.loads(public_path.read_text())
        for name, record in published["runs"].items():
            directory = results / name
            report_path = directory / "report.json"
            if sha256(report_path) != record["reportSha256"]:
                raise ValueError("immutable_published_target_report_required")
            report = json.loads(report_path.read_text())
            log = directory / "private-native.log"
            if sha256(log) != report["filesSha256"]["private-native.log"]:
                raise ValueError("immutable_private_native_log_required")
            result["runs"][kind + "-" + name] = {
                "reportSha256": sha256(report_path), "nativeLogSha256": sha256(log),
                **warning_summary(log.read_text()),
            }
    return result


def run(args):
    if os.sys.platform != "linux" or os.geteuid() == 0:
        raise ValueError("ordinary_user_offline_linux_audit_required")
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to((ROOT / ".local").resolve()):
        raise ValueError("new_private_audit_output_required")
    capture_path = ROOT / "docs/slam-capture-evidence.json"
    capture = json.loads(capture_path.read_text())
    integrity = verify(capture, args.data_root)
    factory_report_path = args.factory_read / "report.json"
    factory_report = json.loads(factory_report_path.read_text())
    factory_path = args.factory_read / "private-factory-calibration.yml"
    if (factory_report["result"] != "two_identical_read_only_factory_copies_captured_not_calibration_acceptance"
            or factory_report["readerSourceSha256"] != sha256(ROOT / "tools/read_robot_calibration.py")
            or factory_report["privateFileSha256"] != sha256(factory_path)
            or any(factory_report["boundaries"].values())):
        raise ValueError("unchanged_read_only_factory_capture_required")
    factory, factory_aggregate = factory_file(factory_path)
    profile_path = args.worker_bundle / "private-profile.json"
    worker_manifest_path = args.worker_bundle / "replay-manifest.json"
    worker_manifest = json.loads(worker_manifest_path.read_text())
    frozen_worker = json.loads((ROOT / "docs/slam-arm64-worker-evidence.json").read_text())
    if (sha256(worker_manifest_path) != frozen_worker["replayManifestSha256"]
            or sha256(profile_path) != worker_manifest["files"]["private-profile.json"]["sha256"]):
        raise ValueError("frozen_worker_profile_required")
    profile = json.loads(profile_path.read_text())
    report = {
        "schemaVersion": 1, "purpose": "factory_and_recorded_frame_audit_not_calibration_acceptance",
        "runnerSha256": sha256(__file__), "factoryReaderSha256": factory_report["readerSourceSha256"],
        "factoryCaptureReportSha256": sha256(factory_report_path),
        "privateFactoryFileSha256": sha256(factory_path), "factoryAggregate": factory_aggregate,
        "captureManifestSha256": sha256(capture_path), "profileSha256": sha256(profile_path),
        "sdkDocument": sdk_document(args.sdk_archive), "datasets": {},
        "analysisVersions": {key: metadata.version(key) for key in ("numpy", "scipy", "ruamel.yaml", "rosbags")},
        "warningSemantics": warning_evidence(args.upstream, args.worker_results, args.live_results),
        "boundaries": {key: False for key in BOUNDARIES},
    }
    for name, expected in capture["datasets"].items():
        report["datasets"][name] = recorded_frames(args.data_root / expected["directoryName"],
                                                  expected, profile, factory)
    if verify(capture, args.data_root) != integrity or sha256(factory_path) != factory_report["privateFileSha256"]:
        raise ValueError("original_captures_or_factory_copy_changed")
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("data-root", "factory-read", "worker-bundle", "worker-results", "live-results",
                 "upstream", "sdk-archive", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        report = run(args)
        print(json.dumps({"result": report["purpose"], "aggregateSha256": sha256(args.output),
                          "factoryAggregate": report["factoryAggregate"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
