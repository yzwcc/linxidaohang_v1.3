"""Isolated synthetic SQLite bags and the public aggregate manifest."""
import copy
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location(
    "verify_slam_capture", ROOT / "tools/verify_slam_capture.py",
)
capture = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
    spec.loader.exec_module(capture)


class CaptureIntegrity(unittest.TestCase):
    def fixture(self, root):
        manifest = {"schemaVersion": 1, "datasets": {}}
        for label in ("static", "dynamic"):
            directory = root / ("explicit-synthetic-" + label)
            directory.mkdir()
            metadata = directory / "metadata.yaml"
            metadata.write_text("explicit_synthetic_fixture: true\n", encoding="ascii")
            path = directory / "synthetic.db3"
            with closing(sqlite3.connect(path)) as database:
                database.execute("CREATE TABLE topics (id INTEGER, name TEXT)")
                database.execute("CREATE TABLE messages (topic_id INTEGER, data BLOB)")
                for index, topic in enumerate(capture.TOPICS.values()):
                    database.execute("INSERT INTO topics VALUES (?, ?)", (index, topic))
                    database.execute("INSERT INTO messages VALUES (?, ?)", (index, b"SYNTHETIC"))
                database.commit()
            manifest["datasets"][label] = {
                "directoryName": directory.name, "databaseBytes": path.stat().st_size,
                "messageCount": 5, "topicCounts": dict.fromkeys(capture.TOPICS, 1),
                "sha256": {file.name: capture.sha256(file) for file in (metadata, path)},
            }
        return manifest

    def test_current_capture_manifest_has_exact_hashes_and_consistent_counts(self):
        manifest = json.loads((ROOT / "docs/slam-capture-evidence.json").read_text(encoding="utf-8"))
        capture.validate_manifest(manifest)
        self.assertFalse(manifest["boundaries"]["slamTrajectoryExecuted"])
        self.assertFalse(manifest["boundaries"]["navigationValidated"])
        self.assertFalse(manifest["rawDataPublished"])

    def test_all_files_and_counts_match_without_modifying_the_bags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.fixture(root)
            before = {path: path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}
            report = capture.verify(manifest, root)
            self.assertEqual("all_declared_files_and_topic_counts_match", report["result"])
            self.assertFalse(report["navigationValidated"])
            self.assertEqual(5, report["datasets"]["dynamic"]["messageCount"])
            self.assertEqual(before, {path: path.stat().st_mtime_ns for path in before})
            self.assertFalse(list(root.rglob("*-wal")))
            self.assertFalse(list(root.rglob("*-shm")))

    def test_incorrect_length_or_non_hexadecimal_sha_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.fixture(Path(directory))
            for digest in ("a" * 63, "a" * 65, "z" * 64, True):
                changed = copy.deepcopy(manifest)
                changed["datasets"]["static"]["sha256"]["metadata.yaml"] = digest
                with self.assertRaisesRegex(ValueError, "invalid_sha256"):
                    capture.validate_manifest(changed)

    def test_traversal_cannot_escape_the_data_root(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.fixture(Path(directory))
            for path in ("..", "../outside", "sub\\outside", "/outside", "C:outside", ""):
                manifest["datasets"]["static"]["directoryName"] = path
                with self.assertRaisesRegex(ValueError, "plain_filename"):
                    capture.validate_manifest(manifest)

    def test_corrupted_copy_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.fixture(root)
            (root / manifest["datasets"]["static"]["directoryName"] / "metadata.yaml").write_text(
                "SYNTHETIC CORRUPTED COPY\n", encoding="ascii",
            )
            with self.assertRaisesRegex(ValueError, "checksum_mismatch"):
                capture.verify(manifest, root)

    def test_partial_or_live_database_sidecar_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.fixture(root)
            bag = root / manifest["datasets"]["dynamic"]["directoryName"]
            (bag / "synthetic.db3-wal").write_bytes(b"SYNTHETIC")
            with self.assertRaisesRegex(ValueError, "unexpected_or_missing"):
                capture.verify(manifest, root)

    def test_unchanged_checksum_cannot_hide_a_false_topic_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.fixture(root)
            manifest["datasets"]["static"]["topicCounts"]["imu"] = 2
            manifest["datasets"]["static"]["messageCount"] = 6
            with self.assertRaisesRegex(ValueError, "size_or_topic_count"):
                capture.verify(manifest, root)


if __name__ == "__main__":
    unittest.main()
