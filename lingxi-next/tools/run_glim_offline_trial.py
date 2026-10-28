"""Real-data GLIM odometry diagnostic with explicit unapproved timing hypotheses."""
import argparse
import ctypes
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import time

import numpy as np

from build_slam_cpu_trial import PINS
from diagnose_slam_sensors import quaternion, stamp_ns, vector
from glim_trial_config import generate
from run_kiss_offline_trial import cloud_arrays, valid_pose
from slam_bag_audit import CLOUD, IMU
from verify_slam_capture import sha256, verify


ROOT = Path(__file__).resolve().parents[1]
HYPOTHESES = ("header_is_scan_start", "header_is_scan_end")


def library_environment(work):
    env = os.environ.copy()
    env["PATH"] = str(work / "sysroot/usr/bin") + os.pathsep + env["PATH"]
    env["LD_LIBRARY_PATH"] = os.pathsep.join((
        str(work / "prefix/lib"), str(work / "sysroot/usr/lib/x86_64-linux-gnu"),
    ))
    env["CMAKE_PREFIX_PATH"] = os.pathsep.join((str(work / "prefix"), str(work / "sysroot/usr")))
    for name in ("ROS_VERSION", "ROS_DISTRO", "AMENT_PREFIX_PATH", "COLCON_PREFIX_PATH"):
        env.pop(name, None)
    env.update(OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2")
    return env


def build(work, output):
    work, output = Path(work).resolve(strict=True), Path(output).resolve()
    core = json.loads((work / "build-report.json").read_text(encoding="utf-8"))
    if (platform.system() != "Linux" or platform.machine() != "x86_64"
            or core.get("result") != "core_libraries_built_not_target_acceptance"
            or core.get("sourceCommits") != PINS or core.get("hostMachine") != "x86_64"):
        raise ValueError("reviewed_native_x86_cpu_build_required")
    if output.exists():
        raise ValueError("existing_replay_build_is_preserved")
    output.mkdir(mode=0o700, parents=True)
    report = {
        "purpose": "ros_free_glim_replay_adapter_build", "sourceCommits": PINS,
        "coreBuildReportSha256": sha256(work / "build-report.json"),
        "adapterSourceSha256": sha256(ROOT / "tools/glim-replay/offline.cpp"),
        "adapterCmakeSha256": sha256(ROOT / "tools/glim-replay/CMakeLists.txt"),
        "stages": [], "navigationValidated": False,
    }
    try:
        for name, command in (
            ("configure", ["cmake", "-S", str(ROOT / "tools/glim-replay"), "-B", str(output / "build")]),
            ("build", ["cmake", "--build", str(output / "build"), "--parallel", "2"]),
        ):
            with (output / (name + ".log")).open("x") as log:
                completed = subprocess.run(command, env=library_environment(work), stdout=log, stderr=log)
            report["stages"].append({"name": name, "exitCode": completed.returncode})
            if completed.returncode:
                raise RuntimeError("replay_adapter_build_failed_see_private_logs")
        report["result"] = "adapter_built_not_algorithm_acceptance"
        report["librarySha256"] = sha256(output / "build/liblingxi_glim_offline.so")
    except BaseException:
        report["result"] = "failed_or_interrupted"
        raise
    finally:
        (output / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def explicit_diagnostic_profile(profile, diagnostic, hypothesis):
    if hypothesis not in HYPOTHESES:
        raise ValueError("explicit_scan_time_hypothesis_required")
    if (profile.get("purpose") != "offline_glim_trial"
            or profile.get("inputClassification") != "target_sensor_profile"
            or profile.get("reviewedForOfflineTrial") is not True
            or profile.get("timingHypothesesOnlyNotCalibration") is not True
            or profile.get("accelerationScale") != 1.0
            or profile.get("angularVelocityScale") != 1.0
            or profile.get("imuTimeOffsetSeconds") != 0.0
            or profile.get("cloudTimeOffsetSeconds") != 0.0):
        raise ValueError("explicit_reviewed_si_and_zero_additional_offset_hypotheses_required")
    for value in diagnostic["datasets"].values():
        observed = value["staticExtrinsicObservationNotCalibration"]["T_lidar_imu"]
        if not np.allclose(profile.get("T_lidar_imu"), observed, atol=1e-12, rtol=0):
            raise ValueError("reviewed_extrinsic_must_match_both_recordings")
    if (diagnostic.get("purpose") != "recorded_sensor_semantics_diagnostic_not_calibration"
            or any(diagnostic["boundaries"].values())):
        raise ValueError("unapproved_aggregate_sensor_diagnostic_required")


class Native:
    def __init__(self, library, config, module):
        self.library = ctypes.CDLL(str(library))
        pointer = ctypes.POINTER(ctypes.c_double)
        self.library.lingxi_error.restype = ctypes.c_char_p
        self.library.lingxi_open.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        self.library.lingxi_imu.argtypes = [ctypes.c_double, pointer, pointer]
        self.library.lingxi_frame.argtypes = [
            ctypes.c_double, ctypes.c_int, pointer, pointer, pointer, pointer,
        ]
        self.library.lingxi_close.argtypes = []
        self.check(self.library.lingxi_open(os.fsencode(config), os.fsencode(module)))

    def check(self, result):
        if result < 0:
            raise RuntimeError(self.library.lingxi_error().decode("utf-8", errors="replace"))
        return result

    @staticmethod
    def pointer(array):
        return array.ctypes.data_as(ctypes.POINTER(ctypes.c_double))

    def imu(self, stamp, acceleration, angular):
        self.check(self.library.lingxi_imu(stamp, self.pointer(acceleration), self.pointer(angular)))

    def frame(self, stamp, points, offsets):
        pose, statistics = np.empty((4, 4)), np.empty(4)
        status = self.check(self.library.lingxi_frame(
            stamp, len(points), self.pointer(points), self.pointer(offsets),
            self.pointer(pose), self.pointer(statistics),
        ))
        return (valid_pose(pose) if status else None), statistics

    def close(self):
        return self.check(self.library.lingxi_close())


def run(args, native_factory=Native, on_native_closed=None):
    if (platform.system() != "Linux" or platform.machine() != "x86_64"
            or os.geteuid() == 0 or os.sys.prefix == os.sys.base_prefix):
        raise ValueError("ordinary_user_isolated_linux_x86_environment_required")
    if metadata.version("rosbags") != "0.11.9":
        raise ValueError("reviewed_rosbags_version_required")
    output, data_root = args.output.resolve(), args.data_root.resolve(strict=True)
    if output.exists():
        raise ValueError("existing_glim_diagnostic_is_preserved")
    if data_root == output or data_root in output.parents:
        raise ValueError("output_must_not_be_inside_recordings")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    diagnostic = json.loads(args.diagnostic.read_text(encoding="utf-8"))
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    explicit_diagnostic_profile(profile, diagnostic, args.hypothesis)
    if diagnostic["captureManifestSha256"] != sha256(args.manifest):
        raise ValueError("sensor_diagnostic_capture_mismatch")
    integrity = verify(manifest, data_root)
    native_build = json.loads((args.adapter / "build-report.json").read_text())
    library = args.adapter / "build/liblingxi_glim_offline.so"
    if (native_build.get("result") != "adapter_built_not_algorithm_acceptance"
            or native_build.get("sourceCommits") != PINS
            or native_build.get("librarySha256") != sha256(library)
            or native_build.get("adapterSourceSha256") != sha256(ROOT / "tools/glim-replay/offline.cpp")
            or native_build.get("adapterCmakeSha256") != sha256(ROOT / "tools/glim-replay/CMakeLists.txt")
            or native_build.get("coreBuildReportSha256") != sha256(args.work / "build-report.json")):
        raise ValueError("replay_adapter_build_provenance_mismatch")
    if os.environ.get("LD_LIBRARY_PATH") != library_environment(args.work)["LD_LIBRARY_PATH"]:
        raise ValueError("explicit_reviewed_native_library_path_required")
    from rosbags.highlevel import AnyReader
    from rosbags.typesys import Stores, get_typestore

    output.mkdir(mode=0o700, parents=True)
    old_umask = os.umask(0o077)
    report = {
        "schemaVersion": 1, "purpose": "real_glim_inertial_odometry_diagnostic_not_navigation",
        "result": "running", "dataset": args.dataset, "timingHypothesis": args.hypothesis,
        "runnerSha256": sha256(__file__), "profileSha256": sha256(args.profile),
        "captureManifestSha256": sha256(args.manifest),
        "sensorDiagnosticSha256": sha256(args.diagnostic),
        "adapterBuildReportSha256": sha256(args.adapter / "build-report.json"),
        "captureIntegrityResult": integrity["result"], "sourceCommits": PINS,
        "host": {"system": platform.system(), "machine": platform.machine(),
                 "python": platform.python_version()},
        "versions": {"rosbags": metadata.version("rosbags"), "numpy": metadata.version("numpy")},
        "nativeLibrariesSha256": {
            name: sha256(args.work / "prefix/lib" / name)
            for name in ("libglim.so", "libodometry_estimation_cpu.so", "libgtsam_points.so",
                         "libgtsam.so", "libgtsam_unstable.so")
        },
        "processedScans": 0, "skippedUnbracketedScans": 0, "initializationScans": 0,
        "boundaries": {
            "containsRosNode": False, "containsMotionAdapter": False,
            "timingHypothesisValidated": False, "robotCalibrationValidated": False,
            "groundTruthAvailable": False, "loopClosureAlgorithmEnabled": False,
            "globalMappingExecuted": False, "mapPersistenceValidated": False,
            "relocalizationValidated": False, "targetArm64RuntimeValidated": False,
            "robotPerformanceBenchmark": False, "navigationValidated": False,
            "deploymentApproved": False, "rawDataPublished": False,
        },
    }

    def checkpoint():
        (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")

    native = None
    original_cwd = Path.cwd()
    stdout_copy, stderr_copy = os.dup(1), os.dup(2)
    try:
        shutil.copyfile(__file__, output / "runner-source.py")
        generate(args.upstream, output / "config", profile)
        checkpoint()
        os.chdir(output)
        with (output / "private-native.log").open("x") as log:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            native = native_factory(library, output / "config", args.work / "prefix/lib/libodometry_estimation_cpu.so")
            expected = manifest["datasets"][args.dataset]
            bag = data_root / expected["directoryName"]
            imu_stamps, accelerations, angular = [], [], []
            with AnyReader([bag], default_typestore=get_typestore(Stores.ROS2_HUMBLE)) as reader:
                connections = [item for item in reader.connections if item.topic == IMU]
                for connection, _, payload in reader.messages(connections=connections):
                    message = reader.deserialize(payload, connection.msgtype)
                    if message.header.frame_id != "lidar_imu_chest_front":
                        raise ValueError("unexpected_imu_frame")
                    quaternion(message.orientation)  # Checked but never used as an attitude measurement.
                    imu_stamps.append(stamp_ns(message))
                    accelerations.append(vector(message.linear_acceleration))
                    angular.append(vector(message.angular_velocity))
            if (len(imu_stamps) != expected["topicCounts"]["imu"]
                    or np.any(np.diff(imu_stamps) <= 0)):
                raise ValueError("ordered_complete_imu_required")
            imu_stamps = np.asarray(imu_stamps, dtype=np.int64)
            origin = int(imu_stamps[0])
            imu_times = (imu_stamps - origin).astype(np.float64) / 1e9 + 1000
            cursor, seen, last_stamp = 0, 0, None
            poses, pose_stamps, algorithm_stamps, measurements = [], [], [], []
            started, cpu_started = time.monotonic(), time.process_time()
            with AnyReader([bag], default_typestore=get_typestore(Stores.ROS2_HUMBLE)) as reader:
                connections = [item for item in reader.connections if item.topic == CLOUD]
                for connection, _, payload in reader.messages(connections=connections):
                    message = reader.deserialize(payload, connection.msgtype)
                    seen += 1
                    stamp = stamp_ns(message)
                    if (message.header.frame_id != "lidar_chest_front"
                            or (last_stamp is not None and stamp <= last_stamp)):
                        raise ValueError("ordered_expected_cloud_required")
                    last_stamp = stamp
                    points, offsets, dropped = cloud_arrays(message)
                    if dropped:
                        raise ValueError("diagnostic_requires_finite_points")
                    frame_stamp = (stamp - origin) / 1e9 + 1000
                    if args.hypothesis == "header_is_scan_end":
                        frame_stamp -= offsets.max()
                    scan_end = frame_stamp + offsets.max()
                    if frame_stamp < imu_times[0] or scan_end > imu_times[-1]:
                        report["skippedUnbracketedScans"] += 1
                        continue
                    while cursor < len(imu_times) and (cursor == 0 or imu_times[cursor - 1] < scan_end):
                        native.imu(imu_times[cursor], accelerations[cursor], angular[cursor])
                        cursor += 1
                    pose, statistics = native.frame(frame_stamp, points, offsets)
                    measurements.append(statistics)
                    report["processedScans"] += 1
                    if pose is None:
                        report["initializationScans"] += 1
                    else:
                        poses.append(pose)
                        pose_stamps.append(stamp)
                        algorithm_stamps.append(frame_stamp)
                    if report["processedScans"] % 100 == 0:
                        checkpoint()
            report["processingWallSeconds"] = time.monotonic() - started
            report["processCpuSeconds"] = time.process_time() - cpu_started
            report["remainingFixedLagFramesAtEnd"] = native.close()
            if on_native_closed is not None:
                on_native_closed(native, report)
            native = None
        if seen != expected["topicCounts"]["cloud"] or len(poses) < 2:
            raise ValueError("complete_capture_and_initialized_trajectory_required")
        poses, measurements = np.stack(poses), np.stack(measurements)
        positions = poses[:, :3, 3]
        steps = np.linalg.norm(np.diff(positions, axis=0), axis=1)
        rotations = poses[:-1, :3, :3].transpose(0, 2, 1) @ poses[1:, :3, :3]
        angles = np.degrees(np.arccos(np.clip((np.trace(rotations, axis1=1, axis2=2) - 1) / 2, -1, 1)))
        np.savez_compressed(output / "private-trajectory.npz", poses=poses, header_stamps_ns=pose_stamps,
                            algorithm_stamps_relative_seconds=algorithm_stamps,
                            preprocess_odometry_pointcount_marginalized=measurements)
        report.update({
            "result": "all_scans_accounted_for_not_quality_acceptance",
            "recordedScans": seen, "estimatedPoses": len(poses), "imuSamplesInserted": cursor,
            "unusedTrailingImuSamples": len(imu_times) - cursor,
            "preprocessMillisecondsP50P95P99": np.percentile(measurements[:, 0], [50, 95, 99]).tolist(),
            "odometryMillisecondsP50P95P99": np.percentile(measurements[:, 1], [50, 95, 99]).tolist(),
            "combinedMillisecondsMaximum": float(measurements[:, :2].sum(axis=1).max()),
            "preprocessedPointCountMinMax": [int(measurements[:, 2].min()), int(measurements[:, 2].max())],
            "peakResidentMiB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "trajectoryKind": "online_lidar_poses_not_final_smoothed_or_loop_optimized",
            "estimatedMotionNotGroundTruth": {
                "accumulatedPositionStepsRawUnits": float(steps.sum()),
                "endToStartDistanceRawUnits": float(np.linalg.norm(positions[-1] - positions[0])),
                "maximumConsecutivePositionStepRawUnits": float(steps.max()),
                "maximumConsecutiveRotationDegrees": float(angles.max()),
            },
            "privateTrajectorySha256": sha256(output / "private-trajectory.npz"),
            "privateNativeLogSha256": sha256(output / "private-native.log"),
        })
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
            os.chdir(original_cwd)
            try:
                checkpoint()
            finally:
                os.umask(old_umask)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build")
    builder.add_argument("--work", type=Path, required=True)
    builder.add_argument("--output", type=Path, required=True)
    replay = commands.add_parser("run")
    for name in ("manifest", "data-root", "diagnostic", "profile", "adapter", "work", "upstream", "output"):
        replay.add_argument("--" + name, type=Path, required=True)
    replay.add_argument("--dataset", choices=("static", "dynamic"), required=True)
    replay.add_argument("--hypothesis", choices=HYPOTHESES, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    print(json.dumps(build(args.work, args.output) if args.command == "build" else run(args), indent=2))


if __name__ == "__main__":
    main()
