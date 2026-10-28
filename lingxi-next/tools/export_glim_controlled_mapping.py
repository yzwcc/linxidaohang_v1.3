"""Recompute complete controlled-map comparisons; publish no points or trajectories."""
import argparse
import itertools
import json
from pathlib import Path

import json5
import numpy as np

from build_slam_cpu_trial import PINS
from compare_glim_architectures import (
    compare_runs, ordered_stamps, pose_array, read_membership, require_hash, saved_lidar_poses,
)
from export_glim_mapping_evidence import check_reload_log
from export_glim_preprocess_evidence import same_bit_rows
from run_glim_controlled_mapping import (
    BOUNDARIES, CONDITIONS, ROOT, SOURCE_FILES, config_hashes, label,
)
from run_glim_mapping_trial import CORE_LIBRARIES, compare_points, inventory_map
from verify_slam_capture import sha256, verify


def verify_control(path, expected_manifest):
    manifest = json.loads((path / "control-manifest.json").read_text())
    baseline, effective = path / "config", path / "control-config"
    before, after = config_hashes(baseline), config_hashes(effective)
    expected_changes = [{"file": "config_preprocess.json", "path": ["preprocess", "num_threads"],
                         "before": 2, "after": 1}]
    if (before != manifest["baselineFilesSha256"] or after != manifest["effectiveFilesSha256"]
            or before["trial-manifest.json"] != expected_manifest
            or manifest["semanticParameterChanges"] != expected_changes
            or manifest["productionConfigurationSelected"] is not False
            or manifest["sensorMappingOdometryAndRangeParametersUnchanged"] is not True
            or set(before) != set(after)
            or {name for name in before if before[name] != after[name]}
            != {"config_preprocess.json", "trial-manifest.json"}):
        raise ValueError("exactly_declared_single_thread_configuration_control_required")
    old = json5.loads((baseline / "config_preprocess.json").read_text(), allow_duplicate_keys=False)
    new = json.loads((effective / "config_preprocess.json").read_text())
    if (type(old["preprocess"]["num_threads"]) is not int or old["preprocess"]["num_threads"] != 2
            or type(new["preprocess"]["num_threads"]) is not int or new["preprocess"]["num_threads"] != 1):
        raise ValueError("explicit_two_to_one_preprocessing_thread_change_required")
    new["preprocess"]["num_threads"] = 2
    if new != old:
        raise ValueError("undeclared_preprocessing_parameter_change")
    for directory in (baseline, effective):
        trial = json.loads((directory / "trial-manifest.json").read_text())
        observed = config_hashes(directory)
        if (trial["files"] != {name: digest for name, digest in observed.items() if name != "trial-manifest.json"}
                or any(trial[name] is not False for name in (
                    "robotCalibrationValidated", "navigationValidated", "deploymentApproved"))):
            raise ValueError("consistent_unapproved_actual_configuration_manifest_required")
    return manifest


