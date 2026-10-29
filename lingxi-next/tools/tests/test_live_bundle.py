"""Synthetic assets, package metadata and nonexecutable ARM64 header only."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "tools")]
import live_bundle as bundle
import prepare_live_bundle as prepare
import launch_live_bundle as launcher
from test_slam_point_consumer_preparation import bundle_fixture, save_manifest


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return bundle.sha256(path)


class LiveBundleContracts(unittest.TestCase):
    def test_standalone_module_resolves_helpers_without_inherited_pythonpath(self):
        script = (
            "import runpy; "
            f"runpy.run_path({str(ROOT / 'robot-runtime/python/live_bundle.py')!r}); "
            "import prepare_glim_point_consumer, glim_point_target_probe; "
            "print('standalone_helpers_resolved')"
        )
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script], cwd=directory,
                capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("standalone_helpers_resolved", result.stdout.strip())

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project, self.assets, self.consumer = (self.root / name for name in ("project", "assets", "consumer"))
        self.consumer.mkdir()
        self.frozen = bundle_fixture(self.consumer)
        metadata = self.consumer / ("node_modules/fixture/.github/workflows/" + "synthetic-" * 12 + ".yml")
        metadata.parent.mkdir(parents=True)
        metadata.write_text("synthetic published package metadata")
        self.frozen["files"] = prepare.consumer.inventory(self.consumer)
        self.consumer_digest = save_manifest(self.consumer, self.frozen)
        for name in bundle.METADATA:
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic source", encoding="utf-8")
        for name in bundle.DIRECTORIES:
            path = self.project / name
            path.mkdir(parents=True, exist_ok=True)
            (path / "synthetic.txt").write_text("synthetic source", encoding="utf-8")
        (self.project / "tools").mkdir()
        (self.project / "tools/synthetic.py").write_text('"""Synthetic source, not executed."""')
        lock = json.loads((self.consumer / "package-lock.json").read_text())
        lock["packages"].update({
            "node_modules/esbuild": {"version": "0.25.0", "dev": True},
            "node_modules/vite": {"version": "6.4.0", "dev": True},
        })
        save(self.project / "package-lock.json", lock)
        # Dependency fixture and source tree share a lock, as actual preparation requires.
        save(self.consumer / "package-lock.json", lock)
        self.frozen["packageLockSha256"] = bundle.sha256(self.consumer / "package-lock.json")
        self.frozen["files"] = prepare.consumer.inventory(self.consumer)
        self.consumer_digest = save_manifest(self.consumer, self.frozen)
        save(self.project / "docs/slam-point-consumer-preparation-evidence.json", {
            "manifestSha256": self.consumer_digest, "nodeBinarySha256": bundle.sha256(self.consumer / "node/bin/node"),
            "nodeVersionPreparedNotExecuted": self.frozen["nodeVersion"],
            "packageLockSha256": self.frozen["packageLockSha256"],
        })
        contents = {
            "api/main.mjs": b"// Synthetic API, not executed\n",
            "web/index.html": b'<script src="/assets/main.js"></script><link href="/assets/main.css">',
            "web/assets/main.js": b"// Synthetic web asset\n", "web/assets/main.css": b"body{color:black}",
        }
        for name, data in contents.items():
            path = self.assets / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.build = {
            "schemaVersion": 1, "purpose": "isolated_live_point_assets_not_target_acceptance",
            "nodeBuildHostVersion": "v22.0.0", "esbuildVersion": "0.25.0", "viteVersion": "6.4.0",
            "environmentFilesLoaded": False, "customEnvironmentVariablesExposed": False,
            "sourceSha256": bundle.source_hashes(self.project),
            "files": {name: {"sha256": bundle.sha256(self.assets / name), "bytes": len(data)}
                      for name, data in contents.items()},
        }
        self.build_digest = save(self.assets / "build-manifest.json", self.build)
        self.addCleanup(patch.stopall)
        patch.object(bundle, "ROOT", self.project).start()
        patch.object(prepare, "ROOT", self.project).start()
        patch.object(prepare.sys, "platform", "linux").start()
        patch.object(prepare.platform, "machine", return_value="x86_64").start()
        patch.object(prepare.os, "geteuid", return_value=1000, create=True).start()

    def prepared(self, name="preparation"):
        args = argparse.Namespace(
            build=self.assets, consumer=self.consumer, output=self.root / name,
            build_manifest_sha256=self.build_digest, consumer_manifest_sha256=self.consumer_digest)
        return args, prepare.prepare(args)

    def test_source_bound_preparation_and_archive_are_deterministic_without_execution(self):
        with patch.object(prepare.consumer.subprocess, "run", side_effect=AssertionError("must not execute")):
            first_args, first = self.prepared()
            second_args, second = self.prepared("second")
        self.assertEqual(first, second)
        self.assertEqual((first_args.output / prepare.ARCHIVE).read_bytes(),
                         (second_args.output / prepare.ARCHIVE).read_bytes())
        self.assertEqual("live_runtime_prepared_not_target_executed", first["result"])
        self.assertTrue(all(value is False for value in first["boundaries"].values()))
        self.assertEqual(1, first["installedPackageCount"])
        self.assertEqual(first["manifestSha256"], prepare.evidence(first_args.output)["manifestSha256"])

    def test_build_manifest_hash_source_change_and_asset_change_are_rejected(self):
        with self.assertRaises(ValueError):
            bundle.verify_build(self.assets, "0" * 64)
        source = self.project / "web/synthetic.txt"
        source.write_text("changed synthetic source")
        with self.assertRaisesRegex(ValueError, "source_bound"):
            bundle.verify_build(self.assets, self.build_digest)
        source.write_text("synthetic source")
        (self.assets / "api/main.mjs").write_text("changed synthetic API")
        with self.assertRaisesRegex(ValueError, "build_assets"):
            bundle.verify_build(self.assets, self.build_digest)

    def test_env_files_private_data_and_cache_are_never_packaged_as_sources(self):
        for name in (".env", "private-profile.json", "synthetic.key", "data.npz"):
            path = self.project / "web" / name
            path.write_text("isolated synthetic privacy fixture")
            with self.subTest(name=name), self.assertRaises(ValueError):
                bundle.source_hashes()
            path.unlink()
        cache = self.project / "web/__pycache__"
        cache.mkdir()
        (cache / "synthetic.pyc").write_bytes(b"synthetic cache")
        self.assertNotIn("web/__pycache__/synthetic.pyc", bundle.source_hashes())

    def test_incomplete_assets_external_or_missing_html_references_are_rejected(self):
        for value in (b'<script src="/assets/absent.js"></script>',
                      b'<script src="https://synthetic.invalid/main.js"></script>', b"<html></html>"):
            (self.assets / "web/index.html").write_bytes(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                bundle.check_assets(self.assets, self.build["files"])

    def test_consumer_manifest_and_node_change_fail_before_output_creation(self):
        args = argparse.Namespace(
            build=self.assets, consumer=self.consumer, output=self.root / "uncreated",
            build_manifest_sha256=self.build_digest, consumer_manifest_sha256="0" * 64)
        with self.assertRaises(ValueError):
            prepare.prepare(args)
        self.assertFalse(args.output.exists())
        args.consumer_manifest_sha256 = self.consumer_digest
        (self.consumer / "node/bin/node").write_bytes(b"changed synthetic binary")
        with self.assertRaises(ValueError):
            prepare.prepare(args)
        self.assertFalse(args.output.exists())

    def test_package_cannot_claim_live_acceptance_or_elevate_readiness(self):
        args, report = self.prepared()
        root = args.output / "bundle"
        original_bytes = (root / bundle.MANIFEST).read_bytes()
        original = json.loads(original_bytes)
        for name in bundle.BOUNDARIES:
            modified = deepcopy(original)
            modified["boundaries"][name] = True
            digest = save(root / bundle.MANIFEST, modified)
            with self.subTest(name=name), self.assertRaises(ValueError):
                bundle.verify_bundle(root, digest)
        (root / bundle.MANIFEST).write_bytes(original_bytes)
        self.assertEqual(report["manifestSha256"], bundle.sha256(root / bundle.MANIFEST))

    def test_unlisted_files_and_changed_dependency_bytes_are_rejected(self):
        args, report = self.prepared()
        root = args.output / "bundle"
        extra = root / "synthetic-extra"
        extra.write_bytes(b"synthetic unlisted member")
        with self.assertRaises(ValueError):
            bundle.verify_bundle(root, report["manifestSha256"])
        extra.unlink()
        (root / "node_modules/fixture/index.js").write_bytes(b"changed synthetic dependency")
        with self.assertRaises(ValueError):
            bundle.verify_bundle(root, report["manifestSha256"])

    def test_changed_or_incomplete_archives_and_mismatched_extraction_digest_are_rejected(self):
        args, report = self.prepared()
        archive = args.output / prepare.ARCHIVE
        with self.assertRaises(ValueError):
            bundle.verify_archive(archive, "0" * 64)
        output = self.root / "not-extracted"
        with self.assertRaises(ValueError):
            bundle.extract(archive, output, "0" * 64, report["manifestSha256"])
        self.assertFalse(output.exists())
        archive.write_bytes(archive.read_bytes()[:-100])
        with self.assertRaises((EOFError, OSError, ValueError)):
            bundle.verify_archive(archive, report["manifestSha256"])

    def test_archive_extracts_exact_members_and_can_be_reverified_against_mirrored_sources(self):
        args, report = self.prepared()
        output = self.root / "extracted"
        result = bundle.extract(args.output / prepare.ARCHIVE, output,
                                report["archiveSha256"], report["manifestSha256"])
        self.assertEqual("live_runtime_extracted_not_executed", result["result"])
        self.assertEqual(report["bundleFiles"], result["bundleFiles"])
        self.assertEqual(report["manifestSha256"],
                         bundle.sha256(output / bundle.MANIFEST))
        bundle.verify_bundle(output, report["manifestSha256"], project=output / "source")

    def test_report_counters_and_preparation_only_flags_are_independently_rechecked(self):
        args, report = self.prepared()
        record = args.output / "preparation-report.json"
        for key, value in (("bundleFiles", True), ("sourceFiles", 9999), ("installedPackageCount", True),
                           ("result", "accepted")):
            save(record, {**report, key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                prepare.evidence(args.output)

    def test_verified_launcher_forces_read_only_private_data_and_packaged_web_then_restores_environment(self):
        args, report = self.prepared()
        trial = argparse.Namespace(operator_confirmed=True, bundle=args.output / "bundle",
                                   manifest_sha256=report["manifestSha256"], output=self.root / "trial")
        with patch.dict(os.environ, {"LINGXI_PORT": "3443"}, clear=True), \
                patch.object(launcher.live_points, "run", return_value={"result": "stopped"}) as run:
            def inspect(arguments):
                self.assertEqual("1", os.environ["LINGXI_POINT_READ_ONLY"])
                self.assertEqual(str(trial.bundle / "web"), os.environ["LINGXI_WEB_ROOT"])
                self.assertEqual(str(trial.output / "data"), os.environ["LINGXI_DATA_DIR"])
                self.assertEqual(trial.bundle / "node/bin/node", arguments.node)
                return {"result": "stopped"}
            run.side_effect = inspect
            self.assertEqual({"result": "stopped"}, launcher.launch(trial))
            self.assertEqual({"LINGXI_PORT": "3443"}, dict(os.environ))

    def test_launcher_requires_fresh_confirmation_and_refuses_default_service_port_or_data_override(self):
        with patch.object(launcher.bundle, "verify_bundle") as verify:
            with self.assertRaisesRegex(ValueError, "fresh_stationary"):
                launcher.launch(argparse.Namespace(operator_confirmed=False))
            verify.assert_not_called()
        args, report = self.prepared()
        trial = argparse.Namespace(operator_confirmed=True, bundle=args.output / "bundle",
                                   manifest_sha256=report["manifestSha256"], output=self.root / "trial")
        for environment in ({"LINGXI_PORT": "3000"}, {"LINGXI_PORT": "0"},
                            {"LINGXI_PORT": "3443", "LINGXI_POINT_READ_ONLY": "0"},
                            {"LINGXI_PORT": "3443", "LINGXI_DATA_DIR": str(self.root / "existing-data")}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                    patch.object(launcher.live_points, "run") as run, self.assertRaises(ValueError):
                launcher.launch(trial)
            run.assert_not_called()
        trial.output = trial.bundle / "uncreated-private-data"
        with patch.dict(os.environ, {"LINGXI_PORT": "3443"}, clear=True), \
                patch.object(launcher.live_points, "run") as run, self.assertRaises(ValueError):
            launcher.launch(trial)
        run.assert_not_called()
        self.assertFalse(trial.output.exists())

    def test_launcher_rechecks_executing_source_tree_before_starting_process_owner(self):
        args, report = self.prepared()
        trial = argparse.Namespace(operator_confirmed=True, bundle=args.output / "bundle",
                                   manifest_sha256=report["manifestSha256"], output=self.root / "trial")
        (self.project / "robot-runtime/python/synthetic.txt").write_text("changed executing source")
        with patch.dict(os.environ, {"LINGXI_PORT": "3443"}, clear=True), \
                patch.object(launcher.live_points, "run") as run, self.assertRaises(ValueError):
            launcher.launch(trial)
        run.assert_not_called()
        self.assertFalse(trial.output.exists())


if __name__ == "__main__":
    unittest.main()
