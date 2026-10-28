"""Whitelist aggregate GLIM/sensor evidence; never export trajectories or raw logs."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
# Historical reports are checked against their immutable source snapshots.
HISTORICAL_FINAL_RUNNER = "612d4a4818fd9a5dff288a5652bcfdc5b639262aa0e56c496c461a893d21ac9f"
RUNS = {
    "initialStaticStart": ("static", "header_is_scan_start", "glim-static-start-20261028-a"),
    "staticStart": ("static", "header_is_scan_start", "glim-static-start-20261028-b"),
    "staticEnd": ("static", "header_is_scan_end", "glim-static-end-20261028-a"),
    "dynamicStart": ("dynamic", "header_is_scan_start", "glim-dynamic-start-20261028-a"),
    "dynamicEnd": ("dynamic", "header_is_scan_end", "glim-dynamic-end-20261028-a"),
}
FIELDS = (
    "dataset", "timingHypothesis", "result", "processedScans", "skippedUnbracketedScans",
    "initializationScans", "estimatedPoses", "recordedScans", "processingWallSeconds",
    "processCpuSeconds", "peakResidentMiB", "preprocessMillisecondsP50P95P99",
    "odometryMillisecondsP50P95P99", "combinedMillisecondsMaximum",
    "preprocessedPointCountMinMax", "imuSamplesInserted", "unusedTrailingImuSamples",
    "trajectoryKind", "runnerSha256", "profileSha256", "captureManifestSha256",
    "sensorDiagnosticSha256", "adapterBuildReportSha256", "privateTrajectorySha256",
    "privateNativeLogSha256", "remainingFixedLagFramesAtEnd",
)
MOTION_FIELDS = (
    "accumulatedPositionStepsRawUnits", "endToStartDistanceRawUnits",
    "maximumConsecutivePositionStepRawUnits", "maximumConsecutiveRotationDegrees",
)


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def sdk_evidence(archive):
    with zipfile.ZipFile(archive) as source:
        members = {}
        for label, suffix in (
            ("imuExample", "/src/examples/src/hal/echo_imu_data.cpp"),
            ("sensorDocumentation", "/docs/cn/_build/html/zh-cn/dev/Interface/hal/sensor.html"),
        ):
            names = [name for name in source.namelist() if name.endswith(suffix)]
            if len(names) != 1:
                raise ValueError("unique_reviewed_sdk_member_required")
            payload = source.read(names[0])
            members[label] = {"member": names[0], "sha256": hashlib.sha256(payload).hexdigest()}
            if label == "imuExample":
                if any(token not in payload for token in (
                    b"angular_velocity (rad/s)", b"linear_accel (m/s^2)",
                    b"/aima/hal/sensor/lidar_chest_front/imu",
                )):
                    raise ValueError("sdk_imu_example_no_longer_matches_review")
            else:
                parser = Text()
                parser.feed(payload.decode("utf-8"))
                text = " ".join(" ".join(parser.parts).split())
                if not all(token in text for token in (
                    "PointXYZIRT", "lidar_imu_chest_front",
                    "\u8ddd\u79bb\u5355\u4f4d : \u7c73",
                )):
                    raise ValueError("sdk_cloud_units_no_longer_match_review")
    return {
        "archiveSha256": sha256(archive), "members": members,
        "cloudDistanceUnitDeclared": "metres",
        "imuExampleAngularVelocityUnitDeclared": "radians_per_second",
        "imuExampleAccelerationUnitDeclared": "metres_per_second_squared",
        "declarationsAreNotMeasuredCalibration": True,
        "scanHeaderReferenceSpecifiedInReviewedMaterial": False,
    }


def export(cache, sdk_archive):
    capture_path = ROOT / "docs/slam-capture-evidence.json"
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    sensor_path = cache / "sensor-diagnostic-20261028-a/report.json"
    sensor = json.loads(sensor_path.read_text())
    if (sensor["captureManifestSha256"] != sha256(capture_path)
            or sensor["runnerSha256"] != sha256(ROOT / "tools/diagnose_slam_sensors.py")
            or any(value is not False for value in sensor["boundaries"].values())):
        raise ValueError("unapproved_sensor_diagnostic_required")
    result = {
        "schemaVersion": 1, "observedDate": capture["observedDate"],
        "purpose": "real_glim_odometry_and_sensor_diagnostic_not_navigation_acceptance",
        "selectionStatus": "pending_timing_calibration_mapping_and_target_validation",
        "sdkDeclarations": sdk_evidence(sdk_archive), "sensorReportSha256": sha256(sensor_path),
        "sensorRunnerSha256": sensor["runnerSha256"], "sensorAggregates": {}, "runs": {},
        "boundaries": {
            "groundTruthAvailable": False, "robotCalibrationValidated": False,
            "timingHypothesisValidated": False, "fullSameDataComparisonPassed": False,
            "globalMappingExecuted": False, "mapPersistenceValidated": False,
            "relocalizationValidated": False, "targetArm64RuntimeValidated": False,
            "navigationValidated": False, "deploymentApproved": False, "rawDataPublished": False,
        },
    }
    for label, value in sensor["datasets"].items():
        comparison = value["angularRateComparison"]
        extrinsic = value["staticExtrinsicObservationNotCalibration"]["T_lidar_imu"]
        result["sensorAggregates"][label] = {
            "identityImuOrientationSamples": value["imuOrientation"]["identityRotationSamples"],
            "imuOrientationSamples": value["imuOrientation"]["count"],
            "imuMaximumRotationFromFirstDegrees": value["imuOrientation"]["maximumRotationFromFirstDegrees"],
            "odometryMaximumRotationFromFirstDegrees": value["odometryOrientation"]["maximumRotationFromFirstDegrees"],
            "imuAngularNormRawMinP50P95Max": value["imuAngularVelocityRaw"]["normMinP50P95Max"],
            "imuAccelerationNormRawMinP50P95Max": value["imuAccelerationRaw"]["normMinP50P95Max"],
            "odometryAngularTwistMaximumRawNorm": value["odometryAngularTwistRaw"]["normMinP50P95Max"][-1],
            "gyroOdometryCenteredVectorCorrelation": comparison["centeredVectorCorrelation"],
            "gyroOdometryCenteredLeastSquaresScaleDiagnosticOnly": comparison["centeredLeastSquaresScaleDiagnosticOnly"],
            "gyroOdometryRadiansHypothesisVectorRmse": comparison["fixedScaleHypotheses"]["radiansPerSecond"]["vectorRmse"],
            "gyroOdometryDegreesHypothesisVectorRmse": comparison["fixedScaleHypotheses"]["degreesPerSecond"]["vectorRmse"],
            "imuHeaderGapSecondsP50P95Max": value["imuHeaderGapSecondsP50P95Max"],
            "staticExtrinsicTranslationNormRawUnits": math.hypot(*extrinsic[:3]),
        }
    build_path = cache / "glim-replay-build-20261028-a/build-report.json"
    build = json.loads(build_path.read_text())
    if (build["result"] != "adapter_built_not_algorithm_acceptance"
            or build["adapterSourceSha256"] != sha256(ROOT / "tools/glim-replay/offline.cpp")
            or build["librarySha256"] != sha256(build_path.parent / "build/liblingxi_glim_offline.so")):
        raise ValueError("adapter_build_provenance_mismatch")
    result["adapterBuild"] = build
    for label, (dataset, hypothesis, folder) in RUNS.items():
        path = cache / folder / "report.json"
        report = json.loads(path.read_text())
        if (report.get("result") != "all_scans_accounted_for_not_quality_acceptance"
                or report["dataset"] != dataset or report["timingHypothesis"] != hypothesis
                or any(value is not False for value in report["boundaries"].values())):
            raise ValueError("completed_unapproved_diagnostics_required")
        if (report["recordedScans"] != capture["datasets"][dataset]["topicCounts"]["cloud"]
                or report["processedScans"] + report["skippedUnbracketedScans"] != report["recordedScans"]
                or report["initializationScans"] + report["estimatedPoses"] != report["processedScans"]):
            raise ValueError("every_recorded_scan_must_be_accounted_for")
        if (report["captureManifestSha256"] != sha256(capture_path)
                or report["sensorDiagnosticSha256"] != sha256(sensor_path)
                or report["adapterBuildReportSha256"] != sha256(build_path)
                or report["runnerSha256"] != sha256(path.parent / "runner-source.py")
                or report["privateTrajectorySha256"] != sha256(path.parent / "private-trajectory.npz")
                or report["privateNativeLogSha256"] != sha256(path.parent / "private-native.log")):
            raise ValueError("private_run_provenance_mismatch")
        if label != "initialStaticStart" and report["runnerSha256"] != HISTORICAL_FINAL_RUNNER:
            raise ValueError("final_runner_provenance_mismatch")
        run = {name: report[name] for name in FIELDS}
        run["estimatedMotionNotGroundTruth"] = {
            name: report["estimatedMotionNotGroundTruth"][name] for name in MOTION_FIELDS
        }
        run["localReportSha256"] = sha256(path)
        run["initialDevelopmentRun"] = label == "initialStaticStart"
        result["runs"][label] = run
        if label == "dynamicStart":
            result["runtime"] = {
                name: report[name] for name in ("host", "versions", "nativeLibrariesSha256", "sourceCommits")
            }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cache-root", "sdk-archive", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("existing_public_evidence_is_preserved")
    evidence = export(args.cache_root, args.sdk_archive)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"result": "aggregate_only_evidence_exported", "runs": len(evidence["runs"])}))


if __name__ == "__main__":
    main()
