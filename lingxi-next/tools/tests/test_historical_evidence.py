"""History provenance and current-source checks use an isolated real Git repo."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_historical_evidence as history


class HistoricalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.root = self.repo / "product"
        self.root.mkdir()
        (self.root / "src").mkdir()
        (self.root / "docs").mkdir()
        self.source = self.root / "src/worker.py"
        self.source.write_bytes(b"first\nsecond\n")
        self.name = "docs/report.json"
        self.report = {"result": "historical_fixture_only",
                       "sourceSha256": {"src/worker.py": history.sha256(self.source.read_bytes())}}
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "core.safecrlf", "false")
        self.freeze()

    def git(self, *args):
        return subprocess.check_output(["git", "-c", "user.name=Offline Test",
                                        "-c", "user.email=offline@example.invalid", *args],
                                       cwd=self.repo, stderr=subprocess.PIPE).decode().strip()

    def freeze(self, transforms=None):
        (self.root / self.name).write_text(json.dumps(self.report), encoding="utf-8")
        self.git("add", "product")
        self.git("commit", "-qm", "freeze synthetic history")
        self.revision = self.git("rev-parse", "HEAD")
        self.value = {"schemaVersion": 1,
                      "purpose": "immutable_historical_evidence_not_current_target_acceptance",
                      "entries": {self.name: {"revision": self.revision,
                                             "reportCanonicalSha256": history.canonical_sha256(self.report),
                                             "sourceByteTransforms": transforms or {}}}}
        self.binding = self.repo / "bindings.json"
        self.write_binding()

    def write_binding(self):
        self.binding.write_text(json.dumps(self.value), encoding="utf-8")

    def verify(self):
        return history.verify(self.name, root=self.root, catalog_path=self.binding)

    def test_nested_project_verifies_exact_frozen_sources_without_accepting_target(self):
        verified = self.verify()
        self.assertEqual(self.revision, verified["revision"])
        self.assertEqual(1, verified["recordedSourceCount"])
        self.assertTrue(verified["historicalSourcesVerified"])
        self.assertTrue(verified["currentSourceBindingsMatch"])
        result = history.audit(root=self.root, catalog_path=self.binding)
        self.assertTrue(all(value is False for value in result["boundaries"].values()))

    def test_explicit_runtime_source_section_is_verified_against_frozen_git(self):
        self.report["actualRuntimeSourceSha256"] = self.report.pop("sourceSha256")
        self.freeze()
        self.value["entries"][self.name]["sourceSection"] = "actualRuntimeSourceSha256"
        self.write_binding()
        self.assertTrue(self.verify()["historicalSourcesVerified"])
        self.source.write_bytes(b"current receipt-aware successor\n")
        self.assertFalse(self.verify()["currentSourceBindingsMatch"])

    def test_unknown_or_absent_source_sections_cannot_bypass_verification(self):
        for section in ("result", "unknown", "actualRuntimeSourceSha256", "trialSourceSha256",
                        "inputIntegrity.sourcesSha256"):
            self.value["entries"][self.name]["sourceSection"] = section
            self.write_binding()
            with self.subTest(section=section), self.assertRaises(ValueError):
                self.verify()

    def test_explicit_trial_sources_remain_bound_to_original_git_revision(self):
        self.report['trialSourceSha256'] = self.report.pop('sourceSha256')
        self.freeze()
        self.value['entries'][self.name]['sourceSection'] = 'trialSourceSha256'
        self.write_binding()
        self.assertTrue(self.verify()['historicalSourcesVerified'])
        self.source.write_bytes(b'updated runtime selector, not a repeated trial\n')
        self.assertEqual({'src/worker.py'}, set(self.verify()['currentSourceDrift']))

    def test_explicit_nested_integrity_sources_remain_git_bound_and_expose_drift(self):
        self.report['inputIntegrity'] = {'sourcesSha256': self.report.pop('sourceSha256')}
        self.freeze()
        self.value['entries'][self.name]['sourceSection'] = 'inputIntegrity.sourcesSha256'
        self.write_binding()
        self.assertTrue(self.verify()['historicalSourcesVerified'])
        self.source.write_bytes(b'new source, not a replay\n')
        self.assertFalse(self.verify()['currentSourceBindingsMatch'])
        self.report['inputIntegrity']['sourcesSha256']['src/worker.py'] = '0' * 64
        self.freeze()
        self.value['entries'][self.name]['sourceSection'] = 'inputIntegrity.sourcesSha256'
        self.write_binding()
        with self.assertRaisesRegex(ValueError, 'historical_source_mismatch'):
            self.verify()

    def test_malformed_nested_integrity_is_not_a_source_binding(self):
        for integrity in (None, [], 'not-an-object', {'sourcesSha256': {}}):
            self.report = {'inputIntegrity': integrity}
            self.freeze()
            self.value['entries'][self.name]['sourceSection'] = 'inputIntegrity.sourcesSha256'
            self.write_binding()
            with self.subTest(integrity=integrity), self.assertRaisesRegex(ValueError, 'historical_sources_required'):
                self.verify()

    def test_mutation_and_deletion_are_current_drift_not_rewritten_history(self):
        frozen = (self.root / self.name).read_bytes()
        self.source.write_bytes(b"changed\n")
        result = self.verify()
        self.assertTrue(result["historicalSourcesVerified"])
        drift = result["currentSourceDrift"]["src/worker.py"]
        self.assertEqual(history.sha256(b"changed\n"), drift["currentSha256"])
        self.source.unlink()
        self.assertIsNone(self.verify()["currentSourceDrift"]["src/worker.py"]["currentSha256"])
        self.assertEqual(frozen, (self.root / self.name).read_bytes())

    def test_strict_cli_blocks_drift_but_matching_never_accepts_arm64(self):
        self.source.write_bytes(b"changed\n")
        result = history.audit(root=self.root, catalog_path=self.binding)
        self.assertEqual("historical_integrity_verified_current_sources_drifted", result["result"])
        with patch.object(history, "audit", return_value=result), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(0, history.main([]))
            self.assertEqual(2, history.main(["--require-current"]))
        self.assertIn('"currentArm64Accepted": false', out.getvalue())

    def test_report_whitespace_is_not_source_byte_normalization(self):
        (self.root / self.name).write_bytes(json.dumps(self.report, indent=2).replace("\n", "\r\n").encode())
        self.assertTrue(self.verify()["currentSourceBindingsMatch"])
        self.source.write_bytes(b"first\r\nsecond\r\n")
        self.assertFalse(self.verify()["currentSourceBindingsMatch"])

    def test_tampered_report_and_forged_catalog_digest_do_not_match_frozen_report(self):
        self.report["result"] = "forged"
        (self.root / self.name).write_text(json.dumps(self.report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "historical_report_changed"):
            self.verify()
        self.value["entries"][self.name]["reportCanonicalSha256"] = history.canonical_sha256(self.report)
        self.write_binding()
        with self.assertRaisesRegex(ValueError, "historical_report_revision_mismatch"):
            self.verify()

    def test_wrong_revision_fails_closed(self):
        self.value["entries"][self.name]["revision"] = "0" * 40
        self.write_binding()
        with self.assertRaises(subprocess.CalledProcessError):
            self.verify()

    def test_incorrect_historical_source_digest_fails(self):
        self.report["sourceSha256"]["src/worker.py"] = "0" * 64
        self.freeze()
        with self.assertRaisesRegex(ValueError, "historical_source_mismatch"):
            self.verify()

    def test_crlf_checkout_needs_explicit_frozen_blob_transformation(self):
        crlf = b"first\r\nsecond\r\n"
        self.report["sourceSha256"]["src/worker.py"] = history.sha256(crlf)
        self.freeze()
        self.source.write_bytes(crlf)
        with self.assertRaisesRegex(ValueError, "historical_source_mismatch"):
            self.verify()
        self.value["entries"][self.name]["sourceByteTransforms"] = {"src/worker.py": "lf_to_crlf"}
        self.write_binding()
        self.assertTrue(self.verify()["currentSourceBindingsMatch"])

    def test_duplicate_keys_and_non_object_reports_rejected(self):
        (self.root / self.name).write_bytes(b'{"sourceSha256":{},"sourceSha256":{}}')
        with self.assertRaisesRegex(ValueError, "duplicate_json_key"):
            self.verify()
        (self.root / self.name).write_bytes(b"[]")
        with self.assertRaisesRegex(ValueError, "historical_report_object_required"):
            self.verify()

    def test_catalog_traversal_aliases_and_unknown_transforms_rejected(self):
        for name in ("../outside", "/absolute", "C:/drive", "./docs/report.json", ".", "src\\worker.py"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                history._path(name)
        original = deepcopy(self.value)
        for transform in ("normalize", "auto", True):
            self.value = deepcopy(original)
            self.value["entries"][self.name]["sourceByteTransforms"] = {"src/worker.py": transform}
            self.write_binding()
            with self.subTest(transform=transform), self.assertRaisesRegex(ValueError, "unsupported"):
                self.verify()

    def test_symlink_cannot_supply_current_source_or_report(self):
        outside = self.repo / "outside.py"
        outside.write_bytes(self.source.read_bytes())
        self.source.unlink()
        try:
            self.source.symlink_to(outside)
        except OSError:
            self.skipTest("OS symlink permission unavailable")
        self.assertIsNone(self.verify()["currentSourceDrift"]["src/worker.py"]["currentSha256"])
        report = self.root / self.name
        saved = self.repo / "outside-report.json"
        saved.write_bytes(report.read_bytes())
        report.unlink()
        report.symlink_to(saved)
        with self.assertRaisesRegex(ValueError, "ordinary_evidence_file_required"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
