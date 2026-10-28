"""Recompute and whitelist map-persistence aggregates, excluding all map coordinates."""
import argparse
import json
from pathlib import Path

from run_glim_mapping_trial import CORE_LIBRARIES, ROOT, SOURCE, SOURCE_FILES, compare_points, inventory_map
from verify_slam_capture import sha256


RUNS = {
    "staticStart": ("static", "header_is_scan_start", "glim-map-static-start-20261028-a"),
    "staticEnd": ("static", "header_is_scan_end", "glim-map-static-end-20261028-a"),
    "dynamicStart": ("dynamic", "header_is_scan_start", "glim-map-dynamic-start-20261028-a"),
    "dynamicEnd": ("dynamic", "header_is_scan_end", "glim-map-dynamic-end-20261028-a"),
}
REPORT_FIELDS = (
    "dataset", "timingHypothesis", "result",
    "mappingBuildReportSha256", "baseReplayReportSha256", "reloadReportSha256",
    "privateSavedPointsSha256", "privateReloadedPointsSha256", "privateReloadLogSha256",
    "totalWallSecondsIncludingReload",
)
NESTED_FIELDS = {
    "mapping": ("finalizedFramesFedOnce", "submaps", "points", "savedFrames",
                "minimumLoopTravel", "submapTravel"),
    "mapInventory": ("num_submaps", "num_all_frames", "num_matching_cost_factors",
                     "fileCount", "totalBytes", "artifactInventorySha256"),
    "roundTrip": ("pointCount", "maximumPointDisplacementRawUnits", "rmsPointDisplacementRawUnits",
                  "predeclaredToleranceRawUnits", "samePointOrderCompared", "geometricAccuracyValidated"),
    "replayHost": ("system", "machine", "python"),
    "sourceCommits": ("glim", "gtsam", "gtsam_points"),
    "checks": ("globalMappingExecuted", "nonemptyMapSaved", "allFinalizedFramesFedExactlyOnce",
               "freshProcessReloadPassed", "finiteSameCountPointRoundTripPassed", "mapUnchangedDuringReload"),
    "boundaries": ("geometricAccuracyValidated", "robotCalibrationValidated", "timingHypothesisValidated",
                   "loopClosureValidated", "relocalizationValidated", "targetArm64RuntimeValidated",
                   "navigationValidated", "deploymentApproved", "rawDataPublished"),
    "sourceSha256": SOURCE_FILES,
}


def aggregate_report(report):
    result = {name: report[name] for name in REPORT_FIELDS}
    result.update({
        group: {name: report[group][name] for name in fields}
        for group, fields in NESTED_FIELDS.items()
    })
    return result


def check_reload_log(path):
    text = path.read_text()
    if any(token in text.lower() for token in (
        "[error]", "[critical]", "failed to deserialize", "recovering factor graph",
        "corrupted", "unsupported matching cost", "invalid factors",
    )):
        raise ValueError("reload_error_or_recovery_is_not_a_clean_round_trip")


