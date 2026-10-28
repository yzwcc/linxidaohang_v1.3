"""Keep review baselines distinct from unperformed deployment acceptance."""
from datetime import date, datetime
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).parents[2]


class CandidateBaselines(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(
            (ROOT / "docs" / "slam-candidates.json").read_text(encoding="utf-8")
        )

    def test_all_reviewed_repositories_have_immutable_commit_ids(self):
        repositories = self.manifest["repositories"]
        self.assertEqual(7, len(repositories))
        self.assertEqual(7, len({item["repository"] for item in repositories}))
        for item in repositories:
            self.assertIsNotNone(re.fullmatch("[0-9a-f]{40}", item["commit"]))
            self.assertFalse(item["robotBuildVerified"])
            self.assertTrue(item["rootLicense"])

    def test_commit_dates_do_not_postdate_review(self):
        reviewed = date.fromisoformat(self.manifest["reviewDate"])
        for item in self.manifest["repositories"]:
            committed = datetime.fromisoformat(item["commitDate"].replace("Z", "+00:00"))
            self.assertLessEqual(committed.date(), reviewed)

    def test_review_does_not_claim_deployment_or_full_dependency_lock(self):
        self.assertFalse(self.manifest["deploymentApproved"])
        self.assertFalse(self.manifest["dependenciesPinned"])
        self.assertEqual("provisional_glim_supported_pending_timing_mapping_and_target_validation",
                         self.manifest["selectionStatus"])
        self.assertEqual("not_run", self.manifest["targetValidation"]["navigationAndEmergencyStop"])
        self.assertEqual("host_persistence_only_passed_relocalization_not_run",
                         self.manifest["targetValidation"]["mapPersistenceAndRelocalization"])
        self.assertEqual("passed_four_real_data_save_and_fresh_process_cpu_reload_diagnostics",
                         self.manifest["targetValidation"]["hostMapPersistence"])
        self.assertEqual("not_accepted_common_window_and_mapping_comparison_pending",
                         self.manifest["targetValidation"]["fullSameDataComparison"])
        self.assertEqual("kiss_and_glim_odometry_diagnostics_completed_not_navigation_acceptance",
                         self.manifest["targetValidation"]["dynamicReplay"])

    def test_documentation_links_the_same_reviewed_commits(self):
        document = (ROOT / "docs" / "OPEN_SOURCE_NAV_EVALUATION.md").read_text(encoding="utf-8")
        for item in self.manifest["repositories"]:
            self.assertIn(item["repository"], document)
            self.assertIn(item["commit"], document)

    def test_lio_and_navigation_are_not_recorded_as_the_same_role(self):
        roles = {item["repository"]: item["role"] for item in self.manifest["repositories"]}
        self.assertEqual("downstream_navigation_not_lio", roles["ros-navigation/navigation2"])
        self.assertEqual("lio_algorithm_comparison_ros1_upstream", roles["hku-mars/FAST_LIO"])

    def test_point_time_observation_is_not_recorded_as_calibration_or_replay(self):
        target = self.manifest["targetValidation"]
        self.assertIn("hypothesis", target["pointTimeUnits"])
        evidence = target["pointTimeEvidence"]
        self.assertEqual(evidence["sampledCloudFrames"], evidence["matchingFrames"])
        self.assertFalse(evidence["calibrationValidated"])
        self.assertEqual("unverified", target["dynamicTimeAlignment"])
        self.assertEqual("kiss_and_glim_odometry_diagnostics_completed_not_navigation_acceptance",
                         target["dynamicReplay"])
        self.assertEqual("all_clouds_processed_quality_not_accepted", target["kissOfflineDiagnostic"])
        self.assertEqual("all_scans_accounted_for_two_unapproved_timing_hypotheses", target["glimOfflineDiagnostic"])
        self.assertIn("not_calibration", target["offlineConfigGeneration"])

    def test_wrapper_package_declaration_is_not_a_verified_root_license_file(self):
        wrapper = next(
            item for item in self.manifest["repositories"]
            if item["repository"] == "koide3/glim_ros2"
        )
        self.assertEqual("not_present_at_reviewed_commit", wrapper["rootLicense"])
        self.assertEqual("MIT", wrapper["packageLicense"])
        self.assertEqual("package.xml_declaration_only", wrapper["licenseEvidence"])

    def test_cross_link_and_synthetic_target_loading_do_not_imply_ros_or_real_data_runtime(self):
        target = self.manifest["targetValidation"]
        self.assertEqual("completed_static_and_dynamic_integrity_verified", target["operatorControlledCapture"])
        self.assertEqual("passed_jammy_arm64_cross_link_not_target_execution", target["arm64CoreCrossBuild"])
        self.assertEqual("passed_four_cpu_modules_isolated_on_actual_arm64_robot",
                         target["targetDependencyLoading"])
        self.assertEqual("not_run", target["targetRealDataRuntime"])
        self.assertEqual("not_run", target["rosWrapperBuild"])


if __name__ == "__main__":
    unittest.main()
