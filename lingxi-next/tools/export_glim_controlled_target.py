"""Recompute all controlled target maps and complete point geometry; publish aggregates only."""
import argparse
from importlib import metadata
import itertools
import json
from pathlib import Path

import numpy as np

from build_slam_cpu_trial import PINS
from compare_glim_architectures import (
    ordered_stamps, pose_array, read_membership, require_hash, saved_lidar_poses,
)
from compare_pointcloud_geometry import compare_maps
from export_glim_controlled_mapping import comparison, load_run as load_host_run
from export_glim_mapping_evidence import check_reload_log
from export_glim_target_mapping_evidence import INVENTORY_FIELDS, ROUND_TRIP_FIELDS
from export_glim_target_replay_evidence import COUNTS, recompute
from glim_target_controlled_mapping import (
    BASELINE_MANIFEST, BOUNDARIES, EFFECTIVE_MANIFEST, FILES, PURPOSE, verify_control,
)
from glim_target_mapping import PURPOSE as PARENT_PURPOSE, validate_mapping_counts
from glim_target_replay import verify_replay_bundle
from run_glim_controlled_target import CONDITIONS, label
from run_glim_mapping_trial import compare_points, inventory_map
from slam_target_probe import verify_bundle
from verify_slam_capture import sha256, verify


ROOT = Path(__file__).resolve().parents[1]
COSTS = ("processingWallSeconds", "processCpuSeconds", "peakResidentMiB",
         "preprocessMillisecondsP50P95P99", "odometryMillisecondsP50P95P99",
         "combinedMillisecondsP50P95P99", "combinedMillisecondsMaximum")
BASE_BOUNDARIES = (
    "rosNodeStarted", "liveSensorsSubscribed", "motionCommandsSent", "timingHypothesisValidated",
    "robotCalibrationValidated", "globalMappingExecuted", "mapPersistenceValidatedOnTarget",
    "relocalizationValidated", "navigationValidated", "targetRealtimePerformanceValidated",
    "voiceConcurrencyValidated", "deploymentApproved", "rawDataPublished",
)
ROUND_TRIP_RMS_MAXIMUM_ULPS = 2


def matching_round_trip(recomputed, recorded):
    rms = "rmsPointDisplacementRawUnits"
    if set(recorded) != set(ROUND_TRIP_FIELDS):
        return False
    if any(recorded[name] != recomputed[name] for name in ROUND_TRIP_FIELDS if name != rms):
        return False
    observed, expected = recorded[rms], recomputed[rms]
    if type(observed) not in (int, float) or not np.isfinite(observed) or observed < 0:
        return False
    # Cross-architecture reduction may round RMS differently; all other fields remain exact.
    allowance = ROUND_TRIP_RMS_MAXIMUM_ULPS * np.spacing(expected) if expected else 0.
    return abs(observed - expected) <= allowance


