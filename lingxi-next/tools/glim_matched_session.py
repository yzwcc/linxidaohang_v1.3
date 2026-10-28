"""Capture actual callback CDR; replay its completed prefix without starting ROS."""
import argparse
import json
import os
from pathlib import Path
import resource
import shutil
import threading
import time

import numpy as np

from glim_live_session import (
    ARRAYS, BOUNDARIES, DDS_PROFILE, MeasuredWorker, NativeOwner, Observations,
    duration, metrics, validate_environment as live_environment,
)
from glim_target_controlled_mapping import configuration_files, verify_control
from glim_target_replay import validate_environment as offline_environment, verify_replay_bundle
from glim_target_worker import observed_libraries
from lingxi_slam.messages import SensorProfile, cloud_snapshot, decode_cloud, imu_snapshot
from lingxi_slam.passive_ros import Topics, run_passive
from lingxi_slam.stream import SensorStream
from run_glim_offline_trial import HYPOTHESES, explicit_diagnostic_profile
from slam_bag_audit import CLOUD, IMU
from slam_callback_journal import Writer, inspect, records
from slam_target_probe import verify_bundle
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
PURPOSE = "arm64_identical_callback_input_glim_diagnostic"
RESULT = "same_input_session_closed_not_calibration_or_navigation_acceptance"
SOURCES = ("tools/glim_matched_session.py", "tools/slam_callback_journal.py")
OUTPUT_ARRAYS = ("frame_headers_ns", "frame_algorithm_seconds", "frame_statistics", "pose_headers_ns", "poses")
COUNT_KEYS = ("receivedImu", "receivedClouds", "insertedImu", "processedScans", "initializationScans",
              "estimatedPoses", "skippedBeforeImu", "skippedAfterImuAtEndOfInput", "discardedOnFailure")
PROFILE = SensorProfile("lidar_chest_front", "lidar_imu_chest_front",
                        "frame_relative_nanoseconds_hypothesis")


class MatchedObservations(Observations):
    def __init__(self):
        super().__init__()
        self.algorithms = []

    def frame(self, header, algorithm, pose, statistics):
        self.algorithms.append(algorithm)
        super().frame(header, algorithm, pose, statistics)

    def arrays(self):
        return {**super().arrays(), "frame_algorithm_seconds": np.asarray(self.algorithms, dtype=np.float64)}


class JournalWorker(MeasuredWorker):
    def __init__(self, factory, hypothesis, observations, journal, serialize, clock=time.monotonic_ns):
        super().__init__(factory, hypothesis, observations)
        self.journal, self.serialize, self.journal_clock = journal, serialize, clock
        self.journal_origin = clock()
        self.journal_costs_ns = []

    def _offer(self, kind, message):
        begin = self.journal_clock()
        self.journal.append(kind, begin - self.journal_origin, bytes(self.serialize(message)))
        self.journal_costs_ns.append(self.journal_clock() - begin)
        if not super()._offer(kind, message):
            raise RuntimeError("captured_callback_rejected_session_not_comparable")
        return True


def replay_prefix(path, completed, hypothesis, native, deserialize):
    if type(completed) is not int or completed <= 0:
        raise ValueError("positive_completed_callback_prefix_required")
    frames, poses = [], []
    def on_frame(header, algorithm, pose, statistics):
        frames.append((header, algorithm, statistics))
        if pose is not None:
            poses.append((header, pose))
    stream = SensorStream(native, hypothesis, on_frame=on_frame)
    seen = 0
    for entry in records(path):
        seen += 1
        if entry.sequence >= completed:
            continue
        message = deserialize(entry.kind, entry.payload)
        if entry.kind == "imu":
            packet = imu_snapshot(message, PROFILE)
            stream.push_imu(packet.stamp_ns, packet.acceleration, packet.angular, entry.elapsed_ns / 1e9)
        else:
            packet = cloud_snapshot(message, PROFILE, stream.limits.maximum_points_per_cloud,
                                    8 * 1024 * 1024)
            points, offsets = decode_cloud(packet)
            stream.push_cloud(packet.stamp_ns, points, offsets, entry.elapsed_ns / 1e9)
    if seen < completed:
        raise ValueError("complete_recorded_callback_prefix_required")
    state = stream.finish()
    return state, {
        "frame_headers_ns": np.asarray([row[0] for row in frames], dtype=np.int64),
        "frame_algorithm_seconds": np.asarray([row[1] for row in frames], dtype=np.float64),
        "frame_statistics": np.asarray([row[2] for row in frames], dtype=np.float64).reshape(-1, 4),
        "pose_headers_ns": np.asarray([row[0] for row in poses], dtype=np.int64),
        "poses": np.asarray([row[1] for row in poses], dtype=np.float64).reshape(-1, 4, 4),
    }


