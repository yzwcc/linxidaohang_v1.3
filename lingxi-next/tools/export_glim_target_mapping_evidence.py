"""Recompute ARM64 saved-map round trips and export only whitelisted aggregates."""
import argparse
import json
from pathlib import Path

import numpy as np

from build_slam_cpu_trial import PINS
from export_glim_mapping_evidence import check_reload_log
from export_glim_target_replay_evidence import COUNTS, recompute
from glim_target_mapping import BOUNDARIES, PURPOSE, validate_mapping_counts
from glim_target_replay import verify_replay_bundle
from run_glim_mapping_trial import compare_points, inventory_map
from run_glim_target_replays import RUNS
from slam_target_probe import sha256, verify_bundle
from verify_slam_capture import verify


ROOT = Path(__file__).resolve().parents[1]
MAPPING_FIELDS = (
    "finalizedFramesFedOnce", "submaps", "points", "savedFrames", "minimumLoopTravel", "submapTravel",
)
INVENTORY_FIELDS = (
    "num_submaps", "num_all_frames", "num_matching_cost_factors",
    "fileCount", "totalBytes", "artifactInventorySha256",
)
ROUND_TRIP_FIELDS = (
    "pointCount", "maximumPointDisplacementRawUnits", "rmsPointDisplacementRawUnits",
    "predeclaredToleranceRawUnits", "samePointOrderCompared", "geometricAccuracyValidated",
)
CHECKS = (
    "globalMappingExecutedOnArm64", "nonemptyMapSavedOnArm64",
    "allFinalizedFramesFedExactlyOnce", "freshArm64ProcessReloadPassed",
    "finiteSameCountPointRoundTripPassed", "mapUnchangedDuringReload",
)
TIMING_FIELDS = (
    "preprocessMillisecondsP50P95P99", "odometryMillisecondsP50P95P99",
    "combinedMillisecondsMaximum", "combinedMillisecondsP50P95P99",
    "processingWallSeconds", "processCpuSeconds", "peakResidentMiB",
)
MOTION_FIELDS = (
    "accumulatedPositionStepsRawUnits", "endToStartDistanceRawUnits",
    "maximumConsecutivePositionStepRawUnits",
)


def public_run(mapped, aggregate):
    return {
        "dataset": mapped["dataset"], "timingHypothesis": mapped["timingHypothesis"],
        "target": {name: mapped["target"][name] for name in ("machine", "python", "numpy")},
        "mapping": {name: mapped["mapping"][name] for name in MAPPING_FIELDS},
        "mapInventory": {name: mapped["mapInventory"][name] for name in INVENTORY_FIELDS},
        "roundTrip": {name: mapped["roundTrip"][name] for name in ROUND_TRIP_FIELDS},
        "scanAccounting": {name: aggregate["counts"][name] for name in COUNTS},
        "timingsWithMappingCallbacksNotRealtimeAcceptance": {
            name: aggregate["timingsNotRealtimeAcceptance"][name] for name in TIMING_FIELDS
        },
        "estimatedMotionNotGroundTruth": {
            name: aggregate["estimatedMotionNotGroundTruth"][name] for name in MOTION_FIELDS
        },
        "totalWallSecondsIncludingReload": mapped["totalWallSecondsIncludingReload"],
        "checks": {name: mapped["checks"][name] for name in CHECKS},
        "boundaries": {name: mapped["boundaries"][name] for name in BOUNDARIES},
        "reloadedInDifferentArm64Process": True,
    }


def verify_mapping_round_trip(mapped, base, reload, local):
    validate_mapping_counts(mapped["mapping"], base["estimatedPoses"])
    inventory = inventory_map(local / "map")
    comparison = compare_points(local / "private-saved-points.f64",
                                local / "private-reloaded-points.f64", mapped["mapping"]["points"])
    if (mapped["mapInventory"] != inventory or reload["mapInventory"] != inventory
            or mapped["roundTrip"] != comparison or reload["comparison"] != comparison
            or base["mapping"] != mapped["mapping"]
            or inventory["num_all_frames"] != base["estimatedPoses"]
            or inventory["num_submaps"] != mapped["mapping"]["submaps"]
            or any(reload[name] != inventory["num_submaps"]
                   for name in ("loadedSubmaps", "loadedGraphFactors", "loadedGraphValues"))
            or type(mapped["replayPid"]) is not int or mapped["replayPid"] <= 0
            or type(reload["pid"]) is not int or reload["pid"] <= 0 or mapped["replayPid"] == reload["pid"]
            or reload["machine"] != "aarch64"
            or reload["result"] != "fresh_process_arm64_map_round_trip_passed_not_relocalization"
            or reload["boundaries"] != {name: False for name in BOUNDARIES}
            or type(mapped["totalWallSecondsIncludingReload"]) not in (float, int)
            or not np.isfinite(mapped["totalWallSecondsIncludingReload"])
            or mapped["totalWallSecondsIncludingReload"] <= 0):
        raise ValueError("recomputed_independent_arm64_map_round_trip_mismatch")