def load_target(path, condition, collection, manifest, bundle_hash, capture_integrity, expected_libraries):
    dataset, hypothesis, _ = condition
    require_hash(path / "controlled-report.json", collection["reportSha256"])
    report = json.loads((path / "controlled-report.json").read_text())
    if (collection["exitCode"] != 0
            or report["result"] != "complete_controlled_arm64_map_saved_and_reloaded_not_geometry_acceptance"
            or (report["dataset"], report["timingHypothesis"]) != (dataset, hypothesis)
            or report["target"]["machine"] != "aarch64" or report["sourceCommits"] != PINS
            or type(report["pid"]) is not int or report["pid"] <= 0
            or report["replayManifestSha256"] != bundle_hash
            or report["controlManifestSha256"] != manifest["controlManifestSha256"]
            or report["runnerSha256"] != manifest["files"]["tools/glim_target_controlled_mapping.py"]["sha256"]
            or report["boundaries"] != {name: False for name in BOUNDARIES}
            or set(report["filesSha256"]) != set(FILES)):
        raise ValueError("complete_verified_controlled_target_report_required")
    for name, digest in report["filesSha256"].items():
        require_hash(path / name, digest)
    if (not {"libglim.so", "libodometry_estimation_cpu.so", "liblingxi_glim_offline.so",
             "liblingxi_glim_mapping.so"}.issubset(report["actualLoadedLibrariesSha256"])
            or any(expected_libraries.get(name) != digest for name, digest in
                   report["actualLoadedLibrariesSha256"].items())):
        raise ValueError("actual_verified_target_native_libraries_required")
    base = json.loads((path / "report.json").read_text())
    reloaded = json.loads((path / "reload-report.json").read_text())
    if (base["result"] != "all_recorded_scans_accounted_for_on_arm64_not_quality_acceptance"
            or (base["dataset"], base["timingHypothesis"]) != (dataset, hypothesis)
            or base["sourceCommits"] != PINS or base["target"] != report["target"]
            or base["runnerSha256"] != manifest["files"]["tools/glim_target_replay.py"]["sha256"]
            or base["controlledMappingRunnerSha256"] != report["runnerSha256"]
            or base["replayManifestSha256"] != bundle_hash
            or base["captureIntegrity"] != capture_integrity or base["captureIntegrityAfterReplay"] != capture_integrity
            or base["recordedScans"] != capture_integrity["datasets"][dataset]["topicCounts"]["cloud"]
            or base["reader"] != "rosbag2_py_humble_sequential_reader_no_ros_init"
            or base["privateTrajectorySha256"] != report["filesSha256"]["private-trajectory.npz"]
            or base["privateNativeLogSha256"] != report["filesSha256"]["private-native.log"]
            or base["processedScans"] + base["skippedUnbracketedScans"] != base["recordedScans"]
            or base["processedScans"] - base["initializationScans"] != base["estimatedPoses"]
            or base["mapping"] != report["mapping"]
            or base["checks"] != {"recordedDataProcessedOnArm64": True, "allScansAccountedFor": True,
                                   "originalCapturesUnchanged": True}
            or base["boundaries"] != {name: name == "globalMappingExecuted" for name in BASE_BOUNDARIES}):
        raise ValueError("complete_once_only_target_scan_accounting_and_capture_integrity_required")
    counts = report["mapping"]
    validate_mapping_counts(counts, base["estimatedPoses"])
    inventory = inventory_map(path / "map")
    round_trip = compare_points(path / "private-saved-points.f64", path / "private-reloaded-points.f64", counts["points"])
    expected_config = {name[7:]: entry["sha256"] for name, entry in manifest["files"].items()
                       if name.startswith("config/")}
    if (inventory != report["mapInventory"] or inventory != reloaded["mapInventory"]
            or inventory["num_submaps"] != counts["submaps"] or inventory["num_all_frames"] != counts["savedFrames"]
            or report["roundTrip"] != reloaded["comparison"]
            or not matching_round_trip(round_trip, report["roundTrip"])
            or reloaded["result"] != "independent_controlled_arm64_map_reloaded_not_relocalization"
            or reloaded["machine"] != "aarch64" or type(reloaded["pid"]) is not int
            or reloaded["pid"] <= 0 or reloaded["pid"] == report["pid"]
            or reloaded["effectiveConfiguration"] != expected_config
            or reloaded["boundaries"] != report["boundaries"]):
        raise ValueError("complete_independent_controlled_target_map_round_trip_required")
    for name in ("private-native.log", "private-reload.log"):
        check_reload_log(path / name)
    with np.load(path / "private-trajectory.npz", allow_pickle=False) as archive:
        if set(archive) != {"poses", "header_stamps_ns", "algorithm_stamps_relative_seconds",
                           "preprocess_odometry_pointcount_marginalized"}:
            raise ValueError("all_controlled_target_arrays_required")
        poses, stamps, algorithm, measures = (archive[name].copy() for name in (
            "poses", "header_stamps_ns", "algorithm_stamps_relative_seconds",
            "preprocess_odometry_pointcount_marginalized"))
        aggregate = recompute(base, archive, capture_integrity["datasets"][dataset])
    ordered_stamps(stamps)
    pose_array(poses)
    if (len(stamps) != base["estimatedPoses"] or len(poses) != len(stamps)
            or algorithm.shape != stamps.shape or not np.isfinite(algorithm).all() or np.any(np.diff(algorithm) <= 0)
            or measures.shape != (base["processedScans"], 4) or not np.isfinite(measures).all()
            or np.any(measures < 0) or np.any(measures[:, 2:] != np.floor(measures[:, 2:]))):
        raise ValueError("finite_complete_controlled_target_trajectory_and_counters_required")
    points = np.fromfile(path / "private-saved-points.f64", dtype="<f8")
    if points.shape != (counts["points"] * 3,) or not np.isfinite(points).all():
        raise ValueError("complete_finite_controlled_target_point_map_required")
    if (type(report["totalWallSecondsIncludingReload"]) not in (int, float)
            or not np.isfinite(report["totalWallSecondsIncludingReload"])
            or report["totalWallSecondsIncludingReload"] < base["processingWallSeconds"]):
        raise ValueError("finite_complete_controlled_target_wall_cost_required")
    return {
        "stamps": stamps, "algorithm": algorithm, "online": poses,
        "saved": saved_lidar_poses(path / "map/traj_lidar.txt", algorithm),
        "members": read_membership(path / "map", counts["submaps"], algorithm),
        "scanCounters": measures[:, 2:], "points": points.reshape(-1, 3),
        "base": base, "report": report, "aggregate": aggregate, "roundTrip": round_trip,
        "roundTripRmsRecomputationDifferenceRawUnits": abs(
            round_trip["rmsPointDisplacementRawUnits"] - report["roundTrip"]["rmsPointDisplacementRawUnits"]),
    }