def load_run(path, condition, collection, baseline_manifest):
    dataset, hypothesis, _ = condition
    require_hash(path / "controlled-mapping-report.json", collection["reportSha256"])
    report = json.loads((path / "controlled-mapping-report.json").read_text())
    if (collection["exitCode"] != 0
            or report["result"] != "controlled_full_map_saved_and_reloaded_not_geometry_acceptance"
            or (report["dataset"], report["timingHypothesis"]) != (dataset, hypothesis)
            or report["hostMachine"] != "x86_64" or type(report["pid"]) is not int
            or report["pid"] <= 0 or report["boundaries"] != {name: False for name in BOUNDARIES}
            or report["allOriginalCapturesUnchangedBeforeAndAfter"] is not True
            or report["sourceVersions"] != {"numpy": "2.5.4", "rosbags": "0.11.9", "json5": "0.12.1"}
            or set(report["sourceSha256"]) != set(SOURCE_FILES)):
        raise ValueError("complete_controlled_mapping_report_required")
    for name, digest in report["filesSha256"].items():
        require_hash(path / name, digest)
    required = {"report.json", "private-trajectory.npz", "private-native.log", "reload-report.json",
                "private-reload.log", "private-saved-points.f64", "private-reloaded-points.f64"}
    if set(report["filesSha256"]) != required:
        raise ValueError("all_controlled_mapping_artifact_hashes_required")
    for name, digest in report["sourceSha256"].items():
        require_hash(path / "source-snapshot" / name, digest)
        require_hash(ROOT / "tools" / name, digest)
    require_hash(path / "control-manifest.json", report["controlManifestSha256"])
    control = verify_control(path, baseline_manifest)
    base = json.loads((path / "report.json").read_text())
    reload = json.loads((path / "reload-report.json").read_text())
    mapping = report["mapping"]
    if (base["effectiveConfigurationDirectory"] != "control-config"
            or base["baselineConfigurationDirectory"] != "config"
            or base["controlManifestSha256"] != report["controlManifestSha256"]
            or base["privateTrajectorySha256"] != report["filesSha256"]["private-trajectory.npz"]
            or base["privateNativeLogSha256"] != report["filesSha256"]["private-native.log"]
            or base["result"] != "all_scans_accounted_for_not_quality_acceptance"
            or base["captureManifestSha256"] != report["captureManifestSha256"]
            or base["processedScans"] - base["initializationScans"] != base["estimatedPoses"]
            or base["processedScans"] + base["skippedUnbracketedScans"] != base["recordedScans"]
            or mapping["finalizedFramesFedOnce"] != base["estimatedPoses"]
            or mapping["savedFrames"] != base["estimatedPoses"]
            or not 0 <= mapping["submapTravel"] < mapping["minimumLoopTravel"]
            or reload["pid"] == report["pid"] or reload["boundaries"] != report["boundaries"]
            or reload["effectiveConfigFilesSha256"] != control["effectiveFilesSha256"]):
        raise ValueError("complete_once_only_controlled_replay_and_independent_reload_required")
    inventory = inventory_map(path / "map")
    round_trip = compare_points(path / "private-saved-points.f64", path / "private-reloaded-points.f64",
                                mapping["points"])
    if (inventory != report["mapInventory"] or inventory != reload["mapInventory"]
            or inventory["num_submaps"] != mapping["submaps"]
            or inventory["num_all_frames"] != mapping["savedFrames"]
            or round_trip != report["roundTrip"] or round_trip != reload["comparison"]):
        raise ValueError("controlled_map_and_round_trip_recomputation_mismatch")
    for name in ("private-native.log", "private-reload.log"):
        check_reload_log(path / name)
    with np.load(path / "private-trajectory.npz", allow_pickle=False) as archive:
        if set(archive) != {"poses", "header_stamps_ns", "algorithm_stamps_relative_seconds",
                           "preprocess_odometry_pointcount_marginalized"}:
            raise ValueError("complete_controlled_trajectory_arrays_required")
        arrays = {name: archive[name].copy() for name in archive}
    stamps, poses, algorithm, measures = (arrays[name] for name in (
        "header_stamps_ns", "poses", "algorithm_stamps_relative_seconds",
        "preprocess_odometry_pointcount_marginalized"))
    ordered_stamps(stamps)
    pose_array(poses)
    if (len(stamps) != base["estimatedPoses"] or len(poses) != len(stamps)
            or algorithm.shape != stamps.shape or not np.isfinite(algorithm).all()
            or np.any(np.diff(algorithm) <= 0) or measures.shape != (base["processedScans"], 4)
            or not np.isfinite(measures).all() or np.any(measures < 0)
            or np.any(measures[:, 2:] != np.floor(measures[:, 2:]))):
        raise ValueError("finite_complete_controlled_trajectory_and_measurements_required")
    points = np.fromfile(path / "private-saved-points.f64", dtype="<f8")
    if points.shape != (mapping["points"] * 3,) or not np.isfinite(points).all():
        raise ValueError("all_finite_saved_controlled_map_points_required")
    return {
        "stamps": stamps, "algorithm": algorithm, "online": poses,
        "saved": saved_lidar_poses(path / "map/traj_lidar.txt", algorithm),
        "members": read_membership(path / "map", mapping["submaps"], algorithm),
        "scanCounters": measures[:, 2:], "points": points.reshape(-1, 3),
        "report": report, "base": base, "control": control,
    }


def comparison(first, second, same_hypothesis):
    result = compare_runs(first, second, same_hypothesis)
    result["fullOnlinePoseArraysBitIdentical"] = same_bit_rows(
        first["online"].reshape(-1, 16), second["online"].reshape(-1, 16))
    result["completeSavedMapPoints"] = {
        "referencePointCount": len(first["points"]), "candidatePointCount": len(second["points"]),
        "samePointCount": len(first["points"]) == len(second["points"]),
        "orderedCoordinateBitsIdentical": same_bit_rows(first["points"], second["points"]),
        "coordinateBitMultisetsIdentical": same_bit_rows(
            np.column_stack((first["points"], np.ones(len(first["points"])))),
            np.column_stack((second["points"], np.ones(len(second["points"])))), ignore_order=True),
        "noNearestNeighborGeometryOrTruthAccuracyClaimed": True,
    }
    return result


