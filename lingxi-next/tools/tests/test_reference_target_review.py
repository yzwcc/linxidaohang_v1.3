import hashlib
import json
import os
from pathlib import Path
import sys
from contextlib import asynccontextmanager
from tempfile import TemporaryDirectory
from threading import Event, RLock
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "robot-runtime" / "python"))
import reference_target as target
from reference_service import make_app


def sample_target(path, _limit):
    files = {"firmware_version": target.FIRMWARE.encode() + b"\n", "version": b"aimdk v0.0.0-g14fe2e8\n",
             "machine-id": b"abcd" * 8 + b"\n", "app.yaml": b"app", "fastlio.yaml": b"individual calibration"}
    return files[Path(path).name]


class TargetReviewTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "requires a Linux root-owned temporary fixture")
    def test_read_only_review_file_gate(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            direct = root / "profile.yaml"
            direct.write_bytes(b"known bytes")
            direct.chmod(0o640)
            self.assertEqual(target.protected_bytes(direct, 32), b"known bytes")
            with self.assertRaisesRegex(ValueError, "invalid_or_changing_motion_review_input"):
                target.protected_bytes(direct, 8)
            link = root / "link.yaml"
            link.symlink_to(direct)
            with self.assertRaisesRegex(ValueError, "canonical_motion_review_input_required"):
                target.protected_bytes(link, 32)
            direct.chmod(0o660)
            with self.assertRaisesRegex(ValueError, "root_owned_read_only_motion_review_input_required"):
                target.protected_bytes(direct, 32)

    def test_vendor_version_is_read_from_installed_records_and_machine_id_is_not_exposed(self):
        with patch.object(target, "protected_bytes", side_effect=sample_target), patch.object(Path, "resolve", autospec=True, side_effect=lambda path, **_: path):
            result = target.review_fields(Path("app.yaml"), Path("fastlio.yaml"))
            self.assertEqual(result["firmware"], target.FIRMWARE)
            self.assertEqual(result["aimdk"], "v0.0.0-g14fe2e8")
            self.assertEqual(result["fastlioConfigSha256"], hashlib.sha256(b"individual calibration").hexdigest())
            self.assertNotIn("abcd" * 8, json.dumps(result))

    def test_review_rejects_manifest_version_another_robot_and_calibration_change(self):
        fields = dict(schemaVersion=1, firmware=target.FIRMWARE, aimdk="v0.0.0-g14fe2e8", targetIdentitySha256="0" * 64,
                      configSha256="1" * 64, fastlioConfigSha256="2" * 64)
        maps = {"fastlio-" + "f" * 32: {name: "a" * 64 for name in target.MAP_FILES}}
        with patch.object(target, "review_fields", return_value=fields), patch.object(target, "protected_bytes") as read:
            read.return_value = json.dumps({**fields, "maps": maps}).encode()
            approved = target.load_motion_review(Path("review.json"), Path("app.yaml"), Path("fastlio.yaml"))
            self.assertEqual(approved.maps, maps)
            self.assertTrue(approved.matches_current_target())
            read.return_value += b"\n"
            self.assertFalse(approved.matches_current_target())
            for wrong in (dict(aimdk="v1.1.0-rc10"), dict(targetIdentitySha256="f" * 64), dict(fastlioConfigSha256="f" * 64)):
                with self.subTest(wrong=wrong):
                    read.return_value = json.dumps({**fields, **wrong, "maps": maps}).encode()
                    with self.assertRaisesRegex(ValueError, "motion_review_target_or_configuration_mismatch"):
                        target.load_motion_review(Path("review.json"), Path("app.yaml"), Path("fastlio.yaml"))
            for wrong_maps in ({}, {"vendor-map": {}}, {"fastlio-" + "f" * 32: {"metadata.json": "a" * 64}}):
                read.return_value = json.dumps({**fields, "maps": wrong_maps}).encode()
                with self.assertRaises(ValueError):
                    target.load_motion_review(Path("review.json"), Path("app.yaml"), Path("fastlio.yaml"))

    def test_motion_watchdog_retires_and_does_not_resume_after_a_review_change(self):
        from fastapi.testclient import TestClient

        removed = Event()
        stop = Event()
        map_id = "fastlio-" + "f" * 32
        task = SimpleNamespace(task_id="active", phase="running", map_id=map_id)

        def revoke(item, _reason):
            self.assertIs(item, task)
            item.phase = "stopping"
            stop.set()

        @asynccontextmanager
        async def lifecycle(_app):
            yield

        ros = SimpleNamespace(motion_enabled=True, motion_task=task, lock=RLock(), motion_session="a" * 32,
                              revoke=Mock(side_effect=revoke), submit=Mock())
        upstream = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ros=ros, active_map_id=map_id)),
                                   lifespan=lifecycle)
        review = SimpleNamespace(maps={}, matches_current_target=lambda: not removed.is_set())
        with TestClient(make_app(upstream, Path.cwd(), motion_review=review)) as client:
            removed.set()
            self.assertTrue(stop.wait(3), "changed review must retire the existing simulated task")
            removed.clear()
            with ros.lock:
                task.phase = "paused"
            result = client.post("/lingxi/navigation/control", json={"action": "resume", "session": "a" * 32, "taskId": "active"})
            self.assertEqual(result.status_code, 409)
            ros.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
