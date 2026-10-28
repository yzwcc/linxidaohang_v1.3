"""Recompute recorded streaming aggregates, without live or calibration claims."""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path

import numpy as np

from compare_glim_architectures import ordered_stamps, pose_array, pose_differences, require_hash
from export_glim_controlled_mapping import load_run
from export_glim_mapping_evidence import check_reload_log
from glim_target_controlled_mapping import BASELINE_MANIFEST
from run_glim_stream_trial import (
    BOUNDARIES, CONDITIONS, ROOT, SOURCES, label, source_hashes,
)
from lingxi_slam.stream import Limits
from verify_slam_capture import sha256, verify


COUNT_FIELDS = (
    "receivedImu", "receivedClouds", "insertedImu", "processedScans", "initializationScans", "estimatedPoses",
    "skippedBeforeImu", "skippedAfterImuAtEndOfInput", "discardedOnFailure",
    "maximumPendingImu", "maximumPendingClouds", "maximumPendingBytes",
)


def public_stream(state, expected):
    counts = state["counts"]
    limits = asdict(Limits())
    if (state["state"] != "finished" or state["failure"] is not None or state["limits"] != limits
            or set(counts) != set(COUNT_FIELDS)
            or any(type(counts[name]) is not int or counts[name] < 0 for name in COUNT_FIELDS)
            or counts["receivedImu"] != expected["topicCounts"]["imu"]
            or counts["receivedClouds"] != expected["topicCounts"]["cloud"]
            or not 0 < counts["insertedImu"] <= counts["receivedImu"]
            or counts["processedScans"] != counts["initializationScans"] + counts["estimatedPoses"]
            or counts["receivedClouds"] != sum(counts[key] for key in (
                "processedScans", "skippedBeforeImu", "skippedAfterImuAtEndOfInput"))
            or counts["discardedOnFailure"] != 0
            or counts["maximumPendingImu"] > limits["maximum_pending_imu"]
            or counts["maximumPendingClouds"] > limits["maximum_pending_clouds"]
            or counts["maximumPendingBytes"] > limits["maximum_pending_bytes"]
            or any(state[name] != 0 for name in ("pendingImu", "pendingClouds", "pendingCloudBytes"))
            or type(state["maximumObservedScanWaitSeconds"]) not in (float, int)
            or not math.isfinite(state["maximumObservedScanWaitSeconds"])
            or not 0 <= state["maximumObservedScanWaitSeconds"] <= limits["initial_wait_seconds"]):
        raise ValueError("complete_bounded_stream_accounting_required")
    return {
        "counts": {name: counts[name] for name in COUNT_FIELDS},
        "limits": limits, "maximumRecordedArrivalScanWaitSeconds": state["maximumObservedScanWaitSeconds"],
        "allBuffersReleasedAtExplicitEndOfInput": True,
    }


def recompute_comparison(arrays, baseline):
    headers, poses, algorithm, measures = (arrays[name] for name in (
        "header_stamps_ns", "poses", "algorithm_stamps_relative_seconds",
        "preprocess_odometry_pointcount_marginalized"))
    common = np.array_equal(headers, baseline["stamps"])
    value = {
        "sameEstimatedHeaderDomain": common,
        "sameAlgorithmTimestamps": common and np.array_equal(algorithm, baseline["algorithm"]),
        "sameScanCounters": (measures[:, 2:].shape == baseline["scanCounters"].shape
                            and np.array_equal(measures[:, 2:], baseline["scanCounters"])),
    }
    if common:
        value["onlinePoseDifference"] = pose_differences(baseline["online"], poses)
    return value