def export(cache):
    build_path = cache / "glim-mapping-build-20261028-a/build-report.json"
    build = json.loads(build_path.read_text())
    core = cache / "slam-cpu-20261028-b"
    if (build["result"] != "extension_built_not_mapping_acceptance"
            or build["librarySha256"] != sha256(build_path.parent / "build/liblingxi_glim_mapping.so")
            or build["coreBuildReportSha256"] != sha256(core / "build-report.json")
            or build["coreLibrariesSha256"] != {
                name: sha256(core / "prefix/lib" / name) for name in CORE_LIBRARIES
            }):
        raise ValueError("mapping_build_provenance_mismatch")
    for name in ("mapping.cpp", "CMakeLists.txt"):
        if build["sources"][name] != sha256(SOURCE / name) or build["sources"][name] != sha256(build_path.parent / name):
            raise ValueError("mapping_native_source_provenance_mismatch")
    result = {
        "schemaVersion": 1, "observedDate": "2026-10-28",
        "purpose": "offline_map_persistence_aggregate_not_geometry_relocalization_or_navigation_acceptance",
        "adapterBuild": build, "runs": {},
        "boundaries": {name: False for name in (
            "geometricAccuracyValidated", "robotCalibrationValidated", "timingHypothesisValidated",
            "loopClosureValidated", "relocalizationValidated", "targetArm64RuntimeValidated",
            "navigationValidated", "deploymentApproved", "rawDataPublished",
        )},
    }
    capture_path = ROOT / "docs/slam-capture-evidence.json"
    capture = json.loads(capture_path.read_text())
    for label, (dataset, hypothesis, folder) in RUNS.items():
        path = cache / folder
        report = json.loads((path / "mapping-report.json").read_text())
        base = json.loads((path / "report.json").read_text())
        reload = json.loads((path / "reload-report.json").read_text())
        check_reload_log(path / "private-reload.log")
        check_reload_log(path / "private-native.log")
        if (report["result"] != "nonempty_map_saved_and_fresh_process_reloaded_not_quality_acceptance"
                or report["dataset"] != dataset or report["timingHypothesis"] != hypothesis
                or not report["checks"] or any(value is not True for value in report["checks"].values())
                or report["boundaries"] != result["boundaries"]
                or report["mappingBuildReportSha256"] != sha256(build_path)
                or base["captureManifestSha256"] != sha256(capture_path)
                or base["recordedScans"] != capture["datasets"][dataset]["topicCounts"]["cloud"]
                or base["runnerSha256"] != sha256(path / "runner-source.py")
                or base["privateTrajectorySha256"] != sha256(path / "private-trajectory.npz")
                or base["privateNativeLogSha256"] != sha256(path / "private-native.log")
                or report["baseReplayReportSha256"] != sha256(path / "report.json")
                or report["reloadReportSha256"] != sha256(path / "reload-report.json")
                or report["privateSavedPointsSha256"] != sha256(path / "private-saved-points.f64")
                or report["privateReloadedPointsSha256"] != sha256(path / "private-reloaded-points.f64")
                or report["privateReloadLogSha256"] != sha256(path / "private-reload.log")):
            raise ValueError("completed_mapping_trial_provenance_mismatch")
        if (report["mapInventory"] != inventory_map(path / "map")
                or reload["mapInventory"] != report["mapInventory"]
                or report["roundTrip"] != compare_points(path / "private-saved-points.f64",
                                                        path / "private-reloaded-points.f64",
                                                        report["mapping"]["points"])
                or reload["comparison"] != report["roundTrip"]
                or report["mapping"]["savedFrames"] != base["estimatedPoses"]
                or report["mapping"]["finalizedFramesFedOnce"] != base["estimatedPoses"]
                or report["mapInventory"]["num_all_frames"] != base["estimatedPoses"]
                or report["mapInventory"]["num_submaps"] != report["mapping"]["submaps"]
                or reload["loadedSubmaps"] != report["mapping"]["submaps"]
                or reload["loadedGraphFactors"] != report["mapping"]["submaps"]
                or reload["loadedGraphValues"] != report["mapping"]["submaps"]
                or report["mapping"]["submapTravel"] >= report["mapping"]["minimumLoopTravel"]):
            raise ValueError("mapping_trial_recomputation_mismatch")
        if set(report["sourceSha256"]) != set(SOURCE_FILES):
            raise ValueError("complete_mapping_runner_source_snapshot_required")
        for name in SOURCE_FILES:
            if (report["sourceSha256"][name] != sha256(path / "mapping-source-snapshot" / name)
                    or report["sourceSha256"][name] != sha256(ROOT / "tools" / name)):
                raise ValueError("mapping_runner_source_provenance_mismatch")
        entry = aggregate_report(report)
        entry["reloadLogCheckedForErrorsAndRecovery"] = True
        entry["replayLogCheckedForErrorsAndRecovery"] = True
        entry["localMappingReportSha256"] = sha256(path / "mapping-report.json")
        entry["scanAccounting"] = {
            name: base[name] for name in ("recordedScans", "processedScans", "skippedUnbracketedScans",
                                         "initializationScans", "estimatedPoses")
        }
        result["runs"][label] = entry
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("existing_public_mapping_evidence_is_preserved")
    evidence = export(args.cache_root)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"result": "recomputed_aggregate_only_mapping_evidence", "runs": len(evidence["runs"])}))


if __name__ == "__main__":
    main()
