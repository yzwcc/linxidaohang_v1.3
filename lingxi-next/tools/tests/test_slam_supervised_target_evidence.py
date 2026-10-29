"""Synthetic evidence mutations; actual ARM64 execution is separately documented."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import export_glim_supervised_target as exporter
from build_slam_cpu_trial import PINS
from test_slam_supervised_replay import child_report, EXPECTED, stopped
from verify_slam_capture import sha256


class EvidenceContracts(unittest.TestCase):
    def fixture(self, directory, mutate=None):
        root = Path(directory)
        name = "tools/synthetic.py"
        source = root / "synthetic-source"
        (source / "tools").mkdir(parents=True)
        (source / name).write_bytes(b"# explicitly synthetic source\n")
        sources = {name: {"sha256": sha256(source / name), "bytes": (source / name).stat().st_size}}
        integrity = {
            "sourceSha256": {name: sources[name]["sha256"]}, "sourceCommits": PINS,
            "journal": EXPECTED, "journalSha256": "a" * 64, "configurationSha256": {},
            "referenceLibrariesSha256": {"synthetic.so": "b" * 64},
        }
        child = {
            **child_report(), "result": exporter.target.shared.CHILD_RESULT,
            "session": "c" * 32, "machine": "aarch64", "replayRate": 1.,
            "versions": {"python": "3.10.12", "numpy": "synthetic",
                         "decoder": "installed_humble_rclpy", "private": "PRIVATE_MARKER"},
            "inputIntegrity": integrity,
            "actualLoadedLibrariesSha256": integrity["referenceLibrariesSha256"],
            "private": "PRIVATE_MARKER",
        }
        child["worker"]["stream"]["counts"]["initializationScans"] = 0
        child["worker"]["private"] = "PRIVATE_MARKER"
        parent = {
            "result": exporter.target.RESULT, "inputIntegrity": deepcopy(integrity),
            "process": {**stopped(), "session": child["session"], "workerState": "stopped",
                        "failure": None, "childFailure": None, "processedScans": 2, "estimatedPoses": 2,
                        "healthEvents": 10, "retiredHealthEvents": 1, "mappingReady": False,
                        "localizationReady": False, "navigationReady": False, "private": "PRIVATE_MARKER"},
            "runtimeSourceFilesSha256": integrity["sourceSha256"],
            "limits": asdict(exporter.target.shared.LIMITS),
            "boundaries": dict.fromkeys(exporter.target.shared.BOUNDARIES, False),
            "replayRate": 1., "pacedRecordedInputNotLivePerformance": True,
        }
        collection = {
            "result": exporter.coordinator.RESULT, "exitCode": 0,
            "coordinatorSha256": sha256(exporter.coordinator.__file__),
            "uploadedSourceFiles": sources,
        }
        if mutate:
            mutate(collection, parent, child)
        exporter.target.shared.private_json(root / "child-report.json", child)
        (root / "private-child.log").write_bytes(b"PRIVATE_MARKER")
        parent["childReportSha256"] = sha256(root / "child-report.json")
        parent["privateLogSha256"] = sha256(root / "private-child.log")
        exporter.target.shared.private_json(root / "report.json", parent)
        collection["filesSha256"] = {name: sha256(root / name) for name in exporter.coordinator.FILES}
        exporter.target.shared.private_json(root / "collection-report.json", collection)
        return root, source, integrity["sourceSha256"]

    def export_fixture(self, directory, mutate=None):
        root, source, hashes = self.fixture(directory, mutate)
        with patch.object(exporter.coordinator, "ROOT", source), \
                patch.object(exporter.target, "hashes", return_value=hashes):
            return exporter.evidence(root)

    def test_only_explicit_aggregates_leave_private_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.export_fixture(directory)
        import json
        self.assertNotIn("PRIVATE_MARKER", json.dumps(result))
        self.assertNotIn("session", result["process"])
        self.assertNotIn("pid", result)
        self.assertNotIn("worker", result)
        self.assertTrue(result["observations"]["arm64RecordedChildLifecycleClosed"])
        self.assertFalse(any(result["acceptance"].values()))
        self.assertEqual(5, result["completedWorkerCounts"]["completedMessages"])

    def test_rejects_collection_failure_and_wrong_host_decoder_session_or_pins(self):
        mutations = (
            lambda c, p, r: c.update(result="failed_or_interrupted"),
            lambda c, p, r: c.update(exitCode=1),
            lambda c, p, r: c.update(exitCode=False),
            lambda c, p, r: r.update(machine="x86_64"),
            lambda c, p, r: r["versions"].update(decoder="synthetic-other"),
            lambda c, p, r: r["versions"].update(python="3.12.0"),
            lambda c, p, r: r.update(session="d" * 32),
            lambda c, p, r: r.update(replayRate=.5),
            lambda c, p, r: p["inputIntegrity"].update(sourceCommits={}),
            lambda c, p, r: r.update(actualLoadedLibrariesSha256={}),
            lambda c, p, r: r.update(nativeOwnerIsSingleNonMainThread=1),
            lambda c, p, r: r.update(stopReceived=1),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    self.export_fixture(directory, mutate)

    def test_complete_counts_cannot_hide_failed_cleanup_or_readiness_claims(self):
        mutations = (
            lambda c, p, r: p["process"].update(cleanupComplete=False),
            lambda c, p, r: p["process"].update(navigationReady=True),
            lambda c, p, r: p["process"].update(processAlive=True),
            lambda c, p, r: p["process"].update(forcedTermination=True),
            lambda c, p, r: p["process"].update(failure="synthetic"),
            lambda c, p, r: p["process"].update(completedMessages=True),
            lambda c, p, r: p["process"].update(processedScans=1),
            lambda c, p, r: r["worker"]["counts"].update(completedMessages=4),
            lambda c, p, r: r["worker"]["counts"].update(rejectedMessages=1),
            lambda c, p, r: r["worker"]["stream"]["counts"].update(initializationScans=1),
            lambda c, p, r: p["limits"].update(startup_seconds=999.),
            lambda c, p, r: p["boundaries"].update(navigationValidated=True),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    self.export_fixture(directory, mutate)

    def test_uploaded_staged_and_current_sources_must_match(self):
        mutations = (
            lambda c, p, r: c.update(uploadedSourceFiles={}),
            lambda c, p, r: p.update(runtimeSourceFilesSha256={}),
            lambda c, p, r: c["uploadedSourceFiles"]["tools/synthetic.py"].update(sha256="e" * 64),
            lambda c, p, r: c["uploadedSourceFiles"]["tools/synthetic.py"].update(bytes=1),
            lambda c, p, r: c.update(coordinatorSha256="e" * 64),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    self.export_fixture(directory, mutate)

    def test_changed_private_file_is_rejected_even_with_unchanged_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root, source, hashes = self.fixture(directory)
            (root / "private-child.log").write_bytes(b"changed")
            with patch.object(exporter.coordinator, "ROOT", source), \
                    patch.object(exporter.target, "hashes", return_value=hashes):
                with self.assertRaises(ValueError):
                    exporter.evidence(root)


class PublicRecordedAggregate(unittest.TestCase):
    def test_checked_in_evidence_is_source_bound_arm64_recorded_not_product_acceptance(self):
        report = exporter.target.shared.load(ROOT / "docs/slam-supervised-arm64-evidence.json")
        self.assertEqual(exporter.target.RESULT, report["result"])
        self.assertEqual(exporter.target.hashes(), report["sourceSha256"])
        self.assertEqual(sha256(exporter.coordinator.__file__), report["coordinatorSha256"])
        self.assertEqual("aarch64", report["host"]["machine"])
        self.assertTrue(all(value is True for value in report["observations"].values()))
        self.assertTrue(all(value is False for value in report["acceptance"].values()))
        self.assertEqual("stopped", report["process"]["state"])
        self.assertTrue(report["process"]["cleanupComplete"])
        self.assertFalse(report["process"]["forcedTermination"])
        self.assertEqual(report["inputJournal"]["records"],
                         report["completedWorkerCounts"]["completedMessages"])
        self.assertEqual(report["streamCounts"]["processedScans"],
                         report["streamCounts"]["estimatedPoses"] + report["streamCounts"]["initializationScans"])
        self.assertEqual({"create": 1, "close": 1, "frame": report["streamCounts"]["processedScans"],
                          "imu": report["streamCounts"]["insertedImu"]}, report["nativeCalls"])

    def test_checked_in_evidence_does_not_publish_private_identifiers_sensor_values_or_credentials(self):
        report = exporter.target.shared.load(ROOT / "docs/slam-supervised-arm64-evidence.json")
        forbidden = {"session", "pid", "host", "address", "password", "credential", "poses", "points",
                     "payload", "private-child.log", "worker", "runtimeSourceFilesSha256"}

        def check(value, top=False):
            if isinstance(value, dict):
                for name, child in value.items():
                    self.assertFalse(name in forbidden and not (top and name == "host"), name)
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
            elif isinstance(value, str):
                for marker in ("/home/run", "10.201.", "PRIVATE_MARKER"):
                    self.assertNotIn(marker, value)

        check(report, top=True)


if __name__ == "__main__":
    unittest.main()
