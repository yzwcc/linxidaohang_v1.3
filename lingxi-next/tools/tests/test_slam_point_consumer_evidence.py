"""Synthetic nonexecutable consumer archive; no Node, native SLAM or robot."""
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import export_glim_point_consumer as exporter
import prepare_glim_point_consumer as consumer
from test_slam_point_consumer_preparation import bundle_fixture, save_manifest


def fixture(root, *, omitted=None, changed=None):
    bundle = root / "bundle"
    manifest = bundle_fixture(bundle)
    digest = save_manifest(bundle, manifest)
    archive = root / "point-consumer-arm64.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        for name in (*manifest["files"], consumer.MANIFEST):
            if name == omitted:
                continue
            payload = (bundle / name).read_bytes()
            if name == changed:
                payload = bytes([payload[0] ^ 1]) + payload[1:]
            member = tarfile.TarInfo("point-consumer/" + name)
            member.size, member.mode = len(payload), 0o700 if name == "node/bin/node" else 0o600
            stream.addfile(member, io.BytesIO(payload))
    report = {
        "schemaVersion": 1, "result": consumer.RESULT, "npmExitCode": 0,
        "manifestSha256": digest, "archiveSha256": consumer.sha256(archive),
        "archiveBytes": archive.stat().st_size, "bundleFiles": len(manifest["files"]) + 1,
        "installedPackageCount": 1, "preparerSha256": consumer.sha256(consumer.__file__),
        "nodeArchiveSha256": consumer.NODE_ARCHIVE_SHA256,
        "consumerBundleSha256": manifest["consumerBundleSha256"],
        "packageLockSha256": manifest["packageLockSha256"],
        "boundaries": dict.fromkeys(consumer.BOUNDARIES, False),
        "privatePathThatMustNotLeak": "/home/synthetic/private",
    }
    (root / "preparation-report.json").write_text(json.dumps(report))
    return manifest, report


class ConsumerEvidenceTests(unittest.TestCase):
    def export(self, root):
        with patch.object(consumer, "verified_consumer"), \
                patch.object(exporter.binding, "source_hashes", return_value={"synthetic.py": "a" * 64}):
            return exporter.evidence(root)

    def test_verified_preparation_exports_only_named_aggregates_and_no_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root)
            result = self.export(root)
            self.assertEqual(consumer.RESULT, result["result"])
            self.assertEqual(8, result["bundleFiles"])
            self.assertFalse(any(result["boundaries"].values()))
            self.assertFalse(any(result["acceptance"].values()))
            self.assertNotIn("/home/synthetic", json.dumps(result))

    def test_missing_archive_member_or_changed_member_bytes_are_rejected(self):
        for condition in ({"omitted": "consumer.mjs"}, {"changed": "consumer.mjs"}):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                fixture(root, **condition)
                with self.subTest(condition=condition), self.assertRaises(ValueError):
                    self.export(root)

    def test_changed_preparer_identity_counts_or_execution_claim_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, original = fixture(root)
            for key, value in (("preparerSha256", "0" * 64), ("installedPackageCount", 99),
                               ("bundleFiles", 99), ("npmExitCode", False)):
                report = {**deepcopy(original), key: value}
                (root / "preparation-report.json").write_text(json.dumps(report))
                with self.subTest(key=key), self.assertRaises(ValueError):
                    self.export(root)
            report = deepcopy(original)
            report["boundaries"]["targetNodeExecuted"] = True
            (root / "preparation-report.json").write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                self.export(root)

    def test_changed_stage_file_is_not_hidden_by_unchanged_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root)
            (root / "bundle/consumer.mjs").write_bytes(b"changed synthetic consumer")
            with self.assertRaises(ValueError):
                self.export(root)


if __name__ == "__main__":
    unittest.main()
