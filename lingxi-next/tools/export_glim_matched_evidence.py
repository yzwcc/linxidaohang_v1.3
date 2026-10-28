"""Independently check matched callback bytes/results, exporting aggregates only."""
import argparse
import json
from pathlib import Path

import numpy as np

from audit_slam_calibration import warning_summary
from export_glim_live_evidence import matching_observations, public_worker
from glim_live_session import ARRAYS, BOUNDARIES, metrics
from glim_matched_session import COUNT_KEYS, OUTPUT_ARRAYS, PURPOSE, RESULT, SOURCES, compare
from glim_target_controlled_mapping import configuration_files, verify_control
from glim_target_replay import verify_replay_bundle
from slam_callback_journal import inspect, records
from slam_target_probe import verify_bundle
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]


def matching_comparison(actual, expected):
    if not isinstance(actual, dict) or set(actual) != set(expected):
        return False
    flags = {key for key, value in expected.items() if type(value) is bool}
    return (all(actual[key] is expected[key] for key in flags)
            and matching_observations({key: actual[key] for key in actual if key not in flags},
                                      {key: expected[key] for key in expected if key not in flags}))


def audit_callbacks(path, arrays, decoder=None):
    if decoder is None:
        from rosbags.typesys import Stores, get_typestore
        store = get_typestore(Stores.ROS2_HUMBLE)
        def decoder(kind, payload):
            message = store.deserialize_cdr(payload, "sensor_msgs/msg/" + ("PointCloud2" if kind == "cloud" else "Imu"))
            expected = "lidar_chest_front" if kind == "cloud" else "lidar_imu_chest_front"
            if message.header.frame_id != expected:
                raise ValueError("expected_recorded_cdr_frame_required")
            return int(message.header.stamp.sec) * 10**9 + int(message.header.stamp.nanosec)
    counts = dict.fromkeys(("cloud", "imu"), 0)
    payload_bytes = 0
    for entry in records(path):
        headers = arrays[entry.kind + "_headers_ns"]
        index = counts[entry.kind]
        if index >= len(headers) or decoder(entry.kind, entry.payload) != int(headers[index]):
            raise ValueError("every_cdr_header_must_match_actual_callback_observation")
        counts[entry.kind] += 1
        payload_bytes += len(entry.payload)
    if any(counts[kind] != len(arrays[kind + "_headers_ns"]) for kind in counts):
        raise ValueError("all_observed_callbacks_require_recorded_cdr")
    return {"counts": counts, "records": sum(counts.values()), "payloadBytes": payload_bytes,
            "allCallbackHeadersIndependentlyDeserializedAndMatched": True}


