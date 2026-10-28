"""Isolated recorded-cloud preprocessing repeatability; never SLAM or robot control."""
import argparse
import ctypes
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

import numpy as np

from build_slam_cpu_trial import PINS
from compare_glim_architectures import require_hash
from diagnose_slam_sensors import stamp_ns
from run_glim_mapping_trial import core_hashes
from run_glim_offline_trial import library_environment
from run_kiss_offline_trial import cloud_arrays
from slam_bag_audit import CLOUD, IMU
from verify_slam_capture import sha256, verify


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools/glim-preprocess"
PREPROCESS_CONFIG_SHA256 = "68d2996d4cdab4c4daace3647e6eed934092cc6529aefd0dc71d4409226eb31d"
SOURCE_PINS = {
    "glim/src/glim/preprocess/cloud_preprocessor.cpp":
        "7cd07271d1e500abbbc868168678f7addcffc057485b3c0f9fa0c7be8ccbb5b2",
    "glim/include/glim/preprocess/cloud_preprocessor.hpp":
        "148f746058e8b1cc6018f110fb1ceeda3c68598a5818d0acdf14beaaf8a9c67f",
    "gtsam_points/src/gtsam_points/types/point_cloud_cpu_funcs.cpp":
        "b4d9966781698ff682b1eae29b9c67709c14d2729aa7771303d5ab85a4a2389c",
    "gtsam_points/include/gtsam_points/util/sort_omp.hpp":
        "16eb260e9f1711d2138091c9d0ecff5eeee6acab8e8f866037aa541dd8209db0",
}
CONDITIONS = tuple((dataset, threads, repeat)
                   for dataset in ("static", "dynamic")
                   for threads in (2, 1) for repeat in range(1, 4))
BOUNDARIES = (
    "robotContacted", "rosNodeStarted", "odometryExecuted", "globalMappingExecuted",
    "historicalDivergenceFullyExplained", "crossArchitecturePreprocessingAccepted",
    "timingHypothesisValidated", "robotCalibrationValidated", "navigationValidated",
    "realtimePerformanceValidated", "deploymentApproved", "rawDataPublished",
)


def save(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False) + "\n")


def environment_check():
    if (platform.system() != "Linux" or platform.machine() != "x86_64"
            or os.geteuid() == 0 or sys.prefix == sys.base_prefix
            or "LD_PRELOAD" in os.environ):
        raise ValueError("ordinary_user_isolated_native_linux_environment_required")


def new_output(output, inputs):
    if output.exists():
        raise ValueError("existing_preprocess_output_is_preserved")
    if any(output == path or path in output.parents for path in inputs):
        raise ValueError("preprocess_output_must_be_outside_input_directories")


def build(core, upstream, output):
    environment_check()
    new_output(output, (core, upstream))
    core_report = json.loads((core / "build-report.json").read_text())
    if core_report["sourceCommits"] != PINS or core_report["result"] != "core_libraries_built_not_target_acceptance":
        raise ValueError("existing_pinned_cpu_core_required")
    for relative, digest in SOURCE_PINS.items():
        require_hash(upstream / relative, digest)
    output.mkdir(mode=0o700, parents=True)
    report = {
        "purpose": "native_recorded_preprocessing_diagnostic_build",
        "sourceCommits": PINS, "coreBuildReportSha256": sha256(core / "build-report.json"),
        "coreLibrariesSha256": core_hashes(core), "upstreamSourcesSha256": SOURCE_PINS,
        "adapterSourcesSha256": {}, "stages": [],
    }
    try:
        source = output / "source"
        source.mkdir(mode=0o700)
        for name in ("preprocess.cpp", "CMakeLists.txt"):
            shutil.copyfile(SOURCE / name, source / name)
            report["adapterSourcesSha256"][name] = sha256(source / name)
        for name, command in (
            ("configure", ["cmake", "-S", str(source), "-B", str(output / "build")]),
            ("build", ["cmake", "--build", str(output / "build"), "--parallel", "2"]),
        ):
            with (output / (name + ".log")).open("x") as log:
                result = subprocess.run(command, env=library_environment(core), stdout=log, stderr=log, timeout=180)
            report["stages"].append({"name": name, "exitCode": result.returncode})
            if result.returncode:
                raise RuntimeError("native_preprocess_build_failed_see_private_logs")
        report.update(result="native_preprocess_adapter_built_not_executed",
                      librarySha256=sha256(output / "build/liblingxi_glim_preprocess.so"))
    except BaseException:
        report["result"] = "failed_or_interrupted"
        raise
    finally:
        save(output / "build-report.json", report)
    return report


