"""Shared-workspace packaging and launcher contracts using labeled synthetic assets."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_live_bundle as fixtures
import live_bundle as bundle
import prepare_live_bundle as prepare
import launch_slam_bundle as launcher

PROFILE = bundle.SLAM
save = fixtures.save
ROOT = Path(__file__).resolve().parents[2]


class SlamBundleContracts(unittest.TestCase):
    def setUp(self):
        fixtures.LiveBundleContracts.setUp(self)
        (self.project / "robot-runtime/build-slam.mjs").write_text("// Synthetic builder\n")
        self.build["purpose"] = PROFILE.build_purpose
        self.build["sourceSha256"] = bundle.source_hashes(self.project, profile=PROFILE)
        self.build_digest = save(self.assets / "build-manifest.json", self.build)

    def prepared(self, name="preparation"):
        args = argparse.Namespace(build=self.assets, consumer=self.consumer, output=self.root / name,
            build_manifest_sha256=self.build_digest, consumer_manifest_sha256=self.consumer_digest)
        return args, prepare.prepare(args, profile=PROFILE)

    def extracted(self):
        args, report = self.prepared()
        root = self.root / "extracted"
        result = bundle.extract(args.output / PROFILE.archive, root, report["archiveSha256"],
                                report["manifestSha256"], profile=PROFILE)
        self.assertEqual("slam_runtime_extracted_not_executed", result["result"])
        return root, report

    def test_profiles_remain_distinct_and_preparation_is_deterministic(self):
        for profile in (bundle.LIVE, bundle.MAPPING):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                bundle.verify_build(self.assets, self.build_digest, profile=profile)
        first, report = self.prepared()
        second, repeated = self.prepared("second")
        self.assertEqual(report, repeated)
        self.assertEqual((first.output / PROFILE.archive).read_bytes(), (second.output / PROFILE.archive).read_bytes())
        self.assertEqual(report["manifestSha256"], prepare.evidence(first.output, profile=PROFILE)["manifestSha256"])
        self.assertTrue(all(value is False for value in report["boundaries"].values()))

    def test_extracted_inventory_binds_shared_builder_api_and_mirrored_source(self):
        root, report = self.extracted()
        manifest = bundle.verify_bundle(root, report["manifestSha256"], project=root / "source", profile=PROFILE)
        self.assertIn("robot-runtime/build-slam.mjs", manifest["sourceSha256"])
        (root / "api/main.mjs").write_bytes(b"changed synthetic API asset")
        with self.assertRaises(ValueError):
            bundle.verify_bundle(root, report["manifestSha256"], project=root / "source", profile=PROFILE)

    def fixture(self):
        root, report = self.extracted()
        config = self.root / "external-config.json"
        config.write_text("{}")
        maps = self.root / "maps"
        maps.mkdir(mode=0o700)
        environment = {"LINGXI_PORT": "3443", "LINGXI_PAIRING_CODE_SHA256": "a" * 64}
        for name in ("LINGXI_TLS_KEY", "LINGXI_TLS_CERT"):
            path = self.root / (name + ".synthetic")
            path.write_text("synthetic TLS fixture, not loaded")
            environment[name] = str(path)
        trial = argparse.Namespace(bundle=root, manifest_sha256=report["manifestSha256"], config=config,
            maps=maps, output=self.root / "run", seconds=None, check_only=False)
        return root, environment, trial

    def test_launcher_uses_packaged_assets_persistent_maps_and_restores_environment(self):
        root, environment, trial = self.fixture()
        managed = SimpleNamespace(run=Mock(return_value=0), validate=Mock())
        with patch.object(bundle, "ROOT", root / "source"), patch.object(launcher.platform, "machine", return_value="aarch64"), \
                patch.object(launcher.sys, "version_info", (3, 10, 0)), patch.dict(os.environ, environment, clear=True), \
                patch.dict(sys.modules, {"managed_slam": managed}):
            managed.run.side_effect = lambda args: self.assertEqual(str(root / "web"), os.environ["LINGXI_WEB_ROOT"]) or 0
            self.assertEqual(0, launcher.launch(trial))
            self.assertEqual(root / "node/bin/node", trial.node)
            self.assertIsNone(trial.seconds)
            self.assertEqual(environment, dict(os.environ))
            trial.check_only = True
            self.assertEqual(0, launcher.launch(trial))
        managed.run.assert_called_once()
        managed.validate.assert_called_once()
        self.assertFalse(trial.output.exists())

    def test_launcher_requires_packaged_executing_source_before_any_runtime(self):
        _, environment, trial = self.fixture()
        with patch.dict(os.environ, environment, clear=True), self.assertRaisesRegex(ValueError, "extracted_slam"):
            launcher.launch(trial)
        self.assertFalse(trial.output.exists())

    def test_launcher_rejects_reserved_port_and_conflicting_runtime_overrides(self):
        root, environment, trial = self.fixture()
        changes = [{"LINGXI_PORT": value} for value in ("0", "3000", "03443", "65536")]
        changes += [{"LINGXI_WEB_ROOT": "/other"}, {"LINGXI_DATA_DIR": "/other"}]
        with patch.object(bundle, "ROOT", root / "source"), patch.object(launcher.platform, "machine", return_value="aarch64"), \
                patch.object(launcher.sys, "version_info", (3, 10, 0)):
            for change in changes:
                with self.subTest(change=change), patch.dict(os.environ, {**environment, **change}, clear=True), \
                        self.assertRaises(ValueError):
                    launcher.launch(trial)
        self.assertFalse(trial.output.exists())

    def test_cli_entrypoints_load_without_project_pythonpath(self):
        for name in ("prepare_slam_bundle", "launch_slam_bundle", "slam_deploy"):
            result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "robot-runtime/python" / (name + ".py")),
                                     "--help"], capture_output=True, text=True, timeout=15, check=False)
            self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