def summary(value):
    report = value["report"]
    return {
        "dataset": report["dataset"], "timingHypothesis": report["timingHypothesis"],
        "target": {name: report["target"][name] for name in ("machine", "python", "numpy")},
        "mapping": {name: report["mapping"][name] for name in (
            "finalizedFramesFedOnce", "savedFrames", "submaps", "points", "minimumLoopTravel", "submapTravel")},
        "mapInventory": {name: report["mapInventory"][name] for name in INVENTORY_FIELDS},
        "roundTrip": {name: value["roundTrip"][name] for name in ROUND_TRIP_FIELDS},
        "roundTripRmsRecomputationDifferenceRawUnits": value["roundTripRmsRecomputationDifferenceRawUnits"],
        "scanAccounting": {name: value["aggregate"]["counts"][name] for name in COUNTS},
        "observedOfflineCostsNotRealtimeAcceptance": {
            name: value["aggregate"]["timingsNotRealtimeAcceptance"][name] for name in COSTS
        },
        "estimatedMotionNotTruth": {
            name: value["aggregate"]["estimatedMotionNotGroundTruth"][name] for name in (
                "accumulatedPositionStepsRawUnits", "endToStartDistanceRawUnits",
                "maximumConsecutivePositionStepRawUnits")
        },
        "totalWallSecondsIncludingReload": report["totalWallSecondsIncludingReload"],
        "filesSha256": {name: report["filesSha256"][name] for name in FILES},
    }


def compare_with_geometry(first, second):
    result = comparison(first, second, True)
    del result["completeSavedMapPoints"]["noNearestNeighborGeometryOrTruthAccuracyClaimed"]
    result["completeSavedMapPoints"]["bitComparisonDoesNotProveGeometricAccuracy"] = True
    result["completePointGeometryNotTruthAccuracy"] = compare_maps(first, second)
    return result