def compare(reference, replay):
    for key in ("frame_headers_ns", "frame_algorithm_seconds", "pose_headers_ns"):
        if not np.array_equal(reference[key], replay[key]):
            raise ValueError("identical_native_frame_domain_and_algorithm_times_required")
    if (not np.array_equal(reference["frame_statistics"][:, 2:], replay["frame_statistics"][:, 2:])
            or reference["poses"].shape != replay["poses"].shape or len(reference["poses"]) < 2
            or not all(np.isfinite(values).all() for values in (*reference.values(), *replay.values()))):
        raise ValueError("identical_initialized_native_counts_and_finite_outputs_required")
    a, b = reference["poses"], replay["poses"]
    translation = np.linalg.norm(a[:, :3, 3] - b[:, :3, 3], axis=1)
    matrix_difference = np.abs(a - b)
    return {
        "exactFrameDomainAndAlgorithmTimes": True,
        "exactPreprocessedPointAndMarginalizedCounts": True,
        "processedScans": len(reference["frame_headers_ns"]), "estimatedPoses": len(a),
        "maximumPoseMatrixElementDifference": float(matrix_difference.max()),
        "positionDifferenceRawUnitsP50P95Max": np.percentile(translation, [50, 95, 100]).tolist(),
        "posesComparedWithoutAlignmentOrScaleFitting": True,
        "physicalAccuracyValidated": False,
    }


