"""Audit preserved passive-live reports; no robot connection or native execution."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot-runtime/python"))
from controlled_localization import source_hashes
from live_localization import verify_completion
from localization_control import LocalizationBinding, strict_json
from run_live_localization_arm64_trial import RESULT
from run_localization_arm64_trial import FILES
from verify_slam_capture import sha256
from check_managed_live_localization import verify_base_input

FLAGS = ("motionCommandsSent", "localizationReady", "navigationReady", "mapProfileAssociationVerified")
SOURCES = (*source_hashes(), "robot-runtime/python/managed_localization.py",
           "robot-runtime/python/localization_owner.py", "tools/check_managed_live_localization.py")


def load(file):
    return strict_json(file.read_bytes())


def evidence(directory):
    collection, check, owner, child, final = (load(directory / name) for name in (
        "collection-report.json", "check-report.json", "run/owner-report.json",
        "run/native/child-report.json", "run/native/localization-final.json"))
    integrity = child["inputIntegrity"]
    frozen = collection["prepare"]["frozenInputs"]
    binding = LocalizationBinding(**integrity["localizationBinding"])
    sources = {name: sha256(ROOT / name) for name in SOURCES}
    base = child.get("basePoseInput")
    if base is not None:
        verify_base_input(base)
        if check.get("basePoseInputChecked") is not True or check.get("basePoseInput") != base:
            raise ValueError("identical_verified_live_base_input_required")
    verify_completion(child, final, frozen=frozen, binding=binding,
        sources=source_hashes(), config_hash=integrity["localizationConfigSha256"],
        base_scale=base["metresPerMapUnit"] if base is not None else None)
    api = dict(exitCode=0, forcedTermination=False, processAlive=False, groupAlive=False)
    native = dict(state="stopped", exitCode=0, forcedTermination=False,
                  cleanupComplete=True, processAlive=False, monitorAlive=False)
    process, worker = owner["nativeProcess"], child["worker"]
    references = check["referenceFramesObserved"]
    expected_reports = {"owner-report.json", "native/child-report.json", "native/localization-final.json"}
    if (collection["result"] != RESULT or collection["checkSummary"] != check
            or collection["prepareExitCode"] != 0 or collection["checkExitCode"] != 0
            or collection["prepare"]["result"] != "live_localization_inputs_prepared"
            or collection["prepare"]["smallGicpExtensionSha256"] != integrity["smallGicpExtensionSha256"]
            or any(collection[key] is not True for key in (
                "robotContacted", "hostIdentityVerified", "connectionClosed"))
            or check["result"] != "managed_live_unseeded_localization_https_checked"
            or check["sourceSha256"] != sources or child["machine"] != "aarch64"
            or child["versions"]["input"] != "installed_humble_rclpy"
            or child["versions"]["small-gicp"] != "1.0.1"
            or any(report[key] is not False for report in (collection, check, owner) for key in FLAGS)
            or any(check[key] is not True for key in (
                "pauseConfirmed", "stopConfirmed", "staleGenerationRejected", "singleNativeWorker"))
            or check["seedRequests"] != 0 or check["seedSource"] is not None
            or check["stoppedQueries"] < 5 or len(references) < 5
            or any(type(value) is not int or value < 1 for value in references)
            or any(left >= right for left, right in zip(references, references[1:]))
            or owner["result"] != "completed" or owner["reason"] != "live_interval_complete"
            or owner["failure"] is not None or owner["failurePhase"] is not None
            or owner["inputMode"] != "live" or owner["inputComplete"] is not False
            or owner["intervalComplete"] is not True or owner["inputsRechecked"] is not True
            or check["apiProcess"] != api or owner["apiProcess"] != api
            or check["nativeProcess"] != native
            or any(process[key] != value for key, value in native.items())
            or process["failure"] is not None or process["childFailure"] is not None
            or process["session"] != binding.session or process["workerState"] != "stopped"
            or check["nativeCalls"] != child["nativeCalls"]
            or any(check[key] != process[key] or process[key] != value for key, value in (
                ("completedMessages", worker["counts"]["completedMessages"]),
                ("processedScans", worker["stream"]["counts"]["processedScans"]),
                ("estimatedPoses", worker["stream"]["counts"]["estimatedPoses"])))
            or check["receiptSha256"] != binding.receipt_sha256
            or check["profileSha256"] != binding.profile_sha256
            or check["apiBundleSha256"] != collection["uploadedFiles"]["api.mjs"]["sha256"]
            or set(check["privateReportsSha256"]) != expected_reports
            or any(sha256(directory / "run" / name) != digest
                   for name, digest in check["privateReportsSha256"].items())
            or not set(FILES).issubset(collection["filesSha256"])
            or any(Path(name).is_absolute() or ".." in Path(name).parts
                   or sha256(directory / name) != digest
                   for name, digest in collection["filesSha256"].items())):
        raise ValueError("complete_source_bound_passive_live_reports_required")
    for name, digest in sources.items():
        if collection["uploadedFiles"][name]["sha256"] != digest:
            raise ValueError("identical_uploaded_and_executed_live_sources_required")
    report = {
        "schemaVersion": 1, "observedDate": "2026-10-30", "result": RESULT,
        "platform": "linux-aarch64-humble", "inputMode": "live", "robotContacted": True,
        **dict.fromkeys(FLAGS, False),
        **{name: check[name] for name in (
            "seedRequests", "seedSource", "pauseConfirmed", "stopConfirmed", "staleGenerationRejected",
            "stoppedQueries", "referenceFramesObserved", "completedMessages", "processedScans",
            "estimatedPoses", "nativeCalls", "singleNativeWorker", "sourceSha256",
            "apiBundleSha256", "nodeSha256", "receiptSha256", "profileSha256", "privateReportsSha256")},
        "workerCounts": worker["counts"],
        "maximumObservedQueueAgeSeconds": worker["maximumObservedQueueAgeSeconds"],
        "apiProcess": api, "nativeProcess": native, "versions": child["versions"],
        "ddsProfileSha256": frozen["ddsProfileSha256"],
        "smallGicpExtensionSha256": integrity["smallGicpExtensionSha256"],
        "privateCheckReportSha256": sha256(directory / "check-report.json"),
        "privateCollectionReportSha256": sha256(directory / "collection-report.json"),
        "limitations": [
            "unseeded_live_input_and_loopback_https_only_not_map_localization_accuracy",
            "estimated_poses_are_native_odometry_not_saved_map_tracking",
            "copied_x64_saved_map_not_arm64_native_reload_or_current_site_association",
            "raw_imu_coordinates_no_base_calibration_or_navigation_tf",
            "native_imu_prediction_quality_warning_not_resolved_by_this_trial",
            "no_lan_browser_android_motion_or_acoustic_acceptance",
            "system_scipy_warned_about_numpy_range_no_system_packages_changed",
            "short_bounded_trial_not_long_run_or_product_acceptance",
        ],
    }
    if base is not None:
        report.update(basePoseInputChecked=True, basePoseInput=base)
        report["limitations"] = [value for value in report["limitations"] if value not in (
            "copied_x64_saved_map_not_arm64_native_reload_or_current_site_association",
            "raw_imu_coordinates_no_base_calibration_or_navigation_tf")]
        report["limitations"].extend((
            "saved_map_not_verified_as_current_site",
            "exact_time_tf_input_only_no_seeded_live_base_pose_or_physical_calibration",
            "unavailable_tf_refused_no_latest_or_identity_fallback"))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = evidence(args.trial)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps({"result": report["result"], "completedMessages": report["completedMessages"]}))


if __name__ == "__main__":
    main()
