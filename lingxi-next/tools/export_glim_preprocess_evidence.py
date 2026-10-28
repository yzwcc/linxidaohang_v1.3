"""Recompute whole-cloud repeatability and whitelist aggregates, never point coordinates."""
import argparse
import itertools
import json
from pathlib import Path
import re

import numpy as np

from build_slam_cpu_trial import PINS
from compare_glim_architectures import ordered_stamps, require_hash
from export_glim_mapping_evidence import RUNS
from run_glim_preprocess_trial import BOUNDARIES, CONDITIONS, ROOT, SOURCE_PINS, validate_build
from verify_slam_capture import sha256, verify


FILES = ("private-index.npz", "private-prepared-xyzt.f64", "private-neighbors.i32", "private-native.log")


def load_observation(path, expected, collection):
    require_hash(path / "report.json", collection["reportSha256"])
    report = json.loads((path / "report.json").read_text())
    dataset, threads, _ = expected
    if (collection["exitCode"] != 0
            or report["result"] != "complete_native_preprocess_recording_saved_not_slam_acceptance"
            or report["dataset"] != dataset or report["diagnosticThreads"] != threads
            or report["hostMachine"] != "x86_64"
            or report["runnerSourceSha256"] != sha256(ROOT / "tools/run_glim_preprocess_trial.py")
            or report["boundaries"] != {name: False for name in BOUNDARIES}
            or report["originalCapturesUnchanged"] is not True
            or type(report["pid"]) is not int or report["pid"] <= 0
            or set(report["filesSha256"]) != set(FILES)):
        raise ValueError("completed_native_preprocess_observation_required")
    for name in FILES:
        require_hash(path / name, report["filesSha256"][name])
    text = (path / "private-native.log").read_text().lower()
    if any(token in text for token in ("[error]", "[critical]", "terminate called", "too few points")):
        raise ValueError("native_preprocessing_log_contains_failure")
    with np.load(path / "private-index.npz", allow_pickle=False) as archive:
        if set(archive) != {"counts", "header_stamps_ns", "algorithm_stamps_relative_seconds", "input_cloud_sha256"}:
            raise ValueError("complete_preprocessing_index_required")
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
        raise ValueError("complete_finite_preprocessing_index_required")
    total = int(counts[:, 1].sum())
    if ((path / "private-prepared-xyzt.f64").stat().st_size != total * 4 * 8
            or (path / "private-neighbors.i32").stat().st_size != total * 10 * 4):
        raise ValueError("complete_native_cloud_and_neighbor_arrays_required")
    points = np.memmap(path / "private-prepared-xyzt.f64", mode="r", dtype="<f8").reshape(total, 4)
    neighbors = np.memmap(path / "private-neighbors.i32", mode="r", dtype="<i4").reshape(total, 10)
    offsets = np.concatenate(([0], np.cumsum(counts[:, 1])))
    for ordinal, (begin, end) in enumerate(zip(offsets[:-1], offsets[1:])):
        if (not np.isfinite(points[begin:end]).all() or np.any(np.diff(points[begin:end, 3]) < 0)
                or np.any(points[begin:end, 3] < 0) or np.any(points[begin:end, 3] > 0.2)
                or np.any(neighbors[begin:end] < 0) or np.any(neighbors[begin:end] >= counts[ordinal, 1])):
            raise ValueError("finite_ordered_complete_prepared_frames_and_neighbors_required")
    return {"report": report, "index": index, "points": points, "neighbors": neighbors, "offsets": offsets}


def same_bit_rows(first, second, ignore_order=False):
    if first.shape != second.shape:
        return False
    first, second = np.ascontiguousarray(first).view("<u8"), np.ascontiguousarray(second).view("<u8")
    if ignore_order:
        first = first[np.lexsort(tuple(first[:, column] for column in (3, 2, 1, 0)))]
        second = second[np.lexsort(tuple(second[:, column] for column in (3, 2, 1, 0)))]
    return np.array_equal(first, second)


def require_same_inputs(first, second):
    left, right = first["index"], second["index"]
    for name in ("header_stamps_ns", "algorithm_stamps_relative_seconds", "input_cloud_sha256"):
        if not np.array_equal(left[name], right[name]):
            raise ValueError("identical_ordered_raw_input_and_time_domain_required")
    if not np.array_equal(left["counts"][:, 0], right["counts"][:, 0]):
        raise ValueError("identical_raw_cloud_sizes_required")


