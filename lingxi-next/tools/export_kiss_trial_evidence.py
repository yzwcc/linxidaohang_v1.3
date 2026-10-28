"""Export only aggregate fields from completed local diagnostic reports."""
import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIELDS = (
    "dataset", "result", "processedFrames", "pointsPassedToAlgorithm",
    "nonfiniteCoordinatesDropped", "recordedCloudHeaderSpanSeconds",
    "processingWallSeconds", "processCpuSeconds", "peakResidentMiB",
    "offlineRecordedSpanToProcessingTimeRatio", "decodeMillisecondsP50P95P99",
    "registrationMillisecondsP50P95P99", "registrationMillisecondsMaximum",
    "registrationOver100MillisecondsFrames", "latencySamplesIncludeFirstFrame",
    "perFrameRegistrationPointCountMinMax", "perFramePointTimeSpanSecondsMinMax",
    "estimatedMotionNotGroundTruth", "privateTrajectorySha256",
    "runnerSha256", "captureManifestSha256", "profileSha256",
)
MOTION_FIELDS = (
    "accumulatedPositionStepsRawUnits", "endToStartDistanceRawUnits",
    "maximumDistanceFromStartRawUnits", "maximumConsecutivePositionStepRawUnits",
    "maximumConsecutiveRotationDegrees",
)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def summarize(paths):
    capture_path = ROOT / "docs/slam-capture-evidence.json"
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    runner_hash = sha256(ROOT / "tools/run_kiss_offline_trial.py")
    runs, baseline = {}, None
    for label, path in paths.items():
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        expected_dataset = "static" if label in ("initialStatic", "staticDeskew") else "dynamic"
        if report["dataset"] != expected_dataset:
            raise ValueError("wrong_dataset_for_comparison")
        if report["result"] != "all_recorded_clouds_processed_not_quality_acceptance":
            raise ValueError("completed_diagnostic_reports_required")
        if report["processedFrames"] != capture["datasets"][report["dataset"]]["topicCounts"]["cloud"]:
            raise ValueError("report_frame_count_does_not_match_capture")
        if any(value is not False for value in report["boundaries"].values()):
            raise ValueError("diagnostic_must_not_claim_acceptance")
        profile_name = (
            "kiss-offline-no-deskew-profile.json"
            if label == "dynamicNoDeskew" else "kiss-offline-profile.json"
        )
        profile_path = ROOT / "docs" / profile_name
        if (report["captureManifestSha256"] != sha256(capture_path)
                or report["profileSha256"] != sha256(profile_path)
                or report["profile"] != json.loads(profile_path.read_text(encoding="utf-8"))):
            raise ValueError("report_input_provenance_mismatch")
        if not label.startswith("initial") and report.get("runnerSha256") != runner_hash:
            raise ValueError("report_runner_provenance_mismatch")
        common = {key: report[key] for key in ("host", "versions", "wheelInventory", "installedPackages")}
        if baseline is not None and common != baseline:
            raise ValueError("diagnostic_runtime_changed_between_runs")
        baseline = common
        runs[label] = {key: report[key] for key in FIELDS if key in report}
        runs[label]["estimatedMotionNotGroundTruth"] = {
            key: report["estimatedMotionNotGroundTruth"][key]
            for key in MOTION_FIELDS if key in report["estimatedMotionNotGroundTruth"]
        }
        runs[label].update(
            localReportSha256=sha256(path),
            profileFile=profile_name,
            initialDevelopmentRun=label.startswith("initial"),
        )
    if set(runs) != {"initialStatic", "initialDynamic", "staticDeskew", "dynamicDeskew", "dynamicNoDeskew"}:
        raise ValueError("all_declared_runs_must_be_retained")
    return {
        "schemaVersion": 1, "observedDate": capture["observedDate"],
        "purpose": "same_capture_kiss_only_diagnostic_not_full_algorithm_comparison",
        "selectionStatus": "not_accepted_pending_calibration_and_glim_comparison",
        "runtime": baseline, "runs": runs,
        "boundaries": {
            "groundTruthAvailable": False, "physicalUnitsCalibrated": False,
            "binaryMatchesReviewedGitCommitProven": False,
            "glimRealReplayExecuted": False, "fullSameDataComparisonPassed": False,
            "targetArm64RuntimeValidated": False, "mapPersistenceValidated": False,
            "relocalizationValidated": False, "navigationValidated": False,
            "deploymentApproved": False, "rawDataPublished": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    names = {
        "initial-static": "initialStatic", "initial-dynamic": "initialDynamic",
        "static-deskew": "staticDeskew", "dynamic-deskew": "dynamicDeskew",
        "dynamic-no-deskew": "dynamicNoDeskew",
    }
    for name in (*names, "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("existing_evidence_is_preserved")
    result = summarize({label: getattr(args, name.replace("-", "_")) for name, label in names.items()})
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"result": "aggregate_evidence_exported", "runs": len(result["runs"])}))


if __name__ == "__main__":
    main()
