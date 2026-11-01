"""Build and replay immediate acquisition rays offline, without robot IO."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET

from build_slam_cpu_trial import PINS
from run_glim_offline_trial import library_environment
from run_glim_point_output_trial import private_output
from run_glim_supervised_replay import load, private_json
import run_glim_supervised_replay as replay
from verify_slam_capture import sha256

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = "liblingxi_glim_acquisition_output.so"
NATIVE_NAMES = tuple("robot-slam/native-acquisition-output/" + name for name in (
    "acquisition.h", "acquisition.cpp", "acquisition_contract.cpp", "CMakeLists.txt", "exports.map"))
SOURCE_NAMES = (*NATIVE_NAMES, "robot-slam/lingxi_slam/acquisition_output.py",
                "robot-slam/lingxi_slam/observation_archive.py", "robot-slam/lingxi_slam/point_output.py",
                "tools/run_glim_acquisition_trial.py", "tools/glim_matched_session.py")
RESULT = "isolated_acquisition_extension_synthetic_contracts_not_live_acceptance"
REPLAY_RESULT = "complete_recorded_immediate_acquisition_not_live_or_metric_acceptance"


def source_hashes():
    return {name: sha256(ROOT / name) for name in SOURCE_NAMES}


def verify_synthetic_copy(data):
    """Compare actual C++ bytes with independently composed per-return transforms."""
    import numpy as np
    from scipy.spatial.transform import Rotation
    sys.path.insert(0, str(ROOT / "robot-slam"))
    from lingxi_slam.acquisition_output import decode_acquisition

    def pose(axis, angle, xyz):
        value = np.eye(4)
        value[:3, :3] = Rotation.from_euler(axis, angle, degrees=True).as_matrix()
        value[:3, 3] = xyz
        return value

    reference = pose("z", 20, [2, 3, 4])
    corrected = pose("y", -15, [-2, 1, 3])
    extrinsic = pose("x", 30, [.2, -.1, .3])
    packet = decode_acquisition(data, (1, 3, 2, 840), 1002., corrected @ np.linalg.inv(extrinsic))
    for index, (fraction, height) in enumerate(((0, -.2), (.5, .5), (1, 2))):
        imu_at_return = pose("z", 20 + 90 * fraction, np.array([2, 3, 4]) + 2 * fraction)
        odom_lidar = corrected @ np.linalg.inv(reference) @ imu_at_return @ np.linalg.inv(extrinsic)
        np.testing.assert_allclose(packet.origins[index], (odom_lidar @ [0, 0, 0, 1])[:3], rtol=0, atol=1e-12)
        np.testing.assert_allclose(packet.endpoints[index], (odom_lidar @ [1, 0, height, 1])[:3], rtol=0, atol=1e-12)
    if (packet.origins.flags.writeable or packet.endpoints.flags.writeable or packet.world_lidar_pose.flags.writeable
            or np.allclose(packet.origins[0], packet.origins[-1])):
        raise ValueError("owned_readonly_moving_acquisition_geometry_required")
    return {"returns": 3, "trajectorySamples": 2, "coordinateFrame": packet.coordinate_frame,
            "sourceSha256": packet.source_sha256, "archiveSha256": hashlib.sha256(data).hexdigest(),
            "nonzeroLidarExtrinsic": True, "noncommutingPoseCorrection": True,
            "rawGroundAndHighReturnsPreserved": True, "perReturnOriginsVerified": True}


def build(work, output):
    output = private_output(output)
    core = load(work / "build-report.json")
    if (sys.platform != "linux" or platform.machine() != "x86_64" or os.geteuid() == 0
            or core["result"] != "core_libraries_built_not_target_acceptance"
            or core["sourceCommits"] != PINS or core["hostMachine"] != "x86_64"):
        raise ValueError("ordinary_user_pinned_cpu_acquisition_core_required")
    output.mkdir(mode=0o700, parents=True)
    environment = library_environment(work)
    report = {"result": "running", "sourceCommits": PINS, "hostMachine": platform.machine(),
              "coreBuildReportSha256": sha256(work / "build-report.json"),
              "sourceSha256": source_hashes(),
              "stages": [], "robotContacted": False, "targetArm64Validated": False,
              "liveTerrainIntegrated": False, "physicalScaleValidated": False, "navigationReady": False}
    commands = (
        ("configure", ["cmake", "-S", str(ROOT / "robot-slam/native-acquisition-output"),
                       "-B", str(output / "build"), "-DCMAKE_BUILD_TYPE=Release"]),
        ("build", ["cmake", "--build", str(output / "build"), "--parallel", "2"]),
        ("contracts", ["ctest", "--test-dir", str(output / "build"), "--output-on-failure",
                       "--output-junit", str(output / "contracts.xml")]),
    )
    try:
        for name, command in commands:
            with (output / (name + ".log")).open("x") as log:
                result = subprocess.run(command, env=environment, stdout=log, stderr=log, timeout=180)
            report["stages"].append({"name": name, "exitCode": result.returncode})
            if result.returncode:
                raise RuntimeError("acquisition_extension_build_failed_see_private_log")
        tests = ET.parse(output / "contracts.xml").getroot().findall("testcase")
        if len(tests) < 29 or any(test.get("status") != "run" or len(test.findall("failure"))
                                 or len(test.findall("skipped")) or len(test.findall("error")) for test in tests):
            raise ValueError("complete_executed_acquisition_native_contracts_required")
        exported = subprocess.run([str(output / "build/acquisition_contract"), "export"],
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=True)
        report["syntheticGeometry"] = verify_synthetic_copy(exported.stdout)
        report.update(result=RESULT, nativeContracts=len(tests),
                      executedContracts=[test.get("name") for test in tests],
                      librarySha256=sha256(output / "build" / LIBRARY))
    except BaseException:
        report["result"] = "failed_or_interrupted"
        raise
    finally:
        private_json(output / "build-report.json", report)
    return report


def recorded_integrity(args):
    import json5
    frozen = replay.inputs(args)
    built = load(args.acquisition / "build-report.json")
    library = args.acquisition / "build" / LIBRARY
    config = json5.loads((args.bundle / "config/config_odometry_cpu.json").read_text(), allow_duplicate_keys=False)
    if (built["result"] != RESULT or built["sourceCommits"] != PINS
            or built["sourceSha256"] != source_hashes()
            or built["coreBuildReportSha256"] != frozen["coreBuildReportSha256"]
            or built["librarySha256"] != sha256(library) or built["nativeContracts"] != 29
            or built["stages"] != [{"name": name, "exitCode": 0} for name in ("configure", "build", "contracts")]
            or config.get("odometry_estimation", {}).get("save_imu_rate_trajectory") is not True):
        raise ValueError("current_tested_acquisition_extension_and_enabled_trajectory_required")
    return {**frozen, "acquisitionBuildReportSha256": sha256(args.acquisition / "build-report.json"),
            "acquisitionLibrarySha256": built["librarySha256"], "acquisitionSourceSha256": source_hashes()}


def loaded_extension(args, expected):
    paths = set()
    for line in Path("/proc/self/maps").read_text().splitlines():
        fields = line.split(None, 5)
        if len(fields) == 6 and Path(fields[5]).name == LIBRARY:
            paths.add(Path(fields[5]))
    wanted = (args.acquisition / "build" / LIBRARY).resolve(strict=True)
    if paths != {wanted} or sha256(wanted) != expected:
        raise ValueError("actual_loaded_current_acquisition_extension_required")
    return {LIBRARY: expected}


class RaySummary:
    """Retain only first/latest packets; all delivered geometry enters the digest."""

    def __init__(self):
        self.owner = threading.get_ident()
        self.count, self.total, self.minimum, self.maximum = 0, 0, None, None
        self.first, self.latest, self.previous_stamp = None, None, None
        self.digest = hashlib.sha256()

    def accept(self, packet):
        import numpy as np
        import struct
        if (threading.get_ident() != self.owner or self.count >= 20_000
                or packet.native_frame_id != self.count + 1
                or self.previous_stamp is not None and packet.algorithm_stamp <= self.previous_stamp
                or packet.coordinate_frame != "lingxi_glim_odom"
                or packet.kind != "native_per_return_original_lio_acquisition"
                or packet.origins.ndim != 2 or packet.origins.shape[1] != 3
                or packet.endpoints.shape != packet.origins.shape or not 1 <= len(packet.origins) <= 65536
                or any(not np.isfinite(value).all() or value.flags.writeable
                       for value in (packet.origins, packet.endpoints, packet.world_lidar_pose))):
            raise ValueError("owned_bounded_contiguous_acquisition_packets_required")
        count = len(packet.origins)
        self.digest.update(struct.pack("<QdQ", packet.native_frame_id, packet.algorithm_stamp, count))
        self.digest.update(bytes.fromhex(packet.source_sha256))
        for value in (packet.origins, packet.endpoints, packet.world_lidar_pose):
            self.digest.update(value.astype("<f8", copy=False).tobytes())
        self.count += 1
        self.total += count
        self.minimum = count if self.minimum is None else min(self.minimum, count)
        self.maximum = count if self.maximum is None else max(self.maximum, count)
        self.first = packet if self.first is None else self.first
        self.latest, self.previous_stamp = packet, packet.algorithm_stamp

    def report(self, output, expected_poses):
        expected = {"callbacks": expected_poses, "initializationSkips": 1,
                    "copies": self.count, "takes": self.count, "discardedOnClose": 0}
        if (threading.get_ident() != self.owner or not output.closed or self.count < 1
                or self.count != expected_poses - 1 or output.counts() != expected):
            raise ValueError("complete_closed_recorded_acquisition_accounting_required")
        return {"packets": self.count, "returns": self.total,
                "returnsPerPacketMinMax": [self.minimum, self.maximum], "nativeCounts": expected,
                "firstNativeFrameId": self.first.native_frame_id, "lastNativeFrameId": self.latest.native_frame_id,
                "allPacketGeometryAndSourceSha256": self.digest.hexdigest(), "maximumRetainedPackets": 2,
                "coordinateFrame": "lingxi_glim_odom", "poseFrame": "lidar_not_robot_base",
                "allPreprocessedReturnsPreserved": True, "perReturnAcquisitionTimeBracketed": True,
                "initializationFrameExcluded": True, "receiptOrMetricAdmissionSynthesized": False}


def recorded(args):
    """Original-arrival-order correctness replay, not paced or worker performance."""
    import numpy as np
    from rosbags.typesys import Stores, get_typestore
    from glim_matched_session import replay_prefix
    from lingxi_slam.acquisition_output import NativeAcquisitionOutput
    from lingxi_slam.point_output import PointOutputNative
    from run_glim_offline_trial import Native
    from run_glim_stream_trial import actual_libraries

    frozen, destination = recorded_integrity(args), private_output(args.output)
    if (any(os.environ.get(key) != value for key, value in replay.child_environment(args.work).items())
            or any(destination == path or path in destination.parents for path in (
                args.work, args.adapter, args.acquisition, args.bundle, args.reference))):
        raise ValueError("explicit_recorded_environment_and_separate_private_output_required")
    destination.mkdir(mode=0o700, parents=True)
    report = {"schemaVersion": 1, "result": "running", "inputIntegrity": frozen,
              "boundaries": dict.fromkeys(replay.BOUNDARIES, False),
              "arrivalClock": "original_journal_elapsed_not_monotonic_wall_or_paced",
              "sensorWorkerOrProcessSupervisorExecuted": False, "liveTerrainIntegrated": False,
              "physicalScaleValidated": False, "navigationReady": False,
              "host": {"machine": platform.machine(), "numpy": np.__version__}}
    calls, owner = dict.fromkeys(("create", "imu", "frame", "close"), 0), threading.get_ident()
    summary, native, output = RaySummary(), None, None
    started = time.monotonic()
    types = get_typestore(Stores.ROS2_HUMBLE)

    def record(name):
        if threading.get_ident() != owner:
            raise RuntimeError("same_recorded_acquisition_native_owner_required")
        calls[name] += 1

    def factory():
        nonlocal output
        output = NativeAcquisitionOutput(args.acquisition / "build" / LIBRARY)
        return PointOutputNative(lambda: Native(
            args.adapter / "build/liblingxi_glim_offline.so", args.bundle / "config",
            args.work / "prefix/lib/libodometry_estimation_cpu.so"), output, summary.accept)

    def verify_loaded():
        report["actualLoadedExtensionSha256"] = loaded_extension(args, frozen["acquisitionLibrarySha256"])
        observed = actual_libraries(args.work, args.adapter, frozen["nativeLibrariesSha256"])
        baseline = load(ROOT / "docs/slam-stream-input-evidence.json")["runs"]["static-start"]
        if observed != baseline["actualLoadedLibrariesSha256"]:
            raise ValueError("unchanged_loaded_acquisition_replay_core_required")
        report["actualLoadedLibrariesSha256"] = observed
        return observed

    stdout, stderr = os.dup(1), os.dup(2)
    try:
        with (destination / "private-native.log").open("x") as log:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            native = replay.OwnedNative(factory, record, verify_loaded)
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
                    or not all(np.isfinite(value).all() for value in arrays.values())):
                raise ValueError("complete_original_journal_acquisition_replay_required")
            report["remainingFixedLagFramesAtEnd"] = native.close()
            native = None
            if calls["create"] != 1 or calls["close"] != 1:
                raise ValueError("one_closed_recorded_acquisition_native_required")
            report["acquisition"] = summary.report(output, counts["estimatedPoses"])
            for name, packet in (("first", summary.first), ("latest", summary.latest)):
                path = destination / ("private-" + name + "-rays.npz")
                with path.open("xb") as stream:
                    np.savez_compressed(stream, origins=packet.origins, endpoints=packet.endpoints,
                        world_lidar_pose=packet.world_lidar_pose, frame_id=packet.native_frame_id,
                        algorithm_stamp=packet.algorithm_stamp, source_sha256=packet.source_sha256)
                report["acquisition"][name + "SampleSha256"] = sha256(path)
            if recorded_integrity(args) != frozen:
                raise ValueError("unchanged_recorded_acquisition_inputs_required")
            report["result"] = REPLAY_RESULT
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        try:
            if native is not None:
                native.close()
        finally:
            os.dup2(stdout, 1)
            os.dup2(stderr, 2)
            os.close(stdout)
            os.close(stderr)
            report.update(nativeCalls=calls, privateLogSha256=sha256(destination / "private-native.log"),
                          totalWallSecondsNotRealtimePerformance=time.monotonic() - started)
            private_json(destination / "recorded-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build")
    for name in ("work", "output"):
        builder.add_argument("--" + name, type=Path, required=True)
    runner = commands.add_parser("recorded")
    for name in ("work", "adapter", "acquisition", "bundle", "reference", "output"):
        runner.add_argument("--" + name, type=Path, required=True)
    runner.add_argument("--hypothesis", choices=("header_is_scan_start", "header_is_scan_end"), required=True)
    runner.add_argument("--rate", type=float, choices=(.5, 1.), required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    previous = os.umask(0o077)
    try:
        report = build(args.work, args.output) if args.command == "build" else recorded(args)
        print(json.dumps({"result": report["result"],
                          "nativeContracts": report.get("nativeContracts"),
                          "acquisition": report.get("acquisition")}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
