"""Offline dependency integrity and installer contracts; no network or robot."""
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import arm64_python_runtime as runtime
import check_arm64_python_runtime as probe


class RuntimeTests(unittest.TestCase):
    def fixture(self, directory, payload=b"synthetic wheel, never installed"):
        folder = Path(directory) / "wheels"
        folder.mkdir()
        lock = {"packages": [{"name": "synthetic", "version": "1", "filename": "synthetic.whl",
                "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                "url": "https://files.pythonhosted.org/packages/synthetic.whl"}]}
        (folder / "synthetic.whl").write_bytes(payload)
        (folder / "requirements.txt").write_text(runtime.requirements(lock), encoding="utf-8")
        return folder.resolve(), lock

    def test_committed_lock_is_complete_and_hash_pinned(self):
        lock = runtime.load_lock()
        self.assertEqual(runtime.PINNED, {entry["name"]: entry["version"] for entry in lock["packages"]})
        self.assertEqual(5, runtime.requirements(lock).count(" --hash=sha256:"))
        self.assertEqual(48246399, sum(entry["bytes"] for entry in lock["packages"]))

    def test_mutated_lock_rejected(self):
        for kind in ("duplicate", "missing", "platform", "version", "path", "host", "query", "hash", "size"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                value = json.loads(runtime.LOCK.read_text(encoding="utf-8"))
                first = value["packages"][0]
                if kind == "duplicate":
                    value["packages"][-1] = first
                elif kind == "missing":
                    value["packages"].pop()
                elif kind == "platform":
                    value["platform"] = "x86_64"
                elif kind == "version":
                    first["version"] = "latest"
                elif kind == "path":
                    first["filename"] = "../escape.whl"
                elif kind == "host":
                    first["url"] = first["url"].replace("files.pythonhosted.org", "example.invalid")
                elif kind == "query":
                    first["url"] += "?extra=1"
                elif kind == "hash":
                    first["sha256"] = "not a digest"
                else:
                    first["bytes"] = True
                path = Path(directory) / "lock.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    runtime.load_lock(path)

    def test_exact_wheelhouse_accepts_known_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            wheels, lock = self.fixture(directory)
            runtime.verify_wheelhouse(wheels, lock)

    def test_inventory_hash_size_and_requirements_mutations_rejected(self):
        for kind in ("extra", "missing", "bytes", "size", "requirements"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                wheels, lock = self.fixture(directory)
                if kind == "extra":
                    (wheels / "unreviewed.whl").write_bytes(b"x")
                elif kind == "missing":
                    (wheels / "synthetic.whl").unlink()
                elif kind == "bytes":
                    path = wheels / "synthetic.whl"
                    path.write_bytes(b"x" * path.stat().st_size)
                elif kind == "size":
                    (wheels / "synthetic.whl").write_bytes(b"short")
                else:
                    (wheels / "requirements.txt").write_text("numpy\n")
                with self.assertRaises(ValueError):
                    runtime.verify_wheelhouse(wheels, lock)

    def test_directory_entry_cannot_replace_wheel(self):
        with tempfile.TemporaryDirectory() as directory:
            wheels, lock = self.fixture(directory)
            (wheels / "synthetic.whl").unlink()
            (wheels / "synthetic.whl").mkdir()
            with self.assertRaises(ValueError):
                runtime.verify_wheelhouse(wheels, lock)

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime, "urlopen") as fetch:
            output = Path(directory).resolve()
            marker = output / "preserve"
            marker.write_text("user data")
            with self.assertRaises(ValueError):
                runtime.download(output)
            fetch.assert_not_called()
            self.assertEqual("user data", marker.read_text())

    def test_relative_output_rejected(self):
        with self.assertRaises(ValueError):
            runtime.new_directory(Path("relative"))

    def download_case(self, actual, *, host="files.pythonhosted.org", valid=True):
        with tempfile.TemporaryDirectory() as directory:
            wheels, lock = self.fixture(directory)
            response = io.BytesIO(actual)
            response.url = "https://" + host + "/packages/synthetic.whl"
            output = Path(directory).resolve() / "download"
            with patch.object(runtime, "load_lock", return_value=lock), patch.object(runtime, "urlopen", return_value=response):
                if valid:
                    runtime.download(output)
                    runtime.verify_wheelhouse(output, lock)
                else:
                    with self.assertRaises(ValueError):
                        runtime.download(output)
                    self.assertFalse((output / "requirements.txt").exists())

    def test_download_verifies_before_marking_complete(self):
        self.download_case(b"synthetic wheel, never installed")

    def test_corrupted_download_is_not_complete(self):
        self.download_case(b"corrupt", valid=False)

    def test_oversize_download_is_bounded(self):
        self.download_case(b"x" * 1000, valid=False)

    def test_redirect_to_other_host_rejected(self):
        self.download_case(b"synthetic wheel, never installed", host="example.invalid", valid=False)

    def test_system_site_inheritance_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for inheritance in ("true", "false"):
                (target / "pyvenv.cfg").write_text("home = /usr/bin\ninclude-system-site-packages = " + inheritance + "\n")
                if inheritance == "true":
                    with self.assertRaises(ValueError):
                        runtime.isolated_venv(target)
                else:
                    runtime.isolated_venv(target)

    @contextmanager
    def target(self, home):
        with patch.object(runtime.sys, "platform", "linux"), patch.object(runtime.platform, "machine", return_value="aarch64"), \
             patch.object(runtime.sys, "version_info", (3, 10)), patch.object(runtime.os, "geteuid", return_value=1000, create=True), \
             patch.object(runtime.sys, "prefix", "synthetic-system"), patch.object(runtime.sys, "base_prefix", "synthetic-system"), \
             patch.object(runtime.Path, "home", return_value=home):
            yield

    def test_install_uses_no_network_clean_env_hashes_and_no_system_site(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            wheels, lock = self.fixture(home)
            output = home / "runtime"

            def run(command, **kwargs):
                self.assertNotIn("PYTHONPATH", kwargs["env"])
                self.assertNotIn("LD_PRELOAD", kwargs["env"])
                self.assertTrue(kwargs["check"])
                if "venv" in command:
                    target = Path(command[-1])
                    target.mkdir()
                    (target / "pyvenv.cfg").write_text("include-system-site-packages = false\n")

            with self.target(home), patch.object(runtime, "load_lock", return_value=lock), \
                    patch.object(runtime.subprocess, "run", side_effect=run) as commands:
                report = runtime.install(wheels, output)
            self.assertEqual(3, commands.call_count)
            pip = commands.call_args_list[1].args[0]
            for flag in ("-I", "--isolated", "--no-index", "--no-cache-dir", "--require-hashes", "--no-deps", "--only-binary=:all:"):
                self.assertIn(flag, pip)
            self.assertFalse(report["systemSitePackages"])
            self.assertFalse(report["runtimeCompatibilityAccepted"])

    def test_install_failure_keeps_partial_without_success_report(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            wheels, lock = self.fixture(home)
            output = home / "runtime"
            with self.target(home), patch.object(runtime, "load_lock", return_value=lock), \
                    patch.object(runtime.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "synthetic")):
                with self.assertRaises(subprocess.CalledProcessError):
                    runtime.install(wheels, output)
            self.assertTrue(output.is_dir())
            self.assertFalse((output / "install-report.json").exists())

    def test_install_rejects_overlapping_output_before_venv(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            wheels, _ = self.fixture(home)
            with self.target(home), patch.object(runtime.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    runtime.install(wheels, wheels / "nested")
                run.assert_not_called()

    def test_target_probe_refuses_wrong_platform_before_imports(self):
        with patch.object(probe.sys, "platform", "not-linux"):
            with self.assertRaises(ValueError):
                probe.check()

    def test_target_evidence_binds_actual_checked_source_and_lock(self):
        evidence = json.loads((ROOT / "docs/slam-python-runtime-arm64-evidence.json").read_text(encoding="utf-8"))
        self.assertEqual("isolated_arm64_numerical_ros_compatibility_passed", evidence["result"])
        self.assertEqual(runtime.PINNED, evidence["versions"])
        self.assertEqual(runtime.sha256(runtime.LOCK), evidence["lockSha256"])
        self.assertEqual(48246399, evidence["wheelBytes"])
        self.assertEqual(0, evidence["numericalImportWarnings"])
        self.assertTrue(evidence["modulesIsolated"])
        self.assertFalse(evidence["systemSitePackages"])
        self.assertEqual({"knownSeedGicp", "wrongSeedRejected", "scipyTreeRotation", "exactTimeHumbleTf"},
                         set(evidence["checks"]))
        self.assertTrue(all(value is True for value in evidence["checks"].values()))
        self.assertTrue(all(value is False for value in evidence["boundaries"].values()))
        self.assertEqual(0, evidence["installExitCode"])
        self.assertEqual(0, evidence["checkExitCode"])
        self.assertLess(evidence["syntheticGicpMaximumMatrixError"], .004)
        self.assertLess(evidence["syntheticTfMaximumMatrixError"], 1e-10)
        import check_base_pose_tf2
        expected = {*check_base_pose_tf2.SOURCES, "tools/arm64_python_runtime.py",
                    "tools/check_arm64_python_runtime.py", "robot-slam/runtime-arm64-cp310.lock.json"}
        self.assertEqual(expected, set(evidence["sourceSha256"]))
        for name, digest in evidence["sourceSha256"].items():
            self.assertEqual(digest, runtime.sha256(ROOT / name), name)


if __name__ == "__main__":
    unittest.main()
