"""Actual ARM64, paced bag-to-worker odometry; no ROS node, DDS or actuation."""
import argparse
import json
import os
from pathlib import Path
import platform
import resource
import sys
import threading
import time

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.messages import SensorProfile
from lingxi_slam.worker import SensorWorker

from build_slam_cpu_trial import PINS
from diagnose_slam_sensors import stamp_ns
from glim_target_controlled_mapping import configuration_files, verify_control
from glim_target_replay import validate_environment, verify_replay_bundle
from run_glim_offline_trial import HYPOTHESES, Native, explicit_diagnostic_profile
from slam_bag_audit import CLOUD, IMU
from slam_target_probe import verify_bundle
from verify_slam_capture import sha256, verify


PURPOSE = "arm64_paced_recorded_sensor_worker"
RESULT = "complete_actual_arm64_paced_worker_odometry_not_live_acceptance"
BOUNDARIES = (
    "rosNodeStarted", "liveSensorsSubscribed", "ddsPerformanceValidated", "motionCommandsSent",
    "timingHypothesisValidated", "robotCalibrationValidated", "globalMappingExecuted",
    "mapSaved", "relocalizationValidated", "voiceConcurrencyValidated",
    "navigationValidated", "deploymentApproved", "rawDataPublished",
)
RUNTIME_SOURCES = (
    "robot-slam/lingxi_slam/__init__.py", "robot-slam/lingxi_slam/messages.py",
    "robot-slam/lingxi_slam/stream.py", "robot-slam/lingxi_slam/worker.py",
    "tools/glim_target_worker.py",
)
ARRAYS = ("poses", "header_stamps_ns", "algorithm_stamps_relative_seconds",
          "preprocess_odometry_pointcount_marginalized")
DELIVERY_ARRAYS = (
    "kind_cloud_1_imu_0", "scheduled_relative_seconds", "callback_begin_relative_seconds",
    "callback_end_relative_seconds", "frame_callback_elapsed_seconds",
)
PACING_ROUNDOFF_SECONDS = 1e-9


def mixed_messages(bag):
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from sensor_msgs.msg import Imu, PointCloud2

    reader = rosbag2_py.SequentialReader()
    reader.open(rosbag2_py.StorageOptions(uri=str(bag), storage_id="sqlite3"),
                rosbag2_py.ConverterOptions("cdr", "cdr"))
    types = {entry.name: entry.type for entry in reader.get_all_topics_and_types()
             if entry.name in (CLOUD, IMU)}
    if types != {CLOUD: "sensor_msgs/msg/PointCloud2", IMU: "sensor_msgs/msg/Imu"}:
        raise ValueError("exact_recorded_cloud_imu_types_required")
    reader.set_filter(rosbag2_py.StorageFilter(topics=[CLOUD, IMU]))
    while reader.has_next():
        topic, payload, received_ns = reader.read_next()
        if topic not in (CLOUD, IMU):
            raise ValueError("unexpected_mixed_recorded_topic")
        yield ("cloud" if topic == CLOUD else "imu", received_ns,
               deserialize_message(payload, PointCloud2 if topic == CLOUD else Imu))


