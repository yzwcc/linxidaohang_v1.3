"""Recompute bounded live-session aggregates without publishing poses or headers."""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path

import numpy as np

from export_glim_stream_evidence import public_stream
from export_glim_worker_evidence import matching_metrics
from glim_live_session import (
    ARRAYS, BOUNDARIES, PURPOSE, RESULT, RUNTIME_SOURCES, metrics,
)
from glim_target_controlled_mapping import configuration_files, verify_control
from glim_target_replay import verify_replay_bundle
from glim_target_worker import PURPOSE as PARENT_PURPOSE
from lingxi_slam.worker import WorkerLimits
from run_glim_live_target import HYPOTHESES
from slam_target_probe import verify_bundle
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
COUNT_FIELDS = (
    "acceptedImu", "acceptedClouds", "completedMessages", "discardedMessages", "rejectedMessages",
    "maximumQueuedMessages", "maximumQueuedClouds", "maximumQueuedPayloadBytes",
)


def matching_observations(actual, expected):
    if not isinstance(actual, dict) or set(actual) != set(expected):
        return False
    return all(matching_observations(actual[key], value) if isinstance(value, dict)
               else matching_metrics({key: actual[key]}, {key: value})
               for key, value in expected.items())


def public_worker(state, observed, native):
    counts, limits = state["counts"], asdict(WorkerLimits())
    if (state["state"] != "stopped" or state["threadAlive"] is not False or state["failure"] is not None
            or state["limits"] != limits or set(counts) != set(COUNT_FIELDS)
            or any(type(counts[key]) is not int or counts[key] < 0 for key in COUNT_FIELDS)
            or counts["acceptedImu"] != observed["imu"]["callbacks"]
            or counts["acceptedClouds"] != observed["cloud"]["callbacks"]
            or counts["completedMessages"] + counts["discardedMessages"]
            != counts["acceptedImu"] + counts["acceptedClouds"] or counts["rejectedMessages"]
            or any(state[key] != 0 for key in (
                "queuedMessages", "queuedClouds", "queuedPayloadBytes", "inFlightMessages"))
            or counts["maximumQueuedMessages"] > limits["maximum_messages"]
            or counts["maximumQueuedClouds"] > limits["maximum_clouds"]
            or counts["maximumQueuedPayloadBytes"] > limits["maximum_payload_bytes"]
            or type(state["maximumObservedQueueAgeSeconds"]) not in (int, float)
            or not math.isfinite(state["maximumObservedQueueAgeSeconds"])
            or not 0 <= state["maximumObservedQueueAgeSeconds"] <= limits["maximum_queue_age_seconds"]):
        raise ValueError("closed_bounded_live_worker_with_complete_accounting_required")
    stream_counts = state["stream"]["counts"]
    if (counts["completedMessages"] != stream_counts["receivedClouds"] + stream_counts["receivedImu"]
            or native != {"create": 1, "close": 1, "frame": stream_counts["processedScans"],
                          "imu": stream_counts["insertedImu"]}
            or stream_counts["processedScans"] != observed["processedScans"]
            or stream_counts["initializationScans"] != observed["initializationScans"]
            or stream_counts["estimatedPoses"] != observed["estimatedPoses"]):
        raise ValueError("complete_live_native_stream_accounting_required")
    expected = {"topicCounts": {"imu": stream_counts["receivedImu"],
                                 "cloud": stream_counts["receivedClouds"]}}
    stream = public_stream(state["stream"], expected)
    stream["maximumActualCallbackScanWaitSeconds"] = stream.pop("maximumRecordedArrivalScanWaitSeconds")
    return {
        "counts": {key: counts[key] for key in COUNT_FIELDS}, "limits": limits,
        "maximumObservedQueueAgeSeconds": state["maximumObservedQueueAgeSeconds"],
        "threadJoinedAndQueuesReleased": True,
        "discardedMessagesAreExplicitSessionStopNotUnreportedLoss": counts["discardedMessages"],
        "stream": stream,
    }


