"""Exercise the shipped voice-state validator without robot or SSH access."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
HELPER = Path(os.environ.get("LINGXI_TEST_HELPER", HERE))
if not (HELPER / "arrival_runtime.py").is_file():
    HELPER = HERE.parent / (
        "tmp/r19-qa/lingxi-v1.3.8-complete-20261021-r19-deployment/"
        "scripts/native-tour-quiet"
    )
sys.path.insert(0, str(HELPER))
spec = importlib.util.spec_from_file_location("r19_voice_installer", HELPER / "install.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class VoiceValidatorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.release = Path(temporary.name)
        self.script = self.release / module.VOICE_SCRIPT
        self.script.parent.mkdir()
        self.script.write_bytes(b"#!/bin/bash\nexit 0\n")
        self.validator = module.Installer.__new__(module.Installer)
        self.validator.release = self.release
        self.validator.meta = {
            "voice_bridge_control_sha256": module.digest(self.script.read_bytes())
        }
        (self.release / module.MANIFEST).write_text(
            json.dumps({"files": [{"path": "java/test.jar", "sha256": "0" * 64}]}),
            encoding="utf-8",
        )

    def call(self, output="robot_voice_bridge=running pid=123\n"):
        result = subprocess.CompletedProcess([], 0, output, "")
        with patch.object(module.subprocess, "run", return_value=result) as run:
            value = self.validator.voice_state()
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["bash", str(self.script), "status"])
        return value

    def test_running_script_passes_when_payload_manifest_has_no_scripts(self):
        self.assertTrue(self.call())

    def test_stopped_script_reports_stopped(self):
        self.assertFalse(self.call("robot_voice_bridge=stopped\n"))

    def test_modified_script_fails_before_it_is_executed(self):
        self.script.write_bytes(b"unexpected field change")
        with patch.object(module.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                self.validator.voice_state()
        run.assert_not_called()

    def test_missing_or_invalid_expected_hash_is_rejected(self):
        for expected in (None, "", "0" * 63, "G" * 64, {}):
            self.validator.meta["voice_bridge_control_sha256"] = expected
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                self.validator.voice_state()

    def test_missing_script_is_rejected(self):
        self.script.unlink()
        with self.assertRaisesRegex(RuntimeError, "不存在"):
            self.validator.voice_state()

    def test_unknown_and_duplicate_status_are_rejected(self):
        for output in ("unknown\n", "robot_voice_bridge=running pid=1\nrobot_voice_bridge=stopped\n"):
            with self.assertRaisesRegex(RuntimeError, "无法确认"):
                self.call(output)

    def test_original_manifest_lookup_reproduces_reported_failure(self):
        original = """def old_voice_state(self):
    script = self.release / VOICE_SCRIPT
    manifest = json.loads(regular(self.release / MANIFEST))
    expected = next((item for item in manifest["files"] if item["path"] == VOICE_SCRIPT), None)
    if expected is None or digest(regular(script)) != expected["sha256"]:
        raise RuntimeError("语音 bridge 控制脚本清单不一致")
"""
        namespace = dict(vars(module))
        exec(compile(original, "original-r18-validator", "exec"), namespace)
        with self.assertRaisesRegex(RuntimeError, "清单不一致"):
            namespace["old_voice_state"](self.validator)
        self.assertTrue(self.call())


if __name__ == "__main__":
    unittest.main()
