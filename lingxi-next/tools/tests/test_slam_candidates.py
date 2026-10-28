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
        self.assertEqual("provisional_no_algorithm_benchmarks", self.manifest["selectionStatus"])
        for key in ("dynamicReplay", "mapPersistenceAndRelocalization", "navigationAndEmergencyStop"):
            self.assertEqual("not_run", self.manifest["targetValidation"][key])

    def test_documentation_links_the_same_reviewed_commits(self):
        document = (ROOT / "docs" / "OPEN_SOURCE_NAV_EVALUATION.md").read_text(encoding="utf-8")
        for item in self.manifest["repositories"]:
            self.assertIn(item["repository"], document)
            self.assertIn(item["commit"], document)

    def test_lio_and_navigation_are_not_recorded_as_the_same_role(self):
        roles = {item["repository"]: item["role"] for item in self.manifest["repositories"]}
        self.assertEqual("downstream_navigation_not_lio", roles["ros-navigation/navigation2"])
        self.assertEqual("lio_algorithm_comparison_ros1_upstream", roles["hku-mars/FAST_LIO"])


if __name__ == "__main__":
    unittest.main()
