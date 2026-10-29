"""Verify downloaded ARM64 lifecycle evidence; publish only explicit aggregates."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path, PurePosixPath

import glim_supervised_target as target
import run_glim_supervised_target as coordinator
from verify_slam_capture import sha256


PROCESS_FIELDS = (
    "state", "failure", "childFailure", "workerState", "completedMessages", "processedScans",
    "estimatedPoses", "healthEvents", "retiredHealthEvents", "exitCode", "processAlive",
    "monitorAlive", "forcedTermination", "cleanupComplete", "mappingReady",
    "localizationReady", "navigationReady",
)
WORKER_COUNTS = ("acceptedImu", "acceptedClouds", "completedMessages", "discardedMessages", "rejectedMessages")
STREAM_COUNTS = (
    "receivedImu", "receivedClouds", "insertedImu", "processedScans", "initializationScans",
    "estimatedPoses", "skippedBeforeImu", "skippedAfterImuAtEndOfInput",
)


def evidence(trial):
    shared = target.shared
    collection, parent, child = (shared.load(trial / name) for name in (
        "collection-report.json", "report.json", "child-report.json"))
    integrity, process = parent["inputIntegrity"], parent["process"]
    shared.replay_rate(parent["replayRate"])
    if (collection["result"] != coordinator.RESULT or type(collection["exitCode"]) is not int
            or collection["exitCode"] != 0 or collection["coordinatorSha256"] != sha256(coordinator.__file__)
            or set(collection["filesSha256"]) != set(coordinator.FILES)
            or any(sha256(trial / name) != digest for name, digest in collection["filesSha256"].items())
            or parent["result"] != target.RESULT or child["result"] != shared.CHILD_RESULT
            or parent["childReportSha256"] != collection["filesSha256"]["child-report.json"]
            or parent["privateLogSha256"] != collection["filesSha256"]["private-child.log"]
            or child["inputIntegrity"] != integrity or integrity["sourceSha256"] != target.hashes()
            or child["machine"] != "aarch64"
            or child["versions"]["decoder"] != "installed_humble_rclpy"
            or not child["versions"]["python"].startswith("3.10.")
            or child["session"] != process["session"] or type(child["replayRate"]) is not float
            or child["replayRate"] != parent["replayRate"]
            or parent["limits"] != asdict(shared.LIMITS)
            or parent["boundaries"] != dict.fromkeys(shared.BOUNDARIES, False)
            or parent["pacedRecordedInputNotLivePerformance"] is not True
            or any(child[name] is not True for name in (
                "nativeOwnerIsSingleNonMainThread", "journalExhausted", "stopReceived"))
            or child["actualLoadedLibrariesSha256"] != integrity["referenceLibrariesSha256"]):
        raise ValueError("verified_same_arm64_recorded_supervision_required")
    from build_slam_cpu_trial import PINS
    if integrity["sourceCommits"] != PINS:
        raise ValueError("same_pinned_native_sources_required")
    uploaded = collection["uploadedSourceFiles"]
    if set(uploaded) != set(parent["runtimeSourceFilesSha256"]):
        raise ValueError("complete_uploaded_and_staged_source_set_required")
    for name, entry in uploaded.items():
        relative = PurePosixPath(name)
        source = coordinator.ROOT / name
        if (relative.is_absolute() or ".." in relative.parts or "\\" in name
                or source.is_symlink() or not source.is_file()
                or not source.resolve().is_relative_to(coordinator.ROOT)
                or sha256(source) != entry["sha256"] or source.stat().st_size != entry["bytes"]
                or parent["runtimeSourceFilesSha256"][name] != entry["sha256"]):
            raise ValueError("same_uploaded_staged_and_local_source_bytes_required")
    shared.complete_child(child, integrity["journal"])
    counts, stream = child["worker"]["counts"], child["worker"]["stream"]["counts"]
    for group, names in ((counts, WORKER_COUNTS), (stream, STREAM_COUNTS),
                         (child["nativeCalls"], ("create", "close", "imu", "frame"))):
        if any(type(group[name]) is not int or group[name] < 0 for name in names):
            raise ValueError("integer_complete_native_counters_required")
    if (process["state"] != "stopped" or process["workerState"] != "stopped"
            or process["failure"] is not None or process["childFailure"] is not None
            or type(process["exitCode"]) is not int or process["exitCode"] != 0
            or any(process[name] is not False for name in (
                "forcedTermination", "processAlive", "monitorAlive", "mappingReady",
                "localizationReady", "navigationReady"))
            or process["cleanupComplete"] is not True
            or any(type(process[name]) is not int or process[name] < 0 for name in (
                "completedMessages", "processedScans", "estimatedPoses", "healthEvents", "retiredHealthEvents"))
            or process["completedMessages"] != counts["completedMessages"]
            or process["processedScans"] != stream["processedScans"]
            or process["estimatedPoses"] != stream["estimatedPoses"]
            or stream["processedScans"] != stream["estimatedPoses"] + stream["initializationScans"]):
        raise ValueError("fully_closed_arm64_recorded_process_required")
    # Never serialize worker reports, private paths, session IDs, sensor data or log text.
    return {
        "schemaVersion": 1, "date": "2026-10-29", "result": target.RESULT,
        "privateCollectionReportSha256": sha256(trial / "collection-report.json"),
        "privateParentReportSha256": sha256(trial / "report.json"),
        "privateChildReportSha256": parent["childReportSha256"],
        "privateNativeLogSha256": parent["privateLogSha256"],
        "coordinatorSha256": collection["coordinatorSha256"], "sourceSha256": target.hashes(),
        "sourceCommits": integrity["sourceCommits"],
        "host": {"machine": child["machine"], "versions": {
            name: child["versions"][name] for name in ("python", "numpy", "decoder")}},
        "inputJournal": {
            "records": integrity["journal"]["records"], "bytes": integrity["journal"]["bytes"],
            "counts": {name: integrity["journal"]["counts"][name] for name in ("imu", "cloud")},
            "sha256": integrity["journalSha256"],
        },
        "configurationSha256": integrity["configurationSha256"],
        "actualLoadedLibrariesSha256": child["actualLoadedLibrariesSha256"],
        "process": {name: process[name] for name in PROCESS_FIELDS},
        "nativeCalls": {name: child["nativeCalls"][name] for name in ("create", "close", "imu", "frame")},
        "nativeOwnerIsSingleNonMainThread": child["nativeOwnerIsSingleNonMainThread"],
        "completedWorkerCounts": {name: counts[name] for name in WORKER_COUNTS},
        "streamCounts": {name: stream[name] for name in STREAM_COUNTS},
        "replayTiming": shared.verified_timing(child, integrity["journal"]),
        "observedWorkerTiming": shared.observed_worker_timing(child),
        "limits": parent["limits"], "replayRate": parent["replayRate"],
        "pacedRecordedInputNotLivePerformance": True,
        "observations": {
            "robotContactedForIsolatedCompute": True, "arm64RecordedChildLifecycleClosed": True,
            "fullRecordedCallbacksCompleted": True, "actualNativeLibrariesMatchReference": True,
            "uploadedAndStagedSourceBytesVerified": True,
        },
        "acceptance": dict.fromkeys((
            "liveDdsSupervisionValidated", "oneTimesLivePerformanceValidated", "pointOutputIntegrated",
            "mappingReady", "mapPersistenceValidated", "localizationReady", "navigationReady",
            "timingHypothesisValidated", "robotCalibrationValidated", "geometryValidated",
            "relocalizationValidated", "systemdRecoveryValidated", "voiceConcurrencyValidated",
            "motionCommandsSent", "vendorServicesChanged", "deploymentApproved", "rawDataPublished",
        ), False),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        result = evidence(args.trial.resolve(strict=True))
        target.shared.private_json(args.output.resolve(), result)
        print(json.dumps({"result": result["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