def compare_observations(first, second):
    require_same_inputs(first, second)
    left, right = first["index"], second["index"]
    ordered_different = selection_different = order_only = neighbors_different = inactive_different = 0
    active = 10000.0 / left["counts"][:, 0] < 0.99
    for ordinal in range(len(active)):
        a, b = (value["points"][value["offsets"][ordinal]:value["offsets"][ordinal + 1]]
                for value in (first, second))
        equal_ordered = same_bit_rows(a, b)
        if not equal_ordered:
            ordered_different += 1
            inactive_different += not bool(active[ordinal])
            if same_bit_rows(a, b, ignore_order=True):
                order_only += 1
            else:
                selection_different += 1
        else:
            a, b = (value["neighbors"][value["offsets"][ordinal]:value["offsets"][ordinal + 1]]
                    for value in (first, second))
            neighbors_different += not np.array_equal(a, b)
    return {
        "processedFrames": len(active), "sourceDerivedSamplingActiveFrames": int(active.sum()),
        "preparedPointCountDifferentFrames": int(np.count_nonzero(left["counts"][:, 1] != right["counts"][:, 1])),
        "orderedXyztBitSequenceDifferentFrames": ordered_different,
        "sameExactXyztMultisetButDifferentOrderFrames": order_only,
        "exactXyztMultisetDifferentFrames": selection_different,
        "samplingInactiveButOrderedOutputDifferentFrames": inactive_different,
        "neighborIndexSequenceDifferentFramesAmongIdenticalOrderedXyzt": neighbors_different,
        "allRawInputContentAndTimesIdentical": True,
        "sameExactBitPatternsComparedNotGeometricTolerance": True,
    }


def historical_reference(path, public):
    require_hash(path / "report.json", public["baseReplayReportSha256"])
    report = json.loads((path / "report.json").read_text())
    require_hash(path / "private-trajectory.npz", report["privateTrajectorySha256"])
    with np.load(path / "private-trajectory.npz", allow_pickle=False) as archive:
        return {
            "headers": archive["header_stamps_ns"].copy(),
            "times": archive["algorithm_stamps_relative_seconds"].copy(),
            "preparedCounts": archive["preprocess_odometry_pointcount_marginalized"][:, 2].copy(),
            "initializationScans": report["initializationScans"],
            "reportSha256": sha256(path / "report.json"),
        }


def compare_historical(observation, reference):
    index = observation["index"]
    initialized = reference["initializationScans"]
    if (not np.array_equal(index["header_stamps_ns"][initialized:], reference["headers"])
            or not np.array_equal(index["algorithm_stamps_relative_seconds"][initialized:], reference["times"])
            or index["counts"].shape[0] != len(reference["preparedCounts"])):
        raise ValueError("historical_preprocessing_frame_domain_mismatch")
    return {
        "historicalReportSha256": reference["reportSha256"],
        "differentPreparedPointCountFrames": int(np.count_nonzero(
            index["counts"][:, 1] != reference["preparedCounts"])),
        "historicalPreparedPointContentWasNotRecorded": True,
        "countsDoNotProvePointIdentityOrCausality": True,
    }


