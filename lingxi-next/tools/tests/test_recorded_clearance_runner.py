"""Supervisor failures never publish an accepted offline-clearance report."""
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import evaluate_recorded_clearance as runner

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy", "json5"))


class ClearanceConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.config = self.root / "configuration.json"
        self.value = {"schemaVersion": 1, "purpose": "offline_declared_clearance_only",
            "savedMap": str(self.root / "source"), "mapReceiptSha256": "a" * 64,
            "bindingSha256": "b" * 64, "observationImplementationSha256": "c" * 64,
            "geometry": {"metresPerRawUnit": 1., "declaredFromRaw": [1., 0, 0, 0, 0, 1., 0, 0, 0, 0, 1., 0, 0, 0, 0, 1.]},
            "selection": {"maximum_rays": 131072, "maximum_per_scan": 8192},
            "projection": {"width": 128, "height": 128, "resolution_metres": .1,
                           "origin_x_metres": -6.4, "origin_y_metres": -6.4},
            "octomap": {"library": "unused", "sha256": "d" * 64, "dependencies": {}},
            "maximumWorkUnits": 16000000, "wallTimeoutSeconds": 300}
        self.write()

    def write(self):
        self.config.write_text(json.dumps(self.value), encoding="utf-8")

    def test_bounded_explicit_configuration(self):
        self.assertEqual(self.value, runner.configuration(self.config))
        for key, value in (("purpose", "navigation"), ("schemaVersion", True), ("wallTimeoutSeconds", 301),
                           ("wallTimeoutSeconds", 0), ("maximumWorkUnits", 16000001), ("maximumWorkUnits", True)):
            with self.subTest(key=key, value=value):
                original = self.value[key]
                self.value[key] = value
                self.write()
                with self.assertRaises(ValueError):
                    runner.configuration(self.config)
                self.value[key] = original
        self.value["extra"] = True
        self.write()
        with self.assertRaises(ValueError):
            runner.configuration(self.config)
        self.config.write_text(" " * 32769)
        with self.assertRaises(ValueError):
            runner.configuration(self.config)

    @unittest.skipUnless(AVAILABLE, "requires isolated scientific Python dependencies")
    def test_timeout_child_failure_missing_result_and_false_acceptance_do_not_publish(self):
        for mode in ("timeout", "exit", "missing", "bad-report", "changed-config", "changed-source",
                     "missing-artifact", "changed-artifact", "bad-counts", "bad-support", "bad-size", "accepted"):
            with self.subTest(mode=mode):
                output = self.root / mode
                self.write()
                def execute(command, **kwargs):
                    self.assertEqual(300, kwargs["timeout"])
                    self.assertEqual("1", kwargs["env"]["OPENBLAS_NUM_THREADS"])
                    self.assertIn("--worker", command)
                    report = {"result": "recorded_declared_clearance_evaluated_not_navigation",
                        "configurationSha256": runner.digest(self.config),
                        "offlineOnly": True, "projection": self.value["projection"],
                        "statistics": {"freeCells": 0, "occupiedCells": 0, "unknownCells": 16384},
                        "nativeSha256": self.value["octomap"]["sha256"],
                        "loadedDependenciesSha256": self.value["octomap"]["dependencies"],
                        "runnerSourcesSha256": {name: runner.digest(runner.ROOT / name) for name in runner.SOURCES},
                        "clearanceBytesSha256": hashlib.sha256(bytes([255]) * 16384).hexdigest(),
                        **dict.fromkeys(("physicalScaleValidated", "groundFrameValidated", "navigationReady",
                            "groundSupportValidated", "obstacleCompletenessValidated",
                            "robotContacted", "sensorsSubscribed", "physicalMotionSent"), False)}
                    if mode != "missing-artifact":
                        (output / "private-declared-clearance.bin").write_bytes(bytes([255]) * 16384)
                    if mode == "bad-report":
                        report["navigationReady"] = True
                    if mode == "bad-support":
                        report["groundSupportValidated"] = True
                    if mode == "changed-source":
                        report["runnerSourcesSha256"] = {}
                    if mode == "changed-artifact":
                        (output / "private-declared-clearance.bin").write_bytes(bytes([0]) * 16384)
                    if mode == "bad-size":
                        (output / "private-declared-clearance.bin").write_bytes(b"\xff")
                    if mode == "bad-counts":
                        report["statistics"]["unknownCells"] = 0
                    if mode == "changed-config":
                        self.config.write_text(self.config.read_text() + "\n")
                    if mode != "missing":
                        (output / "report.pending").write_text(json.dumps(report))
                    if mode == "timeout":
                        raise subprocess.TimeoutExpired(command, 300)
                    return subprocess.CompletedProcess(command, 1 if mode == "exit" else 0)
                with patch("refine_saved_map.private_output", return_value=output), \
                        patch.object(runner.subprocess, "run", side_effect=execute):
                    if mode == "accepted":
                        self.assertFalse(runner.run(self.config, output)["navigationReady"])
                        self.assertTrue((output / "report.json").is_file())
                        self.assertFalse((output / "report.pending").exists())
                    else:
                        with self.assertRaises((RuntimeError, ValueError)):
                            runner.run(self.config, output)
                        self.assertFalse((output / "report.json").exists())

    @unittest.skipIf(sys.platform == "win32", "symlink test requires ordinary-user Linux")
    def test_configuration_symlinks_are_not_resolved_away(self):
        link = self.root / "link.json"
        link.symlink_to(self.config)
        with self.assertRaisesRegex(ValueError, "direct_offline_configuration"):
            runner.configuration(link)
        with self.assertRaises(ValueError):
            runner.run(link, self.root / "output")


if __name__ == "__main__":
    unittest.main()
