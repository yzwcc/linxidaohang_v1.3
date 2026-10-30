"""Published projection evidence is bounded and does not approve robot readiness."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BasePoseEvidence(unittest.TestCase):
    def setUp(self):
        self.report = json.loads((ROOT / "docs/slam-base-pose-evidence.json").read_text())

    def test_recorded_scope_counts_and_frame_are_explicit(self):
        self.assertEqual("same_recorded_native_lidar_to_base_projection_not_calibration_acceptance",
                         self.report["result"])
        self.assertEqual("lingxi_glim_odom", self.report["outputCoordinateFrame"])
        self.assertEqual("base_link", self.report["outputBodyFrame"])
        self.assertEqual({"static-start": 313, "static-end": 311, "dynamic-start": 1190, "dynamic-end": 1189},
                         {name: row["nativePoses"] for name, row in self.report["runs"].items()})
        for row in self.report["runs"].values():
            self.assertEqual(row["nativePoses"], row["projectedBasePoses"] + row["unavailablePoses"])
            self.assertLessEqual(row["largestUsedInterpolationGapNs"], self.report["maximumInterpolationGapNs"])
            self.assertLessEqual(row["maximumReconstructionElementError"], 1e-8)
        self.assertTrue(all(value is False for value in self.report["boundaries"].values()))
        self.assertEqual({"robotContacted", "liveSensorsSubscribed", "motionCommandsSent", "nativeEngineRerun",
                          "tfPublished", "physicalScaleValidated", "timingHypothesisValidated", "calibrationValidated",
                          "siteAssociationVerified", "mapGravityAlignmentValidated", "localizationReady",
                          "navigationReady", "tf2TargetValidated", "deploymentApproved"},
                         set(self.report["boundaries"]))
        self.assertEqual(1., self.report["explicitMetresPerMapUnitNotMeasured"])

    def test_source_and_original_report_identities_match_without_raw_data(self):
        self.assertEqual({"robot-slam/lingxi_slam/base_pose.py", "robot-slam/lingxi_slam/seeded_localization.py",
                          "tools/project_recorded_base_poses.py", "tools/audit_slam_calibration.py",
                          "tools/diagnose_slam_sensors.py", "tools/run_kiss_offline_trial.py",
                          "tools/verify_slam_capture.py", "tools/slam_bag_audit.py"},
                         set(self.report["sourceSha256"]))
        for name, digest in self.report["sourceSha256"].items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest, name)
        baseline = json.loads((ROOT / "docs/slam-stream-input-evidence.json").read_text())
        self.assertEqual(hashlib.sha256((ROOT / "docs/slam-stream-input-evidence.json").read_bytes()).hexdigest(),
                         self.report["streamEvidenceSha256"])
        self.assertEqual(baseline["captureManifestSha256"], self.report["captureManifestSha256"])
        for label, files in self.report["nativeInputSha256"].items():
            self.assertEqual({"report.json", "private-trajectory.npz"}, set(files))
            self.assertEqual(baseline["runs"][label]["reportSha256"], files["report.json"])
            self.assertEqual(baseline["runs"][label]["privateTrajectorySha256"], files["private-trajectory.npz"])
        allowed = {"schemaVersion", "result", "sourceSha256", "captureManifestSha256", "streamEvidenceSha256",
                   "nativeInputSha256", "maximumInterpolationGapNs", "outputCoordinateFrame", "outputBodyFrame",
                   "explicitMetresPerMapUnitNotMeasured", "runs", "boundaries"}
        self.assertEqual(allowed, set(self.report))
        for row in self.report["runs"].values():
            self.assertEqual({"nativePoses", "projectedBasePoses", "unavailablePoses", "unavailableReasons",
                              "baseToLidarPath", "largestUsedInterpolationGapNs", "maximumReconstructionElementError",
                              "maximumLidarBaseOriginDistanceMetresAtDeclaredScale", "maximumLidarBaseRotationDegrees"},
                             set(row))


if __name__ == "__main__":
    unittest.main()
