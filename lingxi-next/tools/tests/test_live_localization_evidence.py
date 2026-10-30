"""Opt-in audit of preserved live reports without SSH, ROS or native execution."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
TRIAL = os.environ.get("LINGXI_LIVE_LOCALIZATION_EVIDENCE_TRIAL")
PUBLISHED = os.environ.get("LINGXI_LIVE_LOCALIZATION_PUBLISHED",
                           "docs/slam-live-localization-arm64-evidence.json")
AVAILABLE = sys.platform == "linux" and all(importlib.util.find_spec(name) is not None
                                           for name in ("numpy", "scipy", "small_gicp"))
if AVAILABLE:
    import export_live_localization_evidence as exporter


@unittest.skipUnless(AVAILABLE and TRIAL, "requires preserved private live reports")
class PreservedLiveReports(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "trial"
        shutil.copytree(TRIAL, self.root)

    def mutate(self, name, action):
        file = self.root / name
        report = exporter.load(file)
        action(report)
        file.write_text(json.dumps(report), encoding="utf-8")

    def test_matches_published_evidence_without_private_paths_or_identity(self):
        report = exporter.evidence(self.root)
        self.assertEqual(exporter.load(ROOT / PUBLISHED), report)
        forbidden = {"session", "pid", "host", "token", "password", "mapFromSensor", "referenceFrame"}
        def inspect(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for item in value.values():
                    inspect(item)
            elif isinstance(value, list):
                for item in value:
                    inspect(item)
            elif isinstance(value, str):
                self.assertFalse(value.startswith(("/home/", "/mnt/", "C:\\", "E:\\")))
        inspect(report)

    def test_terminal_process_residue_is_rejected(self):
        self.mutate("run/owner-report.json", lambda r: r["nativeProcess"].update(processAlive=True))
        with self.assertRaises(ValueError):
            exporter.evidence(self.root)

    def test_nonmonotonic_references_and_seed_claim_are_rejected(self):
        for changes in ({"referenceFramesObserved": [1, 2, 1, 4, 5]}, {"seedRequests": 1}):
            with self.subTest(changes=changes):
                original = (self.root / "check-report.json").read_bytes()
                self.mutate("check-report.json", lambda r: r.update(changes))
                with self.assertRaises(ValueError):
                    exporter.evidence(self.root)
                (self.root / "check-report.json").write_bytes(original)

    def test_changed_bytes_and_uploaded_source_are_rejected(self):
        file = self.root / "run/native/localization-final.json"
        original = file.read_bytes()
        file.write_bytes(original + b"\n")
        with self.assertRaises(ValueError):
            exporter.evidence(self.root)
        file.write_bytes(original)
        self.mutate("collection-report.json", lambda r: r["uploadedFiles"][
            "robot-runtime/python/live_localization.py"].update(sha256="0" * 64))
        with self.assertRaises(ValueError):
            exporter.evidence(self.root)

    def test_optional_base_input_must_match_child_observations(self):
        if "basePoseInput" not in exporter.load(self.root / "check-report.json"):
            self.skipTest("preserved trial did not enable base diagnostics")
        self.mutate("check-report.json", lambda r: r["basePoseInput"]["observations"].update(
            tfLookupsAvailable=0))
        with self.assertRaises(ValueError):
            exporter.evidence(self.root)


if __name__ == "__main__":
    unittest.main()
