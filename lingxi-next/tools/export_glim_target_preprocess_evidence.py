"""Recompute full ARM64 point comparisons and timing; export only aggregate evidence."""
import argparse
import itertools
import json
import math
from pathlib import Path
import re

import numpy as np

from compare_glim_architectures import ordered_stamps, require_hash
from export_glim_preprocess_evidence import compare_observations, load_observation, require_same_inputs
from glim_target_preprocess import BOUNDARIES, FILES, PURPOSE, verify_preprocess_bundle
from run_glim_preprocess_trial import CONDITIONS, ROOT, validate_build
from slam_target_probe import verify_bundle
from verify_slam_capture import sha256, verify


def load_target(path, condition, collection, manifest, bundle):
    require_hash(path / "report.json", collection["reportSha256"])
    report = json.loads((path / "report.json").read_text())
    dataset, threads, _ = condition
    if (collection["exitCode"] != 0 or report["purpose"] != PURPOSE
            or report["result"] != "complete_arm64_native_preprocessing_not_slam_acceptance"
            or report["dataset"] != dataset or report["diagnosticThreads"] != threads
            or report["hostMachine"] != "aarch64" or type(report["pid"]) is not int or report["pid"] <= 0
            or report["runnerSourceSha256"] != sha256(bundle / "tools/glim_target_preprocess.py")
            or report["bundleManifestSha256"] != sha256(bundle / "preprocess-manifest.json")
            or report["captureManifestSha256"] != manifest["files"]["capture-manifest.json"]["sha256"]
            or report["configManifestSha256"] != manifest["files"]["config/trial-manifest.json"]["sha256"]
            or report["nativeAdapterSha256"] != manifest["files"]["lib/liblingxi_glim_preprocess.so"]["sha256"]
            or report["boundaries"] != {name: False for name in BOUNDARIES}
            or report["originalCapturesUnchanged"] is not True
            or set(report["filesSha256"]) != set(FILES)):
        raise ValueError("actual_complete_arm64_preprocessing_report_required")
    for name in FILES:
        require_hash(path / name, report["filesSha256"][name])
    text = (path / "private-native.log").read_text().lower()
    if any(token in text for token in ("[error]", "[critical]", "terminate called", "too few points")):
        raise ValueError("native_arm64_preprocessing_failure_in_log")
    with np.load(path / "private-index.npz", allow_pickle=False) as archive:
        if set(archive) != {"counts", "header_stamps_ns", "algorithm_stamps_relative_seconds", "input_cloud_sha256"}:
            raise ValueError("complete_arm64_preprocessing_index_required")
        index = {name: archive[name].copy() for name in archive}
    counts, headers, times, hashes = (index[name] for name in (
        "counts", "header_stamps_ns", "algorithm_stamps_relative_seconds", "input_cloud_sha256"))
    ordered_stamps(headers)
    if (counts.dtype != np.dtype("int64") or counts.shape != (len(headers), 2)
            or np.any(counts[:, 0] > 2_000_000) or np.any(counts[:, 1] < 50)
            or np.any(counts[:, 1] > counts[:, 0])
            or times.shape != headers.shape or not np.isfinite(times).all() or np.any(np.diff(times) <= 0)
            or hashes.shape != headers.shape or hashes.dtype.kind != "U"
            or any(re.fullmatch("[0-9a-f]{64}", value) is None for value in hashes)
            or len(headers) != report["processedScans"]
            or report["processedScans"] + report["skippedUnbracketedScans"] != report["recordedScans"]
            or report["totalPreparedPoints"] != int(counts[:, 1].sum())):
        raise ValueError("complete_finite_arm64_preprocessing_index_required")
    total = int(counts[:, 1].sum())
    if ((path / "private-prepared-xyzt.f64").stat().st_size != total * 4 * 8
            or (path / "private-neighbors.i32").stat().st_size != total * 10 * 4):
        raise ValueError("whole_arm64_prepared_arrays_required")
    points = np.memmap(path / "private-prepared-xyzt.f64", mode="r", dtype="<f8").reshape(total, 4)
    neighbors = np.memmap(path / "private-neighbors.i32", mode="r", dtype="<i4").reshape(total, 10)
    offsets = np.concatenate(([0], np.cumsum(counts[:, 1])))
    for ordinal, (begin, end) in enumerate(zip(offsets[:-1], offsets[1:])):
        if (not np.isfinite(points[begin:end]).all() or np.any(np.diff(points[begin:end, 3]) < 0)
                or np.any(points[begin:end, 3] < 0) or np.any(points[begin:end, 3] > 0.2)
                or np.any(neighbors[begin:end] < 0) or np.any(neighbors[begin:end] >= counts[ordinal, 1])):
            raise ValueError("finite_complete_arm64_points_and_neighbors_required")
    with np.load(path / "private-metrics.npz", allow_pickle=False) as archive:
        metrics = {name: archive[name].copy() for name in archive}
    validate_metrics(metrics, len(headers))
    for name in ("processingWallSeconds", "processingCpuSeconds", "totalWallSeconds", "peakResidentMiB"):
        if type(report[name]) not in (int, float) or not math.isfinite(report[name]) or report[name] <= 0:
            raise ValueError("finite_positive_observed_resource_values_required")
    return {"report": report, "index": index, "points": points, "neighbors": neighbors,
            "offsets": offsets, "metrics": metrics}


