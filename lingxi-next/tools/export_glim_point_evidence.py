"""Allowlisted aggregate evidence for the non-real-time corrected-point trial."""
import argparse
import json
import math
from pathlib import Path

import run_glim_point_output_trial as trial
from verify_slam_capture import sha256


def evidence(directory, build):
    import numpy as np
    from build_slam_cpu_trial import PINS
    from run_kiss_offline_trial import valid_pose

    report = trial.replay.load(directory / "logical-report.json")
    frozen, calls, state = report["inputIntegrity"], report["nativeCalls"], report["stream"]
    points, counts = report["points"], state["counts"]
    native_build = trial.replay.load(build / "build-report.json")
    if (report["result"] != trial.LOGICAL_RESULT
            or frozen["sourceSha256"] != trial.replay.hashes() or frozen["pointSourceSha256"] != trial.source_hashes()
            or frozen["sourceCommits"] != PINS
            or frozen["pointExtensionBuildSha256"] != sha256(build / "build-report.json")
            or native_build["result"] != trial.BUILD_RESULT or native_build["buildType"] != "Release"
            or native_build["sourceCommits"] != PINS
            or native_build["coreBuildReportSha256"] != frozen["coreBuildReportSha256"]
            or native_build["nativeSourceSha256"] != {name: sha256(trial.ROOT / name) for name in trial.NATIVE_NAMES}
            or native_build["stages"] != [{"name": name, "exitCode": 0}
                                          for name in ("configure", "build", "contracts")]
            or native_build["librarySha256"] != frozen["pointExtensionLibrarySha256"]
            or native_build["librarySha256"] != sha256(build / "build" / trial.LIBRARY)
            or report["boundaries"] != dict.fromkeys(trial.replay.BOUNDARIES, False)
            or report["privateLogSha256"] != sha256(directory / "private-native.log")
            or report["sensorWorkerOrProcessSupervisorExecuted"] is not False
            or report["nativeOwner"] != "calling_main_thread_only"
            or report["arrivalClock"] != "original_journal_elapsed_not_monotonic_wall_or_paced"):
        raise ValueError("unchanged_logical_point_trial_and_build_required")
    expected = frozen["journal"]["counts"]
    if (state["state"] != "finished" or state["failure"]
            or calls["create"] != 1 or calls["close"] != 1
            or counts["receivedImu"] != expected["imu"] or counts["receivedClouds"] != expected["cloud"]
            or calls["imu"] != counts["insertedImu"] or calls["frame"] != counts["processedScans"]
            or sum(counts[key] for key in ("processedScans", "skippedBeforeImu", "skippedAfterImuAtEndOfInput"))
            != expected["cloud"] or counts["initializationScans"] + counts["estimatedPoses"] != calls["frame"]):
        raise ValueError("complete_logical_recorded_scan_and_native_lifetime_required")
    trial.verified_points(points, counts["estimatedPoses"])
    baseline = trial.replay.load(trial.ROOT / "docs/slam-stream-input-evidence.json")["runs"]["static-start"]
    if (report["actualLoadedLibrariesSha256"] != baseline["actualLoadedLibrariesSha256"]
            or report["actualLoadedExtensionSha256"] != {trial.LIBRARY: frozen["pointExtensionLibrarySha256"]}):
        raise ValueError("same_logical_loaded_core_and_point_extension_required")
    stamps = []
    for name, frame_id in (("first", 1), ("latest", points["packets"])):
        path = directory / ("private-" + name + "-points.npz")
        if points[name + "SampleSha256"] != sha256(path):
            raise ValueError("unchanged_logical_private_point_sample_required")
        with np.load(path, allow_pickle=False) as sample:
            if set(sample.files) != {"points", "world_lidar_pose", "frame_id", "algorithm_stamp"}:
                raise ValueError("exact_logical_private_point_sample_schema_required")
            xyz, pose = sample["points"], sample["world_lidar_pose"]
            stamp = float(sample["algorithm_stamp"])
            if (xyz.ndim != 2 or xyz.shape[1] != 3 or xyz.dtype != np.float64 or not np.isfinite(xyz).all()
                    or not points["copiedPointsPerPacketMinMax"][0] <= len(xyz)
                    <= points["copiedPointsPerPacketMinMax"][1]
                    or sample["frame_id"].shape != () or sample["frame_id"].dtype.kind not in "iu"
                    or int(sample["frame_id"]) != frame_id or pose.shape != (4, 4)
                    or sample["algorithm_stamp"].shape != () or not math.isfinite(stamp)):
                raise ValueError("finite_matching_logical_private_point_sample_required")
            valid_pose(pose)
            stamps.append(stamp)
    if points["packets"] > 1 and not stamps[0] < stamps[1]:
        raise ValueError("ordered_logical_private_point_sample_stamps_required")
    wall = report["totalWallSecondsNotRealtimePerformance"]
    if type(wall) not in (int, float) or not math.isfinite(wall) or wall < 0:
        raise ValueError("finite_logical_wall_time_required")
    return {
        "schemaVersion": 1, "date": "2026-10-29", "result": trial.LOGICAL_RESULT,
        "exporterSourceSha256": sha256(__file__), "pointSourceSha256": trial.source_hashes(),
        "frozenSourceSha256": trial.replay.hashes(), "sourceCommits": PINS,
        "privateReportSha256": sha256(directory / "logical-report.json"),
        "inputJournal": {**frozen["journal"], "sha256": frozen["journalSha256"]},
        "actualLoadedLibrariesSha256": report["actualLoadedLibrariesSha256"],
        "actualLoadedExtensionSha256": report["actualLoadedExtensionSha256"],
        "pointExtensionBuildSha256": frozen["pointExtensionBuildSha256"],
        "nativeCalls": calls,
        "streamCounts": {key: counts[key] for key in (
            "receivedImu", "receivedClouds", "insertedImu", "processedScans", "initializationScans",
            "estimatedPoses", "skippedBeforeImu", "skippedAfterImuAtEndOfInput")},
        "points": {key: points[key] for key in (
            "packets", "copiedPoints", "copiedPointsPerPacketMinMax", "firstNativeFrameId", "lastNativeFrameId",
            "nativeCounts", "allPacketBytesSha256", "maximumRetainedPackets", "coordinateFrame",
            "poseFrame", "sourceFrame", "kind", "initializationFrameExcluded", "globalMapOrLoopClosure")},
        "host": {key: report["host"][key] for key in ("machine", "numpy")},
        "arrivalClock": report["arrivalClock"], "nativeOwner": report["nativeOwner"],
        "totalWallSecondsNotRealtimePerformance": wall,
        "sensorWorkerOrProcessSupervisorExecuted": False,
        "boundaries": report["boundaries"], "pacedReplayValidated": False,
        "targetArm64PointExtensionValidated": False, "webOrAndroidIntegrated": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("trial", "build", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = evidence(args.trial.resolve(), args.build.resolve())
    trial.replay.private_json(args.output.resolve(), result)
    print(json.dumps({"result": result["result"]}))


if __name__ == "__main__":
    main()