def run(args):
    duration(args.seconds)
    (live_environment if args.mode == "live" else offline_environment)(args.root, args.core_root)
    if (args.output.exists() or args.output.parent != args.root or args.hypothesis not in HYPOTHESES
            or (args.mode == "replay" and (args.reference is None or args.reference.parent != args.root))):
        raise ValueError("new_scoped_matched_output_and_reference_required")
    manifest = verify_replay_bundle(args.root, PURPOSE)
    core = verify_bundle(args.core_root)
    verify_control(args.root)
    if sha256(args.core_root / "bundle-manifest.json") != manifest["coreBundleManifestSha256"]:
        raise ValueError("same_frozen_matched_core_required")
    explicit_diagnostic_profile(json.loads((args.root / "private-profile.json").read_text()),
                                json.loads((args.root / "private-sensor-diagnostic.json").read_text()),
                                args.hypothesis)
    if args.mode == "live" and shutil.disk_usage(args.root).free < 1024**3:
        raise ValueError("one_gib_free_for_bounded_private_capture_required")
    reference = None
    if args.mode == "replay":
        reference = json.loads((args.reference / "report.json").read_text())
        if (reference["result"] != RESULT or reference["mode"] != "live"
                or reference["replayManifestSha256"] != sha256(args.root / "replay-manifest.json")
                or reference["timingHypothesis"] != args.hypothesis):
            raise ValueError("complete_same_bundle_hypothesis_live_reference_required")
        for name, digest in reference["filesSha256"].items():
            if sha256(args.reference / name) != digest:
                raise ValueError("unchanged_live_reference_required")
    args.output.mkdir(mode=0o700)
    calls = dict.fromkeys(("create", "imu", "frame", "close"), 0)
    owners, main = set(), threading.get_ident()
    report = {
        "schemaVersion": 1, "purpose": PURPOSE, "mode": args.mode, "result": "running",
        "timingHypothesis": args.hypothesis, "requestedSeconds": args.seconds,
        "replayManifestSha256": sha256(args.root / "replay-manifest.json"),
        "sourceSha256": {name: sha256(args.root / name) for name in SOURCES},
        "target": {"machine": os.uname().machine, "pid": os.getpid(), "numpy": np.__version__},
        "effectiveConfiguration": configuration_files(args.root / "config"),
        "boundaries": {key: False for key in BOUNDARIES},
        "rosNodeStarted": args.mode == "live",
        "replayIsLogicalArrivalOrderNotRealtimeMeasurement": args.mode == "replay",
    }
    def record(name):
        owner = threading.get_ident()
        if (owners and owner not in owners) or (args.mode == "live" and owner == main):
            raise RuntimeError("one_native_owner_required")
        owners.add(owner)
        calls[name] += 1
    def loaded():
        report["actualLoadedLibrariesSha256"] = observed_libraries(args.root, args.core_root, manifest, core)
    def factory():
        return NativeOwner(factory, args.root, args.core_root, loaded)
    factory.record = record
    def checkpoint():
        (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    stdout, stderr = os.dup(1), os.dup(2)
    old_cwd = Path.cwd()
    worker = writer = native = None
    started, cpu = time.monotonic(), time.process_time()
    try:
        checkpoint()
        os.chdir(args.output)
        with (args.output / "private-native.log").open("x") as log:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            if args.mode == "live":
                from rclpy.serialization import serialize_message
                report["ddsProfileSha256"] = sha256(Path(DDS_PROFILE))
                observation = MatchedObservations()
                writer = Writer(args.output / "private-callbacks.cdrjournal")
                worker = JournalWorker(factory, args.hypothesis, observation, writer, serialize_message)
                report["session"] = run_passive(worker, Topics(CLOUD, IMU), args.seconds)
                report["worker"] = state = worker.snapshot()
                arrays = observation.arrays()
                report["observations"] = metrics({key: arrays[key] for key in ARRAYS})
                counts = state["counts"]
                if (report["session"]["stopReason"] != "duration_reached" or state["state"] != "stopped"
                        or state["failure"] or state["threadAlive"] or counts["rejectedMessages"]
                        or counts["completedMessages"] + counts["discardedMessages"] != writer.count
                        or counts["acceptedClouds"] != writer.counts["cloud"]
                        or counts["acceptedImu"] != writer.counts["imu"]
                        or state["queuedMessages"] or state["inFlightMessages"]):
                    raise ValueError("closed_live_worker_and_complete_journal_accounting_required")
                report["journal"] = writer.finish()
                if inspect(args.output / "private-callbacks.cdrjournal") != report["journal"]:
                    raise ValueError("independently_verified_callback_journal_required")
                report["captureSerializationAndAppendMillisecondsP50P95P99Max"] = np.percentile(
                    np.asarray(worker.journal_costs_ns) / 1e6, [50, 95, 99, 100]).tolist()
                report["stream"] = state["stream"]
                if sha256(Path(DDS_PROFILE)) != report["ddsProfileSha256"]:
                    raise ValueError("unchanged_dds_profile_required")
            else:
                from rclpy.serialization import deserialize_message
                from sensor_msgs.msg import Imu, PointCloud2
                def deserialize(kind, payload):
                    return deserialize_message(payload, PointCloud2 if kind == "cloud" else Imu)
                path = args.reference / "private-callbacks.cdrjournal"
                if inspect(path) != reference["journal"]:
                    raise ValueError("complete_reference_journal_required")
                native = factory()
                report["stream"], arrays = replay_prefix(
                    path, reference["worker"]["counts"]["completedMessages"],
                    args.hypothesis, native, deserialize)
                native.close()
                native = None
                with np.load(args.reference / "private-results.npz", allow_pickle=False) as archive:
                    report["sameInputComparison"] = compare({key: archive[key] for key in OUTPUT_ARRAYS}, arrays)
                if any(report["stream"]["counts"][key] != reference["stream"]["counts"][key] for key in COUNT_KEYS):
                    raise ValueError("identical_completed_prefix_stream_counts_required")
                report["referenceReportSha256"] = sha256(args.reference / "report.json")
                report["inputJournalSha256"] = sha256(path)
                report["replayedCompletedPrefixMessages"] = reference["worker"]["counts"]["completedMessages"]
                report["excludedExplicitStopTailMessages"] = reference["worker"]["counts"]["discardedMessages"]
            np.savez_compressed(args.output / "private-results.npz", **arrays)
        if calls["create"] != 1 or calls["close"] != 1 or len(owners) != 1:
            raise ValueError("one_closed_native_instance_required")
        if (calls["imu"] != report["stream"]["counts"]["insertedImu"]
                or calls["frame"] != report["stream"]["counts"]["processedScans"]):
            raise ValueError("native_calls_match_completed_stream_required")
        verify_replay_bundle(args.root, PURPOSE)
        report.update(result=RESULT, nativeCalls=calls, nativeOwnerIsSingleThread=True,
                      totalWallSeconds=time.monotonic() - started, processCpuSeconds=time.process_time() - cpu,
                      peakResidentMiB=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        try:
            if worker is not None:
                report["worker"] = worker.stop()
            if writer is not None:
                writer.abort()
            if native is not None:
                native.close()
        finally:
            os.dup2(stdout, 1)
            os.dup2(stderr, 2)
            os.close(stdout)
            os.close(stderr)
            os.chdir(old_cwd)
            report["nativeCalls"] = calls
            report["filesSha256"] = {
                name: sha256(args.output / name) for name in
                ("private-native.log", "private-results.npz", "private-callbacks.cdrjournal")
                if (args.output / name).is_file()
            }
            checkpoint()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "core-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--mode", choices=("live", "replay"), required=True)
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