def export(bundle, results, core_bundle, adapter_build, odometry_bundle, data_root):
    manifest = verify_replay_bundle(bundle, PURPOSE)
    core = verify_bundle(core_bundle)
    odometry = verify_replay_bundle(odometry_bundle)
    build_path = adapter_build / "build-report.json"
    build = json.loads(build_path.read_text())
    collection_path = results / "collection-report.json"
    collection = json.loads(collection_path.read_text())
    manifest_hash = sha256(bundle / "replay-manifest.json")
    if (collection["result"] != "four_actual_arm64_recorded_mapping_trials_completed_not_quality_acceptance"
            or collection["purpose"] != "actual_arm64_recorded_glim_mapping_collection"
            or collection["replayManifestSha256"] != manifest_hash
            or collection["coordinatorSourceSha256"] != sha256(ROOT / "tools/run_glim_target_replays.py")
            or set(collection["runs"]) != {entry[0] for entry in RUNS}
            or core["sourceCommits"] != PINS or build["sourceCommits"] != PINS
            or sha256(core_bundle / "bundle-manifest.json") != manifest["coreBundleManifestSha256"]
            or core["crossBuildReportSha256"] != build["crossCoreReportSha256"]
            or sha256(odometry_bundle / "replay-manifest.json") != manifest["odometryReplayManifestSha256"]
            or odometry["coreBundleManifestSha256"] != manifest["coreBundleManifestSha256"]
            or sha256(build_path) != manifest["adapterBuildReportSha256"]
            or build["result"] != "arm64_mapping_adapter_cross_built_not_executed"
            or build["artifact"]["machine"] != "AArch64"
            or sha256(adapter_build / "build/liblingxi_glim_mapping.so") != build["artifact"]["sha256"]
            or sha256(bundle / "lib/liblingxi_glim_mapping.so") != build["artifact"]["sha256"]
            or build["artifact"]["sha256"] != manifest["mappingAdapterSha256"]
            or sha256(odometry_bundle / "lib/liblingxi_glim_offline.so") != manifest["odometryAdapterSha256"]
            or sha256(bundle / "lib/liblingxi_glim_offline.so") != manifest["odometryAdapterSha256"]):
        raise ValueError("complete_arm64_mapping_build_and_collection_provenance_required")
    for name in ("mapping.cpp", "CMakeLists.txt"):
        if (sha256(adapter_build / "source" / name) != build["sourceSha256"][name]
                or sha256(ROOT / "tools/glim-mapping" / name) != build["sourceSha256"][name]):
            raise ValueError("original_mapping_native_source_changed")
    for name in ("glim_target_mapping.py", "glim_target_replay.py", "run_glim_mapping_trial.py"):
        if sha256(ROOT / "tools" / name) != manifest["files"]["tools/" + name]["sha256"]:
            raise ValueError("actual_target_mapping_python_source_changed")
    capture_path = bundle / "capture-manifest.json"
    if sha256(capture_path) != manifest["captureManifestSha256"]:
        raise ValueError("target_mapping_capture_provenance_mismatch")
    capture = json.loads(capture_path.read_text())
    integrity = verify(capture, data_root)
    evidence = {
        "schemaVersion": 1, "observedDate": "2026-10-28",
        "purpose": "actual_arm64_recorded_map_persistence_not_geometry_relocalization_or_navigation",
        "result": collection["result"], "sourceCommits": PINS,
        "captureManifestSha256": manifest["captureManifestSha256"],
        "replayManifestSha256": manifest_hash, "odometryReplayManifestSha256": manifest["odometryReplayManifestSha256"],
        "privateCollectionReportSha256": sha256(collection_path),
        "mappingBuildReportSha256": sha256(build_path),
        "mappingAdapterSha256": manifest["mappingAdapterSha256"],
        "odometryAdapterSha256": manifest["odometryAdapterSha256"],
        "nativeSourceSha256": {name: build["sourceSha256"][name] for name in ("mapping.cpp", "CMakeLists.txt")},
        "actualTargetSourcesSha256": {
            name: manifest["files"]["tools/" + name]["sha256"] for name in (
                "glim_target_mapping.py", "glim_target_replay.py", "run_glim_mapping_trial.py",
            )
        },
        "coordinatorSourceSha256": collection["coordinatorSourceSha256"],
        "toolSourcesAtExport": {
            name: sha256(ROOT / "tools" / name) for name in (
                "build_glim_target_replay.py", "export_glim_target_mapping_evidence.py",
            )
        },
        "runs": {}, "originalCapturesUnchangedAtExport": True,
        "boundaries": {name: False for name in BOUNDARIES},
    }
    for label, dataset, hypothesis in RUNS:
        local = results / label
        entry = collection["runs"][label]
        base = json.loads((local / "report.json").read_text())
        mapped = json.loads((local / "mapping-report.json").read_text())
        reload = json.loads((local / "reload-report.json").read_text())
        if (entry["exitCode"] != 0 or entry["localReportSha256"] != sha256(local / "report.json")
                or entry["localMappingReportSha256"] != sha256(local / "mapping-report.json")
                or base["result"] != "all_recorded_scans_accounted_for_on_arm64_not_quality_acceptance"
                or (base["dataset"], base["timingHypothesis"]) != (dataset, hypothesis)
                or base["runnerSha256"] != manifest["files"]["tools/glim_target_replay.py"]["sha256"]
                or base["mappingRunnerSha256"] != manifest["files"]["tools/glim_target_mapping.py"]["sha256"]
                or base["target"]["machine"] != "aarch64" or base["sourceCommits"] != PINS
                or base["replayManifestSha256"] != manifest_hash
                or base["captureIntegrity"] != integrity or base["captureIntegrityAfterReplay"] != integrity
                or base["boundaries"]["globalMappingExecuted"] is not True
                or any(value is not False for key, value in base["boundaries"].items()
                       if key != "globalMappingExecuted")
                or base["privateTrajectorySha256"] != sha256(local / "private-trajectory.npz")
                or base["privateNativeLogSha256"] != sha256(local / "private-native.log")
                or mapped["purpose"] != "actual_arm64_recorded_global_mapping_and_persistence_not_navigation"
                or mapped["result"] != "arm64_nonempty_map_saved_and_fresh_process_reloaded_not_quality_acceptance"
                or (mapped["dataset"], mapped["timingHypothesis"]) != (dataset, hypothesis)
                or mapped["runnerSha256"] != base["mappingRunnerSha256"]
                or reload["runnerSha256"] != mapped["runnerSha256"]
                or mapped["target"] != base["target"] or mapped["sourceCommits"] != PINS
                or mapped["replayManifestSha256"] != manifest_hash
                or mapped["mappingBuildReportSha256"] != manifest["adapterBuildReportSha256"]
                or mapped["checks"] != {name: True for name in CHECKS}
                or mapped["boundaries"] != {name: False for name in BOUNDARIES}):
            raise ValueError("complete_actual_arm64_mapping_trial_contract_required")
        for field, name in (
            ("baseReplayReportSha256", "report.json"), ("reloadReportSha256", "reload-report.json"),
            ("privateSavedPointsSha256", "private-saved-points.f64"),
            ("privateReloadedPointsSha256", "private-reloaded-points.f64"),
            ("privateReloadLogSha256", "private-reload.log"),
        ):
            if mapped[field] != sha256(local / name):
                raise ValueError("target_mapping_private_artifact_changed")
        if reload["privateReloadedPointsSha256"] != mapped["privateReloadedPointsSha256"]:
            raise ValueError("target_mapping_reload_output_changed")
        check_reload_log(local / "private-native.log")
        check_reload_log(local / "private-reload.log")
        verify_mapping_round_trip(mapped, base, reload, local)
        with np.load(local / "private-trajectory.npz", allow_pickle=False) as arrays:
            aggregate = recompute(base, arrays, capture["datasets"][dataset])
        evidence["runs"][label] = {
            **public_run(mapped, aggregate),
            "privateMappingReportSha256": sha256(local / "mapping-report.json"),
            "baseReplayReportSha256": mapped["baseReplayReportSha256"],
            "reloadReportSha256": mapped["reloadReportSha256"],
            "privateSavedPointsSha256": mapped["privateSavedPointsSha256"],
            "privateReloadedPointsSha256": mapped["privateReloadedPointsSha256"],
            "privateNativeLogSha256": base["privateNativeLogSha256"],
            "privateReloadLogSha256": mapped["privateReloadLogSha256"],
            "privateTrajectorySha256": base["privateTrajectorySha256"],
            "nativeLogsCheckedForErrorsAndRecovery": True,
        }
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "results", "core-bundle", "adapter-build", "odometry-bundle", "data-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("existing_arm64_mapping_evidence_is_preserved")
    evidence = export(args.bundle, args.results, args.core_bundle, args.adapter_build,
                      args.odometry_bundle, args.data_root)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"result": "four_arm64_saved_maps_and_reload_aggregates_recomputed",
                      "runs": len(evidence["runs"])}))


if __name__ == "__main__":
    main()
