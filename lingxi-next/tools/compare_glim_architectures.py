"""Read private recorded maps and compare exact common frames; never run SLAM or ROS."""
import argparse
import json
from pathlib import Path

import numpy as np

from build_slam_cpu_trial import PINS
from export_glim_mapping_evidence import RUNS
from glim_target_mapping import PURPOSE
from glim_target_replay import verify_replay_bundle
from run_glim_mapping_trial import inventory_map
from run_kiss_offline_trial import valid_pose
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
SAVED_TIME_TOLERANCE_SECONDS = 5.1e-10  # Upstream prints relative stamps to nine decimal places.
REVIEWED_SOURCES = {
    "src/glim/mapping/sub_mapping_passthrough.cpp":
        "7ab652e45aced130c7289cf95413fbfc2a91acf7cdc467827dd63c15562cf86e",
    "src/glim/mapping/sub_map.cpp":
        "9120ec005d4be9afbd7814fef4fb5b63cb7747a7637b1720bef5c5e16e8691b3",
    "src/glim/odometry/estimation_frame.cpp":
        "884761a8b228ecdff59dc272578bf91bceb76c44c12c7a1c305d025b37e134a3",
    "config/config_sub_mapping_passthrough.json":
        "78807724166f5096ced9fb3a19fc9cb9fcb0994209c9486f573337da99c0e6b8",
}
BOUNDARIES = (
    "crossArchitectureGeometryAccepted", "divergenceRootCauseEstablished",
    "timingHypothesisValidated", "robotCalibrationValidated", "groundTruthAvailable",
    "loopClosureValidated", "relocalizationValidated", "targetRealtimePerformanceValidated",
    "voiceConcurrencyValidated", "navigationValidated", "deploymentApproved", "rawDataPublished",
    "robotContactedByComparator", "rosNodeStartedByComparator", "motionCommandsSent",
)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def ordered_stamps(stamps):
    if (stamps.dtype != np.dtype("int64") or stamps.ndim != 1 or len(stamps) < 2
            or np.any(stamps < 0) or np.any(stamps[1:] <= stamps[:-1])):
        raise ValueError("ordered_unique_integer_header_nanoseconds_required")


def pose_array(poses):
    if poses.ndim != 3 or poses.shape[1:] != (4, 4) or len(poses) < 2:
        raise ValueError("complete_pose_matrices_required")
    for pose in poses:
        valid_pose(pose)


def common_frames(first, second):
    ordered_stamps(first)
    ordered_stamps(second)
    common, left, right = np.intersect1d(first, second, assume_unique=True, return_indices=True)
    if len(common) < 2:
        raise ValueError("at_least_two_exact_common_header_stamps_required")
    return common, left, right


def distribution(values):
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or not len(values) or not np.isfinite(values).all():
        raise ValueError("nonempty_finite_metric_required")
    return {
        "p50": float(np.percentile(values, 50)), "p95": float(np.percentile(values, 95)),
        "maximum": float(values.max()), "rms": float(np.sqrt(np.mean(values ** 2))),
    }


def pose_differences(first, second):
    delta_rotation = np.swapaxes(first[:, :3, :3], 1, 2) @ second[:, :3, :3]
    skew = np.stack((
        delta_rotation[:, 2, 1] - delta_rotation[:, 1, 2],
        delta_rotation[:, 0, 2] - delta_rotation[:, 2, 0],
        delta_rotation[:, 1, 0] - delta_rotation[:, 0, 1],
    ), axis=1) / 2
    angles = np.arctan2(np.linalg.norm(skew, axis=1),
                        np.clip((np.trace(delta_rotation, axis1=1, axis2=2) - 1) / 2, -1, 1))
    return {
        "positionDifferenceRawUnits": distribution(np.linalg.norm(first[:, :3, 3] - second[:, :3, 3], axis=1)),
        "orientationDifferenceRadians": distribution(angles),
    }


