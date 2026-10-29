"""Isolated corrected-point extension build and preserved-journal diagnostic.

No ROS initialization, robot connection, deployment, control or new capture.
The frozen replay adapter, worker, stream, configuration and limits are reused.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import threading
import time

import run_glim_supervised_replay as replay
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
NATIVE_NAMES = tuple("robot-slam/native-point-output/" + name for name in (
    "point_output.h", "point_output.cpp", "point_output_contract.cpp", "CMakeLists.txt",
    "exports.map", "verify_exports.cmake"))
SOURCE_NAMES = (*NATIVE_NAMES, "robot-slam/lingxi_slam/point_output.py",
                "tools/run_glim_point_output_trial.py", "tools/glim_matched_session.py")
LIBRARY = "liblingxi_glim_point_output.so"
RESULT = "recorded_corrected_point_output_closed_not_live_or_navigation_acceptance"
BUILD_RESULT = "isolated_corrected_point_extension_built_and_contracts_passed"
LOGICAL_RESULT = "complete_logical_recorded_points_not_paced_or_live_acceptance"


def source_hashes():
    return {name: sha256(ROOT / name) for name in SOURCE_NAMES}


def private_output(path):
    path = Path(path).resolve()
    allowed = ((ROOT / ".local").resolve(), (Path.home() / ".cache/lingxi-next").resolve())
    if path.exists() or not any(path != root and path.is_relative_to(root) for root in allowed):
        raise ValueError("new_ignored_private_point_output_required")
    return path


def build(args):
    from build_slam_cpu_trial import PINS
    from run_glim_offline_trial import library_environment
    import platform

    output = private_output(args.output)
    core = replay.load(args.work / "build-report.json")
    if (sys.platform != "linux" or platform.machine() != "x86_64" or os.geteuid() == 0
            or core["result"] != "core_libraries_built_not_target_acceptance"
            or core["sourceCommits"] != PINS or core["hostMachine"] != "x86_64"):
        raise ValueError("ordinary_user_pinned_cpu_core_required")
    report = {"result": "running", "coreBuildReportSha256": sha256(args.work / "build-report.json"),
              "sourceCommits": PINS, "nativeSourceSha256": {name: sha256(ROOT / name) for name in NATIVE_NAMES},
              "stages": [], "robotContacted": False, "buildType": "Release"}
    output.mkdir(mode=0o700, parents=True)
    environment = library_environment(args.work)
    commands = (
        ("configure", ["cmake", "-S", str(ROOT / "robot-slam/native-point-output"),
                       "-B", str(output / "build"), "-DCMAKE_BUILD_TYPE=Release"]),
        ("build", ["cmake", "--build", str(output / "build"), "--parallel", "2"]),
        ("contracts", ["ctest", "--test-dir", str(output / "build"), "--output-on-failure"]),
    )
    try:
        for name, command in commands:
            with (output / (name + ".log")).open("x") as log:
                result = subprocess.run(command, env=environment, stdout=log, stderr=log, timeout=180)
            report["stages"].append({"name": name, "exitCode": result.returncode})
            if result.returncode:
                raise RuntimeError("point_extension_build_failed_see_private_log")
        report.update(result=BUILD_RESULT, librarySha256=sha256(output / "build" / LIBRARY))
    except BaseException:
        report["result"] = "failed_or_interrupted"
        raise
    finally:
        replay.private_json(output / "build-report.json", report)
    return report


def integrity(args):
    frozen = replay.inputs(args)
    report = replay.load(args.points / "build-report.json")
    if (report["result"] != BUILD_RESULT or report["buildType"] != "Release"
            or report["coreBuildReportSha256"] != frozen["coreBuildReportSha256"]
            or report["sourceCommits"] != frozen["sourceCommits"]
            or report["nativeSourceSha256"] != {name: sha256(ROOT / name) for name in NATIVE_NAMES}
            or report["librarySha256"] != sha256(args.points / "build" / LIBRARY)
            or report["stages"] != [{"name": name, "exitCode": 0} for name in ("configure", "build", "contracts")]):
        raise ValueError("matching_built_and_tested_point_extension_required")
    return {**frozen, "pointExtensionBuildSha256": sha256(args.points / "build-report.json"),
            "pointExtensionLibrarySha256": report["librarySha256"], "pointSourceSha256": source_hashes()}


class PacketSummary:
    """Keep only first/latest bounded copies; no cloud backlog or health payload."""

    def __init__(self):
        self.owner, self.closed = None, False
        self.count, self.total = 0, 0
        self.minimum, self.maximum = None, None
        self.first, self.latest = None, None
        self.digest = hashlib.sha256()
        self.native_counts = None

    def begin(self):
        if self.owner is not None:
            raise RuntimeError("one_point_summary_owner_required")
        self.owner = threading.get_ident()

    def require_owner(self):
        if threading.get_ident() != self.owner or self.closed:
            raise RuntimeError("active_same_thread_point_summary_required")

    def accept(self, packet):
        self.require_owner()
        if self.count >= 20_000 or packet.native_frame_id != self.count + 1:
            raise ValueError("bounded_contiguous_point_packets_required")
        self.digest.update(struct.pack("<QdQ", packet.native_frame_id, packet.algorithm_stamp,
                                       packet.native_point_count))
        self.digest.update(packet.points.astype("<f8", copy=False).tobytes())
        self.digest.update(packet.world_lidar_pose.astype("<f8", copy=False).tobytes())
        count = len(packet.points)
        self.count += 1
        self.total += count
        self.minimum = count if self.minimum is None else min(self.minimum, count)
        self.maximum = count if self.maximum is None else max(self.maximum, count)
        self.first = packet if self.first is None else self.first
        self.latest = packet

    def finish(self, output):
        self.require_owner()
        if not output.closed:
            raise ValueError("native_point_output_closed_before_accounting_required")
        self.native_counts = output.counts()
        self.closed = True

    def report(self, expected_poses):
        if (not self.closed or self.count != expected_poses - 1 or self.count < 1
                or self.native_counts != {"callbacks": expected_poses, "initializationSkips": 1,
                                          "copies": self.count, "takes": self.count, "discardedOnClose": 0}):
            raise ValueError("all_post_initialization_point_packets_and_cleanup_required")
        return {"packets": self.count, "copiedPoints": self.total,
                "copiedPointsPerPacketMinMax": [self.minimum, self.maximum],
                "firstNativeFrameId": self.first.native_frame_id,
                "lastNativeFrameId": self.latest.native_frame_id, "nativeCounts": self.native_counts,
                "allPacketBytesSha256": self.digest.hexdigest(), "maximumRetainedPackets": 2,
                "coordinateFrame": "lingxi_glim_odom", "poseFrame": "lidar_not_robot_base",
                "sourceFrame": "IMU", "kind": "native_deskewed_corrected_odometry",
                "initializationFrameExcluded": True, "globalMapOrLoopClosure": False}


def loaded_extension(args, expected):
    paths = set()
    for line in Path("/proc/self/maps").read_text().splitlines():
        fields = line.split(None, 5)
        if len(fields) == 6 and Path(fields[5]).name == LIBRARY:
            paths.add(Path(fields[5]))
    wanted = (args.points / "build" / LIBRARY).resolve(strict=True)
    if paths != {wanted} or sha256(wanted) != expected:
        raise ValueError("actual_loaded_frozen_point_extension_required")
    return {LIBRARY: expected}


def verified_points(points, expected_poses):
    count = expected_poses - 1
    minimum, maximum = points["copiedPointsPerPacketMinMax"]
    if (type(points["packets"]) is not int or points["packets"] != count or not 1 <= count <= 20_000
            or any(type(value) is not int for value in (minimum, maximum, points["copiedPoints"]))
            or not 1 <= minimum <= maximum <= 8000
            or not minimum * count <= points["copiedPoints"] <= maximum * count
            or points["nativeCounts"] != {"callbacks": expected_poses, "initializationSkips": 1,
                                          "copies": count, "takes": count, "discardedOnClose": 0}
            or any(type(value) is not int for value in points["nativeCounts"].values())
            or type(points["firstNativeFrameId"]) is not int or points["firstNativeFrameId"] != 1
            or type(points["lastNativeFrameId"]) is not int or points["lastNativeFrameId"] != count
            or type(points["maximumRetainedPackets"]) is not int or points["maximumRetainedPackets"] != 2
            or points["coordinateFrame"] != "lingxi_glim_odom"
            or points["poseFrame"] != "lidar_not_robot_base" or points["sourceFrame"] != "IMU"
            or points["kind"] != "native_deskewed_corrected_odometry"
            or points["initializationFrameExcluded"] is not True or points["globalMapOrLoopClosure"] is not False):
        raise ValueError("complete_bounded_corrected_point_accounting_required")
    digest = points["allPacketBytesSha256"]
    if type(digest) is not str or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("complete_point_byte_digest_required")
    return points


def native_factory(args, summary):
    from lingxi_slam.point_output import NativePointOutput, PointOutputNative
    from run_glim_offline_trial import Native

    class ObservedNative(PointOutputNative):
        def close(self):
            was_closed = self.closed
            try:
                return super().close()
            finally:
                if not was_closed:
                    summary.finish(self.output)

    def factory():
        summary.begin()
        output = NativePointOutput(args.points / "build" / LIBRARY)
        return ObservedNative(lambda: Native(
            args.adapter / "build/liblingxi_glim_offline.so", args.bundle / "config",
            args.work / "prefix/lib/libodometry_estimation_cpu.so"), output, summary.accept)
    return factory


def save_samples(output, summary):
    import numpy as np

    result = {}
    for name, packet in (("first", summary.first), ("latest", summary.latest)):
        path = output / ("private-" + name + "-points.npz")
        with path.open("xb") as stream:
            np.savez_compressed(stream, points=packet.points, world_lidar_pose=packet.world_lidar_pose,
                                frame_id=packet.native_frame_id, algorithm_stamp=packet.algorithm_stamp)
        result[name + "SampleSha256"] = sha256(path)
    return result


def child(args, sink):
    import numpy as np
    from importlib import metadata
    from rosbags.typesys import Stores, get_typestore
    from run_glim_stream_trial import actual_libraries

    frozen = integrity(args)
    if any(os.environ.get(key) != value for key, value in replay.child_environment(args.work).items()):
        raise ValueError("unchanged_explicit_child_environment_required")
    types = get_typestore(Stores.ROS2_HUMBLE)
    for name in ("Imu", "PointCloud2"):
        types.get_msgdef("sensor_msgs/msg/" + name)
    summary, loaded = PacketSummary(), {}

    def verify_loaded():
        observed = actual_libraries(args.work, args.adapter, frozen["nativeLibrariesSha256"])
        baseline = replay.load(ROOT / "docs/slam-stream-input-evidence.json")["runs"]["static-start"]
        if observed != baseline["actualLoadedLibrariesSha256"]:
            raise ValueError("unchanged_loaded_core_and_replay_adapter_required")
        loaded.update(loaded_extension(args, frozen["pointExtensionLibrarySha256"]))
        return observed

    code = replay.recorded_child(
        args, sink, integrity=frozen, native_factory=native_factory(args, summary), verify_loaded=verify_loaded,
        decode=lambda kind, data: types.deserialize_cdr(
            data, "sensor_msgs/msg/" + ("Imu" if kind == "imu" else "PointCloud2")),
        verify_inputs=lambda: integrity(args),
        versions={"numpy": np.__version__, "rosbags": metadata.version("rosbags")},
    )
    if code:
        return code
    report = replay.load(args.output / "child-report.json")
    points = summary.report(report["worker"]["stream"]["counts"]["estimatedPoses"])
    points["actualLoadedExtensionSha256"] = loaded
    # These two samples are private and bounded; every packet was already checked.
    points.update(save_samples(args.output, summary))
    replay.private_json(args.output / "point-report.json", points)
    return 0


def run(args):
    frozen = integrity(args)
    output = private_output(args.output)
    if any(output == path or path in output.parents
           for path in (args.work, args.adapter, args.points, args.bundle, args.reference)):
        raise ValueError("separate_private_point_output_required")
    output.mkdir(mode=0o700, parents=True)
    staged, runtime, _ = replay.stage_private_inputs(args)
    for name in SOURCE_NAMES:
        target = runtime / name
        if not target.exists():
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        if sha256(target) != sha256(ROOT / name):
            raise ValueError("identical_point_runtime_source_required")
    command = [sys.executable, "-B", str(runtime / "tools/run_glim_point_output_trial.py"), "child"]
    for name in ("work", "adapter", "points", "bundle", "reference", "output", "hypothesis", "rate"):
        command.extend(("--" + name, str(getattr(staged, name))))
    owner = replay.ProcessSupervisor(command, cwd=output, environment=replay.child_environment(args.work),
                                     stderr_path=output / "private-child.log", limits=replay.LIMITS)
    report = {"schemaVersion": 1, "result": "running", "inputIntegrity": frozen,
              "limits": asdict(replay.LIMITS), "boundaries": dict.fromkeys(replay.BOUNDARIES, False),
              "replayRate": args.rate, "healthChannelContainsPoints": False}
    try:
        report["process"] = replay.coordinate(owner, frozen["journal"]["records"], 110.)
        child_report = replay.load(output / "child-report.json")
        replay.complete_child(child_report, frozen["journal"])
        if (child_report["result"] != replay.CHILD_RESULT or child_report["inputIntegrity"] != frozen
                or child_report["session"] != report["process"]["session"] or integrity(args) != frozen):
            raise ValueError("same_owned_child_and_unchanged_point_inputs_required")
        points = replay.load(output / "point-report.json")
        verified_points(points, child_report["worker"]["stream"]["counts"]["estimatedPoses"])
        if points["actualLoadedExtensionSha256"] != {LIBRARY: frozen["pointExtensionLibrarySha256"]}:
            raise ValueError("same_loaded_point_extension_required")
        report.update(result=RESULT, childReportSha256=sha256(output / "child-report.json"),
                      pointReportSha256=sha256(output / "point-report.json"))
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        report["process"] = owner.stop()
        report["privateLogSha256"] = sha256(output / "private-child.log")
        replay.private_json(output / "report.json", report)
    return report


def logical(args):
    """Synchronous recorded-clock correctness check, explicitly not a worker trial."""
    import numpy as np
    import platform
    from rosbags.typesys import Stores, get_typestore
    from glim_matched_session import replay_prefix
    from run_glim_stream_trial import actual_libraries

    frozen, output = integrity(args), private_output(args.output)
    if any(os.environ.get(key) != value for key, value in replay.child_environment(args.work).items()):
        raise ValueError("unchanged_explicit_child_environment_required")
    output.mkdir(mode=0o700, parents=True)
    report = {"schemaVersion": 1, "result": "running", "inputIntegrity": frozen,
              "boundaries": dict.fromkeys(replay.BOUNDARIES, False),
              "arrivalClock": "original_journal_elapsed_not_monotonic_wall_or_paced",
              "sensorWorkerOrProcessSupervisorExecuted": False,
              "nativeOwner": "calling_main_thread_only",
              "host": {"machine": platform.machine(), "numpy": np.__version__}}
    calls, owner = dict.fromkeys(("create", "imu", "frame", "close"), 0), threading.get_ident()
    summary, native = PacketSummary(), None
    started = time.monotonic()
    types = get_typestore(Stores.ROS2_HUMBLE)

    def record(name):
        if threading.get_ident() != owner:
            raise RuntimeError("one_logical_native_owner_required")
        calls[name] += 1

    def verify_loaded():
        loaded = actual_libraries(args.work, args.adapter, frozen["nativeLibrariesSha256"])
        baseline = replay.load(ROOT / "docs/slam-stream-input-evidence.json")["runs"]["static-start"]
        if loaded != baseline["actualLoadedLibrariesSha256"]:
            raise ValueError("unchanged_loaded_logical_core_required")
        report["actualLoadedLibrariesSha256"] = loaded
        report["actualLoadedExtensionSha256"] = loaded_extension(args, frozen["pointExtensionLibrarySha256"])

    stdout_copy, stderr_copy = os.dup(1), os.dup(2)
    try:
        with (output / "private-native.log").open("x") as log:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            native = replay.OwnedNative(native_factory(args, summary), record, verify_loaded)
            state, arrays = replay_prefix(
                args.reference / "private-callbacks.cdrjournal", frozen["journal"]["records"], args.hypothesis,
                native, lambda kind, data: types.deserialize_cdr(
                    data, "sensor_msgs/msg/" + ("Imu" if kind == "imu" else "PointCloud2")))
            report["stream"] = state
            counts, expected = state["counts"], frozen["journal"]["counts"]
            if (state["state"] != "finished" or state["failure"]
                    or counts["receivedImu"] != expected["imu"] or counts["receivedClouds"] != expected["cloud"]
                    or sum(counts[key] for key in (
                        "processedScans", "skippedBeforeImu", "skippedAfterImuAtEndOfInput")) != expected["cloud"]
                    or calls["imu"] != counts["insertedImu"] or calls["frame"] != counts["processedScans"]
                    or len(arrays["poses"]) != counts["estimatedPoses"]
                    or not all(np.isfinite(values).all() for values in arrays.values())):
                raise ValueError("complete_logical_recorded_frame_accounting_required")
            report["remainingFixedLagFramesAtEnd"] = native.close()
            native = None
            report["points"] = summary.report(counts["estimatedPoses"])
            verified_points(report["points"], counts["estimatedPoses"])
            report["points"].update(save_samples(output, summary))
            if integrity(args) != frozen:
                raise ValueError("unchanged_logical_point_inputs_required")
            report["result"] = LOGICAL_RESULT
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
            report.update(nativeCalls=calls, privateLogSha256=sha256(output / "private-native.log"),
                          totalWallSecondsNotRealtimePerformance=time.monotonic() - started)
            replay.private_json(output / "logical-report.json", report)
    return report


def evidence(trial):
    import numpy as np
    from run_kiss_offline_trial import valid_pose

    parent = replay.load(trial / "report.json")
    child_report = replay.load(trial / "child-report.json")
    points = replay.load(trial / "point-report.json")
    frozen, process = parent["inputIntegrity"], parent["process"]
    if (parent["result"] != RESULT or child_report["result"] != replay.CHILD_RESULT
            or parent["childReportSha256"] != sha256(trial / "child-report.json")
            or parent["pointReportSha256"] != sha256(trial / "point-report.json")
            or parent["privateLogSha256"] != sha256(trial / "private-child.log")
            or child_report["inputIntegrity"] != frozen or frozen["sourceSha256"] != replay.hashes()
            or frozen["pointSourceSha256"] != source_hashes()
            or parent["boundaries"] != dict.fromkeys(replay.BOUNDARIES, False)
            or parent["healthChannelContainsPoints"] is not False
            or child_report["session"] != process["session"]
            or parent["replayRate"] != child_report["replayRate"]):
        raise ValueError("matching_immutable_point_output_reports_required")
    replay.replay_rate(parent["replayRate"])
    replay.complete_child(child_report, frozen["journal"])
    if (process["state"] != "stopped" or process["exitCode"] != 0 or process["forcedTermination"]
            or process["processAlive"] or process["monitorAlive"] or not process["cleanupComplete"]
            or any(process[key] for key in ("mappingReady", "localizationReady", "navigationReady"))
            or process["completedMessages"] != frozen["journal"]["records"]
            or process["processedScans"] != child_report["nativeCalls"]["frame"]
            or process["estimatedPoses"] != child_report["worker"]["stream"]["counts"]["estimatedPoses"]):
        raise ValueError("complete_closed_point_output_process_required")
    verified_points(points, process["estimatedPoses"])
    baseline = replay.load(ROOT / "docs/slam-stream-input-evidence.json")["runs"]["static-start"]
    if (child_report["actualLoadedLibrariesSha256"] != baseline["actualLoadedLibrariesSha256"]
            or points["actualLoadedExtensionSha256"] != {LIBRARY: frozen["pointExtensionLibrarySha256"]}):
        raise ValueError("same_recorded_core_and_loaded_extension_required")
    stamps = []
    for name, frame_id in (("first", 1), ("latest", points["packets"])):
        path = trial / ("private-" + name + "-points.npz")
        if points[name + "SampleSha256"] != sha256(path):
            raise ValueError("unchanged_private_bounded_point_sample_required")
        with np.load(path, allow_pickle=False) as sample:
            if set(sample.files) != {"points", "world_lidar_pose", "frame_id", "algorithm_stamp"}:
                raise ValueError("exact_private_point_sample_schema_required")
            xyz, pose = sample["points"], sample["world_lidar_pose"]
            stamp = float(sample["algorithm_stamp"])
            if (xyz.ndim != 2 or xyz.shape[1] != 3 or xyz.dtype != np.float64
                    or not points["copiedPointsPerPacketMinMax"][0] <= len(xyz)
                    <= points["copiedPointsPerPacketMinMax"][1]
                    or not np.isfinite(xyz).all() or sample["frame_id"].shape != ()
                    or int(sample["frame_id"]) != frame_id or pose.shape != (4, 4)
                    or sample["algorithm_stamp"].shape != () or not math.isfinite(stamp)):
                raise ValueError("finite_matching_private_point_sample_required")
            valid_pose(pose)
            stamps.append(stamp)
    if points["packets"] > 1 and not stamps[0] < stamps[1]:
        raise ValueError("ordered_private_point_sample_stamps_required")
    # Explicit scalar allowlist: never publish poses, XYZ, paths or raw journals.
    public_points = {key: points[key] for key in (
        "packets", "copiedPoints", "copiedPointsPerPacketMinMax", "firstNativeFrameId", "lastNativeFrameId",
        "nativeCounts", "allPacketBytesSha256", "maximumRetainedPackets", "coordinateFrame", "poseFrame",
        "sourceFrame", "kind", "initializationFrameExcluded", "globalMapOrLoopClosure")}
    return {
        "schemaVersion": 1, "date": "2026-10-29", "result": RESULT,
        "sourceCommits": frozen["sourceCommits"], "pointSourceSha256": source_hashes(),
        "frozenSourceSha256": replay.hashes(),
        "privateReportsSha256": {name: sha256(trial / name)
                                for name in ("report.json", "child-report.json", "point-report.json")},
        "inputJournal": {**frozen["journal"], "sha256": frozen["journalSha256"]},
        "actualLoadedLibrariesSha256": child_report["actualLoadedLibrariesSha256"],
        "actualLoadedExtensionSha256": points["actualLoadedExtensionSha256"],
        "host": {"machine": child_report["machine"], "versions": child_report["versions"]},
        "process": {key: value for key, value in process.items() if key != "session"},
        "nativeCalls": child_report["nativeCalls"], "points": public_points,
        "replayTiming": replay.verified_timing(child_report, frozen["journal"]),
        "observedWorkerTiming": replay.observed_worker_timing(child_report),
        "boundaries": parent["boundaries"], "healthChannelContainsPoints": False,
        "replayRate": parent["replayRate"], "pacedRecordedInputNotLivePerformance": True,
        "targetArm64PointExtensionValidated": False, "webOrAndroidIntegrated": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build")
    for name in ("work", "output"):
        builder.add_argument("--" + name, type=Path, required=True)
    for name in ("run", "child", "logical"):
        command = commands.add_parser(name)
        for field in ("work", "adapter", "points", "bundle", "reference", "output"):
            command.add_argument("--" + field, type=Path, required=True)
        command.add_argument("--hypothesis", choices=("header_is_scan_start", "header_is_scan_end"), required=True)
        command.add_argument("--rate", type=float, choices=(.5, 1.), required=True)
    export = commands.add_parser("export")
    for name in ("trial", "output"):
        export.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    previous = os.umask(0o077)
    try:
        if args.command == "child":
            with os.fdopen(os.dup(1), "wb", buffering=0) as sink:
                os.dup2(2, 1)
                return child(args, sink)
        if args.command == "export":
            report = evidence(args.trial)
            replay.private_json(args.output, report)
        else:
            report = {"build": build, "logical": logical, "run": run}[args.command](args)
        print(json.dumps({"result": report["result"]}))
        return 0
    finally:
        os.umask(previous)


if __name__ == "__main__":
    raise SystemExit(main())