def validate_metrics(metrics, count):
    if (set(metrics) != {"call_wall_ns", "call_cpu_ns"}
            or any(value.dtype != np.dtype("int64") or value.shape != (count,) for value in metrics.values())
            or np.any(metrics["call_wall_ns"] <= 0) or np.any(metrics["call_cpu_ns"] < 0)):
        raise ValueError("complete_nonnegative_integer_call_costs_required")


def timing_summary(value):
    metrics, report = value["metrics"], value["report"]
    headers = value["index"]["header_stamps_ns"]
    return {
        "callWallMillisecondsP50P95P99": np.percentile(metrics["call_wall_ns"] / 1e6, [50, 95, 99]).tolist(),
        "callWallMillisecondsMaximum": float(metrics["call_wall_ns"].max() / 1e6),
        "callCpuMillisecondsP50P95P99": np.percentile(metrics["call_cpu_ns"] / 1e6, [50, 95, 99]).tolist(),
        "completeCallWallSeconds": float(metrics["call_wall_ns"].sum() / 1e9),
        "completeCallCpuSeconds": float(metrics["call_cpu_ns"].sum() / 1e9),
        "processedHeaderSpanSeconds": (int(headers[-1]) - int(headers[0])) / 1e9,
        "processedHeaderIntervalMillisecondsP50P95P99": np.percentile(
            np.diff(headers) / 1e6, [50, 95, 99]).tolist(),
        **{name: report[name] for name in (
            "processingWallSeconds", "processingCpuSeconds", "totalWallSeconds", "peakResidentMiB")},
        "callIncludesNativeWrapperAllocationAndCopy": True,
        "processingLoopIncludesCloudReadDecodeHashAndPrivateOutput": True,
        "doesNotMeasureLiveEndToEndOrVoiceConcurrency": True,
    }


def validate_loaded_libraries(observed, known):
    if (not observed or any(name not in known or known[name] != digest for name, digest in observed.items())
            or not {"libglim.so", "liblingxi_glim_preprocess.so"}.issubset(observed)
            or not any(name == "libgtsam_points.so" or name.startswith("libgtsam_points.so.")
                       for name in observed)):
        raise ValueError("actual_verified_preprocessing_libraries_required")
    return {name: known[name] for name in sorted(observed)}


