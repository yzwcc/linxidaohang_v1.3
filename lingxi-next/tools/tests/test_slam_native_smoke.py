"""Verify smoke-runner acceptance boundaries independently of its C++ check."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("run_glim_cpu_smoke", ROOT / "tools/run_glim_cpu_smoke.py")
smoke = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
    spec.loader.exec_module(smoke)


def build_report(work, **overrides):
    report = {
        "result": "core_libraries_built_not_target_acceptance",
        "sourceCommits": smoke.PINS,
        "hostMachine": smoke.platform.machine(),
    }
    report.update(overrides)
    (work / "build-report.json").write_text(json.dumps(report), encoding="utf-8")


class NativeSmokeBoundaries(unittest.TestCase):
    def test_non_linux_host_is_rejected(self):
        with patch.object(smoke.platform, "system", return_value="Windows"):
            with self.assertRaisesRegex(ValueError, "linux_build"):
                smoke.run(Path("unused"), Path("unused"))

    def test_failed_or_incomplete_build_cannot_run_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            build_report(work, result="failed_or_interrupted")
            with patch.object(smoke.platform, "system", return_value="Linux"):
                with self.assertRaisesRegex(ValueError, "successful_core"):
                    smoke.run(work, work / "source")
            self.assertFalse((work / "native-smoke").exists())

    def test_wrong_source_or_host_is_rejected(self):
        for overrides in ({"sourceCommits": {}}, {"hostMachine": "not-the-host"}):
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                build_report(work, **overrides)
                with patch.object(smoke.platform, "system", return_value="Linux"):
                    with self.assertRaisesRegex(ValueError, "build_baseline_or_machine"):
                        smoke.run(work, work / "source")

    def test_existing_smoke_results_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            build_report(work)
            (work / "native-smoke").mkdir()
            marker = work / "native-smoke" / "keep"
            marker.write_text("unchanged")
            with patch.object(smoke.platform, "system", return_value="Linux"):
                with self.assertRaisesRegex(ValueError, "existing_smoke"):
                    smoke.run(work, work / "source")
            self.assertEqual("unchanged", marker.read_text())

    def test_ctest_failure_records_failure_and_never_claims_robot_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            build_report(work)
            with patch.object(smoke.platform, "system", return_value="Linux"), \
                    patch.object(smoke, "generate") as generated, \
                    patch.object(smoke.subprocess, "check_output", return_value="x86_64-linux-gnu"), \
                    patch.object(smoke.subprocess, "run", side_effect=[
                        SimpleNamespace(returncode=0), SimpleNamespace(returncode=0),
                        SimpleNamespace(returncode=1),
                    ]), patch("builtins.print"):
                with self.assertRaisesRegex(RuntimeError, "ctest_failed"):
                    smoke.run(work, work / "source")
                self.assertEqual("synthetic_fixture", generated.call_args.args[2]["inputClassification"])
            report = json.loads((work / "native-smoke/smoke-report.json").read_text())
            self.assertEqual("failed_or_interrupted", report["result"])
            self.assertEqual(1, report["stages"][-1]["exitCode"])
            for key in ("robotCalibrationValidated", "navigationValidated", "rosWrapperValidated"):
                self.assertFalse(report[key])


if __name__ == "__main__":
    unittest.main()
