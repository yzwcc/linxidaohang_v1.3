"""Recompute actual ARM64 pacing, queue accounting and common-frame comparisons."""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path

import numpy as np

from compare_glim_architectures import ordered_stamps, pose_array, require_hash
from export_glim_controlled_target import load_target
from export_glim_mapping_evidence import check_reload_log
from export_glim_stream_evidence import public_stream, recompute_comparison
from glim_target_controlled_mapping import PURPOSE as PARENT_PURPOSE, verify_control
from glim_target_replay import verify_replay_bundle
from glim_target_worker import (
    ARRAYS, BOUNDARIES, DELIVERY_ARRAYS, PURPOSE, RESULT, RUNTIME_SOURCES, delivery_metrics,
)
from lingxi_slam.worker import WorkerLimits
from run_glim_worker_target import CONDITIONS, FILES, label
from slam_bag_audit import CLOUD, IMU
from slam_target_probe import verify_bundle
from verify_slam_capture import sha256, verify


ROOT = Path(__file__).resolve().parents[1]
COUNT_FIELDS = (
    "acceptedImu", "acceptedClouds", "completedMessages", "discardedMessages", "rejectedMessages",
    "maximumQueuedMessages", "maximumQueuedClouds", "maximumQueuedPayloadBytes",
)


def public_worker(value, expected):
    counts, limits = value["counts"], asdict(WorkerLimits())
    if (value["state"] != "stopped" or value["failure"] is not None or value["threadAlive"] is not False
            or value["limits"] != limits or set(counts) != set(COUNT_FIELDS)
            or any(type(counts[name]) is not int or counts[name] < 0 for name in COUNT_FIELDS)
            or counts["acceptedImu"] != expected["topicCounts"]["imu"]
            or counts["acceptedClouds"] != expected["topicCounts"]["cloud"]
            or counts["completedMessages"] != counts["acceptedImu"] + counts["acceptedClouds"]
            or counts["discardedMessages"] != 0 or counts["rejectedMessages"] != 0
            or any(value[name] != 0 for name in (
                "queuedMessages", "queuedClouds", "queuedPayloadBytes", "inFlightMessages"))
            or counts["maximumQueuedMessages"] > limits["maximum_messages"]
            or counts["maximumQueuedClouds"] > limits["maximum_clouds"]
            or counts["maximumQueuedPayloadBytes"] > limits["maximum_payload_bytes"]
            or type(value["maximumObservedQueueAgeSeconds"]) not in (int, float)
            or not math.isfinite(value["maximumObservedQueueAgeSeconds"])
            or not 0 <= value["maximumObservedQueueAgeSeconds"] <= limits["maximum_queue_age_seconds"]):
        raise ValueError("complete_closed_bounded_worker_accounting_required")
    stream = public_stream(value["stream"], expected)
    stream["maximumActualCallbackScanWaitSeconds"] = stream.pop("maximumRecordedArrivalScanWaitSeconds")
    return {
        "counts": {name: counts[name] for name in COUNT_FIELDS}, "limits": limits,
        "maximumObservedQueueAgeSeconds": value["maximumObservedQueueAgeSeconds"],
        "threadJoinedAndAllQueuesReleased": True, "stream": stream,
    }


def matching_metrics(observed, recomputed):
    if set(observed) != set(recomputed):
        return False
    for name, expected in recomputed.items():
        actual = observed[name]
        if isinstance(expected, list):
            if (not isinstance(actual, list) or len(actual) != len(expected)
                    or not all(type(value) in (int, float) and math.isfinite(value) for value in actual)
                    or not np.allclose(actual, expected, rtol=1e-12, atol=1e-9)):
                return False
        elif (type(actual) not in (int, float) or not math.isfinite(actual)
              or not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9)):
            return False
    return True


def original_schedule(data_root, expected):
    from rosbags.highlevel import AnyReader
    from rosbags.typesys import Stores, get_typestore

    kinds, relative = [], []
    origin = None
    with AnyReader([data_root / expected["directoryName"]],
                   default_typestore=get_typestore(Stores.ROS2_HUMBLE)) as reader:
        connections = [item for item in reader.connections if item.topic in (CLOUD, IMU)]
        if (len(connections) != 2 or {item.topic: item.msgtype for item in connections}
                != {CLOUD: "sensor_msgs/msg/PointCloud2", IMU: "sensor_msgs/msg/Imu"}):
            raise ValueError("original_mixed_pacing_connections_required")
        for connection, received_ns, _ in reader.messages(connections=connections):
            origin = received_ns if origin is None else origin
            kinds.append(connection.topic == CLOUD)
            relative.append((received_ns - origin) / 1e9)
    return np.asarray(kinds, dtype=np.uint8), np.asarray(relative)


