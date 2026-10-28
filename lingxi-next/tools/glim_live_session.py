"""Bounded actual Humble subscriptions with frozen native odometry, no actuation."""
import argparse
import json
import math
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
from lingxi_slam.passive_ros import Topics, run_passive
from lingxi_slam.worker import SensorWorker

from glim_target_controlled_mapping import configuration_files, verify_control
from glim_target_replay import verify_replay_bundle
from glim_target_worker import observed_libraries
from run_glim_offline_trial import HYPOTHESES, Native, explicit_diagnostic_profile
from slam_bag_audit import CLOUD, IMU
from slam_target_probe import verify_bundle
from verify_slam_capture import sha256


PURPOSE = "arm64_actual_humble_passive_odometry"
RESULT = "actual_humble_session_closed_not_mapping_or_navigation_acceptance"
DDS_PROFILE = "/agibot/software/entry/cfg/ros_dds_configuration.xml"
RUNTIME_SOURCES = (
    "robot-slam/lingxi_slam/__init__.py", "robot-slam/lingxi_slam/messages.py",
    "robot-slam/lingxi_slam/stream.py", "robot-slam/lingxi_slam/worker.py",
    "robot-slam/lingxi_slam/passive_ros.py", "tools/glim_live_session.py",
)
BOUNDARIES = (
    "motionCommandsSent", "applicationPublishersCreated", "tfPublished", "mapPublished",
    "globalMappingExecuted", "mapSaved", "mapActivated", "modeOrInputSourceChanged",
    "deployedServicesChanged", "sensorToDdsLatencyValidated", "timingHypothesisValidated",
    "robotCalibrationValidated", "geometryValidated", "relocalizationValidated",
    "voiceConcurrencyValidated", "navigationValidated", "deploymentApproved", "rawDataPublished",
)
ARRAYS = ("cloud_headers_ns", "cloud_callback_begin", "cloud_callback_end",
          "imu_headers_ns", "imu_callback_begin", "imu_callback_end",
          "frame_headers_ns", "frame_callback_delay", "frame_statistics",
          "pose_headers_ns", "poses", "active_consumer_headers_ns")
MAXIMUM_OBSERVATIONS = 100_000