def run_summary(value):
    report, base = value["report"], value["base"]
    positions = value["online"][:, :3, 3]
    return {
        "dataset": report["dataset"], "timingHypothesis": report["timingHypothesis"],
        "controlManifestSha256": report["controlManifestSha256"],
        "mapping": {name: report["mapping"][name] for name in (
            "finalizedFramesFedOnce", "submaps", "points", "savedFrames", "minimumLoopTravel", "submapTravel")},
        "mapInventory": report["mapInventory"], "roundTrip": report["roundTrip"],
        "filesSha256": report["filesSha256"], "sourceVersions": report["sourceVersions"],
        "scanAccounting": {name: base[name] for name in (
            "recordedScans", "processedScans", "skippedUnbracketedScans", "initializationScans", "estimatedPoses")},
        "observedOfflineCosts": {name: base[name] for name in (
            "processingWallSeconds", "processCpuSeconds", "peakResidentMiB",
            "preprocessMillisecondsP50P95P99", "odometryMillisecondsP50P95P99", "combinedMillisecondsMaximum")},
        "totalWallSecondsIncludingReload": report["totalWallSecondsIncludingReload"],
        "estimatedMotionNotTruth": {
            "accumulated3dPositionStepsRawUnits": float(np.linalg.norm(np.diff(positions, axis=0), axis=1).sum()),
            "accumulatedXYPositionStepsRawUnits": float(np.linalg.norm(np.diff(positions[:, :2], axis=0), axis=1).sum()),
            "endToStartDistanceRawUnits": float(np.linalg.norm(positions[-1] - positions[0])),
            "mapAxisSpanRawUnits": np.ptp(value["points"], axis=0).tolist(),
        },
    }


def verify_builds(args):
    core = json.loads((args.work / "build-report.json").read_text())
    mapped = json.loads((args.mapping_adapter / "build-report.json").read_text())
    replayed = json.loads((args.adapter / "build-report.json").read_text())
    if (core["result"] != "core_libraries_built_not_target_acceptance"
            or mapped["result"] != "extension_built_not_mapping_acceptance"
            or replayed["result"] != "adapter_built_not_algorithm_acceptance"
            or any(value["sourceCommits"] != PINS for value in (core, mapped, replayed))
            or mapped["coreBuildReportSha256"] != sha256(args.work / "build-report.json")
            or replayed["coreBuildReportSha256"] != sha256(args.work / "build-report.json")
            or mapped["coreLibrariesSha256"] != {
                name: sha256(args.work / "prefix/lib" / name) for name in CORE_LIBRARIES}):
        raise ValueError("same_verified_native_mapping_builds_required")
    for name, digest in mapped["sources"].items():
        require_hash(ROOT / "tools/glim-mapping" / name, digest)
        require_hash(args.mapping_adapter / name, digest)
    for name, key in (("offline.cpp", "adapterSourceSha256"), ("CMakeLists.txt", "adapterCmakeSha256")):
        require_hash(ROOT / "tools/glim-replay" / name, replayed[key])
    require_hash(args.adapter / "build/liblingxi_glim_offline.so", replayed["librarySha256"])
    require_hash(args.mapping_adapter / "build/liblingxi_glim_mapping.so", mapped["librarySha256"])
    return mapped, replayed