def compare_poses(first_stamps, first_poses, second_stamps, second_poses):
    pose_array(first_poses)
    pose_array(second_poses)
    if len(first_poses) != len(first_stamps) or len(second_poses) != len(second_stamps):
        raise ValueError("one_pose_per_header_stamp_required")
    stamps, left, right = common_frames(first_stamps, second_stamps)
    first, second = first_poses[left], second_poses[right]
    alignment = first[0] @ np.linalg.inv(second[0])
    return {
        "matching": {
            "method": "exact_int64_header_nanoseconds_no_interpolation",
            "referenceFrames": len(first_stamps), "candidateFrames": len(second_stamps),
            "commonFrames": len(stamps),
            "excludedReferenceFrames": len(first_stamps) - len(stamps),
            "excludedCandidateFrames": len(second_stamps) - len(stamps),
            "commonSpanSeconds": (int(stamps[-1]) - int(stamps[0])) / 1e9,
            "referenceInternalGaps": int(np.count_nonzero(np.diff(left) != 1)),
            "candidateInternalGaps": int(np.count_nonzero(np.diff(right) != 1)),
        },
        "unadjusted": pose_differences(first, second),
        "firstCommonPoseRigidAlignmentOnly": pose_differences(first, alignment @ second),
        "relativeBetweenConsecutiveCommonFrames": pose_differences(
            np.linalg.inv(first[:-1]) @ first[1:], np.linalg.inv(second[:-1]) @ second[1:]),
        "scaleFitted": False, "bestFitTrajectoryAlignmentUsed": False, "accuracyAgainstTruthMeasured": False,
    }


class SubmapTokens:
    """Strict reader for the reviewed upstream text format, not an external map importer."""

    def __init__(self, text):
        self.tokens = iter(text.split())

    def take(self):
        try:
            return next(self.tokens)
        except StopIteration as error:
            raise ValueError("incomplete_saved_submap_metadata") from error

    def expect(self, value):
        if self.take() != value:
            raise ValueError("unexpected_saved_submap_metadata")

    def integer(self):
        token = self.take()
        if not token.isascii() or not token.isdecimal():
            raise ValueError("nonnegative_saved_frame_integer_required")
        return int(token)

    def numbers(self, count):
        values = np.asarray([float(self.take()) for _ in range(count)])
        if not np.isfinite(values).all():
            raise ValueError("finite_saved_submap_metadata_required")
        return values


def submap_members(text, expected_id):
    reader = SubmapTokens(text)
    reader.expect("id:")
    if reader.integer() != expected_id:
        raise ValueError("ordered_saved_submap_id_required")
    for name in ("T_world_origin:", "T_origin_endpoint_L:", "T_origin_endpoint_R:", "T_lidar_imu:"):
        reader.expect(name)
        reader.numbers(16)
    reader.expect("imu_bias:")
    reader.numbers(6)
    reader.expect("frame_id:")
    if reader.integer() not in (1, 2):
        raise ValueError("saved_sensor_frame_required")
    reader.expect("num_frames:")
    count = reader.integer()
    if count <= 0:
        raise ValueError("nonempty_saved_submap_required")
    ids, stamps = [], []
    for index in range(count):
        reader.expect("frame_" + str(index))
        reader.expect("id:")
        ids.append(reader.integer())
        reader.expect("stamp:")
        stamps.append(reader.numbers(1)[0])
        for name in ("T_odom_lidar:", "T_world_lidar:"):
            reader.expect(name)
            reader.numbers(16)
        reader.expect("v_world_imu:")
        reader.numbers(3)
    if next(reader.tokens, None) is not None:
        raise ValueError("unconsumed_saved_submap_metadata")
    return ids, stamps


def read_membership(path, submaps, algorithm_stamps):
    ids, stamps, counts = [], [], []
    for index in range(submaps):
        members, times = submap_members((path / f"{index:06d}/data.txt").read_text(), index)
        counts.append(len(members))
        ids.extend(members)
        stamps.extend(times)
    if (len(ids) != len(algorithm_stamps) or np.any(np.diff(ids) != 1)
            or not np.allclose(stamps, algorithm_stamps, atol=SAVED_TIME_TOLERANCE_SECONDS, rtol=0)):
        raise ValueError("every_saved_frame_once_in_algorithm_time_order_required")
    return counts