def export(args):
    collection_path = args.results / "collection-report.json"
    collection = json.loads(collection_path.read_text())
    expected_labels = {f"{dataset}-threads{threads}-repeat{repeat}": (dataset, threads, repeat)
                       for dataset, threads, repeat in CONDITIONS}
    if (collection["result"] != "all_twelve_preprocessing_observations_completed_not_slam_acceptance"
            or set(collection["runs"]) != set(expected_labels)
            or collection["runnerSourceSha256"] != sha256(ROOT / "tools/run_glim_preprocess_trial.py")):
        raise ValueError("all_predeclared_complete_preprocessing_observations_required")
    adapter = validate_build(args.core, args.adapter)
    capture = ROOT / "docs/slam-capture-evidence.json"
    manifest = json.loads(capture.read_text())
    verify(manifest, args.data_root)
    host = json.loads((ROOT / "docs/slam-glim-mapping-evidence.json").read_text())
    target = json.loads((ROOT / "docs/slam-arm64-mapping-evidence.json").read_text())
    observations = {label: load_observation(args.results / label, condition, collection["runs"][label])
                    for label, condition in expected_labels.items()}
    if len({value["report"]["pid"] for value in observations.values()}) != len(observations):
        raise ValueError("fresh_independent_processes_required")
    config = args.host_cache / RUNS["dynamicEnd"][2] / "config"
    config_digest = sha256(config / "trial-manifest.json")
    config_manifest = json.loads((config / "trial-manifest.json").read_text())
    for name, digest in config_manifest["files"].items():
        require_hash(config / name, digest)
    if any(value["report"]["configManifestSha256"] != config_digest for value in observations.values()):
        raise ValueError("all_conditions_must_use_identical_unchanged_configuration_files")
    evidence = {
        "schemaVersion": 1, "observedDate": "2026-10-28",
        "purpose": "x86_native_preprocessing_repeatability_not_slam_or_cross_architecture_acceptance",
        "sourceCommits": PINS,
        "sourcePinsSha256": SOURCE_PINS, "captureManifestSha256": sha256(capture),
        "adapterBuildReportSha256": sha256(args.adapter / "build-report.json"),
        "nativeAdapterSha256": sha256(adapter),
        "adapterSourcesSha256": {
            name: sha256(args.adapter / "source" / name) for name in ("preprocess.cpp", "CMakeLists.txt")},
        "collectionReportSha256": sha256(collection_path),
        "runnerSourceSha256": collection["runnerSourceSha256"],
        "unchangedConfigurationManifestSha256": config_digest,
        "exporterSourceSha256": sha256(Path(__file__)), "freshProcessCount": len(observations),
        "baselineThreads": 2, "diagnosticControlThreads": 1, "repeatsPerCondition": 3,
        "controlIsNotProductionConfiguration": True,
        "runs": {}, "withinConditionPairs": {}, "historicalCountComparisons": {},
        "boundaries": {name: False for name in BOUNDARIES},
    }
    for dataset in ("static", "dynamic"):
        label = dataset + "End"
        references = {
            "historicalHost": historical_reference(args.host_cache / RUNS[label][2], host["runs"][label]),
            "historicalTarget": historical_reference(args.target_results / label, target["runs"][label]),
        }
        dataset_observations = [observations[name] for name, condition in expected_labels.items()
                                if condition[0] == dataset]
        for observation in dataset_observations[1:]:
            require_same_inputs(dataset_observations[0], observation)
        for threads in (2, 1):
            labels = [name for name, condition in expected_labels.items()
                      if condition[:2] == (dataset, threads)]
            for first, second in itertools.combinations(labels, 2):
                evidence["withinConditionPairs"][first + "_vs_" + second] = compare_observations(
                    observations[first], observations[second])
            for name in labels:
                value = observations[name]
                report, index = value["report"], value["index"]
                if (report["captureManifestSha256"] != sha256(capture)
                        or report["adapterBuildReportSha256"] != evidence["adapterBuildReportSha256"]
                        or report["recordedScans"] != manifest["datasets"][dataset]["topicCounts"]["cloud"]):
                    raise ValueError("recorded_preprocessing_input_provenance_mismatch")
                evidence["runs"][name] = {
                    "dataset": dataset, "diagnosticThreads": threads,
                    **{key: report[key] for key in (
                        "recordedScans", "processedScans", "skippedUnbracketedScans", "totalPreparedPoints")},
                    "rawPointCountMinMax": [int(index["counts"][:, 0].min()), int(index["counts"][:, 0].max())],
                    "preparedPointCountMinMax": [int(index["counts"][:, 1].min()), int(index["counts"][:, 1].max())],
                    "sourceDerivedSamplingActiveFrames": int((10000.0 / index["counts"][:, 0] < 0.99).sum()),
                    "filesSha256": {key: report["filesSha256"][key] for key in FILES},
                    "reportSha256": collection["runs"][name]["reportSha256"],
                }
                evidence["historicalCountComparisons"][name] = {
                    kind: compare_historical(value, reference) for kind, reference in references.items()
                }
    for label, value in observations.items():
        for name in FILES:
            require_hash(args.results / label / name, value["report"]["filesSha256"][name])
    evidence["allPrivateOutputsUnchangedAtExport"] = True
    evidence["originalCapturesVerifiedUnchangedAtExport"] = True
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("core", "adapter", "results", "data-root", "host-cache", "target-results", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("existing_preprocess_evidence_is_preserved")
    evidence = export(args)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"result": "twelve_native_preprocessing_outputs_recomputed_and_whitelisted"}))


if __name__ == "__main__":
    main()