def export(args):
    mapped, replayed = verify_builds(args)
    collection = json.loads((args.results / "collection-report.json").read_text())
    conditions = {label(*condition): condition for condition in CONDITIONS}
    if (collection["result"] != "twelve_controlled_full_mapping_processes_completed_not_geometry_acceptance"
            or collection["runnerSourceSha256"] != sha256(ROOT / "tools/run_glim_controlled_mapping.py")
            or set(collection["runs"]) != set(conditions)):
        raise ValueError("all_predeclared_controlled_mapping_processes_required")
    previous = json.loads((ROOT / "docs/slam-preprocess-repeatability-evidence.json").read_text())
    baseline_manifest = previous["unchangedConfigurationManifestSha256"]
    capture_path = ROOT / "docs/slam-capture-evidence.json"
    capture = json.loads(capture_path.read_text())
    verify(capture, args.data_root)
    values = {name: load_run(args.results / name, condition, collection["runs"][name], baseline_manifest)
              for name, condition in conditions.items()}
    if len({value["report"]["pid"] for value in values.values()}) != 12:
        raise ValueError("twelve_fresh_full_mapping_processes_required")
    effective_manifests = {value["control"]["effectiveFilesSha256"]["trial-manifest.json"] for value in values.values()}
    if len(effective_manifests) != 1:
        raise ValueError("one_identical_declared_control_configuration_required")
    expected_libraries = {path.name: sha256(path) for path in (args.work / "prefix/lib").glob("*.so*")
                          if path.is_file()}
    expected_libraries.update({"liblingxi_glim_offline.so": replayed["librarySha256"],
                               "liblingxi_glim_mapping.so": mapped["librarySha256"]})
    first_loaded = next(iter(values.values()))["report"]["actualLoadedLibrariesSha256"]
    for value in values.values():
        report = value["report"]
        if (report["captureManifestSha256"] != sha256(capture_path)
                or value["base"]["recordedScans"] != capture["datasets"][report["dataset"]]["topicCounts"]["cloud"]
                or report["mappingBuildReportSha256"] != sha256(args.mapping_adapter / "build-report.json")
                or not report["actualLoadedLibrariesSha256"]
                or report["actualLoadedLibrariesSha256"] != first_loaded
                or any(expected_libraries.get(name) != digest
                       for name, digest in report["actualLoadedLibrariesSha256"].items())):
            raise ValueError("same_captured_input_and_actual_native_libraries_required")
    result = {
        "schemaVersion": 1, "observedDate": "2026-10-28",
        "purpose": "single_preprocessing_thread_full_mapping_repetition_not_geometry_acceptance",
        "freshProcessCount": 12, "repeatsPerCondition": 3,
        "sourceCommits": PINS,
        "runnerSourceSha256": collection["runnerSourceSha256"], "exporterSourceSha256": sha256(__file__),
        "collectionReportSha256": sha256(args.results / "collection-report.json"),
        "captureManifestSha256": sha256(capture_path),
        "baselineConfigurationManifestSha256": baseline_manifest,
        "effectiveConfigurationManifestSha256": next(iter(effective_manifests)),
        "semanticParameterChanges": next(iter(values.values()))["control"]["semanticParameterChanges"],
        "nativeMappingBuildReportSha256": sha256(args.mapping_adapter / "build-report.json"),
        "nativeOdometryBuildReportSha256": sha256(args.adapter / "build-report.json"),
        "nativeMappingSourcesSha256": mapped["sources"],
        "sourceSha256": next(iter(values.values()))["report"]["sourceSha256"],
        "actualLoadedLibrariesSha256": next(iter(values.values()))["report"]["actualLoadedLibrariesSha256"],
        "runs": {name: {**run_summary(value), "controlledReportSha256": collection["runs"][name]["reportSha256"]}
                 for name, value in values.items()},
        "withinConditionPairs": {}, "crossTimingHypothesisPairsNotSameScanInstants": {},
        "boundaries": {name: False for name in BOUNDARIES},
    }
    for dataset in ("static", "dynamic"):
        for hypothesis in ("header_is_scan_start", "header_is_scan_end"):
            labels = [name for name, condition in conditions.items() if condition[:2] == (dataset, hypothesis)]
            for first, second in itertools.combinations(labels, 2):
                result["withinConditionPairs"][first + "_vs_" + second] = comparison(
                    values[first], values[second], True)
        for left, right in itertools.product((1, 2, 3), repeat=2):
            first, second = f"{dataset}-start-repeat{left}", f"{dataset}-end-repeat{right}"
            result["crossTimingHypothesisPairsNotSameScanInstants"][first + "_vs_" + second] = comparison(
                values[first], values[second], False)
    for name, value in values.items():
        for filename, digest in value["report"]["filesSha256"].items():
            require_hash(args.results / name / filename, digest)
        if inventory_map(args.results / name / "map") != value["report"]["mapInventory"]:
            raise ValueError("controlled_map_changed_during_export")
    verify(capture, args.data_root)
    result["allOriginalCapturesAndCompleteOutputsUnchangedAtExport"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("results", "work", "adapter", "mapping-adapter", "data-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("existing_controlled_map_evidence_is_preserved")
    evidence = export(args)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"result": "complete_controlled_mapping_evidence_recomputed"}))


if __name__ == "__main__":
    main()
