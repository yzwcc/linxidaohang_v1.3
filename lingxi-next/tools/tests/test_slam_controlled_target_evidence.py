"""Validate public aggregate completeness and provenance without native execution."""
import hashlib
import itertools
import json
import math
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[2]
PATH = ROOT / "docs/slam-controlled-target-evidence.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ControlledTargetEvidence(unittest.TestCase):
    def setUp(self):
        self.evidence = json.loads(PATH.read_text())

    def test_every_predeclared_run_and_every_pair_is_present(self):
        evidence = self.evidence
        names = {f"{dataset}-{timing}-repeat{repeat}"
                 for dataset in ("static", "dynamic") for timing in ("start", "end")
                 for repeat in (1, 2, 3)}
        self.assertEqual(names, set(evidence["runs"]))
        self.assertEqual(12, evidence["freshTargetProcessCount"])
        self.assertEqual(3, evidence["repeatsPerCondition"])
        within, cross_arch, timing = set(), set(), set()
        for dataset in ("static", "dynamic"):
            for hypothesis in ("start", "end"):
                conditions = [f"{dataset}-{hypothesis}-repeat{repeat}" for repeat in (1, 2, 3)]
                within.update(first + "_vs_" + second for first, second in itertools.combinations(conditions, 2))
                cross_arch.update(first + "_vs_" + second for first, second in itertools.product(conditions, repeat=2))
            timing.update(f"{dataset}-start-repeat{first}_vs_{dataset}-end-repeat{second}"
                          for first, second in itertools.product((1, 2, 3), repeat=2))
        self.assertEqual(within, set(evidence["withinTargetConditionPairs"]))
        self.assertEqual(within, set(evidence["withinHostConditionGeometryPairs"]))
        self.assertEqual(cross_arch, set(evidence["hostTargetSameConditionPairs"]))
        self.assertEqual(timing, set(evidence["crossTargetTimingHypothesisPairsNotSameInstants"]))

    def test_sources_match_executed_target_and_frozen_host(self):
        evidence = self.evidence
        for group in ("actualTargetSourcesSha256", "toolSourcesSha256"):
            for name, expected in evidence[group].items():
                self.assertEqual(expected, digest(ROOT / "tools" / name), name)
        self.assertEqual(evidence["hostControlledEvidenceSha256"],
                         digest(ROOT / "docs/slam-controlled-mapping-evidence.json"))
        self.assertEqual(evidence["captureManifestSha256"], digest(ROOT / "docs/slam-capture-evidence.json"))
        host = json.loads((ROOT / "docs/slam-controlled-mapping-evidence.json").read_text())
        for key in ("sourceCommits", "baselineConfigurationManifestSha256", "effectiveConfigurationManifestSha256"):
            self.assertEqual(host[key], evidence[key])
        self.assertEqual(evidence["semanticParameterChanges"], [
            {"file": "config_preprocess.json", "path": ["preprocess", "num_threads"], "before": 2, "after": 1}])
        self.assertEqual({"numpy": "2.5.4", "scipy": "1.16.3"}, evidence["geometryDependencies"])
        for name, digest_value in evidence["actualTargetNativeLibrariesSha256"].items():
            self.assertRegex(digest_value, r"^[0-9a-f]{64}$", name)
        self.assertEqual(evidence["mappingAdapterSha256"],
                         evidence["actualTargetNativeLibrariesSha256"]["liblingxi_glim_mapping.so"])
        self.assertEqual(evidence["odometryAdapterSha256"],
                         evidence["actualTargetNativeLibrariesSha256"]["liblingxi_glim_offline.so"])

    def test_complete_all_point_distances_are_finite_and_not_truth_or_registration_claims(self):
        evidence = self.evidence
        geometries = list(evidence["withinHostConditionGeometryPairs"].values())
        for key in ("withinTargetConditionPairs", "hostTargetSameConditionPairs"):
            for pair in evidence[key].values():
                geometries.append(pair["completePointGeometryNotTruthAccuracy"])
                self.assertTrue(pair["sameTimingHypothesis"])
                self.assertNotIn("noNearestNeighborGeometryOrTruthAccuracyClaimed", pair["completeSavedMapPoints"])
        self.assertEqual(60, len(geometries))
        for value in geometries:
            for key in ("bestFitCloudRegistrationUsed", "scaleFitted", "geometryAcceptanceThresholdSelected"):
                self.assertIs(False, value[key])
            for key in ("unadjusted", "firstSavedLidarPoseRigidAlignmentOnly"):
                directions = value[key]
                self.assertTrue(directions["exactEuclideanNearestNeighbors"])
                self.assertTrue(directions["allPointsWithoutSamplingOrOutlierRemoval"])
                self.assertFalse(directions["multiplicityOrOneToOneCorrespondenceProven"])
                self.assertFalse(directions["accuracyAgainstTruthMeasured"])
                for direction, count in (("referenceToCandidateRawUnits", "referencePointCount"),
                                         ("candidateToReferenceRawUnits", "candidatePointCount")):
                    metric = directions[direction]
                    self.assertEqual(value[count], metric["pointsCompared"])
                    for field in ("p50", "p95", "p99", "maximum", "rms"):
                        self.assertTrue(math.isfinite(metric[field]) and metric[field] >= 0)
                    self.assertLessEqual(metric["p50"], metric["p95"])
                    self.assertLessEqual(metric["p95"], metric["p99"])
                    self.assertLessEqual(metric["p99"], metric["maximum"])
        for pair in evidence["crossTargetTimingHypothesisPairsNotSameInstants"].values():
            self.assertTrue(pair["differentHypothesisPosesReferToDifferentScanInstants"])

    def test_round_trip_counts_arithmetic_policy_and_unaccepted_boundaries(self):
        evidence = self.evidence
        self.assertEqual(2, evidence["roundTripRmsRecomputationMaximumUlps"])
        self.assertTrue(evidence["allOriginalCapturesAndCompleteHostAndTargetOutputsUnchangedAtExport"])
        self.assertTrue(evidence["boundaries"])
        self.assertTrue(all(value is False for value in evidence["boundaries"].values()))
        for value in evidence["runs"].values():
            self.assertEqual("aarch64", value["target"]["machine"])
            scans = value["scanAccounting"]
            self.assertEqual(scans["recordedScans"], scans["processedScans"] + scans["skippedUnbracketedScans"])
            self.assertEqual(scans["estimatedPoses"], scans["processedScans"] - scans["initializationScans"])
            self.assertEqual(scans["estimatedPoses"], value["mapping"]["savedFrames"])
            self.assertEqual(scans["estimatedPoses"], value["mapping"]["finalizedFramesFedOnce"])
            self.assertEqual(value["mapping"]["points"], value["roundTrip"]["pointCount"])
            self.assertEqual(1e-4, value["roundTrip"]["predeclaredToleranceRawUnits"])
            self.assertLess(value["roundTrip"]["maximumPointDisplacementRawUnits"], 1e-4)
            self.assertFalse(value["roundTrip"]["geometricAccuracyValidated"])
            self.assertGreaterEqual(value["roundTripRmsRecomputationDifferenceRawUnits"], 0)

    def test_public_file_contains_no_points_poses_device_paths_or_credentials(self):
        text = json.dumps(self.evidence)
        for token in ('"poses"', '"online"', '"saved"', '"header_stamps_ns"', '"T_lidar_imu"',
                      '"password"', '"hostname"', '"credential"', '"pid"', "/home/", "/mnt/", "E:\\\\"):
            self.assertNotIn(token, text)
        self.assertLess(PATH.stat().st_size, 2_000_000)


if __name__ == "__main__":
    unittest.main()
