"""Verify private recorded reports and export only anonymous async mapping counters."""
import argparse
import json
from pathlib import Path

import run_glim_async_mapping as asynchronous

FILES = {"report.json", "full-reload-report.json", "private-native.log", "private-full-reload.log",
         "private-saved-points.f64", "private-reloaded-points.f64", "private-snapshot-points.f64",
         "private-global-events.jsonl"}
CASES = ("dynamic-start", "dynamic-end", "static-start", "static-end")


def verify_files(directory, digests):
    if set(digests) != FILES:
        raise ValueError("every_async_report_artifact_required")
    for name, digest in digests.items():
        path = directory / name
        if path.is_symlink() or asynchronous.full.sha256(path) != digest:
            raise ValueError("async_report_artifact_hash_mismatch")


def trial(directory, case, frozen):
    report_path = directory / "async-mapping-report.json"
    report = json.loads(report_path.read_text())
    dataset, timing = case.split("-")
    if (report.get("result") != "bounded_async_recorded_map_saved_reloaded_not_target_acceptance"
            or report.get("dataset") != dataset or report.get("timingHypothesis") != "header_is_scan_" + timing
            or report.get("inputRate") != 1.0 or report.get("implementationInputs") != frozen
            or report.get("sourceInputsUnchanged") is not True
            or report.get("boundaries") != dict.fromkeys(asynchronous.full.BOUNDARIES, False)):
        raise ValueError("complete_paced_source_bound_async_trial_required")
    verify_files(directory, report["filesSha256"])
    base = json.loads((directory / "report.json").read_text())
    counts = asynchronous.full.checked_counts(
        asynchronous.np.array([report["mapping"][name] for name in asynchronous.full.COUNTERS]),
        base["processedScans"] - base["initializationScans"])
    worker = asynchronous.checked_status(
        asynchronous.np.array([report["worker"][name] for name in asynchronous.ASYNC_COUNTERS]), counts)
    asynchronous.full.checked_events(directory / "private-global-events.jsonl", counts)
    mapped = asynchronous.full.inventory(directory / "map")
    if (mapped != report["mapInventory"] or mapped["num_all_frames"] != counts["savedFrames"]
            or counts["imuSamples"] != base["imuSamplesInserted"]
            or base["recordedScans"] != base["skippedUnbracketedScans"] + base["processedScans"]):
        raise ValueError("complete_persisted_async_inventory_required")
    for comparison in (report["roundTrip"], report["finalSnapshot"]["comparison"]):
        if (comparison["pointCount"] != counts["points"] or not comparison["samePointOrderCompared"]
                or comparison["predeclaredToleranceRawUnits"] != 1e-4
                or comparison["maximumPointDisplacementRawUnits"] > 1e-4):
            raise ValueError("complete_same_order_async_point_comparison_required")
    warnings = (directory / "private-native.log").read_text().count("imu_rate stamp does not cover the scan duration range")
    return {"case": case, "privateReportSha256": asynchronous.full.sha256(report_path),
            "recordedScans": base["recordedScans"], "skippedUnbracketedScans": base["skippedUnbracketedScans"],
            "initializationScans": base["initializationScans"], **counts, "worker": worker,
            "processingWallSeconds": report["processingWallSeconds"], "peakResidentMiB": report["peakResidentMiB"],
            "worstCombinedFrameMilliseconds": report["worstCombinedFrameMilliseconds"],
            "odometryAndCallbackP95Milliseconds": report["odometryAndCallbackMillisecondsP50P95P99"][1],
            "snapshotRevisionsObservedDuringInput": len(report["snapshotHeadersObservedDuringInput"]),
            "finalSnapshotRevision": report["finalSnapshot"]["revision"],
            "correctedSubmapPoseEvents": report["finalSnapshot"]["correctedSubmapPoseEvents"],
            "maximumSnapshotDifferenceRaw": report["finalSnapshot"]["comparison"]["maximumPointDisplacementRawUnits"],
            "maximumReloadDifferenceRaw": report["roundTrip"]["maximumPointDisplacementRawUnits"],
            "imuRateScanCoverageWarnings": warnings, "sourceInputsUnchanged": True}


def export(build, directories):
    report = json.loads((build / "build-report.json").read_text())
    if report.get("result") != "bounded_async_mapping_built_not_target_acceptance":
        raise ValueError("actual_async_build_required")
    if asynchronous.full.sha256(build / "build" / asynchronous.full.LIBRARY) != report["librarySha256"]:
        raise ValueError("async_library_hash_mismatch")
    frozen = report["inputs"]
    sources = {"tools/glim-async-mapping/" + name: digest for name, digest in frozen["asyncSources"].items()}
    sources["tools/run_glim_async_mapping.py"] = frozen["asyncRunnerSha256"]
    sources.update({"tools/glim-full-mapping/" + name: digest for name, digest in frozen["nativeSources"].items()})
    sources.update({"tools/" + name: digest for name, digest in frozen["runnerSources"].items()})
    for name, digest in sources.items():
        if asynchronous.full.sha256(asynchronous.ROOT / name) != digest:
            raise ValueError("async_implementation_changed_since_replay")
    if set(directories) != set(CASES):
        raise ValueError("all_four_paced_trials_required")
    return {"schemaVersion": 1, "classification": "anonymous_paced_recorded_bounded_async_glim_mapping",
            "date": "2026-10-31", "host": "ordinary-user isolated Linux x86_64 WSL, not target ARM64",
            "scheduling": "one_fifo_mapping_worker_owns_official_local_and_global_algorithms",
            "upstreamGlimCommit": frozen["sourceCommits"]["glim"], "sourceSha256": sources,
            "buildReportSha256": asynchronous.full.sha256(build / "build-report.json"),
            "librarySha256": report["librarySha256"], "roundTripToleranceRaw": 1e-4,
            "trials": [trial(directories[case], case, frozen) for case in CASES],
            "limits": ["some_trials_shared_host_concurrently_not_controlled_performance_comparison",
                       "count_bounded_input_queue_not_a_bound_on_total_map_memory",
                       "background_optimization_delay_remains_visible_in_queue_age",
                       "timestamp_hypotheses_geometry_calibration_and_real_loop_closure_not_validated",
                       "no_live_web_or_target_arm64_integration_in_this_trial"],
            "boundaries": {**dict.fromkeys(asynchronous.full.BOUNDARIES, False), "liveWebIntegrated": False}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    for case in CASES:
        parser.add_argument("--" + case, type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = export(args.build, {case: getattr(args, case.replace("-", "_")) for case in CASES})
    asynchronous.full.save(args.output, value)
    print(json.dumps({"result": "four_paced_trials_and_artifacts_verified", "trials": len(value["trials"])}))


if __name__ == "__main__":
    main()
