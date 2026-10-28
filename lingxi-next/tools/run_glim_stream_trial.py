"""Verify bounded input against recorded arrival order, not live timing acceptance."""
import argparse
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.stream import SensorStream

from build_slam_cpu_trial import PINS
from compare_glim_architectures import pose_differences
from diagnose_slam_sensors import quaternion, stamp_ns, vector
from export_glim_controlled_mapping import load_run
from glim_target_controlled_mapping import BASELINE_MANIFEST
from run_glim_offline_trial import HYPOTHESES, Native, library_environment
from run_kiss_offline_trial import cloud_arrays
from slam_bag_audit import CLOUD, IMU
from verify_slam_capture import sha256, verify


CONDITIONS = tuple((dataset, hypothesis) for dataset in ("static", "dynamic") for hypothesis in HYPOTHESES)
BOUNDARIES = (
    "robotContacted", "rosNodeStarted", "liveSensorsSubscribed", "motionCommandsSent",
    "recordedArrivalTimingIsLivePerformance", "timingHypothesisValidated", "robotCalibrationValidated",
    "mapGeometryValidated", "globalMappingExecuted", "relocalizationValidated", "voiceConcurrencyValidated",
    "targetArm64StreamingValidated", "navigationValidated", "deploymentApproved", "rawDataPublished",
)
SOURCES = (
    "tools/run_glim_stream_trial.py", "robot-slam/lingxi_slam/__init__.py", "robot-slam/lingxi_slam/stream.py",
    "tools/run_glim_offline_trial.py", "tools/run_kiss_offline_trial.py", "tools/diagnose_slam_sensors.py",
    "tools/export_glim_controlled_mapping.py", "tools/compare_glim_architectures.py",
    "tools/verify_slam_capture.py", "tools/slam_bag_audit.py",
)


def label(dataset, hypothesis):
    return dataset + ("-start" if hypothesis == HYPOTHESES[0] else "-end")