def paced_delivery(worker, messages, expected, before_offer=None, *, clock=time.monotonic, sleep=time.sleep):
    """One-times recorded receive timing; never convert absolute epoch ns to float."""
    started = clock()
    origin = previous = None
    counts = {"cloud": 0, "imu": 0}
    kinds, scheduled, begins, ends = [], [], [], []
    for kind, received_ns, message in messages:
        if (kind not in counts or type(received_ns) is not int or received_ns <= 0
                or (previous is not None and received_ns < previous)):
            raise ValueError("ordered_integer_mixed_recorded_arrivals_required")
        origin = received_ns if origin is None else origin
        previous = received_ns
        relative = (received_ns - origin) / 1e9
        due = started + relative
        while True:
            remaining = due - clock()
            if remaining <= 0:
                break
            if worker.snapshot()["state"] == "failed":
                raise RuntimeError("paced_worker_failed_during_wait")
            sleep(min(remaining, .02))
        begin = clock()
        if before_offer is not None:
            before_offer(kind, message, begin)
        accepted = worker.offer_cloud(message) if kind == "cloud" else worker.offer_imu(message)
        end = clock()
        if not accepted:
            raise RuntimeError("paced_worker_rejected_recorded_packet")
        counts[kind] += 1
        if counts[kind] > expected["topicCounts"][kind]:
            raise ValueError("only_declared_recorded_message_count_allowed")
        kinds.append(kind == "cloud")
        scheduled.append(relative)
        begins.append(begin - started)
        ends.append(end - started)
    if counts != {kind: expected["topicCounts"][kind] for kind in counts}:
        raise ValueError("all_original_mixed_recorded_messages_required")
    return counts, {
        "kind_cloud_1_imu_0": np.asarray(kinds, dtype=np.uint8),
        "scheduled_relative_seconds": np.asarray(scheduled),
        "callback_begin_relative_seconds": np.asarray(begins),
        "callback_end_relative_seconds": np.asarray(ends),
    }


def delivery_metrics(arrays):
    kinds, scheduled, begins, ends = (np.asarray(arrays[name]) for name in DELIVERY_ARRAYS[:4])
    frames = np.asarray(arrays[DELIVERY_ARRAYS[4]])
    if (not len(kinds) or kinds.ndim != 1 or np.any((kinds != 0) & (kinds != 1))
            or not np.any(kinds == 0) or not np.any(kinds == 1)
            or any(value.shape != kinds.shape for value in (scheduled, begins, ends))
            or any(not np.isfinite(value).all() for value in (scheduled, begins, ends, frames))
            or scheduled[0] != 0 or np.any(np.diff(scheduled) < 0)
            or np.any(np.diff(begins) < 0) or np.any(begins - scheduled < -PACING_ROUNDOFF_SECONDS)
            or np.any(ends < begins) or frames.ndim != 1 or not len(frames) or np.any(frames < 0)
            or np.any(begins[1:] < ends[:-1])):
        raise ValueError("complete_monotonic_paced_delivery_measurements_required")
    milliseconds = lambda value: np.percentile(value * 1000, [50, 95, 99]).tolist()
    return {
        "recordedReceiveSpanSeconds": float(scheduled[-1]),
        "actualCallbackSpanSeconds": float(begins[-1] - begins[0]),
        "producerLatenessMillisecondsP50P95P99": milliseconds(begins - scheduled),
        "producerLatenessMillisecondsMaximum": float((begins - scheduled).max() * 1000),
        "cloudCallbackSnapshotMillisecondsP50P95P99": milliseconds((ends - begins)[kinds == 1]),
        "imuCallbackSnapshotMillisecondsP50P95P99": milliseconds((ends - begins)[kinds == 0]),
        "frameResultAfterCloudCallbackBeginMillisecondsP50P95P99": milliseconds(frames),
        "frameResultAfterCloudCallbackBeginMillisecondsMaximum": float(frames.max() * 1000),
    }


def observed_libraries(root, core_root, manifest, core):
    expected = {Path(name).name: (core_root / name, entry["sha256"])
                for name, entry in core["files"].items() if name.startswith("lib/")}
    name = "liblingxi_glim_offline.so"
    expected[name] = (root / "lib" / name, manifest["files"]["lib/" + name]["sha256"])
    result = {}
    for line in Path("/proc/self/maps").read_text().splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6 or Path(fields[5]).name not in expected:
            continue
        path = Path(fields[5])
        wanted, digest = expected[path.name]
        if path.resolve() != wanted.resolve() or sha256(path) != digest:
            raise ValueError("paced_worker_native_library_outside_verified_bundle")
        result[path.name] = digest
    if not {"libglim.so", "libodometry_estimation_cpu.so", name}.issubset(result):
        raise ValueError("actual_worker_odometry_libraries_required")
    return result