def export(args):
    collection_path = args.results / "collection-report.json"
    collection = json.loads(collection_path.read_text())
    current_sources = source_hashes()
    if (collection["result"] != "all_recorded_arrival_streams_completed_not_live_acceptance"
            or set(collection["runs"]) != {label(*item) for item in CONDITIONS}
            or collection["runnerSourceSha256"] != current_sources["tools/run_glim_stream_trial.py"]):
        raise ValueError("all_four_completed_arrival_order_streams_required")
    frozen_path = ROOT / "docs/slam-controlled-mapping-evidence.json"
    capture_path = ROOT / "docs/slam-capture-evidence.json"
    frozen, capture = (json.loads(path.read_text()) for path in (frozen_path, capture_path))
    integrity = verify(capture, args.data_root)
    evidence = {
        "schemaVersion": 1, "observedDate": "2026-10-28",
        "purpose": "bounded_recorded_arrival_order_input_not_live_mapping_or_calibration",
        "baselineEvidenceSha256": sha256(frozen_path), "captureManifestSha256": sha256(capture_path),
        "collectionReportSha256": sha256(collection_path), "sourceSha256": current_sources,
        "exporterSourceSha256": sha256(__file__), "sourceCommits": frozen["sourceCommits"],
        "conditionCount": 4, "runs": {}, "boundaries": {name: False for name in BOUNDARIES},
    }
    for dataset, hypothesis in CONDITIONS:
        name = label(dataset, hypothesis)
        path = args.results / name
        item = collection["runs"][name]
        require_hash(path / "report.json", item["reportSha256"])
        report = json.loads((path / "report.json").read_text())
        baseline_name = name + "-repeat1"
        baseline = load_run(
            args.baselines / baseline_name, (dataset, hypothesis, 1),
            {"exitCode": 0, "reportSha256": frozen["runs"][baseline_name]["controlledReportSha256"]},
            BASELINE_MANIFEST)
        if (item["exitCode"] != 0 or report["result"] != "complete_recorded_arrival_stream_not_live_mapping_acceptance"
                or (report["dataset"], report["timingHypothesis"]) != (dataset, hypothesis)
                or report["sourceSha256"] != current_sources or report["sourceCommits"] != frozen["sourceCommits"]
                or report["host"]["machine"] != "x86_64" or report["host"]["numpy"] != "2.5.4"
                or report["boundaries"] != evidence["boundaries"]
                or report["baselineEvidenceSha256"] != evidence["baselineEvidenceSha256"]
                or report["captureManifestSha256"] != evidence["captureManifestSha256"]
                or report["baselineReportSha256"] != frozen["runs"][baseline_name]["controlledReportSha256"]
                or report["observedMessageCounts"] != {
                    key: capture["datasets"][dataset]["topicCounts"][key] for key in ("cloud", "imu")}
                or report["arrivalClock"]
                != "relative_recorded_rosbag_receive_timestamps_not_wall_clock_or_paced_replay"
                or type(report["totalWallSecondsNotPacedRealtimeAcceptance"]) not in (int, float)
                or not math.isfinite(report["totalWallSecondsNotPacedRealtimeAcceptance"])
                or report["totalWallSecondsNotPacedRealtimeAcceptance"] <= 0):
            raise ValueError("complete_unapproved_recorded_stream_report_required")
        for source in SOURCES:
            require_hash(path / "source-snapshot" / source, current_sources[source])
        expected_libraries = baseline["report"]["actualLoadedLibrariesSha256"]
        loaded = report["actualLoadedLibrariesSha256"]
        if (not {"libglim.so", "libodometry_estimation_cpu.so", "liblingxi_glim_offline.so"}.issubset(loaded)
                or any(expected_libraries.get(key) != value for key, value in loaded.items())):
            raise ValueError("same_actual_stream_native_libraries_required")
        for filename, key in (("private-trajectory.npz", "privateTrajectorySha256"),
                              ("private-native.log", "privateNativeLogSha256")):
            require_hash(path / filename, report[key])
        check_reload_log(path / "private-native.log")
        summary = public_stream(report["stream"], capture["datasets"][dataset])
        with np.load(path / "private-trajectory.npz", allow_pickle=False) as arrays:
            if set(arrays) != {"poses", "header_stamps_ns", "algorithm_stamps_relative_seconds",
                               "preprocess_odometry_pointcount_marginalized"}:
                raise ValueError("all_stream_arrays_required")
            headers, poses, algorithm, measures = (arrays[key] for key in (
                "header_stamps_ns", "poses", "algorithm_stamps_relative_seconds",
                "preprocess_odometry_pointcount_marginalized"))
            ordered_stamps(headers)
            pose_array(poses)
            if (len(poses) != len(headers) or len(headers) != summary["counts"]["estimatedPoses"]
                    or algorithm.shape != headers.shape or not np.isfinite(algorithm).all()
                    or np.any(np.diff(algorithm) <= 0)
                    or measures.shape != (summary["counts"]["processedScans"], 4)
                    or not np.isfinite(measures).all() or np.any(measures < 0)
                    or np.any(measures[:, 2:] != np.floor(measures[:, 2:]))):
                raise ValueError("finite_complete_stream_trajectory_required")
            comparison = recompute_comparison(arrays, baseline)
            if comparison != report["baselineComparisonNotTruthAccuracy"]:
                raise ValueError("recomputed_stream_baseline_comparison_mismatch")
            timings = {
                "preprocessMillisecondsP50P95P99": np.percentile(measures[:, 0], [50, 95, 99]).tolist(),
                "odometryMillisecondsP50P95P99": np.percentile(measures[:, 1], [50, 95, 99]).tolist(),
            }
        evidence["runs"][name] = {
            "dataset": dataset, "timingHypothesis": hypothesis, **summary,
            "baselineComparisonNotTruthAccuracy": comparison,
            "actualLoadedLibrariesSha256": loaded, "observedOfflineNativeTimingsNotLivePerformance": timings,
            "totalWallSecondsNotPacedRealtimeAcceptance": report["totalWallSecondsNotPacedRealtimeAcceptance"],
            "reportSha256": item["reportSha256"], "privateTrajectorySha256": report["privateTrajectorySha256"],
            "privateNativeLogSha256": report["privateNativeLogSha256"],
        }
        require_hash(path / "report.json", item["reportSha256"])
        for filename, key in (("private-trajectory.npz", "privateTrajectorySha256"),
                              ("private-native.log", "privateNativeLogSha256")):
            require_hash(path / filename, report[key])
    if verify(capture, args.data_root) != integrity or source_hashes() != current_sources:
        raise ValueError("original_captures_or_stream_sources_changed_during_export")
    evidence["originalCapturesAndExecutedSourcesUnchangedAtExport"] = True
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("results", "baselines", "data-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("existing_stream_evidence_is_preserved")
    value = export(args)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"result": "all_recorded_stream_aggregates_recomputed"}))


if __name__ == "__main__":
    main()
