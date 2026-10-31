"""Synthetic worker evidence validation and full actual-publication integrity."""
import ast
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "json5", "scipy"))
if AVAILABLE:
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        import export_glim_worker_evidence as exporter
        from lingxi_slam.stream import Limits
        from lingxi_slam.worker import WorkerLimits


@unittest.skipUnless(AVAILABLE, "requires isolated NumPy, json5 and SciPy")
class WorkerEvidenceContracts(unittest.TestCase):
    def synthetic(self):
        return {
            "state": "stopped", "failure": None, "threadAlive": False,
            "counts": {
                "acceptedImu": 50, "acceptedClouds": 6, "completedMessages": 56,
                "discardedMessages": 0, "rejectedMessages": 0,
                "maximumQueuedMessages": 4, "maximumQueuedClouds": 1, "maximumQueuedPayloadBytes": 488,
            },
            "limits": asdict(WorkerLimits()),
            "queuedMessages": 0, "queuedClouds": 0, "queuedPayloadBytes": 0, "inFlightMessages": 0,
            "maximumObservedQueueAgeSeconds": .025,
            "stream": {
                "state": "finished", "failure": None,
                "counts": {
                    "receivedImu": 50, "receivedClouds": 6, "insertedImu": 45,
                    "processedScans": 4, "initializationScans": 1, "estimatedPoses": 3,
                    "skippedBeforeImu": 1, "skippedAfterImuAtEndOfInput": 1,
                    "discardedOnFailure": 0, "maximumPendingImu": 4, "maximumPendingClouds": 1,
                    "maximumPendingBytes": 320,
                },
                "limits": asdict(Limits()), "pendingImu": 0, "pendingClouds": 0,
                "pendingCloudBytes": 0, "maximumObservedScanWaitSeconds": .025,
            },
        }

    def expected(self):
        return {"topicCounts": {"cloud": 6, "imu": 50}}

    def test_complete_worker_accounting_is_allowlisted_with_actual_clock_label(self):
        state = self.synthetic()
        state["privateHost"] = "SYNTHETIC_PRIVATE_HOST_MARKER"
        value = exporter.public_worker(state, self.expected())
        self.assertTrue(value["threadJoinedAndAllQueuesReleased"])
        self.assertEqual(56, value["counts"]["completedMessages"])
        self.assertIn("maximumActualCallbackScanWaitSeconds", value["stream"])
        self.assertNotIn("maximumRecordedArrivalScanWaitSeconds", value["stream"])
        self.assertNotIn("SYNTHETIC_PRIVATE_HOST_MARKER", json.dumps(value))

    def test_running_live_thread_discard_unbounded_incomplete_or_changed_limits_are_rejected(self):
        for kind in ("running", "alive", "discard", "reject", "incomplete", "queued", "peak", "nan", "limits"):
            state = self.synthetic()
            if kind == "running":
                state["state"] = "running"
            elif kind == "alive":
                state["threadAlive"] = True
            elif kind == "discard":
                state["counts"]["discardedMessages"] = 1
            elif kind == "reject":
                state["counts"]["rejectedMessages"] = 1
            elif kind == "incomplete":
                state["counts"]["completedMessages"] = 55
            elif kind == "queued":
                state["inFlightMessages"] = 1
            elif kind == "peak":
                state["counts"]["maximumQueuedClouds"] = 17
            elif kind == "nan":
                state["maximumObservedQueueAgeSeconds"] = float("nan")
            else:
                state["limits"]["maximum_messages"] += 1
            with self.assertRaises(ValueError):
                exporter.public_worker(state, self.expected())

    def test_metric_recomputation_allows_only_tiny_finite_numeric_roundoff(self):
        expected = {"scalar": 1., "quantiles": [1., 2., 3.]}
        self.assertTrue(exporter.matching_metrics(expected, expected))
        self.assertTrue(exporter.matching_metrics(
            {"scalar": 1. + 1e-12, "quantiles": [1. + 1e-12, 2., 3.]}, expected))
        for value in (
            {"scalar": 2., "quantiles": [1., 2., 3.]},
            {"scalar": float("nan"), "quantiles": [1., 2., 3.]},
            {"scalar": 1., "quantiles": [True, 2., 3.]},
            {"scalar": 1., "quantiles": [1., 2.]},
            {"scalar": 1.},
        ):
            self.assertFalse(exporter.matching_metrics(value, expected))

    def test_existing_evidence_is_preserved_without_export_or_robot_contact(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(exporter, "export") as run:
            output = Path(directory) / "existing.json"
            output.write_text("SYNTHETIC EXISTING EVIDENCE")
            arguments = ["export"]
            for name in ("bundle", "parent-bundle", "core-bundle", "baselines", "results", "data-root"):
                arguments.extend(["--" + name, directory])
            arguments.extend(["--output", str(output)])
            with patch.object(sys, "argv", arguments):
                with self.assertRaisesRegex(ValueError, "preserved"):
                    exporter.main()
            run.assert_not_called()
            self.assertEqual("SYNTHETIC EXISTING EVIDENCE", output.read_text())


class PublicWorkerEvidence(unittest.TestCase):
    def setUp(self):
        self.value = json.loads((ROOT / "docs/slam-arm64-worker-evidence.json").read_text())

    def test_all_four_actual_conditions_account_for_every_message_without_acceptance_claims(self):
        evidence = self.value
        capture = json.loads((ROOT / "docs/slam-capture-evidence.json").read_text())
        self.assertEqual(4, evidence["freshTargetProcessCount"])
        self.assertEqual({f"{data}-{time}" for data in ("static", "dynamic") for time in ("start", "end")},
                         set(evidence["runs"]))
        self.assertTrue(evidence["originalCapturesAndBundleUnchangedAtExport"])
        self.assertTrue(all(value is False for value in evidence["boundaries"].values()))
        for run in evidence["runs"].values():
            self.assertEqual("aarch64", run["target"]["machine"])
            self.assertTrue(run["nativeCallsAndConsumerHaveOneNonproducerOwner"])
            worker = run["worker"]
            self.assertTrue(worker["threadJoinedAndAllQueuesReleased"])
            counts = worker["counts"]
            expected = capture["datasets"][run["dataset"]]["topicCounts"]
            self.assertEqual(expected["cloud"], counts["acceptedClouds"])
            self.assertEqual(expected["imu"], counts["acceptedImu"])
            self.assertEqual(expected["cloud"] + expected["imu"], counts["completedMessages"])
            self.assertEqual(0, counts["discardedMessages"])
            self.assertEqual(0, counts["rejectedMessages"])
            comparison = run["baselineComparisonNotTruthAccuracy"]
            for key in ("sameEstimatedHeaderDomain", "sameAlgorithmTimestamps", "sameScanCounters"):
                self.assertTrue(comparison[key])

    def test_historical_sources_and_current_live_successor_have_separate_identities(self):
        evidence = self.value
        live = json.loads((ROOT / "docs/slam-python-runtime-live-arm64-evidence.json").read_text())
        for source, digest in evidence["actualRuntimeSourceSha256"].items():
            current = hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
            if source == "robot-slam/lingxi_slam/worker.py":
                self.assertEqual(digest, live["historicalSourceSha256"][source])
                self.assertEqual(current, live["sourceSha256"][source])
                self.assertFalse(live["historicalRecordedEvidenceReplaced"])
                self.assertFalse(live["boundaries"]["recordedCommissioningRepeated"])
            else:
                self.assertEqual(digest, current)
        for source, digest in evidence["toolSourcesSha256"].items():
            self.assertEqual(digest, hashlib.sha256((ROOT / "tools" / source).read_bytes()).hexdigest())
        for filename, key in (("slam-capture-evidence.json", "captureManifestSha256"),
                              ("slam-controlled-target-evidence.json", "baselineTargetEvidenceSha256")):
            self.assertEqual(evidence[key], hashlib.sha256((ROOT / "docs" / filename).read_bytes()).hexdigest())

    def test_public_report_and_exporter_contain_no_raw_coordinates_or_robot_connections(self):
        text = json.dumps(self.value)
        for marker in ('"poses"', '"header_stamps_ns"', '"T_lidar_imu"', '"password"', '"hostname"',
                       '"credential"', "/home/", "/mnt/", "192.168.", "E:\\\\"):
            self.assertNotIn(marker, text)
        tree = ast.parse((ROOT / "tools/export_glim_worker_evidence.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [item.name for item in node.names] if isinstance(node, ast.Import) else [node.module]
                self.assertFalse(any(name.split(".")[0] in {
                    "rclpy", "ctypes", "socket", "paramiko", "requests", "subprocess",
                } for name in names))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, ("connect", "create_node", "imu", "frame", "start"))


if __name__ == "__main__":
    unittest.main()