def export(args):
    manifest = verify_replay_bundle(args.bundle, PURPOSE)
    parent = json.loads((args.parent_bundle / "replay-manifest.json").read_text())
    core = verify_bundle(args.core_bundle)
    verify_control(args.bundle)
    if (manifest["parentLiveManifestSha256"] != sha256(args.parent_bundle / "replay-manifest.json")
            or manifest["coreBundleManifestSha256"] != sha256(args.core_bundle / "bundle-manifest.json")
            or manifest["preparerSourceSha256"] != sha256(ROOT / "tools/prepare_glim_matched_target.py")):
        raise ValueError("unchanged_parent_core_and_matched_preparer_required")
    verify_replay_bundle(args.parent_bundle, parent["purpose"])
    for name, entry in parent["files"].items():
        if manifest["files"][name] != entry:
            raise ValueError("historical_bundle_members_must_remain_frozen")
    for name, entry in manifest["files"].items():
        if name.startswith(("tools/", "robot-slam/")) and sha256(ROOT / name) != entry["sha256"]:
            raise ValueError("actual_matched_runtime_sources_unchanged_required")
    collection = json.loads((args.results / "collection-report.json").read_text())
    if (collection["result"] != "live_and_identical_prefix_replay_closed_not_product_acceptance"
            or collection["replayManifestSha256"] != sha256(args.bundle / "replay-manifest.json")
            or collection["coordinatorSha256"] != sha256(ROOT / "tools/run_glim_matched_target.py")
            or set(collection["runs"]) != {"live", "replay"}):
        raise ValueError("two_completed_declared_matched_processes_required")
    reports, arrays, warnings = {}, {}, {}
    for mode in ("live", "replay"):
        directory = args.results / mode
        report_path = directory / "report.json"
        report = reports[mode] = json.loads(report_path.read_text())
        record = collection["runs"][mode]
        if (record["exitCode"] != 0 or record["reportSha256"] != sha256(report_path)
                or report["purpose"] != PURPOSE or report["result"] != RESULT or report["mode"] != mode
                or report["target"]["machine"] != "aarch64" or report["nativeOwnerIsSingleThread"] is not True
                or report["replayManifestSha256"] != collection["replayManifestSha256"]
                or report["rosNodeStarted"] is not (mode == "live")
                or report["replayIsLogicalArrivalOrderNotRealtimeMeasurement"] is not (mode == "replay")
                or report["timingHypothesis"] != "header_is_scan_start"
                or report["boundaries"] != dict.fromkeys(BOUNDARIES, False)
                or report["effectiveConfiguration"] != configuration_files(args.bundle / "config")
                or report["sourceSha256"] != {name: sha256(ROOT / name) for name in SOURCES}):
            raise ValueError("complete_matched_process_contract_required")
        expected_files = {"private-native.log", "private-results.npz"}
        if mode == "live":
            expected_files.add("private-callbacks.cdrjournal")
        if set(report["filesSha256"]) != expected_files:
            raise ValueError("exact_private_matched_artifact_set_required")
        for name, digest in report["filesSha256"].items():
            if sha256(directory / name) != digest:
                raise ValueError("unchanged_actual_private_matched_files_required")
        with np.load(directory / "private-results.npz", allow_pickle=False) as archive:
            expected_arrays = set(OUTPUT_ARRAYS) | (set(ARRAYS) if mode == "live" else set())
            if set(archive.files) != expected_arrays:
                raise ValueError("exact_matched_observation_array_set_required")
            arrays[mode] = {key: archive[key] for key in archive.files}
        stream_counts = report["stream"]["counts"]
        if report["nativeCalls"] != {"create": 1, "close": 1, "frame": stream_counts["processedScans"],
                                     "imu": stream_counts["insertedImu"]}:
            raise ValueError("native_calls_match_matched_stream_required")
        expected_libraries = {Path(name).name: entry["sha256"]
                              for name, entry in core["files"].items() if name.startswith("lib/")}
        expected_libraries["liblingxi_glim_offline.so"] = manifest["files"]["lib/liblingxi_glim_offline.so"]["sha256"]
        loaded = report["actualLoadedLibrariesSha256"]
        if (not {"libglim.so", "liblingxi_glim_offline.so", "libodometry_estimation_cpu.so"}.issubset(loaded)
                or any(expected_libraries.get(name) != digest for name, digest in loaded.items())):
            raise ValueError("actual_loaded_matched_libraries_must_match_frozen_core")
        warnings[mode] = warning_summary((directory / "private-native.log").read_text())
    live, replay = reports["live"], reports["replay"]
    if (live["target"]["pid"] == replay["target"]["pid"]
            or live["actualLoadedLibrariesSha256"] != replay["actualLoadedLibrariesSha256"]
            or replay["referenceReportSha256"] != collection["runs"]["live"]["reportSha256"]
            or any(live["stream"]["counts"][key] != replay["stream"]["counts"][key] for key in COUNT_KEYS)
            or replay["replayedCompletedPrefixMessages"] != live["worker"]["counts"]["completedMessages"]
            or replay["excludedExplicitStopTailMessages"] != live["worker"]["counts"]["discardedMessages"]):
        raise ValueError("distinct_processes_and_identical_completed_prefix_required")
    journal = args.results / "live/private-callbacks.cdrjournal"
    if inspect(journal) != live["journal"] or sha256(journal) != replay["inputJournalSha256"]:
        raise ValueError("identical_committed_callback_journal_required")
    callback_counts = audit_callbacks(journal, arrays["live"])
    observed = metrics({key: arrays["live"][key] for key in ARRAYS})
    comparison = compare({key: arrays["live"][key] for key in OUTPUT_ARRAYS}, arrays["replay"])
    if (not matching_observations(live["observations"], observed)
            or not matching_comparison(replay["sameInputComparison"], comparison)):
        raise ValueError("independent_same_input_metrics_recomputation_required")
    worker = public_worker(live["worker"], observed, live["nativeCalls"])
    if callback_counts["counts"] != live["journal"]["counts"]:
        raise ValueError("matching_actual_callback_counts_required")
    return {
        "schemaVersion": 1, "purpose": PURPOSE,
        "result": "same_current_callback_input_replayed_on_arm64_not_quality_acceptance",
        "bundleManifestSha256": collection["replayManifestSha256"],
        "parentLiveManifestSha256": manifest["parentLiveManifestSha256"],
        "coreBundleManifestSha256": manifest["coreBundleManifestSha256"], "sourceCommits": core["sourceCommits"],
        "coordinatorSha256": collection["coordinatorSha256"], "exporterSha256": sha256(__file__),
        "sourceSha256": live["sourceSha256"], "reportsSha256": {
            mode: collection["runs"][mode]["reportSha256"] for mode in ("live", "replay")},
        "distinctActualArm64ProcessIdsVerifiedNotPublished": True,
        "actualLoadedLibrariesSha256": live["actualLoadedLibrariesSha256"],
        "callbackJournalSha256": sha256(journal), "callbackAudit": callback_counts,
        "journalBytes": live["journal"]["bytes"],
        "replayedCompletedPrefixMessages": replay["replayedCompletedPrefixMessages"],
        "excludedExplicitStopTailMessages": replay["excludedExplicitStopTailMessages"],
        "liveObservations": observed, "liveWorker": worker,
        "captureSerializationAndAppendMillisecondsP50P95P99Max":
            live["captureSerializationAndAppendMillisecondsP50P95P99Max"],
        "captureCostIsInstrumentedSummaryNotIndependentlyRecomputedSamples": True,
        "sameInputComparison": comparison, "nativeWarnings": warnings,
        "warningSummariesIdentical": warnings["live"] == warnings["replay"],
        "replayIsLogicalArrivalOrderNotRealtimeMeasurement": True,
        "liveRequestedSeconds": live["requestedSeconds"], "ddsProfileSha256": live["ddsProfileSha256"],
        "peakResidentMiB": {mode: reports[mode]["peakResidentMiB"] for mode in reports},
        "boundaries": {name: False for name in BOUNDARIES},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "parent-bundle", "core-bundle", "results", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.output.resolve().is_relative_to((ROOT / ".local").resolve()):
        parser.error("new ignored aggregate output required")
    result = export(args)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"result": "aggregate_recomputed", "sha256": sha256(args.output)}))


if __name__ == "__main__":
    main()