def run(args):
    validate_environment(args.root, args.core_root)
    if args.dataset not in ("static", "dynamic") or args.hypothesis not in HYPOTHESES:
        raise ValueError("explicit_paced_worker_condition_required")
    if args.output.exists() or args.output.parent != args.root:
        raise ValueError("new_private_worker_output_required")
    manifest = verify_replay_bundle(args.root, PURPOSE)
    core = verify_bundle(args.core_root)
    verify_control(args.root)
    if sha256(args.core_root / "bundle-manifest.json") != manifest["coreBundleManifestSha256"]:
        raise ValueError("unchanged_verified_worker_core_required")
    capture_path = args.root / "capture-manifest.json"
    capture = json.loads(capture_path.read_text())
    if sha256(capture_path) != manifest["captureManifestSha256"]:
        raise ValueError("same_original_worker_capture_required")
    explicit_diagnostic_profile(
        json.loads((args.root / "private-profile.json").read_text()),
        json.loads((args.root / "private-sensor-diagnostic.json").read_text()), args.hypothesis)
    integrity = verify(capture, args.data_root)
    source_hashes = {name: sha256(args.root / name) for name in RUNTIME_SOURCES}
    if any(source_hashes[name] != manifest["files"][name]["sha256"] for name in RUNTIME_SOURCES):
        raise ValueError("frozen_actual_worker_sources_required")
    args.output.mkdir(mode=0o700)
    report = {
        "schemaVersion": 1, "result": "running", "purpose": PURPOSE,
        "dataset": args.dataset, "timingHypothesis": args.hypothesis, "pid": os.getpid(),
        "target": {"machine": platform.machine(), "python": platform.python_version(), "numpy": np.__version__},
        "replayManifestSha256": sha256(args.root / "replay-manifest.json"),
        "sourceSha256": source_hashes, "sourceCommits": PINS,
        "captureManifestSha256": sha256(capture_path), "boundaries": {name: False for name in BOUNDARIES},
        "pacing": "one_times_original_mixed_rosbag_receive_timing_not_live_dds",
        "effectiveConfiguration": configuration_files(args.root / "config"),
    }
    worker = None
    poses, stamps, algorithms, measures, frame_delays = [], [], [], [], []
    pending_clouds, native_threads = {}, set()
    callbacks_lock = threading.Lock()
    main_thread = threading.get_ident()
    native_counts = {"create": 0, "imu": 0, "frame": 0, "close": 0}
    metadata_ = {}
    total_started, cpu_started = time.monotonic(), time.process_time()
    saved_stdout, saved_stderr = os.dup(1), os.dup(2)
    original_cwd = Path.cwd()

    def save_report():
        (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")

    class ObservedNative(Native):
        def record(self, name):
            identity = threading.get_ident()
            if identity == main_thread or (native_threads and identity not in native_threads):
                raise RuntimeError("one_nonproducer_native_owner_required")
            native_threads.add(identity)
            native_counts[name] += 1

        def __init__(self):
            self.record("create")
            super().__init__(args.root / "lib/liblingxi_glim_offline.so", args.root / "config",
                             args.core_root / "lib/libodometry_estimation_cpu.so")
            metadata_["actualLoadedLibrariesSha256"] = observed_libraries(args.root, args.core_root, manifest, core)

        def imu(self, *values):
            self.record("imu")
            super().imu(*values)

        def frame(self, *values):
            self.record("frame")
            return super().frame(*values)

        def close(self):
            self.record("close")
            metadata_["remainingFixedLagFramesAtEnd"] = super().close()
            return metadata_["remainingFixedLagFramesAtEnd"]

    def before_offer(kind, message, begin):
        if kind == "cloud":
            with callbacks_lock:
                key = stamp_ns(message)
                if key in pending_clouds:
                    raise ValueError("unique_cloud_callback_header_required")
                pending_clouds[key] = begin

    def on_frame(header, algorithm, pose, statistics):
        if threading.get_ident() not in native_threads:
            raise RuntimeError("native_owner_result_consumer_required")
        with callbacks_lock:
            begin = pending_clouds.pop(header)
        frame_delays.append(time.monotonic() - begin)
        measures.append(statistics.copy())
        if pose is not None:
            poses.append(pose.copy())
            stamps.append(header)
            algorithms.append(algorithm)

    try:
        save_report()
        os.chdir(args.output)
        with (args.output / "private-native.log").open("x") as log:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            expected = capture["datasets"][args.dataset]
            messages = mixed_messages(args.data_root / expected["directoryName"])
            # Open/deserialize only the first item before starting the native startup watchdog.
            first = next(messages)
            import itertools
            worker = SensorWorker(
                ObservedNative, SensorProfile("lidar_chest_front", "lidar_imu_chest_front",
                                              "frame_relative_nanoseconds_hypothesis"),
                args.hypothesis, on_frame=on_frame,
            ).start()
            while worker.snapshot()["state"] == "starting":
                time.sleep(.005)
            if worker.snapshot()["state"] != "running":
                raise RuntimeError("native_worker_did_not_initialize")
            received, deliveries = paced_delivery(worker, itertools.chain((first,), messages), expected, before_offer)
            drain_deadline = time.monotonic() + 2
            while True:
                state = worker.snapshot()
                if state["state"] == "failed":
                    raise RuntimeError("paced_worker_failed_before_complete_drain")
                if state["counts"]["completedMessages"] == sum(received.values()):
                    break
                if time.monotonic() > drain_deadline:
                    raise TimeoutError("paced_worker_final_drain_deadline_exceeded")
                time.sleep(.005)
            final = worker.stop()
            if (final["state"] != "stopped" or final["threadAlive"] or final["inFlightMessages"]
                    or final["queuedMessages"] or final["counts"]["discardedMessages"]
                    or final["counts"]["rejectedMessages"] or len(poses) < 2
                    or native_counts["create"] != 1 or native_counts["close"] != 1):
                raise ValueError("complete_closed_once_paced_worker_required")
        trajectory = {
            "poses": np.stack(poses), "header_stamps_ns": np.asarray(stamps, dtype=np.int64),
            "algorithm_stamps_relative_seconds": np.asarray(algorithms),
            "preprocess_odometry_pointcount_marginalized": np.stack(measures),
        }
        deliveries["frame_callback_elapsed_seconds"] = np.asarray(frame_delays)
        np.savez_compressed(args.output / "private-trajectory.npz", **trajectory)
        np.savez_compressed(args.output / "private-delivery.npz", **deliveries)
        if verify(capture, args.data_root) != integrity:
            raise ValueError("original_recordings_changed_during_paced_worker")
        verify_replay_bundle(args.root, PURPOSE)
        report.update(
            result=RESULT, worker=final, receivedMessageCounts=received,
            observedPacedDeliveryNotDds=delivery_metrics(deliveries),
            nativeCalls=native_counts, nativeCallsAndConsumerHaveOneNonproducerOwner=len(native_threads) == 1,
            totalWallSecondsIncludingVerification=time.monotonic() - total_started,
            processCpuSeconds=time.process_time() - cpu_started,
            peakResidentMiB=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            originalCapturesAndBundleUnchanged=True, **metadata_,
            filesSha256={name: sha256(args.output / name) for name in
                         ("private-trajectory.npz", "private-delivery.npz", "private-native.log")},
        )
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        try:
            if worker is not None:
                report["worker"] = worker.stop()
                report["nativeCalls"] = native_counts
        finally:
            os.dup2(saved_stdout, 1)
            os.dup2(saved_stderr, 2)
            os.close(saved_stdout)
            os.close(saved_stderr)
            os.chdir(original_cwd)
            save_report()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "core-root", "data-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--dataset", choices=("static", "dynamic"), required=True)
    parser.add_argument("--hypothesis", choices=HYPOTHESES, required=True)
    args = parser.parse_args()
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.resolve())
    previous_umask = os.umask(0o077)
    try:
        print(json.dumps({"result": run(args)["result"]}))
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    main()