def write_report(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def private_output(path):
    path = path.resolve()
    if path.exists() or not path.is_relative_to((ROOT / ".local").resolve()):
        raise ValueError("new_ignored_private_stream_output_required")
    return path


def source_hashes():
    return {name: sha256(ROOT / name) for name in SOURCES}


def actual_libraries(work, adapter, expected):
    wanted = {path.name: path for path in (work / "prefix/lib").glob("*.so*") if path.is_file()}
    wanted["liblingxi_glim_offline.so"] = adapter / "build/liblingxi_glim_offline.so"
    found = {}
    for line in Path("/proc/self/maps").read_text().splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6 or Path(fields[5]).name not in wanted:
            continue
        path = Path(fields[5])
        if path.resolve() != wanted[path.name].resolve() or sha256(path) != expected.get(path.name):
            raise ValueError("stream_library_outside_frozen_baseline")
        found[path.name] = sha256(path)
    if not {"libglim.so", "libodometry_estimation_cpu.so", "liblingxi_glim_offline.so"}.issubset(found):
        raise ValueError("actual_stream_odometry_libraries_required")
    return found


def run(args):
    output = private_output(args.output)
    if (platform.system() != "Linux" or platform.machine() != "x86_64"
            or os.geteuid() == 0 or sys.prefix == sys.base_prefix
            or metadata.version("rosbags") != "0.11.9" or metadata.version("numpy") != "2.5.4"
            or os.environ.get("LD_LIBRARY_PATH") != library_environment(args.work)["LD_LIBRARY_PATH"]):
        raise ValueError("explicit_isolated_recorded_stream_environment_required")
    name = label(args.dataset, args.hypothesis)
    frozen_path = ROOT / "docs/slam-controlled-mapping-evidence.json"
    frozen = json.loads(frozen_path.read_text())
    baseline_name = name + "-repeat1"
    baseline_root = args.baselines / baseline_name
    baseline = load_run(baseline_root, (args.dataset, args.hypothesis, 1),
                        {"exitCode": 0, "reportSha256": frozen["runs"][baseline_name]["controlledReportSha256"]},
                        BASELINE_MANIFEST)
    core = json.loads((args.work / "build-report.json").read_text())
    build = json.loads((args.adapter / "build-report.json").read_text())
    library = args.adapter / "build/liblingxi_glim_offline.so"
    if (core["result"] != "core_libraries_built_not_target_acceptance" or core["sourceCommits"] != PINS
            or build["result"] != "adapter_built_not_algorithm_acceptance" or build["sourceCommits"] != PINS
            or build["coreBuildReportSha256"] != sha256(args.work / "build-report.json")
            or build["librarySha256"] != sha256(library)
            or build["adapterSourceSha256"] != sha256(ROOT / "tools/glim-replay/offline.cpp")
            or build["adapterCmakeSha256"] != sha256(ROOT / "tools/glim-replay/CMakeLists.txt")):
        raise ValueError("unchanged_stream_native_core_required")
    capture_path = ROOT / "docs/slam-capture-evidence.json"
    capture = json.loads(capture_path.read_text())
    integrity = verify(capture, args.data_root)
    if baseline["report"]["captureManifestSha256"] != sha256(capture_path):
        raise ValueError("same_baseline_capture_required")
    sources = source_hashes()
    output.mkdir(mode=0o700, parents=True)
    report = {
        "schemaVersion": 1, "result": "running", "dataset": args.dataset, "timingHypothesis": args.hypothesis,
        "sourceCommits": PINS, "sourceSha256": sources,
        "host": {"machine": platform.machine(), "python": platform.python_version(), "numpy": np.__version__},
        "baselineEvidenceSha256": sha256(frozen_path),
        "baselineReportSha256": frozen["runs"][baseline_name]["controlledReportSha256"],
        "captureManifestSha256": sha256(capture_path), "boundaries": {name: False for name in BOUNDARIES},
        "arrivalClock": "relative_recorded_rosbag_receive_timestamps_not_wall_clock_or_paced_replay",
    }
    native, stream = None, None
    started = time.monotonic()
    poses, headers, algorithms, measurements = [], [], [], []
    observed = {"cloud": 0, "imu": 0}

    def on_frame(header, algorithm, pose, stats):
        measurements.append(stats)
        if pose is not None:
            poses.append(pose.copy())
            headers.append(header)
            algorithms.append(algorithm)

    stdout_copy, stderr_copy = os.dup(1), os.dup(2)
    old_cwd = Path.cwd()
    try:
        for name in SOURCES:
            destination = output / "source-snapshot" / name
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        write_report(output / "report.json", report)
        from rosbags.highlevel import AnyReader
        from rosbags.typesys import Stores, get_typestore
        os.chdir(output)
        with (output / "private-native.log").open("x") as log:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            native = Native(library, baseline_root / "control-config",
                            args.work / "prefix/lib/libodometry_estimation_cpu.so")
            report["actualLoadedLibrariesSha256"] = actual_libraries(
                args.work, args.adapter, baseline["report"]["actualLoadedLibrariesSha256"])
            stream = SensorStream(native, args.hypothesis, on_frame=on_frame)
            expected = capture["datasets"][args.dataset]
            with AnyReader([args.data_root / expected["directoryName"]],
                           default_typestore=get_typestore(Stores.ROS2_HUMBLE)) as reader:
                connections = [connection for connection in reader.connections if connection.topic in (CLOUD, IMU)]
                if ({connection.topic: connection.msgtype for connection in connections}
                        != {CLOUD: "sensor_msgs/msg/PointCloud2", IMU: "sensor_msgs/msg/Imu"}
                        or len(connections) != 2):
                    raise ValueError("exact_recorded_lidar_imu_connections_required")
                origin = None
                for connection, received_ns, payload in reader.messages(connections=connections):
                    origin = received_ns if origin is None else origin
                    received_at = (received_ns - origin) / 1e9 + 1000.
                    message = reader.deserialize(payload, connection.msgtype)
                    if connection.topic == IMU:
                        observed["imu"] += 1
                        if message.header.frame_id != "lidar_imu_chest_front":
                            raise ValueError("explicit_recorded_imu_frame_required")
                        quaternion(message.orientation)
                        stream.push_imu(stamp_ns(message), vector(message.linear_acceleration),
                                        vector(message.angular_velocity), received_at)
                    else:
                        observed["cloud"] += 1
                        if message.header.frame_id != "lidar_chest_front":
                            raise ValueError("explicit_recorded_lidar_frame_required")
                        points, offsets, dropped = cloud_arrays(message)
                        if dropped:
                            raise ValueError("complete_finite_recorded_cloud_required")
                        stream.push_cloud(stamp_ns(message), points, offsets, received_at)
            state = stream.finish()
            if (observed != {key: expected["topicCounts"][key] for key in observed}
                    or state["counts"]["receivedClouds"] != observed["cloud"]
                    or state["counts"]["receivedImu"] != observed["imu"]
                    or sum(state["counts"][key] for key in (
                        "processedScans", "skippedBeforeImu", "skippedAfterImuAtEndOfInput")) != observed["cloud"]
                    or len(poses) < 2):
                raise ValueError("complete_arrival_stream_and_initialized_trajectory_required")
            report["remainingFixedLagFramesAtEnd"] = native.close()
            native = None
        arrays = {
            "poses": np.stack(poses), "header_stamps_ns": np.asarray(headers, dtype=np.int64),
            "algorithm_stamps_relative_seconds": np.asarray(algorithms),
            "preprocess_odometry_pointcount_marginalized": np.stack(measurements),
        }
        np.savez_compressed(output / "private-trajectory.npz", **arrays)
        common = np.array_equal(arrays["header_stamps_ns"], baseline["stamps"])
        report["baselineComparisonNotTruthAccuracy"] = {
            "sameEstimatedHeaderDomain": common,
            "sameAlgorithmTimestamps": common and np.array_equal(arrays["algorithm_stamps_relative_seconds"],
                                                                 baseline["algorithm"]),
            "sameScanCounters": (arrays["preprocess_odometry_pointcount_marginalized"][:, 2:].shape
                                 == baseline["scanCounters"].shape and np.array_equal(
                                     arrays["preprocess_odometry_pointcount_marginalized"][:, 2:],
                                     baseline["scanCounters"])),
        }
        if common:
            report["baselineComparisonNotTruthAccuracy"]["onlinePoseDifference"] = pose_differences(
                baseline["online"], arrays["poses"])
        report["privateTrajectorySha256"] = sha256(output / "private-trajectory.npz")
        if verify(capture, args.data_root) != integrity or source_hashes() != sources:
            raise ValueError("stream_sources_or_original_captures_changed")
        report["result"] = "complete_recorded_arrival_stream_not_live_mapping_acceptance"
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
            os.chdir(old_cwd)
            if stream is not None:
                report["stream"] = stream.snapshot()
            report["observedMessageCounts"] = observed
            report["totalWallSecondsNotPacedRealtimeAcceptance"] = time.monotonic() - started
            report["privateNativeLogSha256"] = sha256(output / "private-native.log") if (
                output / "private-native.log").exists() else None
            write_report(output / "report.json", report)
    return report


def batch(args):
    output = private_output(args.output)
    output.mkdir(mode=0o700, parents=True)
    collection = {"result": "running", "runs": {}, "runnerSourceSha256": sha256(__file__)}
    try:
        for dataset, hypothesis in CONDITIONS:
            name = label(dataset, hypothesis)
            command = [sys.executable, "-B", str(Path(__file__).resolve()), "run",
                       "--dataset", dataset, "--hypothesis", hypothesis, "--output", str(output / name)]
            for key in ("baselines", "work", "adapter", "data_root"):
                command += ["--" + key.replace("_", "-"), str(getattr(args, key))]
            print(json.dumps({"phase": "recorded_arrival_stream_started", "condition": name}), flush=True)
            with (output / (name + "-private-command.log")).open("x") as log:
                completed = subprocess.run(command, stdout=log, stderr=log,
                                           env=library_environment(args.work), timeout=300)
            report_path = output / name / "report.json"
            collection["runs"][name] = {"exitCode": completed.returncode,
                                        "reportSha256": sha256(report_path) if report_path.exists() else None}
            write_report(output / "collection-report.json", collection)
            print(json.dumps({"phase": "recorded_arrival_stream_completed", "condition": name,
                              "exitCode": completed.returncode}), flush=True)
        collection["result"] = ("all_recorded_arrival_streams_completed_not_live_acceptance"
                                if all(item["exitCode"] == 0 for item in collection["runs"].values())
                                else "recorded_arrival_streams_include_failures")
    except BaseException as error:
        collection.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        write_report(output / "collection-report.json", collection)
    return collection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "batch"):
        command = sub.add_parser(name)
        for field in ("output", "baselines", "work", "adapter", "data-root"):
            command.add_argument("--" + field, type=Path, required=True)
        if name == "run":
            command.add_argument("--dataset", choices=("static", "dynamic"), required=True)
            command.add_argument("--hypothesis", choices=HYPOTHESES, required=True)
    args = parser.parse_args()
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.resolve())
    old_umask = os.umask(0o077)
    try:
        result = (batch if args.command == "batch" else run)(args)
        print(json.dumps({"result": result["result"]}))
        if "fail" in result["result"]:
            raise SystemExit(1)
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