def export(args):
    manifest = verify_preprocess_bundle(args.bundle)
    core = verify_bundle(args.core_bundle)
    host_evidence = json.loads((ROOT / "docs/slam-preprocess-repeatability-evidence.json").read_text())
    require_hash(args.bundle / "host-repeatability-evidence.json",
                 sha256(ROOT / "docs/slam-preprocess-repeatability-evidence.json"))
    require_hash(args.core_bundle / "bundle-manifest.json", manifest["coreBundleManifestSha256"])
    require_hash(args.adapter_build / "build-report.json", manifest["adapterBuildReportSha256"])
    require_hash(args.bundle / "adapter-build-report.json", manifest["adapterBuildReportSha256"])
    build = json.loads((args.adapter_build / "build-report.json").read_text())
    if (build["result"] != "arm64_preprocessing_adapter_built_not_executed"
            or build["sourceCommits"] != manifest["sourceCommits"]
            or build["crossCoreReportSha256"] != core["crossBuildReportSha256"]
            or build["sourceSha256"] != host_evidence["adapterSourcesSha256"]
            or build["artifact"]["machine"] != "AArch64"):
        raise ValueError("actual_unchanged_native_arm64_build_provenance_required")
    require_hash(args.bundle / "lib/liblingxi_glim_preprocess.so", build["artifact"]["sha256"])
    for name, digest in build["sourceSha256"].items():
        require_hash(args.adapter_build / "source" / name, digest)
        require_hash(ROOT / "tools/glim-preprocess" / name, digest)
    for name in manifest["files"]:
        if name.startswith("tools/"):
            require_hash(ROOT / name, manifest["files"][name]["sha256"])
    validate_build(args.host_core, args.host_adapter)
    require_hash(args.host_adapter / "build-report.json", host_evidence["adapterBuildReportSha256"])
    require_hash(args.host_results / "collection-report.json", host_evidence["collectionReportSha256"])
    host_collection = json.loads((args.host_results / "collection-report.json").read_text())
    collection = json.loads((args.results / "collection-report.json").read_text())
    conditions = {f"{dataset}-threads{threads}-repeat{repeat}": (dataset, threads, repeat)
                  for dataset, threads, repeat in CONDITIONS}
    if (collection["result"] != "all_twelve_arm64_preprocessing_observations_completed_not_slam_acceptance"
            or collection["purpose"] != "actual_arm64_twelve_preprocessing_observations"
            or set(collection["runs"]) != set(conditions)
            or collection["coordinatorSourceSha256"] != sha256(ROOT / "tools/run_glim_target_preprocess.py")
            or collection["bundleManifestSha256"] != sha256(args.bundle / "preprocess-manifest.json")):
        raise ValueError("all_predeclared_arm64_processes_required")
    capture = json.loads((args.bundle / "capture-manifest.json").read_text())
    require_hash(args.bundle / "capture-manifest.json", sha256(ROOT / "docs/slam-capture-evidence.json"))
    verify(capture, args.data_root)
    target = {name: load_target(args.results / name, condition, collection["runs"][name], manifest, args.bundle)
              for name, condition in conditions.items()}
    host = {name: load_observation(args.host_results / name, condition, host_collection["runs"][name])
            for name, condition in conditions.items()}
    if len({value["report"]["pid"] for value in target.values()}) != 12:
        raise ValueError("twelve_fresh_arm64_processes_required")
    known_libraries = {Path(name).name: entry["sha256"] for name, entry in core["files"].items()
                       if name.startswith("lib/")}
    known_libraries["liblingxi_glim_preprocess.so"] = build["artifact"]["sha256"]
    evidence = {
        "schemaVersion": 1, "observedDate": "2026-10-28",
        "purpose": "actual_arm64_preprocessing_repetition_and_observed_cost_not_navigation_acceptance",
        "sourceCommits": manifest["sourceCommits"],
        "hostEvidenceSha256": sha256(ROOT / "docs/slam-preprocess-repeatability-evidence.json"),
        "captureManifestSha256": sha256(args.bundle / "capture-manifest.json"),
        "bundleManifestSha256": sha256(args.bundle / "preprocess-manifest.json"),
        "coreBundleManifestSha256": manifest["coreBundleManifestSha256"],
        "adapterBuildReportSha256": manifest["adapterBuildReportSha256"],
        "nativeAdapterSha256": build["artifact"]["sha256"],
        "nativeSourceSha256": build["sourceSha256"],
        "unchangedConfigurationManifestSha256": host_evidence["unchangedConfigurationManifestSha256"],
        "collectionReportSha256": sha256(args.results / "collection-report.json"),
        "coordinatorSourceSha256": collection["coordinatorSourceSha256"],
        "runnerSourceSha256": sha256(args.bundle / "tools/glim_target_preprocess.py"),
        "exporterSourceSha256": sha256(__file__),
        "freshArm64ProcessCount": 12, "repeatsPerCondition": 3,
        "robotContactedForIsolatedRecordedReplay": True,
        "controlIsNotProductionConfiguration": True,
        "runs": {}, "withinTargetConditionPairs": {}, "crossArchitectureSameConditionPairs": {},
        "boundaries": {name: False for name in BOUNDARIES},
    }
    for name, value in target.items():
        report = value["report"]
        require_hash(args.host_results / name / "report.json", host_evidence["runs"][name]["reportSha256"])
        if report["recordedScans"] != capture["datasets"][report["dataset"]]["topicCounts"]["cloud"]:
            raise ValueError("actual_complete_recorded_input_required")
        loaded = validate_loaded_libraries(report["loadedLibrariesSha256"], known_libraries)
        evidence["runs"][name] = {
            **{key: report[key] for key in ("dataset", "diagnosticThreads", "hostMachine", "python", "numpy",
                                           "recordedScans", "processedScans", "skippedUnbracketedScans",
                                           "totalPreparedPoints", "filesSha256")},
            "loadedLibrariesSha256": loaded,
            "reportSha256": collection["runs"][name]["reportSha256"],
            "timing": timing_summary(value),
        }
    for dataset in ("static", "dynamic"):
        names = [name for name, condition in conditions.items() if condition[0] == dataset]
        base = host[names[0]]
        for name in names:
            require_same_inputs(base, host[name])
            require_same_inputs(base, target[name])
        for threads in (2, 1):
            labels = [name for name in names if conditions[name][1] == threads]
            for first, second in itertools.combinations(labels, 2):
                evidence["withinTargetConditionPairs"][first + "_vs_" + second] = compare_observations(
                    target[first], target[second])
            for first, second in itertools.product(labels, labels):
                evidence["crossArchitectureSameConditionPairs"]["arm64-" + first + "_vs_x86-" + second] = (
                    compare_observations(target[first], host[second]))
    for group, directory, files in ((target, args.results, FILES),
                                    (host, args.host_results, tuple(host_evidence["runs"][next(iter(host))]["filesSha256"]))):
        for name, value in group.items():
            for file in files:
                require_hash(directory / name / file, value["report"]["filesSha256"][file])
    verify(capture, args.data_root)
    evidence["allOriginalCapturesAndCompletePrivateOutputsUnchangedAtExport"] = True
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "results", "core-bundle", "adapter-build", "host-core", "host-adapter",
                 "host-results", "data-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("existing_arm64_preprocessing_evidence_is_preserved")
    result = export(args)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"result": "complete_arm64_cloud_and_cost_evidence_recomputed"}))


if __name__ == "__main__":
    main()
