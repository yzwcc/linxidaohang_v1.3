"""Bounded scheduling contracts; neither synthetic input nor replay proves geometry."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy", "json5"))
if AVAILABLE:
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        import run_glim_async_mapping as async_mapping
        import export_async_mapping_evidence as evidence


@unittest.skipUnless(AVAILABLE, "requires isolated scientific Python environment")
class AsyncMappingContracts(unittest.TestCase):
    def stats(self):
        return async_mapping.np.array([100, 100, 12, 128, 2000, 2000, 240, 8192, 0, 0, 0.2, 200, 3000])

    def counts(self):
        return {"savedFrames": 100, "imuSamples": 2000}

    def test_completed_work_must_match_every_frame_and_imu(self):
        value = async_mapping.checked_status(self.stats(), self.counts())
        self.assertEqual(100, value["completedFrames"])
        self.assertEqual(2000, value["completedImu"])
        self.assertEqual(3000, value["maximumWorkerTaskMilliseconds"])

    def test_missing_duplicate_or_rejected_work_cannot_report_success(self):
        for index, value in ((0, 99), (1, 99), (1, 101), (4, 1999), (5, 2001), (8, 1), (9, 1)):
            stats = self.stats()
            stats[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                async_mapping.checked_status(stats, self.counts())

    def test_capacity_and_finite_counters_are_enforced(self):
        for index, value in ((2, 129), (3, 256), (6, 8193), (7, 0), (0, 1.5), (10, float("nan")),
                             (11, -1), (12, float("inf"))):
            stats = self.stats()
            stats[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                async_mapping.checked_status(stats, self.counts())

    def test_capacity_race_retries_snapshot_with_current_header(self):
        native = object.__new__(async_mapping.AsyncMapping)
        calls = []
        def callback(header, points, capacity):
            calls.append(capacity)
            header[:] = [1, 1, 2 if len(calls) == 1 else 3, 0, 0, 0]
            return int(len(calls) == 2)
        native.call_snapshot = callback
        header, points = native.snapshot(include_points=True)
        self.assertEqual([0, 2, 3], calls)
        self.assertEqual((3, 3), points.shape)
        self.assertEqual(3, header[2])

    def test_continuously_growing_snapshot_is_not_truncated(self):
        native = object.__new__(async_mapping.AsyncMapping)
        def callback(header, points, capacity):
            header[:] = [1, 1, capacity + 1, 0, 0, 0]
            return int(points is not None)
        native.call_snapshot = callback
        with self.assertRaisesRegex(RuntimeError, "retry_later"):
            native.snapshot(include_points=True)

    def test_empty_map_snapshot_has_no_fabricated_points(self):
        native = object.__new__(async_mapping.AsyncMapping)
        native.call_snapshot = lambda header, points, capacity: 0
        header, points = native.snapshot(include_points=True)
        self.assertEqual([0] * 6, header.tolist())
        self.assertIsNone(points)

    def test_evidence_requires_every_declared_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            digests = {}
            for name in evidence.FILES:
                (root / name).write_bytes(b"isolated synthetic nonrobot artifact")
                digests[name] = async_mapping.full.sha256(root / name)
            evidence.verify_files(root, digests)
            for changed in ({}, {**digests, "../outside": "0" * 64}):
                with self.assertRaisesRegex(ValueError, "every_async_report_artifact"):
                    evidence.verify_files(root, changed)
            (root / "private-snapshot-points.f64").write_bytes(b"changed synthetic data")
            with self.assertRaisesRegex(ValueError, "artifact_hash_mismatch"):
                evidence.verify_files(root, digests)

    def test_published_evidence_covers_all_cases_without_accepting_geometry(self):
        import json
        path = ROOT / "docs/slam-async-mapping-evidence.json"
        self.assertTrue(path.is_file(), "native trials must precede published acceptance evidence")
        value = json.loads(path.read_text())
        self.assertEqual(set(evidence.CASES), {item["case"] for item in value["trials"]})
        for name, digest in value["sourceSha256"].items():
            self.assertEqual(digest, async_mapping.full.sha256(ROOT / name))
        for item in value["trials"]:
            self.assertEqual(item["recordedScans"], item["skippedUnbracketedScans"] +
                             item["initializationScans"] + item["savedFrames"])
            self.assertEqual(0, item["maximumSnapshotDifferenceRaw"])
            self.assertLess(item["maximumReloadDifferenceRaw"], value["roundTripToleranceRaw"])
            self.assertEqual(0, item["worker"]["discardedInputs"])
            self.assertEqual(0, item["worker"]["rejectedTasks"])
        self.assertTrue(all(flag is False for flag in value["boundaries"].values()))
        self.assertNotIn("mapId", json.dumps(value))


@unittest.skipUnless(sys.platform == "linux" and shutil.which("g++"), "requires native Linux C++ compiler")
class NativeBoundedWorker(unittest.TestCase):
    def test_real_thread_fifo_bounded_admission_faults_and_drain(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "bounded-worker-contracts"
            subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pthread",
                            "-I", str(ROOT / "tools/glim-async-mapping"),
                            str(ROOT / "tools/tests/native/test_bounded_worker.cpp"), "-o", str(binary)],
                           check=True, capture_output=True, text=True, timeout=60)
            for _ in range(10):
                result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=10)
                self.assertIn("contracts passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