def saved_lidar_poses(path, algorithm_stamps):
    values = np.loadtxt(path, ndmin=2)
    if (values.shape != (len(algorithm_stamps), 8) or not np.isfinite(values).all()
            or not np.allclose(values[:, 0], algorithm_stamps, atol=SAVED_TIME_TOLERANCE_SECONDS, rtol=0)):
        raise ValueError("saved_lidar_trajectory_time_order_mismatch")
    quaternions = values[:, 4:]
    norms = np.linalg.norm(quaternions, axis=1)
    if np.any(np.abs(norms - 1) > 2e-6):
        raise ValueError("saved_unit_quaternion_required")
    x, y, z, w = (quaternions / norms[:, None]).T
    poses = np.repeat(np.eye(4)[None, :, :], len(values), axis=0)
    poses[:, :3, 3] = values[:, 1:4]
    poses[:, :3, :3] = np.stack((
        1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w),
        2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w),
        2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y),
    ), axis=1).reshape(-1, 3, 3)
    pose_array(poses)
    return poses


def partition_comparison(first, second):
    if not first or not second or any(type(count) is not int or count <= 0 for count in (*first, *second)):
        raise ValueError("positive_complete_partition_counts_required")
    if sum(first) != sum(second):
        raise ValueError("same_complete_frame_domain_required_for_partition_comparison")
    left, right = np.cumsum(first)[:-1], np.cumsum(second)[:-1]
    prefix = 0
    for a, b in zip(left, right):
        if a != b:
            break
        prefix += 1
    two_internal = prefix < min(len(left), len(right))
    both_short = two_internal and first[prefix] < 50 and second[prefix] < 50
    return {
        "referenceSubmaps": len(first), "candidateSubmaps": len(second),
        "referenceFramesPerSubmap": first, "candidateFramesPerSubmap": second,
        "identicalInternalBoundaryPrefixCount": prefix,
        "sharedInternalBoundaries": len(np.intersect1d(left, right)),
        "identicalPartition": first == second,
        "firstDifferentBoundaryIsInternalOnBothSides": two_internal,
        "firstDifferentInternalSubmapsBothHaveFewerThan50Frames": both_short,
        "sourceRuleInferenceNotRuntimeTrace": (
            "adaptive_voxel_criterion_required_for_both_short_internal_submaps"
            if both_short else "not_determined_from_frame_counts"),
        "splitTriggerTraceAvailable": False,
        "runtimeSplitTriggerDirectlyObserved": False,
    }


def require_hash(path, expected):
    if path.is_symlink() or not path.is_file() or sha256(path) != expected:
        raise ValueError("historical_artifact_digest_mismatch")


