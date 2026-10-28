"""Published initialization evidence checks without private data or native libraries."""
import ast
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[2]
EVIDENCE = ROOT / "docs/slam-initialization-audit-evidence.json"


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class InitializationPublic(unittest.TestCase):
    def setUp(self):
        self.report = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def test_independent_recomputation_and_auditor_source_digest(self):
        self.assertEqual("9a2f3cad57259ca73221f8e483dc9c691fe827bfe45dbe9edc4f5829e74cbafa", digest(EVIDENCE))
        self.assertEqual(self.report["auditorSha256"], digest(ROOT / "tools/audit_glim_initialization.py"))
        self.assertEqual(self.report["matchedEvidenceCanonicalSha256"], digest(ROOT / "docs/slam-matched-input-evidence.json"))

    def test_callback_counts_and_initialization_domain_match_prior_verified_capture(self):
        prior = json.loads((ROOT / "docs/slam-matched-input-evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(prior["callbackJournalSha256"], self.report["callbackJournalSha256"])
        self.assertEqual(prior["sourceCommits"], self.report["sourceCommits"])
        for kind in ("imu", "cloud"):
            count = prior["callbackAudit"]["counts"][kind]
            self.assertEqual(count, self.report["callbackClock"][kind]["samples"])
        self.assertEqual(prior["liveObservations"]["initializationScans"],
                         self.report["initialization"]["noPoseScansBeforeFirstPose"])
        self.assertEqual(prior["nativeWarnings"]["live"], self.report["nativeWarnings"])

    def test_timings_are_observations_not_approval_or_transport_measurements(self):
        self.assertTrue(self.report["initialization"]["firstPoseAtFirstFrameReachingConfiguredWindow"])
        self.assertTrue(self.report["initialization"]["algorithmTimesMatchIntegerOriginScanStartFormula"])
        self.assertTrue(self.report["initialization"]["initialImuPrefixReconstructedFromFrozenDrainRuleNotInstrumentedCount"])
        for value in self.report["callbackClock"].values():
            self.assertTrue(value["absoluteTransportLatencyNotMeasured"])
        self.assertTrue(self.report["scanCoverage"]["neitherHypothesisSelectedOrApproved"])
        self.assertTrue(self.report["noNewSensorCaptureOrNativeReplay"])
        self.assertTrue(all(value is False for value in self.report["boundaries"].values()))

    def test_top_level_and_serialized_fields_exclude_raw_data_and_credentials(self):
        self.assertEqual({
            "schemaVersion", "purpose", "result", "auditorSha256", "matchedEvidenceCanonicalSha256",
            "callbackJournalSha256", "sourceCommits", "reviewedUpstreamSourceSha256", "callbackClock",
            "scanCoverage", "initialization", "nativeWarnings", "noNewSensorCaptureOrNativeReplay", "boundaries",
        }, set(self.report))
        for token in ('"poses"', '"headers_ns"', '"T_world_imu"', '"imu_bias"', '"pid"',
                      "192.168.", "10.201.", "/home/run", "/home/agi", "password", "private_key"):
            self.assertNotIn(token, EVIDENCE.read_text(encoding="utf-8"))

    def test_auditor_has_no_robot_connector_or_ros_native_execution_imports(self):
        tree = ast.parse((ROOT / "tools/audit_glim_initialization.py").read_text(encoding="utf-8"))
        forbidden = {"ctypes", "subprocess", "socket", "paramiko", "requests", "rclpy", "rosbag2_py"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue(all(alias.name.split(".")[0] not in forbidden for alias in node.names))
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0], forbidden)


if __name__ == "__main__":
    unittest.main()
