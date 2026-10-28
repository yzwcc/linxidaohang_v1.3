"""Recompute aggregate evidence from four private actual-ARM64 recorded replays."""
import argparse
import json
import math
from pathlib import Path

import numpy as np

from build_slam_cpu_trial import PINS
from glim_target_replay import verify_replay_bundle
from run_glim_target_replays import RUNS
from run_kiss_offline_trial import valid_pose
from slam_target_probe import sha256, verify_bundle
from verify_slam_capture import verify


ROOT = Path(__file__).resolve().parents[1]
RECORDED_ODOMETRY_RUNNER_SHA256 = "05f0ac5301c4e3685c9d9fa74988f8b38ca3548ee5b24dadb106ab502469497e"
BOUNDARIES = (
    "rosNodeStarted", "liveSensorsSubscribed", "motionCommandsSent",
    "timingHypothesisValidated", "robotCalibrationValidated", "globalMappingExecuted",
    "mapPersistenceValidatedOnTarget", "relocalizationValidated", "navigationValidated",
    "targetRealtimePerformanceValidated", "voiceConcurrencyValidated", "deploymentApproved",
    "rawDataPublished",
)
COUNTS = (
    "recordedScans", "processedScans", "skippedUnbracketedScans",
    "initializationScans", "estimatedPoses", "imuSamplesInserted",
)


def equivalent(observed, expected):
    left, right = np.asarray(observed, dtype=float), np.asarray(expected, dtype=float)
    return (left.shape == right.shape and np.isfinite(left).all()
            and np.allclose(left, right, atol=1e-10, rtol=1e-10))


def recompute(report, arrays, expected):
    if any(type(report.get(name)) is not int or report[name] < 0 for name in COUNTS):
        raise ValueError("nonnegative_actual_scan_counts_required")
    recorded, processed, skipped, initialized, estimated, inserted = (report[name] for name in COUNTS)
    if (recorded != expected["topicCounts"]["cloud"] or processed + skipped != recorded
            or initialized + estimated != processed or estimated < 2
            or not 0 < inserted <= expected["topicCounts"]["imu"]):
        raise ValueError("all_recorded_scans_must_be_accounted_for")
    if set(arrays) != {
        "poses", "header_stamps_ns", "algorithm_stamps_relative_seconds",
        "preprocess_odometry_pointcount_marginalized",
    }:
        raise ValueError("expected_private_trajectory_arrays_required")
    poses = arrays["poses"]
    stamps = arrays["header_stamps_ns"]
    algorithm = arrays["algorithm_stamps_relative_seconds"]
    measures = arrays["preprocess_odometry_pointcount_marginalized"]
    if (poses.shape != (estimated, 4, 4) or stamps.shape != (estimated,)
            or stamps.dtype != np.dtype("int64") or np.any(np.diff(stamps) <= 0)
            or algorithm.shape != (estimated,) or not np.isfinite(algorithm).all()
            or np.any(np.diff(algorithm) <= 0) or measures.shape != (processed, 4)
            or not np.isfinite(measures).all() or np.any(measures < 0)
            or np.any(measures[:, 2] < 50)
            or np.any(measures[:, 2:] != np.floor(measures[:, 2:]))):
        raise ValueError("finite_ordered_complete_private_arrays_required")
    for pose in poses:
        valid_pose(pose)
    positions = poses[:, :3, 3]
    steps = np.linalg.norm(np.diff(positions, axis=0), axis=1)
    motion = {
        "accumulatedPositionStepsRawUnits": float(steps.sum()),
        "endToStartDistanceRawUnits": float(np.linalg.norm(positions[-1] - positions[0])),
        "maximumConsecutivePositionStepRawUnits": float(steps.max()),
    }
    timings = {
        "preprocessMillisecondsP50P95P99": np.percentile(measures[:, 0], [50, 95, 99]).tolist(),
        "odometryMillisecondsP50P95P99": np.percentile(measures[:, 1], [50, 95, 99]).tolist(),
        "combinedMillisecondsMaximum": float(measures[:, :2].sum(axis=1).max()),
    }
    if (any(not equivalent(report.get(name), value) for name, value in timings.items())
            or any(not equivalent(report["estimatedMotionNotGroundTruth"].get(name), value)
                   for name, value in motion.items())):
        raise ValueError("reported_aggregates_do_not_match_private_arrays")
    for name in ("processingWallSeconds", "processCpuSeconds", "peakResidentMiB"):
        if type(report.get(name)) not in (int, float) or not math.isfinite(report[name]) or report[name] <= 0:
            raise ValueError("finite_positive_reported_resource_measurements_required")
    timings["combinedMillisecondsP50P95P99"] = np.percentile(
        measures[:, :2].sum(axis=1), [50, 95, 99],
    ).tolist()
    return {
        "counts": {name: report[name] for name in COUNTS},
        "timingsNotRealtimeAcceptance": {
            **timings, **{name: report[name] for name in (
                "processingWallSeconds", "processCpuSeconds", "peakResidentMiB",
            )},
        },
        "estimatedMotionNotGroundTruth": motion,
    }


