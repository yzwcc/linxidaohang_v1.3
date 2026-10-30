"""Audit preserved localization reports without rerunning native code or SSH."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot-runtime/python"))

from controlled_localization import source_hashes, verify_completion
from localization_control import LocalizationBinding, strict_json
from run_localization_arm64_trial import RESULT, FILES, WHEEL_SHA256
from verify_slam_capture import sha256
import run_localization_arm64_trial as coordinator

FLAGS = ("motionCommandsSent", "localizationReady", "navigationReady", "mapProfileAssociationVerified")
SOURCES = (*source_hashes(), "robot-runtime/python/managed_localization.py",
           "robot-runtime/python/localization_owner.py", "tools/check_managed_localization_runtime.py")
SUMMARY = (
    "seedSource", "seedRequests", "seedReferenceFrame", "trackingFrameObserved",
    "pauseConfirmed", "staleGenerationRejected", "stopConfirmed", "stoppedQueries",
    "completedMessages", "processedScans", "estimatedPoses", "singleNativeWorker",
    "apiBundleSha256", "nodeSha256", "inputJournalSha256", "receiptSha256",
    "profileSha256", "seedSampleSha256", "sourceSha256", "privateReportsSha256",
)


def load(file):
    return strict_json(file.read_bytes())


def audit_run(directory, machine):
    check, owner, child, final = (load(directory / name) for name in (
        "check-report.json", "run/owner-report.json", "run/native/child-report.json",
        "run/native/localization-final.json"))
    integrity, process = child["inputIntegrity"], owner["nativeProcess"]
    frozen = {name: value for name, value in integrity.items() if name not in (
        "localizationBinding", "localizationConfigSha256", "localizationSourceSha256",
        "smallGicpExtensionSha256")}
    verify_completion(child, final, frozen=frozen,
        binding=LocalizationBinding(**integrity["localizationBinding"]),
        sources=source_hashes(), config_hash=integrity["localizationConfigSha256"], rate=1.)
    sources = {name: sha256(ROOT / name) for name in SOURCES}
    expected_api = dict(exitCode=0, forcedTermination=False, processAlive=False, groupAlive=False)
    expected_native = dict(state="stopped", exitCode=0, forcedTermination=False,
                           cleanupComplete=True, processAlive=False, monitorAlive=False)
    stream = child["worker"]["stream"]["counts"]
    if (check["result"] != "managed_recorded_localization_https_checked"
            or owner["result"] != "completed" or owner["reason"] != "recorded_input_complete"
            or owner["failure"] is not None or owner["failurePhase"] is not None
            or owner["inputComplete"] is not True or owner["inputsRechecked"] is not True
            or child["machine"] != machine or process["session"] != child["session"]
            or check["sourceSha256"] != sources
            or any(report[name] is not False for report in (check, owner) for name in FLAGS)
            or any(check[name] is not True for name in (
                "pauseConfirmed", "stopConfirmed", "staleGenerationRejected", "singleNativeWorker"))
            or check["seedSource"] != "preserved_recorded_pose_at_explicit_current_reference_not_truth"
            or check["seedRequests"] != 1 or check["stoppedQueries"] < 1
            or check["apiProcess"] != expected_api or owner["apiProcess"] != expected_api
            or check["nativeProcess"] != expected_native
            or any(process[name] != value for name, value in expected_native.items())
            or process["failure"] is not None or process["childFailure"] is not None
            or process["workerState"] != "stopped" or check["nativeCalls"] != child["nativeCalls"]
            or any(check[name] != process[name] or process[name] != value for name, value in (
                ("completedMessages", child["worker"]["counts"]["completedMessages"]),
                ("processedScans", stream["processedScans"]), ("estimatedPoses", stream["estimatedPoses"])))
            or check["inputJournalSha256"] != integrity["journalSha256"]
            or check["receiptSha256"] != final["state"]["binding"]["receipt_sha256"]
            or check["profileSha256"] != integrity["profileSha256"]
            or any(sha256(directory / "run" / name) != digest
                   for name, digest in check["privateReportsSha256"].items())):
        raise ValueError("complete_source_bound_localization_https_reports_required")
    return {**{name: check[name] for name in SUMMARY},
            "nativeCalls": {name: child["nativeCalls"][name] for name in ("create", "imu", "frame", "close")},
            "privateCheckReportSha256": sha256(directory / "check-report.json"),
            "apiProcess": expected_api, "nativeProcess": expected_native}, child


def evidence(directory, compatibility):
    collection = load(directory / "collection-report.json")
    summary, child = audit_run(directory, "aarch64")
    if (collection["result"] != RESULT or collection["checkSummary"] != load(directory / "check-report.json")
            or any(collection[name] is not True for name in (
                "hostIdentityVerified", "robotContacted", "connectionClosed"))
            or any(collection[name] is not False for name in FLAGS)
            or collection["prepareExitCode"] != 0 or collection["checkExitCode"] != 0
            or collection["coordinatorSha256"] != sha256(coordinator.__file__)
            or not set(FILES).issubset(collection["filesSha256"])
            or any(sha256(directory / name) != digest
                   for name, digest in collection["filesSha256"].items())
            or collection["prepare"]["smallGicpExtensionSha256"]
               != child["inputIntegrity"]["smallGicpExtensionSha256"]
            or collection["prepare"]["versions"]["smallGicp"] != "1.0.1"
            or child["versions"]["decoder"] != "installed_humble_rclpy"):
        raise ValueError("verified_complete_arm64_localization_collection_required")
    for name, digest in {**summary["sourceSha256"], **child["inputIntegrity"]["sourceSha256"]}.items():
        if sha256(ROOT / name) != digest or collection["uploadedFiles"][name]["sha256"] != digest:
            raise ValueError("unchanged_executed_localization_sources_required")
    if any(child["inputIntegrity"].get(name) != value
           for name, value in collection["prepare"]["frozenInputs"].items()):
        raise ValueError("same_prepared_and_completed_localization_inputs_required")
    compatibility_summary, _ = audit_run(compatibility, "x86_64")
    for name in ("sourceSha256", "apiBundleSha256", "inputJournalSha256", "receiptSha256",
                 "profileSha256", "seedSampleSha256"):
        if summary[name] != compatibility_summary[name]:
            raise ValueError("same_source_and_recording_compatibility_check_required")
    return {
        "schemaVersion": 1, "observedDate": "2026-10-30", "result": RESULT,
        "platform": "linux-aarch64-humble", "inputMode": "recorded",
        "robotContacted": True, **dict.fromkeys(FLAGS, False), **summary,
        "versions": {name: child["versions"][name] for name in (
            "python", "numpy", "scipy", "small-gicp", "decoder")},
        "smallGicpWheelSha256": WHEEL_SHA256,
        "smallGicpExtensionSha256": child["inputIntegrity"]["smallGicpExtensionSha256"],
        "privateCollectionReportSha256": sha256(directory / "collection-report.json"),
        "coordinatorSha256": collection["coordinatorSha256"],
        "x64Compatibility": compatibility_summary,
        "limitations": [
            "recorded_input_compute_and_loopback_https_only_no_live_localization",
            "seed_from_recording_not_independent_position_truth",
            "copied_x64_saved_map_no_native_arm64_map_reload_in_this_trial",
            "map_may_reuse_recording_no_map_profile_association_or_base_calibration",
            "system_scipy_warned_about_numpy_version_range_no_system_packages_changed",
            "native_library_inventory_in_child_report_is_not_proof_every_entry_was_mapped",
            "no_navigation_android_acoustic_or_product_acceptance",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", required=True, type=Path)
    parser.add_argument("--compatibility", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = evidence(args.trial, args.compatibility)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps({"result": report["result"], "completedMessages": report["completedMessages"]}))


if __name__ == "__main__":
    main()
