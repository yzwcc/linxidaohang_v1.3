"""Preflight contracts for opt-in recorded-only target commissioning; no robot access."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import check_slam_workspace_target as probe
from slam_callback_journal import Writer


class WorkspaceTargetInputs(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        config, seed = self.root / "config.json", self.root / "seed.npz"
        config.write_text(json.dumps({"inputMode": "recorded"}))
        seed.write_bytes(b"synthetic seed bytes, not loaded")
        self.args = argparse.Namespace(config=config, seed_sample=seed, root=self.root / "install",
            output=self.root / "check", bundle=self.root / "package", messages=100, scans=10,
            seed_sha256=hashlib.sha256(seed.read_bytes()).hexdigest())
        self.args.bundle.mkdir()
        self.addCleanup(patch.stopall)
        patch.object(probe.sys, "platform", "linux").start()
        patch.object(probe.sys, "version_info", (3, 10, 0)).start()
        patch.object(probe.platform, "machine", return_value="aarch64").start()
        patch.object(probe.os, "geteuid", return_value=1000, create=True).start()

    def test_recorded_preflight_and_python310_compatible_digest_create_no_output(self):
        probe.inputs(self.args)
        self.assertEqual(self.args.seed_sha256, probe.sha256(self.args.seed_sample))
        self.assertFalse(self.args.output.exists())
        self.assertFalse(self.args.root.exists())

    def test_live_config_is_rejected_before_deployment(self):
        self.args.config.write_text(json.dumps({"inputMode": "live"}))
        with self.assertRaises(ValueError):
            probe.inputs(self.args)
        self.assertFalse(self.args.root.exists())

    def test_existing_installation_and_mismatched_seed_are_preserved(self):
        self.args.root.mkdir()
        with self.assertRaises(ValueError):
            probe.inputs(self.args)
        self.assertTrue(self.args.root.is_dir())
        self.args.root = self.root / "other"
        self.args.seed_sample.write_bytes(b"changed synthetic seed")
        with self.assertRaises(ValueError):
            probe.inputs(self.args)

    def test_overlapping_package_and_output_are_rejected(self):
        self.args.output = self.args.bundle / "output"
        with self.assertRaisesRegex(ValueError, "separate_package"):
            probe.inputs(self.args)

    def receipt(self, directory="separate-storage-session"):
        folder = self.root / "maps" / directory
        folder.mkdir(parents=True)
        receipt = folder / "map-ready.json"
        receipt.write_text(json.dumps({"schemaVersion": 2, "session": "a" * 32}))
        return receipt

    def test_map_identity_does_not_imply_its_storage_directory(self):
        receipt = self.receipt()
        actual, value = probe.saved_receipt(receipt.parent.parent, "slam-" + "a" * 32, probe.sha256(receipt))
        self.assertEqual(receipt, actual)
        self.assertEqual("a" * 32, value["session"])

    def test_receipt_selection_requires_both_identity_and_digest(self):
        receipt = self.receipt()
        for map_id, digest in (("slam-" + "b" * 32, probe.sha256(receipt)), ("slam-" + "a" * 32, "0" * 64)):
            with self.subTest(map_id=map_id, digest=digest), self.assertRaisesRegex(ValueError, "one_identity"):
                probe.saved_receipt(receipt.parent.parent, map_id, digest)

    def test_duplicate_receipt_is_rejected_without_changing_the_catalog(self):
        receipt = self.receipt()
        other = self.receipt("duplicate-storage-session")
        with self.assertRaisesRegex(ValueError, "one_identity"):
            probe.saved_receipt(receipt.parent.parent, "slam-" + "a" * 32, probe.sha256(receipt))
        self.assertTrue(receipt.is_file() and other.is_file())

    def recording(self, entries=None):
        reference = self.root / "reference"
        reference.mkdir()
        writer = Writer(reference / "private-callbacks.cdrjournal")
        for kind, elapsed in (entries or (("imu", 100_000_000), ("cloud", 300_000_000), ("imu", 500_000_000))):
            writer.append(kind, elapsed, b"isolated synthetic callback, not decoded")
        writer.finish()
        return {"reference": str(reference), "rate": 2.0}

    def test_recorded_duration_uses_verified_complete_journal_and_configured_rate(self):
        result = probe.recorded_input(self.recording(), 3, 1)
        self.assertEqual(.2, result["seconds"])
        self.assertEqual({"records": 3, "counts": {"imu": 2, "cloud": 1}}, result["journal"])

    def test_invalid_rate_and_mismatched_recorded_counts_are_rejected(self):
        config = self.recording()
        for rate in (True, 0, float("nan"), -1):
            with self.subTest(rate=rate), self.assertRaisesRegex(ValueError, "playback_rate"):
                probe.recorded_input({**config, "rate": rate}, 3, 1)
        with self.assertRaisesRegex(ValueError, "complete_recorded"):
            probe.recorded_input(config, 4, 1)
        with self.assertRaisesRegex(ValueError, "complete_recorded"):
            probe.recorded_input(config, 3, 2)

    def test_raw_cloud_callbacks_and_processed_scans_are_distinct(self):
        config = self.recording((("imu", 100_000_000), ("cloud", 200_000_000),
                                 ("cloud", 300_000_000), ("imu", 500_000_000)))
        result = probe.recorded_input(config, 4, 1)
        self.assertEqual(2, result["journal"]["counts"]["cloud"])


if __name__ == "__main__":
    unittest.main()
