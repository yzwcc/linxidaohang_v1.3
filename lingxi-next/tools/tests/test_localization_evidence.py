"""Opt-in preserved-report checks; never connect to a robot or rerun SLAM."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
AVAILABLE = all(importlib.util.find_spec(name) is not None
                for name in ("numpy", "scipy", "small_gicp"))
TRIAL = os.environ.get("LINGXI_LOCALIZATION_EVIDENCE_TRIAL")
COMPATIBILITY = os.environ.get("LINGXI_LOCALIZATION_EVIDENCE_COMPATIBILITY")
if AVAILABLE:
    import export_localization_arm64_evidence as exporter


@unittest.skipUnless(AVAILABLE and TRIAL and COMPATIBILITY,
                     "requires isolated scientific dependencies and preserved private reports")
class PreservedLocalizationEvidence(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.trial = self.root / "trial"
        self.compatibility = self.root / "compatibility"
        shutil.copytree(TRIAL, self.trial)
        self.compatibility.mkdir()
        for name in ("check-report.json", "run/owner-report.json",
                     "run/native/child-report.json", "run/native/localization-final.json"):
            target = self.compatibility / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(COMPATIBILITY) / name, target)

    def rewrite_run_report(self, name, report):
        file = self.trial / "run" / name
        file.write_text(json.dumps(report), encoding="utf-8")
        check_file = self.trial / "check-report.json"
        check = exporter.load(check_file)
        check["privateReportsSha256"][name] = exporter.sha256(file)
        check_file.write_text(json.dumps(check), encoding="utf-8")

    def test_success_matches_published_aggregate_and_omits_private_data(self):
        report = exporter.evidence(self.trial, self.compatibility)
        published = exporter.load(ROOT / "docs/slam-managed-localization-arm64-evidence.json")
        self.assertEqual(published, report)
        self.assertTrue(report["robotContacted"])
        self.assertTrue(all(report[name] is False for name in exporter.FLAGS))
        private_keys = {"session", "sessionId", "host", "password", "token", "command",
                        "pid", "mapFromSensor", "referenceFrame", "seedMatrix"}

        def inspect(value):
            if isinstance(value, dict):
                self.assertFalse(private_keys.intersection(value))
                for item in value.values():
                    inspect(item)
            elif isinstance(value, list):
                for item in value:
                    inspect(item)
            elif isinstance(value, str):
                self.assertFalse(value.startswith(("/home/", "/mnt/", "C:\\", "E:\\")))
                self.assertIsNone(re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", value))
        inspect(report)

    def test_changed_report_bytes_are_rejected(self):
        file = self.trial / "run/native/localization-final.json"
        file.write_bytes(file.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "complete_source_bound"):
            exporter.audit_run(self.trial, "aarch64")

    def test_live_process_is_rejected_even_with_updated_report_hash(self):
        owner = exporter.load(self.trial / "run/owner-report.json")
        owner["nativeProcess"]["processAlive"] = True
        self.rewrite_run_report("owner-report.json", owner)
        with self.assertRaisesRegex(ValueError, "complete_source_bound"):
            exporter.audit_run(self.trial, "aarch64")

    def test_wrong_map_binding_is_rejected_even_with_updated_report_hash(self):
        final = exporter.load(self.trial / "run/native/localization-final.json")
        final["state"]["binding"]["receipt_sha256"] = "0" * 64
        self.rewrite_run_report("native/localization-final.json", final)
        with self.assertRaisesRegex(ValueError, "complete_same_session"):
            exporter.audit_run(self.trial, "aarch64")

    def test_different_current_sources_are_rejected(self):
        file = self.trial / "check-report.json"
        report = exporter.load(file)
        report["sourceSha256"]["robot-runtime/python/controlled_localization.py"] = "0" * 64
        file.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "complete_source_bound"):
            exporter.audit_run(self.trial, "aarch64")

    def test_compatibility_with_another_seed_is_rejected(self):
        file = self.compatibility / "check-report.json"
        report = exporter.load(file)
        report["seedSampleSha256"] = "0" * 64
        file.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "same_source_and_recording"):
            exporter.evidence(self.trial, self.compatibility)


if __name__ == "__main__":
    unittest.main()