def duration(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 5 <= value <= 60:
        raise ValueError("finite_five_to_sixty_second_session_required")
    return float(value)


def validate_environment(root, core):
    wanted = {
        "ROS_DISTRO": "humble", "ROS_DOMAIN_ID": "0", "RMW_IMPLEMENTATION": "rmw_fastrtps_cpp",
        "FASTRTPS_DEFAULT_PROFILES_FILE": DDS_PROFILE, "AMENT_PREFIX_PATH": "/opt/ros/humble",
        "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
        "LD_LIBRARY_PATH": ":".join((str(root / "lib"), str(core / "lib"),
                                   "/opt/ros/humble/lib/aarch64-linux-gnu", "/opt/ros/humble/lib")),
    }
    if (platform.system() != "Linux" or platform.machine() != "aarch64" or os.geteuid() == 0
            or any(os.environ.get(key) != value for key, value in wanted.items())
            or any(key in os.environ for key in ("LD_PRELOAD", "ROS_ARGS", "CYCLONEDDS_URI"))):
        raise ValueError("explicit_ordinary_user_arm64_humble_live_environment_required")


class NativeOwner:
    """Composition leaves historical adapters unchanged, including failed-init cleanup."""

    def __init__(self, factory, root, core, verify_loaded, *, native_type=Native):
        self.factory = factory
        self.native = native_type.__new__(native_type)
        self.factory.record("create")
        try:
            native_type.__init__(self.native, root / "lib/liblingxi_glim_offline.so",
                                 root / "config", core / "lib/libodometry_estimation_cpu.so")
            verify_loaded()
        except BaseException:
            self.close()
            raise

    def imu(self, *values):
        self.factory.record("imu")
        return self.native.imu(*values)

    def frame(self, *values):
        self.factory.record("frame")
        return self.native.frame(*values)

    def close(self):
        if self.native is not None:
            native, self.native = self.native, None
            if getattr(native, "library", None) is not None:
                self.factory.record("close")
                return native.close()


class Observations:
    def __init__(self):
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.callbacks = {"cloud": [], "imu": []}
        self.clouds = {}
        self.frames, self.poses, self.delivered = [], [], []

    def before(self, kind, message):
        stamp = message.header.stamp
        header = stamp.sec * 1_000_000_000 + stamp.nanosec
        begin = time.monotonic() - self.started
        with self.lock:
            if sum(map(len, self.callbacks.values())) >= MAXIMUM_OBSERVATIONS:
                raise RuntimeError("bounded_private_live_observations_exceeded")
            rows = self.callbacks[kind]
            if rows and header <= rows[-1][0]:
                raise ValueError("ordered_live_headers_required")
            rows.append([header, begin, None])
            if kind == "cloud":
                self.clouds[header] = begin
        return rows[-1]

    def after(self, row):
        with self.lock:
            row[2] = time.monotonic() - self.started

    def frame(self, header, algorithm, pose, statistics):
        with self.lock:
            begin = self.clouds.pop(header)
            self.frames.append((header, time.monotonic() - self.started - begin, statistics.copy()))
            if pose is not None:
                self.poses.append((header, pose.copy()))

    def arrays(self):
        with self.lock:
            result = {}
            for kind, rows in self.callbacks.items():
                result[kind + "_headers_ns"] = np.asarray([row[0] for row in rows], dtype=np.int64)
                result[kind + "_callback_begin"] = np.asarray([row[1] for row in rows], dtype=np.float64)
                result[kind + "_callback_end"] = np.asarray([row[2] for row in rows], dtype=np.float64)
            result.update(
                frame_headers_ns=np.asarray([row[0] for row in self.frames], dtype=np.int64),
                frame_callback_delay=np.asarray([row[1] for row in self.frames], dtype=np.float64),
                frame_statistics=np.asarray([row[2] for row in self.frames], dtype=np.float64).reshape(-1, 4),
                pose_headers_ns=np.asarray([row[0] for row in self.poses], dtype=np.int64),
                poses=np.asarray([row[1] for row in self.poses], dtype=np.float64).reshape(-1, 4, 4),
                active_consumer_headers_ns=np.asarray(self.delivered, dtype=np.int64),
            )
            return result

    def active_consumer(self, header, algorithm, pose, statistics):
        with self.lock:
            self.delivered.append(header)


class MeasuredWorker(SensorWorker):
    def __init__(self, factory, hypothesis, observations):
        self.observations = observations
        super().__init__(factory, SensorProfile("lidar_chest_front", "lidar_imu_chest_front",
                                               "frame_relative_nanoseconds_hypothesis"),
                         hypothesis, on_frame=observations.active_consumer)

    def _frame(self, *values):
        # Audit every completed native result; keep the inherited late-result gate.
        self.observations.frame(*values)
        super()._frame(*values)

    def _measure(self, kind, message, callback):
        row = self.observations.before(kind, message)
        try:
            return callback(message)
        finally:
            self.observations.after(row)

    def offer_cloud(self, message):
        return self._measure("cloud", message, super().offer_cloud)

    def offer_imu(self, message):
        return self._measure("imu", message, super().offer_imu)


def metrics(arrays):
    if set(arrays) != set(ARRAYS):
        raise ValueError("complete_private_live_arrays_required")
    result = {}
    for kind in ("cloud", "imu"):
        headers, begins, ends = (arrays[kind + suffix] for suffix in
                                ("_headers_ns", "_callback_begin", "_callback_end"))
        if (headers.dtype != np.int64 or headers.ndim != 1 or len(headers) < 2
                or np.any(headers <= 0) or np.any(np.diff(headers) <= 0)
                or any(values.shape != headers.shape for values in (begins, ends))
                or not np.isfinite(begins).all() or not np.isfinite(ends).all()
                or np.any(begins < 0) or begins[-1] <= begins[0]
                or np.any(ends < begins) or np.any(begins[1:] < ends[:-1])):
            raise ValueError("complete_ordered_live_callback_observations_required")
        result[kind] = {
            "callbacks": len(headers), "callbackSpanSeconds": float(begins[-1] - begins[0]),
            "callbackArrivalHz": float((len(headers) - 1) / (begins[-1] - begins[0])),
            "maximumHeaderGapMilliseconds": float(np.diff(headers).max() / 1e6),
            "callbackSnapshotMillisecondsP50P95P99": np.percentile((ends - begins) * 1000, [50, 95, 99]).tolist(),
        }
    frame_headers, delays, statistics = (arrays[name] for name in
                                        ("frame_headers_ns", "frame_callback_delay", "frame_statistics"))
    pose_headers, poses = arrays["pose_headers_ns"], arrays["poses"]
    active = arrays["active_consumer_headers_ns"]
    if (frame_headers.dtype != np.int64 or frame_headers.ndim != 1 or len(frame_headers) < 2
            or not np.isin(frame_headers, arrays["cloud_headers_ns"]).all()
            or np.any(np.diff(frame_headers) <= 0) or delays.shape != frame_headers.shape
            or not np.isfinite(delays).all() or np.any(delays < 0)
            or statistics.shape != (len(frame_headers), 4) or not np.isfinite(statistics).all()
            or np.any(statistics < 0) or np.any(statistics[:, 2:] != np.floor(statistics[:, 2:]))
            or pose_headers.dtype != np.int64 or pose_headers.ndim != 1 or len(pose_headers) < 2
            or not np.isin(pose_headers, frame_headers).all() or np.any(np.diff(pose_headers) <= 0)
            or poses.shape != (len(pose_headers), 4, 4) or not np.isfinite(poses).all()
            or not np.allclose(poses[:, 3], [0, 0, 0, 1], rtol=0, atol=1e-7)
            or not np.allclose(poses[:, :3, :3].transpose(0, 2, 1) @ poses[:, :3, :3],
                               np.eye(3), rtol=0, atol=1e-5)
            or not np.allclose(np.linalg.det(poses[:, :3, :3]), 1, rtol=0, atol=1e-5)):
        raise ValueError("complete_ordered_live_frames_and_valid_poses_required")
    if (active.dtype != np.int64 or active.ndim != 1 or np.any(np.diff(active) <= 0)
            or not np.array_equal(active, frame_headers[:len(active)])):
        raise ValueError("active_consumer_receives_only_a_prefix_before_session_stop")
    first = float(arrays["cloud_callback_begin"][np.searchsorted(arrays["cloud_headers_ns"], pose_headers[0])])
    result.update(
        processedScans=len(frame_headers), initializationScans=len(frame_headers) - len(pose_headers),
        estimatedPoses=len(pose_headers),
        activeConsumerFrameResults=len(active), retiredAtStopFrameResults=len(frame_headers) - len(active),
        firstPoseCloudCallbackBeginAfterObservationStartSeconds=first,
        firstPoseResultAfterObservationStartSeconds=first + float(
            delays[np.searchsorted(frame_headers, pose_headers[0])]),
        frameResultAfterCallbackBeginMillisecondsP50P95P99=np.percentile(delays * 1000, [50, 95, 99]).tolist(),
        frameResultAfterCallbackBeginMillisecondsMaximum=float(delays.max() * 1000),
        nativePreprocessAndOdometryMillisecondsP50P95P99=np.percentile(
            statistics[:, :2].sum(axis=1), [50, 95, 99]).tolist(),
    )
    return result


def run(args):
    duration(args.seconds)
    validate_environment(args.root, args.core_root)
    if args.output.exists() or args.output.parent != args.root or args.hypothesis not in HYPOTHESES:
        raise ValueError("new_home_scoped_live_session_output_required")
    manifest = verify_replay_bundle(args.root, PURPOSE)
    core = verify_bundle(args.core_root)
    verify_control(args.root)
    if sha256(args.core_root / "bundle-manifest.json") != manifest["coreBundleManifestSha256"]:
        raise ValueError("unchanged_verified_live_core_required")
    explicit_diagnostic_profile(json.loads((args.root / "private-profile.json").read_text()),
                                json.loads((args.root / "private-sensor-diagnostic.json").read_text()),
                                args.hypothesis)
    sources = {name: sha256(args.root / name) for name in RUNTIME_SOURCES}
    if any(digest != manifest["files"][name]["sha256"] for name, digest in sources.items()):
        raise ValueError("frozen_live_runtime_sources_required")
    args.output.mkdir(mode=0o700)
    main_thread, owner_threads = threading.get_ident(), set()
    calls = dict.fromkeys(("create", "imu", "frame", "close"), 0)
    report = {
        "schemaVersion": 1, "purpose": PURPOSE, "result": "running", "pid": os.getpid(),
        "target": {"machine": platform.machine(), "python": platform.python_version(), "numpy": np.__version__},
        "timingHypothesis": args.hypothesis, "requestedSeconds": args.seconds,
        "replayManifestSha256": sha256(args.root / "replay-manifest.json"), "sourceSha256": sources,
        "ddsProfileSha256": sha256(Path(DDS_PROFILE)),
        "effectiveConfiguration": configuration_files(args.root / "config"),
        "boundaries": {name: False for name in BOUNDARIES},
        "rosInfrastructureMayPublishDiscoveryAndParameterEvents": True,
    }
    worker, observations = None, Observations()
    started, cpu_started = time.monotonic(), time.process_time()
    saved_stdout, saved_stderr = os.dup(1), os.dup(2)
    original_cwd = Path.cwd()

    def save_report():
        (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")

    def record(name):
        identity = threading.get_ident()
        if identity == main_thread or (owner_threads and identity not in owner_threads):
            raise RuntimeError("one_nonproducer_live_native_owner_required")
        owner_threads.add(identity)
        calls[name] += 1

    def verify_loaded():
        report["actualLoadedLibrariesSha256"] = observed_libraries(args.root, args.core_root, manifest, core)

    def factory():
        return NativeOwner(factory, args.root, args.core_root, verify_loaded)

    factory.record = record
    try:
        save_report()
        os.chdir(args.output)
        with (args.output / "private-native.log").open("x") as log:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            worker = MeasuredWorker(factory, args.hypothesis, observations)
            report["session"] = run_passive(worker, Topics(CLOUD, IMU), args.seconds)
        arrays = observations.arrays()
        np.savez_compressed(args.output / "private-live-observations.npz", **arrays)
        report["observations"] = metrics(arrays)
        state = worker.snapshot()
        stream = state["stream"]
        if (report["session"]["stopReason"] != "duration_reached" or state["state"] != "stopped"
                or state["threadAlive"] or state["failure"] or stream is None
                or state["queuedMessages"] or state["inFlightMessages"]
                or state["counts"]["rejectedMessages"] or calls["create"] != 1 or calls["close"] != 1
                or state["counts"]["acceptedClouds"] != len(arrays["cloud_headers_ns"])
                or state["counts"]["acceptedImu"] != len(arrays["imu_headers_ns"])
                or state["counts"]["completedMessages"] + state["counts"]["discardedMessages"]
                != state["counts"]["acceptedClouds"] + state["counts"]["acceptedImu"]
                or state["counts"]["completedMessages"]
                != stream["counts"]["receivedClouds"] + stream["counts"]["receivedImu"]
                or stream["counts"]["processedScans"] != len(arrays["frame_headers_ns"])
                or stream["counts"]["estimatedPoses"] != len(arrays["pose_headers_ns"])
                or calls["frame"] != stream["counts"]["processedScans"]
                or calls["imu"] != stream["counts"]["insertedImu"]):
            raise ValueError("complete_closed_live_session_accounting_required")
        verify_replay_bundle(args.root, PURPOSE)
        if sha256(Path(DDS_PROFILE)) != report["ddsProfileSha256"]:
            raise ValueError("dds_profile_changed_during_live_session")
        report.update(
            result=RESULT, nativeCalls=calls, nativeCallsHaveOneNonproducerOwner=len(owner_threads) == 1,
            totalWallSecondsIncludingVerification=time.monotonic() - started,
            processCpuSeconds=time.process_time() - cpu_started,
            peakResidentMiB=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            bundleAndDdsProfileUnchanged=True,
        )
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        try:
            if worker is not None:
                report["worker"] = worker.stop()
                if not report["worker"]["threadAlive"]:
                    np.savez_compressed(args.output / "private-live-observations.npz", **observations.arrays())
            report["nativeCalls"] = calls
        finally:
            os.dup2(saved_stdout, 1)
            os.dup2(saved_stderr, 2)
            os.close(saved_stdout)
            os.close(saved_stderr)
            os.chdir(original_cwd)
            report["filesSha256"] = {name: sha256(args.output / name) for name in
                                    ("private-native.log", "private-live-observations.npz")
                                    if (args.output / name).is_file()}
            save_report()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "core-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--hypothesis", choices=HYPOTHESES, required=True)
    parser.add_argument("--seconds", type=float, required=True)
    args = parser.parse_args()
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.resolve())
    previous = os.umask(0o077)
    try:
        print(json.dumps({"result": run(args)["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