def load_run(path, label, architecture, evidence):
    public = evidence["runs"][label]
    target = architecture == "aarch64"
    report_key = "privateMappingReportSha256" if target else "localMappingReportSha256"
    require_hash(path / "mapping-report.json", public[report_key])
    mapped = read_json(path / "mapping-report.json")
    require_hash(path / "report.json", public["baseReplayReportSha256"])
    base = read_json(path / "report.json")
    require_hash(path / "private-trajectory.npz", base["privateTrajectorySha256"])
    dataset, hypothesis, _ = RUNS[label]
    actual_machine = base["target"]["machine"] if target else mapped["replayHost"]["machine"]
    if (actual_machine != architecture or mapped["sourceCommits"] != PINS
            or (base["dataset"], base["timingHypothesis"]) != (dataset, hypothesis)
            or (mapped["dataset"], mapped["timingHypothesis"]) != (dataset, hypothesis)
            or mapped["baseReplayReportSha256"] != public["baseReplayReportSha256"]
            or mapped["mapInventory"] != inventory_map(path / "map")
            or mapped["mapInventory"] != public["mapInventory"]
            or mapped["mapping"] != public["mapping"]):
        raise ValueError("historical_map_run_contract_mismatch")
    with np.load(path / "private-trajectory.npz", allow_pickle=False) as arrays:
        stamps = arrays["header_stamps_ns"].copy()
        algorithm = arrays["algorithm_stamps_relative_seconds"].copy()
        poses = arrays["poses"].copy()
        measurements = arrays["preprocess_odometry_pointcount_marginalized"].copy()
    ordered_stamps(stamps)
    pose_array(poses)
    if (len(stamps) != base["estimatedPoses"] or len(poses) != len(stamps)
            or algorithm.shape != stamps.shape or not np.isfinite(algorithm).all()
            or np.any(np.diff(algorithm) <= 0)
            or measurements.shape != (base["processedScans"], 4)
            or not np.isfinite(measurements).all() or np.any(measurements < 0)
            or np.any(measurements[:, 2:] != np.floor(measurements[:, 2:]))):
        raise ValueError("complete_historical_trajectory_required")
    members = read_membership(path / "map", mapped["mapping"]["submaps"], algorithm)
    final = saved_lidar_poses(path / "map/traj_lidar.txt", algorithm)
    return {
        "stamps": stamps, "algorithm": algorithm, "online": poses, "saved": final, "members": members,
        "scanCounters": measurements[:, 2:],
        "provenance": {
            "mappingReportSha256": sha256(path / "mapping-report.json"),
            "baseReplayReportSha256": sha256(path / "report.json"),
            "privateTrajectorySha256": base["privateTrajectorySha256"],
            "mapArtifactInventorySha256": mapped["mapInventory"]["artifactInventorySha256"],
        },
    }


def compare_scan_counters(first, second):
    if (first.ndim != 2 or first.shape[1] != 2 or first.shape != second.shape or len(first) < 2
            or not np.isfinite(first).all() or not np.isfinite(second).all()
            or np.any(first < 0) or np.any(second < 0)
            or np.any(first != np.floor(first)) or np.any(second != np.floor(second))):
        raise ValueError("same_complete_processed_scan_counter_domain_required")
    different = np.flatnonzero(first[:, 0] != second[:, 0])
    return {
        "processedScansComparedInRecordedOrder": len(first),
        "preparedPointCountDifferentScans": len(different),
        "maximumAbsolutePreparedPointCountDifference": int(np.abs(first[:, 0] - second[:, 0]).max()),
        "firstPreparedCountDifferenceProcessedOrdinalOneBased": int(different[0]) + 1 if len(different) else None,
        "marginalizedFrameCountDifferentScans": int(np.count_nonzero(first[:, 1] != second[:, 1])),
        "preparedPointCoordinatesAndOrderWereNotRecorded": True,
        "equalCountsAreNotProofOfIdenticalPreparedPoints": True,
    }


def compare_runs(first, second, same_hypothesis):
    _, left, right = common_frames(first["stamps"], second["stamps"])
    time_difference = np.abs(first["algorithm"][left] - second["algorithm"][right])
    result = {
        "onlineLidarPosesNotFinalSmoothed": compare_poses(
            first["stamps"], first["online"], second["stamps"], second["online"]),
        "savedGlobalLidarPosesRoundedToSixDecimals": compare_poses(
            first["stamps"], first["saved"], second["stamps"], second["saved"]),
        "algorithmTimeDifferenceSeconds": distribution(time_difference),
        "sameTimingHypothesis": same_hypothesis,
        "differentHypothesisPosesReferToDifferentScanInstants": not same_hypothesis,
    }
    if same_hypothesis:
        if (not np.array_equal(first["stamps"], second["stamps"])
                or not np.array_equal(first["algorithm"], second["algorithm"])):
            raise ValueError("cross_architecture_complete_frame_domains_and_times_differ")
        result["savedSubmapPartition"] = partition_comparison(first["members"], second["members"])
        result["recordedPreprocessingCounters"] = compare_scan_counters(
            first["scanCounters"], second["scanCounters"])
    return result