def export(args):
    manifest = verify_replay_bundle(args.bundle, PURPOSE)
    parent = verify_replay_bundle(args.parent_bundle, PARENT_PURPOSE)
    core = verify_bundle(args.core_bundle)
    control = verify_control(args.bundle)
    frozen_path = ROOT / "docs/slam-controlled-target-evidence.json"
    frozen = json.loads(frozen_path.read_text())
    require_hash(args.parent_bundle / "replay-manifest.json", manifest["parentControlledManifestSha256"])
    require_hash(args.core_bundle / "bundle-manifest.json", manifest["coreBundleManifestSha256"])
    require_hash(ROOT / "tools/prepare_glim_worker_target.py", manifest["preparerSourceSha256"])
    if (sha256(args.parent_bundle / "replay-manifest.json") != frozen["replayManifestSha256"]
            or manifest["controlManifestSha256"] != parent["controlManifestSha256"]
            or manifest["coreBundleManifestSha256"] != parent["coreBundleManifestSha256"]
            or manifest["files"]["lib/liblingxi_glim_offline.so"] != parent["files"]["lib/liblingxi_glim_offline.so"]
            or manifest["recordedReceiveTimingPacedAtOneTimes"] is not True or manifest["liveDdsTrial"] is not False):
        raise ValueError("unchanged_native_core_config_and_frozen_target_baseline_required")
    for source, entry in manifest["files"].items():
        if source.startswith(("tools/", "robot-slam/")):
            require_hash(ROOT / source, entry["sha256"])
    capture_path = ROOT / "docs/slam-capture-evidence.json"
    require_hash(args.bundle / "capture-manifest.json", sha256(capture_path))
    capture = json.loads(capture_path.read_text())
    integrity = verify(capture, args.data_root)
    bundle_hash = sha256(args.bundle / "replay-manifest.json")
    collection_path = args.results / "collection-report.json"
    collection = json.loads(collection_path.read_text())
    require_hash(ROOT / "tools/run_glim_worker_target.py", collection["coordinatorSourceSha256"])
    conditions = {label(*condition): condition for condition in CONDITIONS}
    if (collection["result"] != "four_actual_arm64_paced_workers_completed_not_live_acceptance"
            or collection["replayManifestSha256"] != bundle_hash or set(collection["runs"]) != set(conditions)):
        raise ValueError("all_four_predeclared_actual_worker_processes_required")
    baseline_collection = json.loads((args.baselines / "collection-report.json").read_text())
    expected_libraries = {Path(name).name: entry["sha256"] for name, entry in core["files"].items()
                          if name.startswith("lib/")}
    expected_libraries.update({name: parent["files"]["lib/" + name]["sha256"]
                               for name in ("liblingxi_glim_offline.so", "liblingxi_glim_mapping.so")})
    sources = {name: sha256(ROOT / name) for name in RUNTIME_SOURCES}
    result = {
        "schemaVersion": 1, "observedDate": "2026-10-28",
        "purpose": "actual_arm64_paced_sensor_worker_not_live_dds_mapping_or_navigation_acceptance",
        "freshTargetProcessCount": 4, "captureManifestSha256": sha256(capture_path),
        "baselineTargetEvidenceSha256": sha256(frozen_path),
        "replayManifestSha256": bundle_hash, "collectionReportSha256": sha256(collection_path),
        "coreBundleManifestSha256": manifest["coreBundleManifestSha256"],
        "controlManifestSha256": manifest["controlManifestSha256"],
        "actualRuntimeSourceSha256": sources,
        "toolSourcesSha256": {name: sha256(ROOT / "tools" / name) for name in (
            "prepare_glim_worker_target.py", "run_glim_worker_target.py", "export_glim_worker_evidence.py")},
        "numericPacingRoundoffAllowanceSeconds": 1e-9,
        "metricRecomputationAbsoluteTolerance": 1e-9,
        "runs": {}, "boundaries": {name: False for name in BOUNDARIES},
    }
    pids, loaded_sets = set(), []
    for name, (dataset, hypothesis) in conditions.items():
        path, item = args.results / name, collection["runs"][name]
        require_hash(path / "report.json", item["reportSha256"])
        report = json.loads((path / "report.json").read_text())
        expected = capture["datasets"][dataset]
        if (item["exitCode"] != 0 or report["result"] != RESULT or report["purpose"] != PURPOSE
                or (report["dataset"], report["timingHypothesis"]) != (dataset, hypothesis)
                or report["target"]["machine"] != "aarch64" or type(report["pid"]) is not int or report["pid"] <= 0
                or report["replayManifestSha256"] != bundle_hash or report["sourceSha256"] != sources
                or report["sourceCommits"] != parent["sourceCommits"]
                or report["captureManifestSha256"] != result["captureManifestSha256"]
                or report["boundaries"] != result["boundaries"]
                or report["pacing"] != "one_times_original_mixed_rosbag_receive_timing_not_live_dds"
                or report["effectiveConfiguration"] != control["effectiveFilesSha256"]
                or report["originalCapturesAndBundleUnchanged"] is not True
                or report["nativeCallsAndConsumerHaveOneNonproducerOwner"] is not True
                or report["receivedMessageCounts"] != {key: expected["topicCounts"][key] for key in ("cloud", "imu")}
                or set(report["filesSha256"]) != set(FILES) - {"report.json"}):
            raise ValueError("complete_unchanged_actual_paced_worker_report_required")
        pids.add(report["pid"])
        for filename, digest in report["filesSha256"].items():
            require_hash(path / filename, digest)
        check_reload_log(path / "private-native.log")
        summary = public_worker(report["worker"], expected)
        counts = summary["stream"]["counts"]
        if report["nativeCalls"] != {
            "create": 1, "close": 1, "imu": counts["insertedImu"], "frame": counts["processedScans"],
        }:
            raise ValueError("once_only_single_owner_native_calls_required")
        loaded = report["actualLoadedLibrariesSha256"]
        if (not {"libglim.so", "libodometry_estimation_cpu.so", "liblingxi_glim_offline.so"}.issubset(loaded)
                or any(expected_libraries.get(key) != value for key, value in loaded.items())):
            raise ValueError("same_actual_worker_native_libraries_required")
        loaded_sets.append(loaded)
        baseline_name = name + "-repeat1"
        baseline_item = baseline_collection["runs"][baseline_name]
        if baseline_item["reportSha256"] != frozen["runs"][baseline_name]["controlledReportSha256"]:
            raise ValueError("frozen_target_baseline_report_required")
        baseline = load_target(
            args.baselines / baseline_name, (dataset, hypothesis, 1), baseline_item, parent,
            manifest["parentControlledManifestSha256"], integrity, expected_libraries)
        with np.load(path / "private-trajectory.npz", allow_pickle=False) as archive:
            if set(archive) != set(ARRAYS):
                raise ValueError("complete_paced_worker_trajectory_arrays_required")
            arrays = {key: archive[key].copy() for key in ARRAYS}
        poses, headers, algorithm, measures = (arrays[key] for key in ARRAYS)
        ordered_stamps(headers)
        pose_array(poses)
        if (len(poses) != counts["estimatedPoses"] or len(headers) != len(poses)
                or algorithm.shape != headers.shape or not np.isfinite(algorithm).all()
                or np.any(np.diff(algorithm) <= 0) or measures.shape != (counts["processedScans"], 4)
                or not np.isfinite(measures).all() or np.any(measures < 0)
                or np.any(measures[:, 2:] != np.floor(measures[:, 2:]))):
            raise ValueError("finite_complete_paced_worker_poses_and_counters_required")
        with np.load(path / "private-delivery.npz", allow_pickle=False) as archive:
            if set(archive) != set(DELIVERY_ARRAYS):
                raise ValueError("all_paced_delivery_measurements_required")
            deliveries = {key: archive[key].copy() for key in DELIVERY_ARRAYS}
        original_kinds, original_relative = original_schedule(args.data_root, expected)
        if (not np.array_equal(deliveries["kind_cloud_1_imu_0"], original_kinds)
                or not np.array_equal(deliveries["scheduled_relative_seconds"], original_relative)
                or len(deliveries["frame_callback_elapsed_seconds"]) != counts["processedScans"]):
            raise ValueError("complete_original_mixed_receive_schedule_required")
        metrics = delivery_metrics(deliveries)
        if not matching_metrics(report["observedPacedDeliveryNotDds"], metrics):
            raise ValueError("recomputed_paced_worker_delivery_metrics_mismatch")
        costs = {}
        for key in ("totalWallSecondsIncludingVerification", "processCpuSeconds", "peakResidentMiB"):
            value = report[key]
            if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
                raise ValueError("finite_positive_actual_worker_costs_required")
            costs[key] = value
        costs["combinedNativeMillisecondsP50P95P99"] = np.percentile(
            measures[:, :2].sum(axis=1), [50, 95, 99]).tolist()
        result["runs"][name] = {
            "dataset": dataset, "timingHypothesis": hypothesis,
            "target": {key: report["target"][key] for key in ("machine", "python", "numpy")},
            "worker": summary, "observedPacedDeliveryNotDds": metrics,
            "nativeCalls": report["nativeCalls"], "nativeCallsAndConsumerHaveOneNonproducerOwner": True,
            "baselineComparisonNotTruthAccuracy": recompute_comparison(arrays, baseline),
            "observedPacedProcessCostsNotLivePerformance": costs,
            "reportSha256": item["reportSha256"], "filesSha256": report["filesSha256"],
        }
        for filename, digest in report["filesSha256"].items():
            require_hash(path / filename, digest)
    if len(pids) != 4 or any(loaded != loaded_sets[0] for loaded in loaded_sets):
        raise ValueError("four_distinct_target_processes_with_identical_native_libraries_required")
    if verify(capture, args.data_root) != integrity:
        raise ValueError("original_capture_changed_during_worker_export")
    verify_replay_bundle(args.bundle, PURPOSE)
    result["actualTargetNativeLibrariesSha256"] = loaded_sets[0]
    result["originalCapturesAndBundleUnchangedAtExport"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "parent-bundle", "core-bundle", "baselines", "results", "data-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("existing_paced_worker_evidence_is_preserved")
    result = export(args)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"result": "all_actual_arm64_paced_worker_evidence_recomputed"}))


if __name__ == "__main__":
    main()