def export(args):
    if metadata.version("scipy") != "1.16.3" or metadata.version("numpy") != "2.5.4":
        raise ValueError("pinned_isolated_geometry_dependencies_required")
    manifest = verify_replay_bundle(args.bundle, PURPOSE)
    parent = verify_replay_bundle(args.parent_bundle, PARENT_PURPOSE)
    core = verify_bundle(args.core_bundle)
    control = verify_control(args.bundle)
    require_hash(args.parent_bundle / "replay-manifest.json", manifest["parentMappingManifestSha256"])
    require_hash(ROOT / "tools/prepare_glim_controlled_target.py", manifest["preparerSourceSha256"])
    if (manifest["coreBundleManifestSha256"] != sha256(args.core_bundle / "bundle-manifest.json")
            or any(manifest[key] != parent[key] for key in (
                "mappingAdapterSha256", "odometryAdapterSha256", "coreBundleManifestSha256",
                "adapterBuildReportSha256"))):
        raise ValueError("unchanged_native_mapping_adapters_and_core_required")
    for name, entry in manifest["files"].items():
        if name.startswith("tools/"):
            require_hash(ROOT / name, entry["sha256"])
    host_evidence_path = ROOT / "docs/slam-controlled-mapping-evidence.json"
    host_evidence = json.loads(host_evidence_path.read_text())
    if (host_evidence["baselineConfigurationManifestSha256"] != BASELINE_MANIFEST
            or host_evidence["effectiveConfigurationManifestSha256"] != EFFECTIVE_MANIFEST):
        raise ValueError("same_host_and_target_control_configuration_required")
    collection = json.loads((args.results / "collection-report.json").read_text())
    require_hash(ROOT / "tools/run_glim_controlled_target.py", collection["coordinatorSourceSha256"])
    bundle_hash = sha256(args.bundle / "replay-manifest.json")
    conditions = {label(*condition): condition for condition in CONDITIONS}
    if (collection["result"] != "twelve_actual_arm64_controlled_maps_completed_not_geometry_acceptance"
            or collection["replayManifestSha256"] != bundle_hash or set(collection["runs"]) != set(conditions)):
        raise ValueError("all_twelve_predeclared_controlled_target_maps_required")
    capture_path = ROOT / "docs/slam-capture-evidence.json"
    require_hash(args.bundle / "capture-manifest.json", sha256(capture_path))
    capture = json.loads(capture_path.read_text())
    integrity = verify(capture, args.data_root)
    expected_libraries = {Path(name).name: entry["sha256"] for name, entry in core["files"].items()
                          if name.startswith("lib/")}
    expected_libraries.update({name: manifest["files"]["lib/" + name]["sha256"]
                               for name in ("liblingxi_glim_offline.so", "liblingxi_glim_mapping.so")})
    target = {name: load_target(args.results / name, condition, collection["runs"][name],
                               manifest, bundle_hash, integrity, expected_libraries)
              for name, condition in conditions.items()}
    if len({value["report"]["pid"] for value in target.values()}) != 12:
        raise ValueError("twelve_distinct_controlled_target_processes_required")
    loaded = next(iter(target.values()))["report"]["actualLoadedLibrariesSha256"]
    if any(value["report"]["actualLoadedLibrariesSha256"] != loaded for value in target.values()):
        raise ValueError("same_actual_target_native_libraries_in_all_runs_required")
    host = {name: load_host_run(
        args.host_results / name, condition,
        {"exitCode": 0, "reportSha256": host_evidence["runs"][name]["controlledReportSha256"]},
        BASELINE_MANIFEST) for name, condition in conditions.items()}
    result = {
        "schemaVersion": 1, "observedDate": "2026-10-28",
        "purpose": "actual_arm64_controlled_full_mapping_and_complete_geometry_not_truth_accuracy",
        "sourceCommits": PINS, "freshTargetProcessCount": 12, "repeatsPerCondition": 3,
        "captureManifestSha256": sha256(capture_path),
        "coreBundleManifestSha256": manifest["coreBundleManifestSha256"],
        "nativeMappingBuildReportSha256": manifest["adapterBuildReportSha256"],
        "mappingAdapterSha256": manifest["mappingAdapterSha256"],
        "odometryAdapterSha256": manifest["odometryAdapterSha256"],
        "hostControlledEvidenceSha256": sha256(host_evidence_path),
        "parentMappingManifestSha256": manifest["parentMappingManifestSha256"],
        "replayManifestSha256": bundle_hash, "collectionReportSha256": sha256(args.results / "collection-report.json"),
        "controlManifestSha256": manifest["controlManifestSha256"],
        "baselineConfigurationManifestSha256": BASELINE_MANIFEST,
        "effectiveConfigurationManifestSha256": EFFECTIVE_MANIFEST,
        "semanticParameterChanges": control["semanticParameterChanges"],
        "actualTargetSourcesSha256": {name[6:]: entry["sha256"] for name, entry in manifest["files"].items()
                                     if name.startswith("tools/")},
        "toolSourcesSha256": {name: sha256(ROOT / "tools" / name) for name in (
            "prepare_glim_controlled_target.py", "run_glim_controlled_target.py",
            "export_glim_controlled_target.py", "compare_pointcloud_geometry.py",
            "compare_glim_architectures.py", "export_glim_controlled_mapping.py",
            "export_glim_mapping_evidence.py", "export_glim_target_mapping_evidence.py",
            "export_glim_target_replay_evidence.py", "export_glim_preprocess_evidence.py",
            "run_glim_mapping_trial.py", "verify_slam_capture.py")},
        "actualTargetNativeLibrariesSha256": loaded,
        "geometryDependencies": {name: metadata.version(name) for name in ("numpy", "scipy")},
        "roundTripRmsRecomputationMaximumUlps": ROUND_TRIP_RMS_MAXIMUM_ULPS,
        "runs": {name: {**summary(value), "controlledReportSha256": collection["runs"][name]["reportSha256"]}
                 for name, value in target.items()},
        "withinTargetConditionPairs": {}, "hostTargetSameConditionPairs": {},
        "withinHostConditionGeometryPairs": {}, "crossTargetTimingHypothesisPairsNotSameInstants": {},
        "boundaries": {name: False for name in BOUNDARIES},
    }
    for dataset in ("static", "dynamic"):
        for hypothesis in ("header_is_scan_start", "header_is_scan_end"):
            names = [name for name, condition in conditions.items() if condition[:2] == (dataset, hypothesis)]
            for first, second in itertools.combinations(names, 2):
                key = first + "_vs_" + second
                result["withinTargetConditionPairs"][key] = compare_with_geometry(target[first], target[second])
                result["withinHostConditionGeometryPairs"][key] = compare_maps(host[first], host[second])
            for first, second in itertools.product(names, repeat=2):
                result["hostTargetSameConditionPairs"][first + "_vs_" + second] = compare_with_geometry(
                    host[first], target[second])
        for left, right in itertools.product((1, 2, 3), repeat=2):
            first, second = f"{dataset}-start-repeat{left}", f"{dataset}-end-repeat{right}"
            result["crossTargetTimingHypothesisPairsNotSameInstants"][first + "_vs_" + second] = comparison(
                target[first], target[second], False)
    for name, value in target.items():
        for filename, digest in value["report"]["filesSha256"].items():
            require_hash(args.results / name / filename, digest)
        if inventory_map(args.results / name / "map") != value["report"]["mapInventory"]:
            raise ValueError("target_map_changed_during_geometry_export")
    for name, value in host.items():
        for filename, digest in value["report"]["filesSha256"].items():
            require_hash(args.host_results / name / filename, digest)
        if inventory_map(args.host_results / name / "map") != value["report"]["mapInventory"]:
            raise ValueError("host_map_changed_during_geometry_export")
    if verify(capture, args.data_root) != integrity:
        raise ValueError("capture_integrity_changed_during_geometry_export")
    result["allOriginalCapturesAndCompleteHostAndTargetOutputsUnchangedAtExport"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "parent-bundle", "core-bundle", "results", "host-results", "data-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("existing_controlled_target_evidence_is_preserved")
    result = export(args)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"result": "controlled_target_map_and_all_point_geometry_recomputed"}))


if __name__ == "__main__":
    main()
