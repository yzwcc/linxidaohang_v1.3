"""Local input and collection checks; no robot connection or native execution."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import run_localization_arm64_trial as trial
import glim_supervised_target


class Arm64TrialInputs(unittest.TestCase):
    def test_upload_includes_all_frozen_sources_and_localization_dependencies(self):
        files = trial.source_files()
        self.assertTrue(set(glim_supervised_target.SOURCES).issubset(files))
        for name in ("tools/glim-replay/offline.cpp", "tools/glim-replay/CMakeLists.txt",
                     "robot-runtime/python/controlled_localization.py",
                     "robot-slam/lingxi_slam/seeded_localization.py",
                     "docs/slam-point-arm64-target-evidence.json"):
            self.assertEqual(ROOT / name, files[name])

    def test_private_tree_rejects_oversized_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / "input"
            file.touch()
            self.assertEqual({"input": {"sha256": hashlib.sha256(b"").hexdigest(), "bytes": 0}},
                             trial.inventory(root))
            with file.open("r+b") as stream:
                stream.truncate(64 * 1024**2 + 1)
            with self.assertRaises(ValueError):
                trial.inventory(root)

    def test_unpinned_wheel_rejected_before_environment_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheel = root / trial.WHEEL
            wheel.write_bytes(b"synthetic untrusted wheel")
            with self.assertRaises(ValueError):
                trial.install_wheel(wheel, root / "env")
            self.assertFalse((root / "env").exists())

    def test_remote_report_bytes_rechecked_independently_of_command_logs(self):
        payload = json.dumps({"result": "synthetic"}).encode()
        class Sftp:
            def open(self, *_args):
                return io.BytesIO(payload)
        expected = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        with patch.object(trial.remote, "remote_digest", return_value=expected):
            self.assertEqual({"result": "synthetic"}, trial.read_remote_report(Sftp(), "/report"))
        with patch.object(trial.remote, "remote_digest", return_value={**expected, "sha256": "0" * 64}):
            with self.assertRaises(ValueError):
                trial.read_remote_report(Sftp(), "/report")


if __name__ == "__main__":
    unittest.main()