def export(args):
    manifest = verify_replay_bundle(args.bundle, PURPOSE)
    parent = verify_replay_bundle(args.parent_bundle, PARENT_PURPOSE)
    core = verify_bundle(args.core_bundle)
    verify_control(args.bundle)
    if (manifest["parentWorkerManifestSha256"] != sha256(args.parent_bundle / "replay-manifest.json")
            or manifest["coreBundleManifestSha256"] != sha256(args.core_bundle / "bundle-manifest.json")
            or manifest["coreBundleManifestSha256"] != parent["coreBundleManifestSha256"]
            or manifest["liveDdsTrial"] is not True
            or manifest["recordedReceiveTimingPacedAtOneTimes"] is not False
            or sha256(ROOT / "tools/prepare_glim_live_target.py") != manifest["preparerSourceSha256"]):
        raise ValueError("same_frozen_parent_core_and_live_preparer_required")
    for name, entry in parent["files"].items():
        if manifest["files"][name] != entry:
            raise ValueError("historical_worker_bundle_members_changed")
    collection_path = args.results / "collection-report.json"
    collection = json.loads(collection_path.read_text())
    bundle_digest = sha256(args.bundle / "replay-manifest.json")
    if (collection["result"] != "two_actual_live_humble_sessions_closed_not_product_acceptance"
            or collection["replayManifestSha256"] != bundle_digest
            or collection["coordinatorSourceSha256"] != sha256(ROOT / "tools/run_glim_live_target.py")
            or set(collection["runs"]) != {"start", "end"}):
        raise ValueError("two_complete_declared_actual_live_processes_required")
    public = {
        "schemaVersion": 1, "purpose": PURPOSE,
        "result": "actual_short_humble_subscriptions_verified_not_product_acceptance",
        "replayManifestSha256": bundle_digest,
        "parentWorkerManifestSha256": manifest["parentWorkerManifestSha256"],
        "coreBundleManifestSha256": manifest["coreBundleManifestSha256"],
        "sourceCommits": core["sourceCommits"],
        "coordinatorSourceSha256": collection["coordinatorSourceSha256"],
        "exporterSourceSha256": sha256(__file__),
        "rosInfrastructureMayPublishDiscoveryAndParameterEvents": True,
        "boundaries": {key: False for key in BOUNDARIES}, "runs": {},
    }
    for hypothesis in HYPOTHESES:
        name = hypothesis.removeprefix("header_is_scan_")
        directory = args.results / name
        report_path = directory / "report.json"
        report = json.loads(report_path.read_text())
        record = collection["runs"][name]
        if (record["exitCode"] != 0 or record["reportSha256"] != sha256(report_path)
                or report["result"] != RESULT or report["purpose"] != PURPOSE
                or report["replayManifestSha256"] != bundle_digest or report["timingHypothesis"] != hypothesis
                or report["target"]["machine"] != "aarch64" or not 5 <= report["requestedSeconds"] <= 60
                or report["boundaries"] != public["boundaries"]
                or report["bundleAndDdsProfileUnchanged"] is not True
                or report["nativeCallsHaveOneNonproducerOwner"] is not True
                or report["rosInfrastructureMayPublishDiscoveryAndParameterEvents"] is not True
                or report["session"]["stopReason"] != "duration_reached"
                or report["session"]["worker"] != report["worker"]
                or report["effectiveConfiguration"] != configuration_files(args.bundle / "config")
                or set(report["sourceSha256"]) != set(RUNTIME_SOURCES)
                or set(report["filesSha256"]) != {"private-native.log", "private-live-observations.npz"}):
            raise ValueError("complete_real_target_report_and_boundaries_required")
        for source in RUNTIME_SOURCES:
            digest = manifest["files"][source]["sha256"]
            if report["sourceSha256"][source] != digest or sha256(ROOT / source) != digest:
                raise ValueError("unchanged_frozen_live_source_required")
        expected_libraries = {Path(key).name: value["sha256"]
                              for key, value in core["files"].items() if key.startswith("lib/")}
        expected_libraries["liblingxi_glim_offline.so"] = manifest["files"]["lib/liblingxi_glim_offline.so"]["sha256"]
        loaded = report["actualLoadedLibrariesSha256"]
        if (not {"libglim.so", "libodometry_estimation_cpu.so", "liblingxi_glim_offline.so"}.issubset(loaded)
                or any(digest != expected_libraries.get(key) for key, digest in loaded.items())):
            raise ValueError("verified_actual_loaded_live_libraries_required")
        for filename, digest in report["filesSha256"].items():
            if sha256(directory / filename) != digest:
                raise ValueError("complete_private_live_files_required")
        with np.load(directory / "private-live-observations.npz", allow_pickle=False) as archive:
            if set(archive.files) != set(ARRAYS):
                raise ValueError("exact_private_live_archive_required")
            observed = metrics({key: archive[key] for key in ARRAYS})
        if not matching_observations(report["observations"], observed):
            raise ValueError("actual_live_metrics_recomputation_mismatch")
        native_log = (directory / "private-native.log").read_text()
        public["runs"][name] = {
            "timingHypothesis": hypothesis, "target": report["target"],
            "requestedSeconds": report["requestedSeconds"],
            "actualSessionWallSecondsIncludingCleanup": report["session"]["elapsedWallSecondsIncludingCleanup"],
            "reportSha256": sha256(report_path), "ddsProfileSha256": report["ddsProfileSha256"],
            "sourceSha256": report["sourceSha256"],
            "actualLoadedLibrariesSha256": loaded, "observations": observed,
            "nativeCalls": report["nativeCalls"],
            "worker": public_worker(report["worker"], observed, report["nativeCalls"]),
            "peakResidentMiB": report["peakResidentMiB"], "processCpuSeconds": report["processCpuSeconds"],
            "nativeWarningCounts": {
                "imuPredictionNotGood": native_log.count("IMU prediction is not good."),
                "imuExtrinsicOrBiasWarning": native_log.count(
                    "Possibly T_lidar_imu is not accurate or IMU bias is not well estimated."),
            },
        }
    if public["runs"]["start"]["ddsProfileSha256"] != public["runs"]["end"]["ddsProfileSha256"]:
        raise ValueError("unchanged_dds_profile_across_declared_conditions_required")
    return public


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
    print(json.dumps({"result": result["result"], "publicAggregateSha256": sha256(args.output)}))


if __name__ == "__main__":
    main()
