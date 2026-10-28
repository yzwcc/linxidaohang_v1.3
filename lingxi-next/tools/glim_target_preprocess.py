"""Full recorded ARM64 preprocessing outputs; no ROS initialization or movement."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import resource
import time

import numpy as np

from build_slam_cpu_trial import PINS
from diagnose_slam_sensors import stamp_ns
from glim_target_replay import recorded_messages, validate_environment
from run_glim_preprocess_trial import Native, PREPROCESS_CONFIG_SHA256, save
from run_kiss_offline_trial import cloud_arrays
from slam_bag_audit import CLOUD, IMU
from slam_target_probe import verify_bundle
from verify_slam_capture import sha256, verify


PURPOSE = "arm64_recorded_native_preprocessing_repeatability"
FILES = ("private-index.npz", "private-prepared-xyzt.f64", "private-neighbors.i32",
         "private-metrics.npz", "private-native.log")
BOUNDARIES = (
    "rosNodeStarted", "liveSensorsSubscribed", "motionCommandsSent",
    "odometryExecuted", "globalMappingExecuted", "historicalDivergenceFullyExplained",
    "timingHypothesisValidated", "robotCalibrationValidated", "navigationValidated",
    "relocalizationValidated", "realtimePerformanceValidated", "voiceConcurrencyValidated",
    "deploymentApproved", "rawDataPublished",
)


def verify_preprocess_bundle(root):
    manifest = json.loads((root / "preprocess-manifest.json").read_text())
    if (manifest["purpose"] != PURPOSE or manifest["sourceCommits"] != PINS
            or manifest["timingHypothesis"] != "header_is_scan_end"
            or manifest["controlIsNotProductionConfiguration"] is not True
            or manifest["navigationValidated"] is not False):
        raise ValueError("explicit_recorded_preprocessing_bundle_required")
    for name, entry in manifest["files"].items():
        relative = PurePosixPath(name)
        path = root / name
        if (relative.is_absolute() or ".." in relative.parts or "\\" in name
                or path.is_symlink() or not path.is_file()
                or not path.resolve().is_relative_to(root.resolve())
                or path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]):
            raise ValueError("preprocessing_bundle_integrity_mismatch")
    host = json.loads((root / "host-repeatability-evidence.json").read_text())
    if (sha256(root / "config/trial-manifest.json") != host["unchangedConfigurationManifestSha256"]
            or sha256(root / "tools/run_glim_preprocess_trial.py") != host["runnerSourceSha256"]
            or sha256(root / "config/config_preprocess.json") != PREPROCESS_CONFIG_SHA256):
        raise ValueError("same_host_preprocessing_source_and_configuration_required")
    for name, digest in host["adapterSourcesSha256"].items():
        if sha256(root / "source" / name) != digest:
            raise ValueError("unchanged_native_preprocessing_source_required")
    return manifest


def loaded_libraries(root, core_root, core_manifest, text):
    expected = {Path(name).name: core_root / name for name in core_manifest["files"] if name.startswith("lib/")}
    expected["liblingxi_glim_preprocess.so"] = root / "lib/liblingxi_glim_preprocess.so"
    found = {}
    for line in text.splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6:
            continue
        path = Path(fields[5])
        if path.name not in expected:
            continue
        if path.resolve() != expected[path.name].resolve():
            raise ValueError("native_preprocessing_library_outside_verified_directories")
        found[path.name] = sha256(path)
    point_aliases = {name for name in expected if name == "libgtsam_points.so"
                     or name.startswith("libgtsam_points.so.")}
    if (not {"libglim.so", "liblingxi_glim_preprocess.so"}.issubset(found)
            or not point_aliases.intersection(found)):
        raise ValueError("actual_native_preprocessing_libraries_required")
    return found


def process_recording(bag, expected, native, points_stream, neighbors_stream,
                      messages=recorded_messages):
    stamps = []
    for message in messages(bag, IMU):
        if message.header.frame_id != "lidar_imu_chest_front":
            raise ValueError("recorded_imu_time_frame_required")
        stamps.append(stamp_ns(message))
    imu = np.asarray(stamps, dtype=np.int64)
    if len(imu) != expected["topicCounts"]["imu"] or not len(imu) or np.any(np.diff(imu) <= 0):
        raise ValueError("complete_ordered_recorded_imu_times_required")
    origin = int(imu[0])
    imu_times = (imu - origin).astype(np.float64) / 1e9 + 1000
    counts, headers, algorithm, raw_hashes, wall, cpu = [], [], [], [], [], []
    seen = skipped = 0
    last_stamp = None
    started, cpu_started = time.monotonic(), time.process_time()
    for message in messages(bag, CLOUD):
        seen += 1
        stamp = stamp_ns(message)
        if (message.header.frame_id != "lidar_chest_front"
                or (last_stamp is not None and stamp <= last_stamp)):
            raise ValueError("ordered_recorded_lidar_frame_required")
        last_stamp = stamp
        points, offsets, dropped = cloud_arrays(message)
        if dropped:
            raise ValueError("complete_finite_recorded_cloud_required")
        frame_stamp = (stamp - origin) / 1e9 + 1000 - offsets.max()
        if frame_stamp < imu_times[0] or frame_stamp + offsets.max() > imu_times[-1]:
            skipped += 1
            continue
        begin, cpu_begin = time.perf_counter_ns(), time.process_time_ns()
        prepared, neighbors = native.frame(frame_stamp, points, offsets)
        cpu.append(time.process_time_ns() - cpu_begin)
        wall.append(time.perf_counter_ns() - begin)
        prepared.tofile(points_stream)
        neighbors.tofile(neighbors_stream)
        counts.append((len(points), len(prepared)))
        headers.append(stamp)
        algorithm.append(frame_stamp)
        raw_hashes.append(hashlib.sha256(points.tobytes() + offsets.tobytes()).hexdigest())
    if seen != expected["topicCounts"]["cloud"] or len(counts) < 2:
        raise ValueError("all_recorded_preprocessing_scans_required")
    return {
        "recordedScans": seen, "processedScans": len(counts),
        "skippedUnbracketedScans": skipped,
        "processingWallSeconds": time.monotonic() - started,
        "processingCpuSeconds": time.process_time() - cpu_started,
    }, {
        "counts": np.asarray(counts, dtype=np.int64),
        "header_stamps_ns": np.asarray(headers, dtype=np.int64),
        "algorithm_stamps_relative_seconds": np.asarray(algorithm, dtype=np.float64),
        "input_cloud_sha256": np.asarray(raw_hashes),
    }, {
        "call_wall_ns": np.asarray(wall, dtype=np.int64),
        "call_cpu_ns": np.asarray(cpu, dtype=np.int64),
    }


def run(args):
    validate_environment(args.root, args.core_root)
    if args.output.exists() or args.output.parent != args.root or args.threads not in (1, 2):
        raise ValueError("new_private_predeclared_preprocessing_trial_required")
    manifest = verify_preprocess_bundle(args.root)
    core_manifest = verify_bundle(args.core_root)
    if sha256(args.core_root / "bundle-manifest.json") != manifest["coreBundleManifestSha256"]:
        raise ValueError("unchanged_isolated_cpu_core_required")
    capture = json.loads((args.root / "capture-manifest.json").read_text())
    integrity = verify(capture, args.data_root)
    args.output.mkdir(mode=0o700)
    started = time.monotonic()
    report = {
        "purpose": PURPOSE, "result": "running", "dataset": args.dataset,
        "diagnosticThreads": args.threads, "pid": os.getpid(),
        "hostMachine": platform.machine(), "python": platform.python_version(), "numpy": np.__version__,
        "runnerSourceSha256": sha256(__file__),
        "bundleManifestSha256": sha256(args.root / "preprocess-manifest.json"),
        "captureManifestSha256": sha256(args.root / "capture-manifest.json"),
        "configManifestSha256": sha256(args.root / "config/trial-manifest.json"),
        "nativeAdapterSha256": sha256(args.root / "lib/liblingxi_glim_preprocess.so"),
        "boundaries": {key: False for key in BOUNDARIES},
    }
    native = None
    stdout_copy, stderr_copy = os.dup(1), os.dup(2)
    try:
        with ((args.output / "private-native.log").open("x") as log,
              (args.output / "private-prepared-xyzt.f64").open("xb") as points,
              (args.output / "private-neighbors.i32").open("xb") as neighbors):
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            native = Native(args.root / "lib/liblingxi_glim_preprocess.so", args.root / "config", args.threads)
            report["loadedLibrariesSha256"] = loaded_libraries(
                args.root, args.core_root, core_manifest, Path("/proc/self/maps").read_text())
            expected = capture["datasets"][args.dataset]
            summary, index, metrics = process_recording(
                args.data_root / expected["directoryName"], expected, native, points, neighbors)
            native.close()
            native = None
        np.savez_compressed(args.output / "private-index.npz", **index)
        np.savez_compressed(args.output / "private-metrics.npz", **metrics)
        if integrity != verify(capture, args.data_root):
            raise ValueError("original_captures_changed")
        report.update(summary)
        report.update(
            result="complete_arm64_native_preprocessing_not_slam_acceptance",
            originalCapturesUnchanged=True,
            totalPreparedPoints=int(index["counts"][:, 1].sum()),
            peakResidentMiB=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            totalWallSeconds=time.monotonic() - started,
            filesSha256={name: sha256(args.output / name) for name in FILES},
        )
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        try:
            if native is not None:
                native.close()
        finally:
            os.dup2(stdout_copy, 1)
            os.dup2(stderr_copy, 2)
            os.close(stdout_copy)
            os.close(stderr_copy)
            save(args.output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "core-root", "data-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--dataset", choices=("static", "dynamic"), required=True)
    parser.add_argument("--threads", type=int, choices=(1, 2), required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    old_umask = os.umask(0o077)
    try:
        result = run(args)
        print(json.dumps({"result": result["result"], "processedScans": result["processedScans"]}))
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
