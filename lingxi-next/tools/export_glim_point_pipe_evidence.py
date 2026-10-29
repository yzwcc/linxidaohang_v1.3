"""Independently verify offline point-pipe reports; export scalar allowlists only."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import re

import run_glim_point_pipe_trial as trial
from verify_slam_capture import sha256


replay, points = trial.replay, trial.points
REPORTS = ("child-report.json", "point-report.json", "consumer-report.json")
PROCESS_COUNTERS = ("completedMessages", "processedScans", "estimatedPoses", "healthEvents", "retiredHealthEvents")
PROCESS_FALSE = ("processAlive", "monitorAlive", "forcedTermination", "mappingReady",
                 "localizationReady", "navigationReady")
PROCESS_FIELDS = ("state", "failure", "childFailure", "workerState", *PROCESS_COUNTERS,
                  "exitCode", *PROCESS_FALSE, "cleanupComplete")
WORKER_COUNTS = ("acceptedImu", "acceptedClouds", "completedMessages", "discardedMessages", "rejectedMessages")
STREAM_COUNTS = ("receivedImu", "receivedClouds", "insertedImu", "processedScans", "initializationScans",
                 "estimatedPoses", "skippedBeforeImu", "skippedAfterImuAtEndOfInput")
NATIVE_CALLS = ("create", "imu", "frame", "close")
POINT_COUNTS = ("callbacks", "initializationSkips", "copies", "takes", "discardedOnClose")
POINT_FIELDS = ("packets", "copiedPoints", "copiedPointsPerPacketMinMax", "firstNativeFrameId",
                "lastNativeFrameId", "allPacketBytesSha256", "maximumRetainedPackets", "coordinateFrame",
                "poseFrame", "sourceFrame", "kind", "initializationFrameExcluded", "globalMapOrLoopClosure")
CONSUMER_FIELDS = ("records", "copiedPoints", "retired", "firstNativeFrameId", "lastNativeFrameId",
                   "allPacketBytesSha256", "inputMode")
API_FIELDS = ("pairedWebSocketChecked", "recordedInputRejectedAsLive", "beforeAndAfterEofUnavailable",
              "readinessUnchanged", "robotCommandsSent", "tcpListenerStarted")


def counters(group, names):
    if any(type(group[name]) is not int or group[name] < 0 for name in names):
        raise ValueError("nonnegative_integer_point_pipe_counters_required")
    return {name: group[name] for name in names}


def closed_process(parent, child):
    process, worker = parent["process"], child["worker"]
    counts, stream = worker["counts"], worker["stream"]["counts"]
    counters(process, PROCESS_COUNTERS)
    counters(counts, WORKER_COUNTS)
    counters(stream, STREAM_COUNTS)
    counters(child["nativeCalls"], NATIVE_CALLS)
    if (process["state"] != "stopped" or process["workerState"] != "stopped"
            or process["failure"] is not None or process["childFailure"] is not None
            or type(process["exitCode"]) is not int or process["exitCode"] != 0
            or process["cleanupComplete"] is not True
            or any(process[name] is not False for name in PROCESS_FALSE)
            or any(child[name] is not True for name in (
                "stopReceived", "journalExhausted", "nativeOwnerIsSingleNonMainThread"))
            or worker["threadAlive"] is not False or worker["failure"] is not None
            or worker["stream"]["failure"] is not None
            or process["completedMessages"] != counts["completedMessages"]
            or process["processedScans"] != stream["processedScans"]
            or process["estimatedPoses"] != stream["estimatedPoses"]
            or stream["processedScans"] != stream["initializationScans"] + stream["estimatedPoses"]):
        raise ValueError("clean_complete_recorded_point_pipe_native_process_required")
    consumer = parent["consumerProcess"]
    if (type(consumer["exitCode"]) is not int or consumer["exitCode"] != 0
            or consumer["forcedTermination"] is not False or consumer["processAlive"] is not False):
        raise ValueError("natural_recorded_point_consumer_exit_required")


def evidence(args):
    from build_slam_cpu_trial import PINS

    directory = args.trial
    parent = replay.load(directory / "report.json")
    child, native, consumer = (replay.load(directory / name) for name in REPORTS)
    frozen, journal = parent["inputIntegrity"], parent["inputIntegrity"]["journal"]
    replay.replay_rate(parent["replayRate"])
    counters(journal, ("records", "bytes"))
    counters(journal["counts"], ("imu", "cloud"))
    if not 1 <= journal["records"] <= 20_000 or journal["records"] != sum(
            journal["counts"][name] for name in ("imu", "cloud")):
        raise ValueError("complete_bounded_point_pipe_input_journal_required")
    if (type(parent["schemaVersion"]) is not int or parent["schemaVersion"] != 1
            or parent["result"] != trial.RESULT or child["result"] != replay.CHILD_RESULT
            or parent["privateReportsSha256"] != {name: sha256(directory / name) for name in REPORTS}
            or child["inputIntegrity"] != frozen or trial.integrity(args) != frozen
            or frozen["sourceCommits"] != PINS or frozen["sourceSha256"] != replay.hashes()
            or frozen["pointSourceSha256"] != points.source_hashes()
            or frozen["transportSourceSha256"] != trial.source_hashes()
            or parent["limits"] != asdict(replay.LIMITS)
            or parent["boundaries"] != dict.fromkeys(replay.BOUNDARIES, False)
            or any(parent["boundaries"][name] is not False for name in replay.BOUNDARIES)
            or parent["healthChannelContainsPoints"] is not False or parent["inputMode"] != "recorded"
            or type(child["replayRate"]) is not float or child["replayRate"] != parent["replayRate"]
            or args.rate != parent["replayRate"]
            or child["session"] != parent["process"]["session"]):
        raise ValueError("unchanged_same_session_recorded_point_pipe_reports_required")
    for inventory in ("sourceSha256", "pointSourceSha256", "transportSourceSha256"):
        for name, digest in frozen[inventory].items():
            staged = directory / "runtime-source" / name
            if staged.is_symlink() or not staged.is_file() or sha256(staged) != digest:
                raise ValueError("unchanged_staged_point_pipe_source_bytes_required")
    # Read/hash only: these checks do not load a native library or run an engine.
    replay.session_id(child["session"])
    replay.complete_child(child, journal)
    closed_process(parent, child)
    points.verified_points(native, child["worker"]["stream"]["counts"]["estimatedPoses"])
    if (native["inputMode"] != "recorded"
            or native["actualLoadedExtensionSha256"] != {points.LIBRARY: frozen["pointExtensionLibrarySha256"]}
            or native["writer"] != {"sent": native["packets"], "replaced": 0, "failed": False, "threadAlive": False}
            or any(type(native["writer"][name]) is not int for name in ("sent", "replaced"))
            or any(native["writer"][name] is not False for name in ("failed", "threadAlive"))):
        raise ValueError("complete_point_writer_delivery_and_thread_shutdown_required")
    trial.verify_consumer(consumer, native)
    counters(consumer["points"], CONSUMER_FIELDS[:5])
    if (type(consumer["schemaVersion"]) is not int or consumer["schemaVersion"] != 1
            or any(type(consumer["api"][name]) is not bool for name in API_FIELDS)):
        raise ValueError("typed_recorded_api_guard_evidence_required")
    baseline = replay.load(trial.ROOT / "docs/slam-stream-input-evidence.json")["runs"]["static-start"]
    if child["actualLoadedLibrariesSha256"] != baseline["actualLoadedLibrariesSha256"]:
        raise ValueError("same_frozen_actually_loaded_point_pipe_libraries_required")
    if (child["machine"] != "x86_64" or consumer["host"]["machine"] != "x64"
            or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", consumer["host"]["node"])
            or any(not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", child["versions"][name])
                   for name in ("numpy", "rosbags"))):
        raise ValueError("explicit_x86_recorded_point_pipe_host_versions_required")
    # Never serialize reports or extensible nested dictionaries wholesale.
    return {
        "schemaVersion": 1, "date": "2026-10-29", "result": trial.RESULT,
        "exporterSourceSha256": sha256(__file__),
        "privateParentReportSha256": sha256(directory / "report.json"),
        "privateReportsSha256": {name: parent["privateReportsSha256"][name] for name in REPORTS},
        "frozenSourceSha256": replay.hashes(), "pointSourceSha256": points.source_hashes(),
        "transportSourceSha256": trial.source_hashes(), "sourceCommits": dict(PINS),
        "consumerBundleSha256": frozen["consumerBundleSha256"], "nodeBinarySha256": frozen["nodeBinarySha256"],
        "pointExtensionBuildSha256": frozen["pointExtensionBuildSha256"],
        "actualLoadedExtensionSha256": {points.LIBRARY: frozen["pointExtensionLibrarySha256"]},
        "actualLoadedLibrariesSha256": dict(baseline["actualLoadedLibrariesSha256"]),
        "host": {
            "machine": child["machine"], "consumerMachine": consumer["host"]["machine"],
            "node": consumer["host"]["node"],
            "versions": {name: child["versions"][name] for name in ("numpy", "rosbags")},
        },
        "inputJournal": {
            "records": journal["records"], "bytes": journal["bytes"], "sha256": frozen["journalSha256"],
            "counts": {name: journal["counts"][name] for name in ("imu", "cloud")},
        },
        "process": {name: parent["process"][name] for name in PROCESS_FIELDS},
        "consumerProcess": {name: parent["consumerProcess"][name] for name in (
            "exitCode", "forcedTermination", "processAlive")},
        "nativeCalls": counters(child["nativeCalls"], NATIVE_CALLS),
        "completedWorkerCounts": counters(child["worker"]["counts"], WORKER_COUNTS),
        "streamCounts": counters(child["worker"]["stream"]["counts"], STREAM_COUNTS),
        "points": {
            **{name: native[name] for name in POINT_FIELDS},
            "nativeCounts": counters(native["nativeCounts"], POINT_COUNTS),
        },
        "writer": {name: native["writer"][name] for name in ("sent", "replaced", "failed", "threadAlive")},
        "consumerPoints": {name: consumer["points"][name] for name in CONSUMER_FIELDS},
        "api": {name: consumer["api"][name] for name in API_FIELDS},
        "transport": "private_same_host_unix_stream", "inputMode": "recorded",
        "healthChannelContainsPoints": False, "nativeOwnerIsSingleNonMainThread": True,
        "replayTiming": replay.verified_timing(child, journal),
        "observedWorkerTiming": replay.observed_worker_timing(child),
        "limits": asdict(replay.LIMITS), "boundaries": dict.fromkeys(replay.BOUNDARIES, False),
        "replayRate": parent["replayRate"], "pacedRecordedInputNotLivePerformance": True,
        "observations": {
            "stagedRuntimeSourceBytesVerified": True, "allOriginalCallbacksCompleted": True,
            "allPacketContentDigestsMatched": True, "bothOwnedProcessesExitedNaturally": True,
            "recordedInputRejectedAsLive": True,
        },
        "acceptance": dict.fromkeys((
            "livePointChannelValidated", "targetArm64PointChannelValidated", "webOrAndroidRenderingValidated",
            "lidarToScreenLatencyValidated", "mappingReady", "mapPersistenceValidated",
            "localizationReady", "navigationReady", "deploymentApproved", "rawDataPublished",
        ), False),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("trial", "work", "adapter", "points", "bundle", "reference", "node", "consumer", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--hypothesis", choices=("header_is_scan_start", "header_is_scan_end"), required=True)
    parser.add_argument("--rate", type=float, choices=(.5, 1.), required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    previous = os.umask(0o077)
    try:
        result = evidence(args)
        replay.private_json(args.output, result)
        print(json.dumps({"result": result["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