def export(host_cache, target_results, target_bundle, upstream):
    for relative, digest in REVIEWED_SOURCES.items():
        require_hash(upstream / relative, digest)
    host_evidence = read_json(ROOT / "docs/slam-glim-mapping-evidence.json")
    target_evidence = read_json(ROOT / "docs/slam-arm64-mapping-evidence.json")
    manifest = verify_replay_bundle(target_bundle, PURPOSE)
    require_hash(target_bundle / "replay-manifest.json", target_evidence["replayManifestSha256"])
    capture_digest = sha256(ROOT / "docs/slam-capture-evidence.json")
    if manifest["captureManifestSha256"] != capture_digest:
        raise ValueError("identical_recorded_capture_manifest_required")
    result = {
        "schemaVersion": 1, "observedDate": "2026-10-28",
        "purpose": "read_only_exact_common_frame_comparison_not_accuracy_or_causal_acceptance",
        "captureManifestSha256": capture_digest, "sourceCommits": PINS,
        "historicalEvidenceSha256": {
            "host": sha256(ROOT / "docs/slam-glim-mapping-evidence.json"),
            "target": sha256(ROOT / "docs/slam-arm64-mapping-evidence.json"),
        },
        "comparatorSourceSha256": sha256(Path(__file__)),
        "reviewedUpstreamSourcesSha256": REVIEWED_SOURCES.copy(),
        "submappingRules": {
            "translationKeyframeStrictlyGreaterThanRawUnits": 0.1,
            "rotationKeyframeStrictlyGreaterThanRadians": 0.01,
            "maximumKeyframes": 50, "absoluteVoxelLimitDisabled": True,
            "adaptiveVoxelRatioRelativeToThirdKeyframe": 2.5,
            "endOfSequenceFlushesNonemptyRemainder": True,
            "runtimeKeyframeAndVoxelTraceWasNotRecorded": True,
            "roundedSavedPosesAreNotAnExactSplitDecisionTrace": True,
            "repeatTrialsSeparatingArchitectureFromRunVariationCompleted": False,
        },
        "provenance": {}, "crossArchitecture": {}, "crossTimingHypotheses": {},
        "boundaries": {name: False for name in BOUNDARIES},
    }
    loaded = {"x86_64": {}, "aarch64": {}}
    for label, (_, _, folder) in RUNS.items():
        host_path = host_cache / folder
        if read_json(host_path / "report.json")["captureManifestSha256"] != capture_digest:
            raise ValueError("host_capture_manifest_mismatch")
        for name, entry in manifest["files"].items():
            if name.startswith("config/"):
                require_hash(host_path / name, entry["sha256"])
        for architecture, path, evidence in (
            ("x86_64", host_path, host_evidence), ("aarch64", target_results / label, target_evidence),
        ):
            loaded[architecture][label] = load_run(path, label, architecture, evidence)
        result["provenance"][label] = {
            architecture: loaded[architecture][label]["provenance"] for architecture in loaded
        }
        result["crossArchitecture"][label] = compare_runs(
            loaded["x86_64"][label], loaded["aarch64"][label], True)
    for architecture, runs in loaded.items():
        result["crossTimingHypotheses"][architecture] = {
            dataset: compare_runs(runs[dataset + "Start"], runs[dataset + "End"], False)
            for dataset in ("static", "dynamic")
        }
    # Recheck every saved map and trajectory after all comparisons; no upstream loading occurs.
    for label, (_, _, folder) in RUNS.items():
        for architecture, path in (
            ("x86_64", host_cache / folder), ("aarch64", target_results / label),
        ):
            provenance = loaded[architecture][label]["provenance"]
            require_hash(path / "private-trajectory.npz", provenance["privateTrajectorySha256"])
            if inventory_map(path / "map")["artifactInventorySha256"] != provenance["mapArtifactInventorySha256"]:
                raise ValueError("historical_map_changed_during_comparison")
    result["allHistoricalMapAndTrajectoryDigestsUnchanged"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host-cache", "target-results", "target-bundle", "upstream-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("existing_comparison_evidence_is_preserved")
    evidence = export(args.host_cache, args.target_results, args.target_bundle, args.upstream_root)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"result": "eight_historical_maps_compared_without_robot_contact",
                      "crossArchitectureComparisons": len(evidence["crossArchitecture"])}))


if __name__ == "__main__":
    main()
