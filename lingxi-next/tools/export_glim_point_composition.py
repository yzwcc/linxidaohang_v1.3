"""Independently verify ARM64 recorded point collection and export named aggregates."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path, PurePosixPath

import export_glim_point_pipe_evidence as checks
import run_glim_point_composition_target as coordinator
from verify_slam_capture import sha256


binding, shared = coordinator.binding, coordinator.binding.shared
PRIVATE_LOGS = ("private-preflight-command.log", "private-extract-command.log", "private-node-command.log",
                coordinator.remote.PRIVATE_COMMAND_LOG)


def validate_reports(directory, collection, prepared, sources):
    from build_slam_cpu_trial import PINS

    parent, child, native, consumer = (shared.load(directory / name) for name in coordinator.FILES[:4])
    frozen, process = parent["inputIntegrity"], parent["process"]
    journal = frozen["journal"]
    shared.replay_rate(parent["replayRate"])
    checks.counters(journal, ("records", "bytes"))
    checks.counters(journal["counts"], ("imu", "cloud"))
    loading = shared.load(coordinator.ROOT / "docs/slam-point-arm64-target-evidence.json")
    baseline = shared.load(coordinator.ROOT / "docs/slam-supervised-arm64-evidence.json")
    if (type(parent["schemaVersion"]) is not int or parent["schemaVersion"] != 1
            or parent["result"] != binding.RESULT or child["result"] != shared.CHILD_RESULT
            or child["inputIntegrity"] != frozen or frozen["sourceCommits"] != PINS
            or frozen["sourceSha256"] != binding.target.hashes()
            or frozen["pointSourceSha256"] != binding.source_hashes()
            or frozen["pointManifestSha256"] != loading["bundleManifestSha256"]
            or frozen["coreBundleManifestSha256"] != loading["coreManifestSha256"]
            or frozen["pointExtensionLibrarySha256"] != loading["actualLoadedLibrariesSha256"][binding.probe.LIBRARY]
            or frozen["pointLoadingEvidenceSha256"] != sha256(coordinator.ROOT / "docs/slam-point-arm64-target-evidence.json")
            or frozen["consumerManifestSha256"] != prepared["manifestSha256"]
            or frozen["consumerBundleSha256"] != prepared["consumerBundleSha256"]
            or frozen["nodeBinarySha256"] != prepared["nodeBinarySha256"]
            or parent["privateReportsSha256"] != {
                name: sha256(directory / name) for name in coordinator.FILES[1:]}
            or parent["runtimeSourceFilesSha256"] != {name: entry["sha256"] for name, entry in sources.items()}
            or parent["limits"] != asdict(shared.LIMITS)
            or parent["boundaries"] != dict.fromkeys(shared.BOUNDARIES, False)
            or any(value is not False for value in parent["boundaries"].values())
            or parent["inputMode"] != "recorded" or parent["healthChannelContainsPoints"] is not False
            or parent["pacedRecordedInputNotLivePerformance"] is not True
            or child["machine"] != "aarch64" or child["versions"]["decoder"] != "installed_humble_rclpy"
            or not child["versions"]["python"].startswith("3.10.")
            or type(child["replayRate"]) is not float or child["replayRate"] != parent["replayRate"]
            or type(collection["requestedReplayRate"]) is not float
            or collection["requestedReplayRate"] != parent["replayRate"]
            or collection["requestedTimingHypothesis"] not in ("header_is_scan_start", "header_is_scan_end")
            or child["session"] != process["session"]
            or child["actualLoadedLibrariesSha256"] != frozen["referenceLibrariesSha256"]
            or child["actualLoadedLibrariesSha256"] != baseline["actualLoadedLibrariesSha256"]
            or consumer["host"] != {"machine": "arm64", "node": "v" + binding.consumer_bundle.VERSION}):
        raise ValueError("same_complete_arm64_recorded_point_composition_required")
    if (not 1 <= journal["records"] <= 20_000
            or journal["records"] != sum(journal["counts"][name] for name in ("imu", "cloud"))):
        raise ValueError("complete_bounded_recorded_callback_journal_required")
    shared.session_id(child["session"])
    shared.complete_child(child, journal)
    checks.closed_process(parent, child)
    binding.points.verified_points(native, child["worker"]["stream"]["counts"]["estimatedPoses"])
    binding.pipe.verify_consumer(consumer, native)
    checks.counters(consumer["points"], checks.CONSUMER_FIELDS[:5])
    if (native["inputMode"] != "recorded"
            or native["actualLoadedExtensionSha256"] != {binding.probe.LIBRARY: frozen["pointExtensionLibrarySha256"]}
            or native["writer"] != {"sent": native["packets"], "replaced": 0, "failed": False, "threadAlive": False}
            or any(type(native["writer"][name]) is not int for name in ("sent", "replaced"))
            or any(native["writer"][name] is not False for name in ("failed", "threadAlive"))
            or type(consumer["schemaVersion"]) is not int or consumer["schemaVersion"] != 1
            or any(type(consumer["api"][name]) is not bool for name in checks.API_FIELDS)):
        raise ValueError("complete_recorded_point_delivery_and_api_isolation_required")
    preflight = collection["readOnlyPreflight"]
    coordinator.validate_preflight(preflight, PurePosixPath(preflight["home"]), argparse.Namespace(
        core_manifest_sha256=frozen["coreBundleManifestSha256"],
        replay_manifest_sha256=frozen["replayManifestSha256"],
        point_manifest_sha256=frozen["pointManifestSha256"]))
    if (preflight["coreManifestSha256"] != frozen["coreBundleManifestSha256"]
            or preflight["replayManifestSha256"] != frozen["replayManifestSha256"]
            or preflight["pointManifestSha256"] != frozen["pointManifestSha256"]
            or preflight["python"] != child["versions"]["python"]
            or preflight["machine"] != child["machine"]
            or collection["nodeRuntime"] != {**consumer["host"], "platform": "linux"}):
        raise ValueError("same_preflight_and_actual_arm64_composition_required")
    return parent, child, native, consumer


def evidence(directory, preparation):
    prepared, sources = coordinator.preparation.evidence(preparation), coordinator.source_files()
    collection = shared.load(directory / "collection-report.json")
    expected_files = set((*coordinator.FILES, *PRIVATE_LOGS))
    if (type(collection["schemaVersion"]) is not int or collection["schemaVersion"] != 1
            or collection["result"] != coordinator.RESULT
            or collection["coordinatorSha256"] != sha256(coordinator.__file__)
            or collection["preparationEvidenceSha256"] != sha256(
                coordinator.ROOT / "docs/slam-point-consumer-preparation-evidence.json")
            or prepared != shared.load(coordinator.ROOT / "docs/slam-point-consumer-preparation-evidence.json")
            or any(collection[name] is not True for name in (
                "operatorConfirmedForThisExecution", "hostIdentityVerified", "connectionClosed"))
            or type(collection["exitCode"]) is not int or collection["exitCode"] != 0
            or collection["uploadedSourceFiles"] != sources
            or set(collection["filesSha256"]) != expected_files
            or any((directory / name).is_symlink() or sha256(directory / name) != digest
                   for name, digest in collection["filesSha256"].items())
            or collection["uploadedArchive"] != {"sha256": prepared["archiveSha256"], "bytes": prepared["archiveBytes"]}
            or type(collection["uploadedArchive"]["bytes"]) is not int
            or collection["consumerExtraction"] != {
                "result": coordinator.extractor.RESULT, "archiveSha256": prepared["archiveSha256"],
                "manifestSha256": prepared["manifestSha256"], "bundleFiles": prepared["bundleFiles"],
                "nodeExecuted": False}
            or collection["consumerExtraction"]["nodeExecuted"] is not False):
        raise ValueError("verified_closed_arm64_point_composition_collection_required")
    parent, child, native, consumer = validate_reports(directory, collection, prepared, sources)
    journal = parent["inputIntegrity"]["journal"]
    return {
        "schemaVersion": 1, "date": "2026-10-29", "result": binding.RESULT, "inputMode": "recorded",
        "coordinatorSha256": sha256(coordinator.__file__), "exporterSha256": sha256(__file__),
        "privateCollectionReportSha256": sha256(directory / "collection-report.json"),
        "privateReportsSha256": {name: collection["filesSha256"][name] for name in coordinator.FILES},
        "sourceSha256": binding.source_hashes(),
        "preparedManifestSha256": prepared["manifestSha256"], "preparedArchiveSha256": prepared["archiveSha256"],
        "nodeBinarySha256": prepared["nodeBinarySha256"],
        "consumerBundleSha256": prepared["consumerBundleSha256"],
        "actualLoadedLibrariesSha256": dict(child["actualLoadedLibrariesSha256"]),
        "actualLoadedExtensionSha256": {
            binding.probe.LIBRARY: native["actualLoadedExtensionSha256"][binding.probe.LIBRARY]},
        "host": {"machine": child["machine"], "node": consumer["host"]["node"],
                 "versions": {name: child["versions"][name] for name in ("python", "numpy", "decoder")}},
        "inputJournal": {
            "records": journal["records"], "bytes": journal["bytes"],
            "sha256": parent["inputIntegrity"]["journalSha256"],
            "counts": {name: journal["counts"][name] for name in ("imu", "cloud")}},
        "process": {name: parent["process"][name] for name in checks.PROCESS_FIELDS},
        "consumerProcess": {name: parent["consumerProcess"][name] for name in (
            "exitCode", "forcedTermination", "processAlive")},
        "nativeCalls": checks.counters(child["nativeCalls"], checks.NATIVE_CALLS),
        "completedWorkerCounts": checks.counters(child["worker"]["counts"], checks.WORKER_COUNTS),
        "streamCounts": checks.counters(child["worker"]["stream"]["counts"], checks.STREAM_COUNTS),
        "points": {**{name: native[name] for name in checks.POINT_FIELDS},
                   "nativeCounts": checks.counters(native["nativeCounts"], checks.POINT_COUNTS)},
        "writer": {name: native["writer"][name] for name in ("sent", "replaced", "failed", "threadAlive")},
        "consumerPoints": {name: consumer["points"][name] for name in checks.CONSUMER_FIELDS},
        "api": {name: consumer["api"][name] for name in checks.API_FIELDS},
        "replayTiming": shared.verified_timing(child, journal),
        "observedWorkerTiming": shared.observed_worker_timing(child),
        "replayRate": parent["replayRate"], "pacedRecordedInputNotLivePerformance": True,
        "timingHypothesisRequestedNotValidated": collection["requestedTimingHypothesis"],
        "observations": dict.fromkeys((
            "identityCheckedRobotContacted", "samePreparedArm64NodeExecuted",
            "uploadedAndStagedSourceBytesVerified", "allPacketContentDigestsMatched",
            "bothOwnedProcessesExitedNaturally", "recordedPointsRejectedAsLive"), True),
        "acceptance": dict.fromkeys((
            "livePointChannelValidated", "webOrAndroidRenderingValidated", "pointDisplayLatencyValidated",
            "mappingReady", "mapPersistenceValidated", "calibrationValidated", "localizationReady",
            "navigationReady", "motionCommandsSent", "vendorServicesChanged", "deploymentApproved",
            "rawDataPublished", "productAccepted"), False),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("trial", "preparation", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        result = evidence(args.trial.resolve(strict=True), args.preparation.resolve(strict=True))
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write("\n")
        print(json.dumps({"result": result["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