def export(bundle, results, core_bundle, adapter_build, data_root):
    manifest = verify_replay_bundle(bundle)
    core = verify_bundle(core_bundle)
    build_path = adapter_build / "build-report.json"
    build = json.loads(build_path.read_text())
    collection_path = results / "collection-report.json"
    collection = json.loads(collection_path.read_text())
    manifest_hash = sha256(bundle / "replay-manifest.json")
    if (collection.get("result") != "four_actual_arm64_recorded_trials_completed_not_quality_acceptance"
            or collection.get("purpose") != "actual_arm64_recorded_glim_replay_collection"
            or collection.get("replayManifestSha256") != manifest_hash
            or set(collection["runs"]) != {entry[0] for entry in RUNS}
            or core["sourceCommits"] != PINS
            or sha256(core_bundle / "bundle-manifest.json") != manifest["coreBundleManifestSha256"]
            or sha256(build_path) != manifest["adapterBuildReportSha256"]
            or build["sourceCommits"] != PINS
            or build.get("result") != "arm64_adapter_cross_built_not_executed"
            or build["artifact"]["machine"] != "AArch64"
            or sha256(adapter_build / "build/liblingxi_glim_offline.so") != build["artifact"]["sha256"]
            or sha256(bundle / "lib/liblingxi_glim_offline.so") != build["artifact"]["sha256"]):
        raise ValueError("complete_provenance_checked_four_target_trials_required")
    for name, digest in build["sourceSha256"].items():
        if (sha256(adapter_build / "source" / name) != digest
                or sha256(ROOT / "tools/glim-replay" / name) != digest):
            raise ValueError("recorded_adapter_source_changed")
    for name in ("glim_target_replay.py", "run_glim_offline_trial.py", "run_kiss_offline_trial.py"):
        allowed = {sha256(ROOT / "tools" / name)}
        if name == "glim_target_replay.py":
            allowed.add(RECORDED_ODOMETRY_RUNNER_SHA256)
        if sha256(bundle / "tools" / name) not in allowed:
            raise ValueError("actual_recorded_replay_source_changed")
    capture_path = bundle / "capture-manifest.json"
    if sha256(capture_path) != manifest["captureManifestSha256"]:
        raise ValueError("recorded_capture_manifest_changed")
    capture = json.loads(capture_path.read_text())
    integrity = verify(capture, data_root)
    runs = {}
    for label, dataset, hypothesis in RUNS:
        local = results / label
        entry = collection["runs"][label]
        report_path = local / "report.json"
        report = json.loads(report_path.read_text())
        if (entry["exitCode"] != 0 or entry["localReportSha256"] != sha256(report_path)
                or report.get("purpose") != "actual_arm64_recorded_glim_odometry_not_navigation"
                or report.get("result") != "all_recorded_scans_accounted_for_on_arm64_not_quality_acceptance"
                or report.get("dataset") != dataset or report.get("timingHypothesis") != hypothesis
                or report.get("sourceCommits") != PINS or report["target"]["machine"] != "aarch64"
                or report.get("reader") != "rosbag2_py_humble_sequential_reader_no_ros_init"
                or report.get("replayManifestSha256") != manifest_hash
                or report.get("runnerSha256") != manifest["files"]["tools/glim_target_replay.py"]["sha256"]
                or report.get("boundaries") != {name: False for name in BOUNDARIES}
                or report.get("captureIntegrity") != integrity
                or report.get("captureIntegrityAfterReplay") != integrity
                or report.get("checks") != {
                    "recordedDataProcessedOnArm64": True, "allScansAccountedFor": True,
                    "originalCapturesUnchanged": True,
                }
                or sha256(local / "private-trajectory.npz") != report["privateTrajectorySha256"]
                or sha256(local / "private-native.log") != report["privateNativeLogSha256"]):
            raise ValueError("completed_actual_target_recording_contract_mismatch")
        text = (local / "private-native.log").read_text(errors="replace").lower()
        if any(token in text for token in ("[error]", "[critical]", "segmentation fault", "terminate called")):
            raise ValueError("target_native_log_contains_failure")
        with np.load(local / "private-trajectory.npz", allow_pickle=False) as arrays:
            aggregate = recompute(report, arrays, capture["datasets"][dataset])
        runs[label] = {
            "dataset": dataset, "timingHypothesis": hypothesis,
            "privateReportSha256": sha256(report_path),
            "privateTrajectorySha256": report["privateTrajectorySha256"],
            "privateNativeLogSha256": report["privateNativeLogSha256"],
            "target": {name: report["target"][name] for name in ("machine", "python", "numpy")},
            **aggregate,
        }
    return {
        "schemaVersion": 1, "observedDate": "2026-10-28",
        "purpose": "actual_arm64_recorded_glim_odometry_not_quality_or_realtime_acceptance",
        "result": collection["result"], "sourceCommits": PINS,
        "captureManifestSha256": manifest["captureManifestSha256"],
        "replayManifestSha256": manifest_hash, "privateCollectionReportSha256": sha256(collection_path),
        "adapterBuildReportSha256": sha256(build_path),
        "adapterSha256": build["artifact"]["sha256"],
        "replaySourceSha256": manifest["files"]["tools/glim_target_replay.py"]["sha256"],
        "toolSourcesAtExport": {
            name: sha256(ROOT / "tools" / name) for name in (
                "build_glim_target_replay.py", "run_glim_target_replays.py",
                "export_glim_target_replay_evidence.py",
            )
        },
        "runs": runs, "originalCapturesUnchangedAtExport": True,
        "boundaries": {name: False for name in BOUNDARIES},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "results", "core-bundle", "adapter-build", "data-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("existing_recorded_target_evidence_is_preserved")
    evidence = export(args.bundle, args.results, args.core_bundle, args.adapter_build, args.data_root)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"result": "four_actual_target_replay_aggregates_recomputed_and_exported",
                      "runs": len(evidence["runs"])}))


if __name__ == "__main__":
    main()
