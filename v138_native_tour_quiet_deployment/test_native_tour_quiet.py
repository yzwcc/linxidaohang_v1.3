import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import native_tour_quiet as native

TX = "20261019T180000-123456789abc"
DOCUMENT = {
    "skills": [
        {"skill_name": "start_fixed_line_intro", "enabled": True, "name": "native tour"},
        {"value": "exhibit_commentary", "enabled": True, "args": {"loop": 3}},
        {"skillName": "start_fixed_line_intro"},
        {"skill_name": "navigate", "enabled": True, "reply": "navigation unchanged"},
        {"skill_name": "sing", "enabled": True},
        {"skill_name": "dance", "enabled": True},
        {"skill_name": "handshake", "enabled": True},
        {"skill_name": "chat", "enabled": True},
        {"skill_name": "speech", "enabled": False},
        {"reply": "exhibit_commentary", "enabled": True},
    ],
    "metadata": {"name": "start_fixed_line_intro", "enabled": True},
}


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.native = native.NativeConfig(self.root)
        self.path = self.native.path(native.CONFIGS[0])
        self.path.parent.mkdir(parents=True)
        self.raw = (json.dumps(DOCUMENT, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        self.path.write_bytes(self.raw)
        self.path.chmod(0o640)

    def apply(self):
        return self.native.apply(TX, self.native.plan()["fingerprint"])

    def test_exactly_three_historical_mappings_only(self):
        snapshot = copy.deepcopy(DOCUMENT)
        updated, count, changed = native.disable_mapping(DOCUMENT)
        self.assertEqual(DOCUMENT, snapshot)
        self.assertEqual((count, changed), (3, 3))
        for value in updated["skills"][:3]:
            self.assertIs(value["enabled"], False)
        self.assertEqual(updated["skills"][3:], DOCUMENT["skills"][3:])
        self.assertEqual(updated["metadata"], DOCUMENT["metadata"])

    def test_disabled_mappings_are_idempotent(self):
        updated, _, _ = native.disable_mapping(DOCUMENT)
        self.assertEqual(native.disable_mapping(updated), (updated, 3, 0))

    def test_probe_does_not_write(self):
        plan = self.native.plan()
        self.assertEqual(plan["matches"], 3)
        self.assertEqual(plan["changes"], 3)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse(self.native.backups.exists())

    def test_apply_preserves_owner_and_mode(self):
        before = self.path.stat()
        report = self.apply()
        after = self.path.stat()
        self.assertEqual((before.st_uid, before.st_gid, before.st_mode & 0o777),
                         (after.st_uid, after.st_gid, after.st_mode & 0o777))
        self.assertEqual(report["changes"], 3)
        self.assertEqual(self.native.plan()["changes"], 0)
        backup = self.native.backups / TX
        self.assertEqual((backup / "0.json").read_bytes(), self.raw)
        self.assertEqual((backup / "0.json").stat().st_mode & 0o777, 0o600)

    def test_rollback_restores_exact_bytes(self):
        self.apply()
        self.native.rollback(TX, check=True)
        self.assertNotEqual(self.path.read_bytes(), self.raw)
        self.native.rollback(TX)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.native.rollback(TX)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_two_actual_paths_are_both_disabled(self):
        other = self.native.path(native.CONFIGS[1])
        other.parent.mkdir(parents=True)
        other.write_bytes(self.raw)
        self.assertEqual(self.native.plan()["changes"], 6)
        self.apply()
        self.assertEqual(self.native.plan()["changes"], 0)
        self.native.rollback(TX)
        self.assertEqual(other.read_bytes(), self.raw)

    def test_aliases_to_same_target_are_not_double_patched(self):
        other = self.native.path(native.CONFIGS[1])
        other.parent.mkdir(parents=True)
        try:
            other.symlink_to(self.path)
        except OSError:
            self.skipTest("symlink unavailable")
        self.assertEqual(self.native.plan()["matches"], 3)
        self.apply()
        self.assertTrue(other.is_symlink())
        self.native.rollback(TX)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_root_list_supported(self):
        self.path.write_bytes(native.encoded(DOCUMENT["skills"]))
        self.assertEqual(self.native.plan()["matches"], 3)
        self.apply()
        self.assertEqual(self.native.plan()["changes"], 0)

    def test_no_matching_skill_is_explicit(self):
        self.path.write_bytes(native.encoded({"skills": [{"skill_name": "sing", "enabled": True}]}))
        self.assertEqual(self.native.plan()["matches"], 0)

    def test_missing_config_is_explicit(self):
        self.path.unlink()
        self.assertEqual(self.native.plan()["entries"], [])

    def test_malformed_config_prevents_any_change(self):
        other = self.native.path(native.CONFIGS[1])
        other.parent.mkdir(parents=True)
        other.write_bytes(b"{broken")
        with self.assertRaises(ValueError):
            self.native.plan()
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse(self.native.backups.exists())

    def test_scalar_config_rejected(self):
        self.path.write_bytes(b'"unsupported"\n')
        with self.assertRaises(RuntimeError):
            self.native.plan()

    def test_preflight_concurrent_change_rejected(self):
        expected = self.native.plan()["fingerprint"]
        self.path.write_bytes(self.raw + b"\n")
        with self.assertRaisesRegex(RuntimeError, "changed after preflight"):
            self.native.apply(TX, expected)
        self.assertEqual(self.path.read_bytes(), self.raw + b"\n")

    def test_modified_live_config_not_overwritten_on_rollback(self):
        self.apply()
        changed = native.encoded({"custom": True})
        self.path.write_bytes(changed)
        with self.assertRaisesRegex(RuntimeError, "refusing rollback"):
            self.native.rollback(TX)
        self.assertEqual(self.path.read_bytes(), changed)

    def test_corrupt_backup_rejected(self):
        self.apply()
        (self.native.backups / TX / "0.json").write_bytes(b"corrupt")
        with self.assertRaises(RuntimeError):
            self.native.rollback(TX)
        self.assertEqual(self.native.plan()["changes"], 0)

    def test_partial_write_error_restores_first_file(self):
        other = self.native.path(native.CONFIGS[1])
        other.parent.mkdir(parents=True)
        other.write_bytes(self.raw)
        real_atomic = native.atomic
        writes = []

        def fail_second(path, raw, metadata):
            writes.append(path)
            if len(writes) == 2:
                raise OSError("simulated disk failure")
            real_atomic(path, raw, metadata)

        with patch.object(native, "atomic", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertEqual(other.read_bytes(), self.raw)

    def test_duplicate_transaction_rejected(self):
        self.apply()
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            self.native.apply(TX, self.native.plan()["fingerprint"])

    def test_invalid_transaction_ids_rejected(self):
        for value in ("", "..", "../elsewhere", "/tmp/a", TX + "/x", "x" * 80):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                self.native.backup_path(value)

    def test_backup_filename_traversal_rejected(self):
        self.apply()
        record = self.native.backups / TX / "transaction.json"
        value = json.loads(record.read_bytes())
        value["records"][0]["saved"] = "../../elsewhere"
        record.write_bytes(native.encoded(value))
        with self.assertRaisesRegex(RuntimeError, "filename"):
            self.native.rollback(TX)

    def test_oversized_config_rejected(self):
        with patch.object(native, "LIMIT", 8):
            with self.assertRaisesRegex(RuntimeError, "size limit"):
                self.native.plan()


if __name__ == "__main__":
    unittest.main()