def validate_build(core, adapter):
    report = json.loads((adapter / "build-report.json").read_text())
    if (report["result"] != "native_preprocess_adapter_built_not_executed"
            or report["sourceCommits"] != PINS
            or report["coreBuildReportSha256"] != sha256(core / "build-report.json")
            or report["coreLibrariesSha256"] != core_hashes(core)
            or report["upstreamSourcesSha256"] != SOURCE_PINS):
        raise ValueError("preprocess_build_provenance_mismatch")
    for name, digest in report["adapterSourcesSha256"].items():
        require_hash(adapter / "source" / name, digest)
        require_hash(SOURCE / name, digest)
    library = adapter / "build/liblingxi_glim_preprocess.so"
    require_hash(library, report["librarySha256"])
    return library


class Native:
    def __init__(self, library, config, threads):
        self.library = ctypes.CDLL(str(library))
        doubles = ctypes.POINTER(ctypes.c_double)
        integers = ctypes.POINTER(ctypes.c_int32)
        self.library.lingxi_preprocess_error.restype = ctypes.c_char_p
        self.library.lingxi_preprocess_open.argtypes = [ctypes.c_char_p, ctypes.c_int]
        self.library.lingxi_preprocess_frame.argtypes = [
            ctypes.c_double, ctypes.c_int, doubles, doubles, doubles, integers, ctypes.c_int,
        ]
        self.library.lingxi_preprocess_close.argtypes = []
        self.check(self.library.lingxi_preprocess_open(os.fsencode(config), threads))

    def check(self, result):
        if result < 0:
            raise RuntimeError(self.library.lingxi_preprocess_error().decode("utf-8", errors="replace"))
        return result

    def frame(self, stamp, points, offsets):
        output = np.empty((len(points), 4), dtype="<f8")
        neighbors = np.empty((len(points), 10), dtype="<i4")
        pointer = lambda array: array.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        count = self.check(self.library.lingxi_preprocess_frame(
            stamp, len(points), pointer(points), pointer(offsets), pointer(output),
            neighbors.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)), len(points)))
        return output[:count], neighbors[:count]

    def close(self):
        self.check(self.library.lingxi_preprocess_close())


def replay(args):
    from rosbags.highlevel import AnyReader
    from rosbags.typesys import Stores, get_typestore

    environment_check()
    if metadata.version("rosbags") != "0.11.9":
        raise ValueError("pinned_rosbags_reader_required")
    new_output(args.output, (args.core, args.adapter, args.config, args.data_root))
    if os.environ.get("LD_LIBRARY_PATH") != library_environment(args.core)["LD_LIBRARY_PATH"]:
        raise ValueError("explicit_existing_core_library_path_required")
    library = validate_build(args.core, args.adapter)
    require_hash(args.config / "config_preprocess.json", PREPROCESS_CONFIG_SHA256)
    config_manifest = json.loads((args.config / "trial-manifest.json").read_text())
    for name, digest in config_manifest["files"].items():
        require_hash(args.config / name, digest)
    manifest = json.loads(args.manifest.read_text())
    integrity = verify(manifest, args.data_root)
    expected = manifest["datasets"][args.dataset]
    bag = args.data_root / expected["directoryName"]
    with AnyReader([bag], default_typestore=get_typestore(Stores.ROS2_HUMBLE)) as reader:
        stamps = [stamp_ns(reader.deserialize(payload, connection.msgtype))
                  for connection, _, payload in reader.messages(
                      connections=[item for item in reader.connections if item.topic == IMU])]
    imu = np.asarray(stamps, dtype=np.int64)
    if len(imu) != expected["topicCounts"]["imu"] or np.any(np.diff(imu) <= 0):
        raise ValueError("complete_ordered_recorded_imu_time_domain_required")
    imu_times = (imu - int(imu[0])).astype(np.float64) / 1e9 + 1000
    args.output.mkdir(mode=0o700)
    report = {
        "purpose": "native_preprocessing_only_scan_end_hypothesis_not_calibration",
        "result": "running", "dataset": args.dataset, "diagnosticThreads": args.threads,
        "hostMachine": platform.machine(), "pid": os.getpid(),
        "captureManifestSha256": sha256(args.manifest),
        "adapterBuildReportSha256": sha256(args.adapter / "build-report.json"),
        "runnerSourceSha256": sha256(Path(__file__)),
        "configManifestSha256": sha256(args.config / "trial-manifest.json"),
        "boundaries": {name: False for name in BOUNDARIES},
    }
    native = None
    first_copy, second_copy = os.dup(1), os.dup(2)
    counts, headers, algorithm, raw_digests = [], [], [], []
    seen, skipped, last_stamp = 0, 0, None
    started = time.monotonic()
    try:
        with (args.output / "private-native.log").open("x") as log:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            native = Native(library, args.config, args.threads)
            with ((args.output / "private-prepared-xyzt.f64").open("xb") as points_stream,
                  (args.output / "private-neighbors.i32").open("xb") as neighbor_stream,
                  AnyReader([bag], default_typestore=get_typestore(Stores.ROS2_HUMBLE)) as reader):
                connections = [item for item in reader.connections if item.topic == CLOUD]
                for connection, _, payload in reader.messages(connections=connections):
                    message = reader.deserialize(payload, connection.msgtype)
                    seen += 1
                    stamp = stamp_ns(message)
                    if message.header.frame_id != "lidar_chest_front" or (last_stamp is not None and stamp <= last_stamp):
                        raise ValueError("ordered_recorded_lidar_frame_required")
                    last_stamp = stamp
                    points, offsets, dropped = cloud_arrays(message)
                    if dropped:
                        raise ValueError("finite_complete_recorded_input_required")
                    frame_stamp = (stamp - int(imu[0])) / 1e9 + 1000 - offsets.max()
                    if frame_stamp < imu_times[0] or frame_stamp + offsets.max() > imu_times[-1]:
                        skipped += 1
                        continue
                    prepared, neighbors = native.frame(frame_stamp, points, offsets)
                    prepared.tofile(points_stream)
                    neighbors.tofile(neighbor_stream)
                    counts.append((len(points), len(prepared)))
                    headers.append(stamp)
                    algorithm.append(frame_stamp)
                    raw_digests.append(hashlib.sha256(points.tobytes() + offsets.tobytes()).hexdigest())
            native.close()
            native = None
        if seen != expected["topicCounts"]["cloud"] or len(counts) < 2:
            raise ValueError("all_recorded_clouds_accounted_for_required")
        np.savez_compressed(args.output / "private-index.npz", counts=np.asarray(counts, dtype=np.int64),
                            header_stamps_ns=np.asarray(headers, dtype=np.int64),
                            algorithm_stamps_relative_seconds=np.asarray(algorithm),
                            input_cloud_sha256=np.asarray(raw_digests))
        if integrity != verify(manifest, args.data_root):
            raise ValueError("original_recording_changed")
        report.update(
            result="complete_native_preprocess_recording_saved_not_slam_acceptance",
            recordedScans=seen, processedScans=len(counts), skippedUnbracketedScans=skipped,
            totalPreparedPoints=sum(count[1] for count in counts),
            wallSeconds=time.monotonic() - started,
            filesSha256={name: sha256(args.output / name) for name in (
                "private-index.npz", "private-prepared-xyzt.f64", "private-neighbors.i32", "private-native.log")},
            originalCapturesUnchanged=True,
        )
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        try:
            if native is not None:
                native.close()
        finally:
            os.dup2(first_copy, 1)
            os.dup2(second_copy, 2)
            os.close(first_copy)
            os.close(second_copy)
            save(args.output / "report.json", report)
    return report


