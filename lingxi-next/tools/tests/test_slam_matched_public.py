"""Published aggregate/source consistency, with no private data dependencies."""
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[2]
EVIDENCE = ROOT / "docs/slam-matched-input-evidence.json"


class MatchedPublic(unittest.TestCase):
    def setUp(self):
        self.report = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def digest(self, path):
        return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()

    def test_independently_recomputed_evidence_and_executed_source_hashes(self):
        self.assertEqual("bf2e546d80ca77f587b379b1eced289d32e1b674952cee6d240f459126e1f793",
                         self.digest(EVIDENCE))
        for name, digest in self.report["sourceSha256"].items():
            self.assertEqual(digest, self.digest(ROOT / name))
        self.assertEqual(self.report["coordinatorSha256"], self.digest(ROOT / "tools/run_glim_matched_target.py"))
        self.assertEqual(self.report["exporterSha256"], self.digest(ROOT / "tools/export_glim_matched_evidence.py"))

    def test_same_prefix_and_scan_accounting_is_explicit(self):
        counts = self.report["liveWorker"]["counts"]
        self.assertEqual(self.report["callbackAudit"]["records"], counts["acceptedClouds"] + counts["acceptedImu"])
        self.assertEqual(counts["completedMessages"], self.report["replayedCompletedPrefixMessages"])
        self.assertEqual(counts["discardedMessages"], self.report["excludedExplicitStopTailMessages"])
        self.assertEqual(counts["completedMessages"] + counts["discardedMessages"],
                         self.report["callbackAudit"]["records"])
        self.assertTrue(self.report["sameInputComparison"]["exactFrameDomainAndAlgorithmTimes"])
        self.assertTrue(self.report["distinctActualArm64ProcessIdsVerifiedNotPublished"])

    def test_matching_warnings_are_not_calibration_or_quality_acceptance(self):
        self.assertEqual(self.report["nativeWarnings"]["live"], self.report["nativeWarnings"]["replay"])
        self.assertTrue(self.report["warningSummariesIdentical"])
        self.assertFalse(self.report["sameInputComparison"]["physicalAccuracyValidated"])
        self.assertTrue(all(value is False for value in self.report["boundaries"].values()))
        self.assertTrue(self.report["replayIsLogicalArrivalOrderNotRealtimeMeasurement"])
        self.assertTrue(self.report["captureCostIsInstrumentedSummaryNotIndependentlyRecomputedSamples"])

    def test_raw_headers_coordinates_calibration_addresses_and_credentials_not_published(self):
        text = EVIDENCE.read_text(encoding="utf-8")
        for private in ("headers_ns", '"poses"', '"pid"', "T_lidar_imu", "R_baselink_sensor",
                        "192.168.", "10.201.", "/home/run", "/home/agi", "password", "private_key"):
            self.assertNotIn(private, text)


if __name__ == "__main__":
    unittest.main()
