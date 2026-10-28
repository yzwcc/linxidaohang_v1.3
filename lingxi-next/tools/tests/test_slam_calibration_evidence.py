"""Public aggregate consistency; no private files, robot, ROS or scientific packages."""
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[2]
EVIDENCE = ROOT / "docs/slam-calibration-audit-evidence.json"


class CalibrationEvidence(unittest.TestCase):
    def setUp(self):
        self.report = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def test_public_evidence_is_the_independently_reproduced_aggregate(self):
        canonical = EVIDENCE.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(
            "f5a3c9b12cdfb70a4d20658f5e039f9df478738c55f3150b0972e4b2ceb9622a",
            hashlib.sha256(canonical).hexdigest(),
        )
        for key, source in (
            ("runnerSha256", "tools/audit_slam_calibration.py"),
            ("factoryReaderSha256", "tools/read_robot_calibration.py"),
        ):
            canonical = (ROOT / source).read_bytes().replace(b"\r\n", b"\n")
            self.assertEqual(self.report[key], hashlib.sha256(canonical).hexdigest())

    def test_top_level_and_factory_fields_exclude_raw_parameters(self):
        self.assertEqual({
            "schemaVersion", "purpose", "runnerSha256", "factoryReaderSha256",
            "factoryCaptureReportSha256", "privateFactoryFileSha256", "factoryAggregate",
            "captureManifestSha256", "profileSha256", "sdkDocument", "datasets",
            "analysisVersions", "warningSemantics", "boundaries",
        }, set(self.report))
        self.assertEqual({
            "sections", "lidarImuNamedSectionPresent", "chestImuSectionPresent",
            "waistImuSectionPresent", "chestLidarRotationDeterminant",
            "chestLidarRotationOrthogonalityMaximumError",
            "factoryBaseLidarCannotSupplyLidarImuExtrinsic",
        }, set(self.report["factoryAggregate"]))
        text = EVIDENCE.read_text(encoding="utf-8")
        for private in ("R_baselink_sensor", "t_baselink_sensor", "T_lidar_imu",
                        "headers_ns", "192.168.", "10.201.", "password", "private_key",
                        "/home/run", "/home/agi", ".local/"):
            self.assertNotIn(private, text)

    def test_every_scan_is_accounted_for_without_extrapolation(self):
        capture = json.loads((ROOT / "docs/slam-capture-evidence.json").read_text(encoding="utf-8"))
        for name, dataset in self.report["datasets"].items():
            expected = capture["datasets"][name]["topicCounts"]
            self.assertEqual({key: expected[key] for key in ("cloud", "tf", "tf_static")},
                             dataset["selectedMessageCounts"])
            self.assertFalse(dataset["staticOnlyBaseToLidarPath"])
            self.assertEqual(3, len(dataset["dynamicEdgesOnBaseToLidarPath"]))
            self.assertTrue(dataset["profileMatchesRecordedLidarImuTf"])
            self.assertEqual({"header_is_scan_start", "header_is_scan_end"}, set(dataset["queries"]))
            for query in dataset["queries"].values():
                self.assertEqual(expected["cloud"], query["bracketedQueries"] + query["uncoveredQueries"])
                self.assertEqual(query["uncoveredQueries"], sum(query["uncoveredReasons"].values()))
                self.assertLessEqual(query["largestUsedInterpolationGapMilliseconds"],
                                     dataset["maximumInterpolationGapMilliseconds"])
                self.assertEqual({"literal_matrix", "inverse_matrix"},
                                 set(query["factoryConventionComparisonsNotCalibration"]))

    def test_warning_reports_are_anchored_to_prior_public_reports(self):
        runs = self.report["warningSemantics"]["runs"]
        self.assertEqual(6, len(runs))
        for prefix, filename in (
            ("paced-", "slam-arm64-worker-evidence.json"),
            ("live-", "slam-humble-live-evidence.json"),
        ):
            parent = json.loads((ROOT / "docs" / filename).read_text(encoding="utf-8"))
            for name, record in parent["runs"].items():
                current = runs[prefix + name]
                self.assertEqual(record["reportSha256"], current["reportSha256"])
                self.assertEqual(current["predictionWarningCount"], len(current["loggedCumulativeWindows"]))
                self.assertTrue(current["roundedRatiosAreNotExactThresholdOrCalibrationTests"])
        self.assertEqual("optimized_state_not_external_ground_truth",
                         self.report["warningSemantics"]["comparisonReference"])

    def test_no_diagnostic_result_is_promoted_to_runtime_acceptance(self):
        self.assertEqual("factory_and_recorded_frame_audit_not_calibration_acceptance",
                         self.report["purpose"])
        self.assertEqual({
            "robotCalibrationValidated", "timingHypothesisSelected", "factoryValuesApplied",
            "timeOffsetFitted", "imuBiasSubtracted", "groundTruthAvailable", "navigationValidated",
            "deploymentApproved", "rosNodeStarted", "motionCommandsSent", "rawCalibrationPublished",
        }, set(self.report["boundaries"]))
        self.assertTrue(all(value is False for value in self.report["boundaries"].values()))
        self.assertFalse(self.report["factoryAggregate"]["lidarImuNamedSectionPresent"])
        self.assertTrue(self.report["factoryAggregate"]["factoryBaseLidarCannotSupplyLidarImuExtrinsic"])


if __name__ == "__main__":
    unittest.main()