def run(args):
    environment_check()
    new_output(args.output, (args.core, args.adapter, args.config, args.data_root))
    validate_build(args.core, args.adapter)
    args.output.mkdir(mode=0o700, parents=True)
    report = {"purpose": "twelve_predeclared_native_preprocessing_observations",
              "result": "running", "runs": {}, "runnerSourceSha256": sha256(Path(__file__))}
    try:
        for dataset, threads, repeat in CONDITIONS:
            label = f"{dataset}-threads{threads}-repeat{repeat}"
            command = [sys.executable, str(Path(__file__).resolve()), "_replay",
                       "--dataset", dataset, "--threads", str(threads)]
            for name in ("core", "adapter", "manifest", "data-root", "config"):
                command.extend(("--" + name, str(getattr(args, name.replace("-", "_")))))
            command.extend(("--output", str(args.output / label)))
            with (args.output / (label + ".log")).open("x") as log:
                result = subprocess.run(command, env=library_environment(args.core),
                                        stdout=log, stderr=log, timeout=240)
            entry = {"exitCode": result.returncode}
            if (args.output / label / "report.json").is_file():
                entry["reportSha256"] = sha256(args.output / label / "report.json")
            report["runs"][label] = entry
            if result.returncode:
                raise RuntimeError("recorded_preprocessing_failed_see_private_output")
            print(json.dumps({"completed": len(report["runs"]), "condition": label}), flush=True)
        report["result"] = "all_twelve_preprocessing_observations_completed_not_slam_acceptance"
    except BaseException:
        report["result"] = "failed_or_interrupted"
        raise
    finally:
        save(args.output / "collection-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build")
    for name in ("core", "upstream-root", "output"):
        builder.add_argument("--" + name, type=Path, required=True)
    for command in ("run", "_replay"):
        runner = commands.add_parser(command)
        for name in ("core", "adapter", "manifest", "data-root", "config", "output"):
            runner.add_argument("--" + name, type=Path, required=True)
        if command == "_replay":
            runner.add_argument("--dataset", choices=("static", "dynamic"), required=True)
            runner.add_argument("--threads", type=int, choices=(1, 2), required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    old_umask = os.umask(0o077)
    try:
        if args.command == "build":
            result = build(args.core, args.upstream_root, args.output)
        else:
            result = run(args) if args.command == "run" else replay(args)
        print(json.dumps({"result": result["result"]}))
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
