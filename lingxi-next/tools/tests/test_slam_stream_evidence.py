"""Synthetic exporter contracts and privacy/source checks for recorded input."""
import ast
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "json5"))
if AVAILABLE:
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        spec = importlib.util.spec_from_file_location("stream_evidence", ROOT / "tools/export_glim_stream_evidence.py")
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        spec = importlib.util.spec_from_file_location("stream_runner", ROOT / "tools/run_glim_stream_trial.py")
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)


@unittest.skipUnless(AVAILABLE, "requires isolated NumPy and JSON5")
class StreamEvidenceContracts(unittest.TestCase):
    def synthetic(self):
        return {
            "state": "finished", "failure": None,
            "counts": {"receivedImu": 50, "receivedClouds": 6, "insertedImu": 45,
                       "processedScans": 4, "initializationScans": 1, "estimatedPoses": 3,
                       "skippedBeforeImu": 1, "skippedAfterImuAtEndOfInput": 1,
                       "discardedOnFailure": 0, "maximumPendingImu": 4, "maximumPendingClouds": 1,
                       "maximumPendingBytes": 320},
            "limits": asdict(exporter.Limits()), "pendingImu": 0, "pendingClouds": 0,
            "pendingCloudBytes": 0, "maximumObservedScanWaitSeconds": .025,
        }

    def expected(self):
        return {"topicCounts": {"cloud": 6, "imu": 50}}

    def test_all_received_scans_and_limited_buffers_are_accounted_without_private_fields(self):
        state = self.synthetic()
        state["privateSecret"] = "SYNTHETIC_PRIVATE_MARKER"
        value = exporter.public_stream(state, self.expected())
        self.assertEqual(6, value["counts"]["receivedClouds"])
        self.assertTrue(value["allBuffersReleasedAtExplicitEndOfInput"])
        self.assertNotIn("SYNTHETIC_PRIVATE_MARKER", json.dumps(value))

    def test_failed_incomplete_unbounded_or_unverified_states_are_rejected(self):
        for kind in ("failed", "missing_count", "truncated", "bytes", "imu", "pending", "limit", "nan_wait"):
            with self.subTest(kind=kind):
                state = self.synthetic()
                if kind == "failed":
                    state["state"] = "failed"
                elif kind == "missing_count":
                    state["counts"].pop("estimatedPoses")
                elif kind == "truncated":
                    state["counts"]["processedScans"] -= 1
                elif kind == "bytes":
                    state["counts"]["maximumPendingBytes"] = 100_000_000
                elif kind == "imu":
                    state["counts"]["insertedImu"] = 51
                elif kind == "pending":
                    state["pendingImu"] = 1
                elif kind == "limit":
                    state["limits"]["maximum_pending_bytes"] *= 2
                else:
                    state["maximumObservedScanWaitSeconds"] = float("nan")
                with self.assertRaises((KeyError, ValueError)):
                    exporter.public_stream(state, self.expected())

    def test_existing_evidence_is_not_overwritten_or_recomputed(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(exporter, "export") as run:
            output = Path(directory) / "existing.json"
            output.write_text("synthetic existing result")
            arguments = ["export", "--results", directory, "--baselines", directory, "--data-root", directory,
                         "--output", str(output)]
            with patch.object(sys, "argv", arguments):
                with self.assertRaisesRegex(ValueError, "preserved"):
                    exporter.main()
            run.assert_not_called()
            self.assertEqual("synthetic existing result", output.read_text())

    def test_existing_trial_is_preserved_before_native_or_recording_access(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, "Native") as native:
            with self.assertRaisesRegex(ValueError, "new_ignored"):
                runner.run(NS(output=Path(directory)))
            native.assert_not_called()

    def test_batch_output_stays_inside_private_directory_and_all_conditions_are_unique(self):
        self.assertEqual(4, len(runner.CONDITIONS))
        self.assertEqual(4, len({runner.label(*value) for value in runner.CONDITIONS}))
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, "ROOT", Path(directory)):
            root = Path(directory)
            (root / ".local").mkdir()
            self.assertEqual(root / ".local/new", runner.private_output(root / ".local/new"))
            for path in (root / "public", root / ".local/../../outside"):
                with self.assertRaisesRegex(ValueError, "new_ignored"):
                    runner.private_output(path)
            if sys.platform == "linux":
                (root / ".local/link").symlink_to(root / "outside")
                with self.assertRaisesRegex(ValueError, "new_ignored"):
                    runner.private_output(root / ".local/link/new")


class PublicStreamEvidence(unittest.TestCase):
    def setUp(self):
        self.value = json.loads((ROOT / "docs/slam-stream-input-evidence.json").read_text())

    def test_every_original_condition_is_present_with_no_invented_acceptance(self):
        evidence = self.value
        self.assertEqual(4, evidence["conditionCount"])
        self.assertEqual({f"{data}-{timing}" for data in ("static", "dynamic") for timing in ("start", "end")},
                         set(evidence["runs"]))
        self.assertTrue(evidence["boundaries"])
        self.assertTrue(all(value is False for value in evidence["boundaries"].values()))
        self.assertTrue(evidence["originalCapturesAndExecutedSourcesUnchangedAtExport"])
        for run in evidence["runs"].values():
            self.assertTrue(run["allBuffersReleasedAtExplicitEndOfInput"])
            comparison = run["baselineComparisonNotTruthAccuracy"]
            self.assertTrue(comparison["sameEstimatedHeaderDomain"])
            self.assertTrue(comparison["sameAlgorithmTimestamps"])
            self.assertTrue(comparison["sameScanCounters"])
            counts = run["counts"]
            self.assertEqual(counts["receivedClouds"], counts["processedScans"] + counts["skippedBeforeImu"]
                             + counts["skippedAfterImuAtEndOfInput"])
            self.assertEqual(0, counts["discardedOnFailure"])

    def test_executed_source_and_exporter_digests_match_current_files(self):
        for path, expected in self.value["sourceSha256"].items():
            self.assertEqual(expected, hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), path)
        self.assertEqual(self.value["exporterSourceSha256"],
                         hashlib.sha256((ROOT / "tools/export_glim_stream_evidence.py").read_bytes()).hexdigest())
        for name, key in (("slam-capture-evidence.json", "captureManifestSha256"),
                          ("slam-controlled-mapping-evidence.json", "baselineEvidenceSha256")):
            self.assertEqual(self.value[key], hashlib.sha256((ROOT / "docs" / name).read_bytes()).hexdigest())

    def test_public_evidence_and_exporter_contain_no_raw_data_or_robot_connections(self):
        text = json.dumps(self.value)
        for token in ('"poses"', '"header_stamps_ns"', '"T_lidar_imu"', '"password"', '"hostname"',
                      '"credential"', "/home/", "/mnt/", "E:\\\\"):
            self.assertNotIn(token, text)
        tree = ast.parse((ROOT / "tools/export_glim_stream_evidence.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [item.name for item in node.names] if isinstance(node, ast.Import) else [node.module]
                for module in modules:
                    self.assertNotIn(module.split(".")[0],
                                     {"subprocess", "ctypes", "socket", "requests", "paramiko", "rclpy"})


if __name__ == "__main__":
    unittest.main()
