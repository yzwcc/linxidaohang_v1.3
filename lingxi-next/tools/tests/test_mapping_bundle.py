"""Mapping archive contracts; synthetic assets and nonexecutable ARM64 header."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_live_bundle as fixtures
import live_bundle as bundle
import prepare_live_bundle as prepare
import launch_mapping_bundle as launcher


PROFILE = bundle.MAPPING
ROOT = Path(__file__).resolve().parents[2]
save = fixtures.save


class MappingBundleContracts(unittest.TestCase):
    def setUp(self):
        fixtures.LiveBundleContracts.setUp(self)
        (self.project / "robot-runtime/build-mapping.mjs").write_text("// Synthetic builder\n")
        self.build["purpose"] = PROFILE.build_purpose
        self.build["sourceSha256"] = bundle.source_hashes(self.project, profile=PROFILE)
        self.build_digest = save(self.assets / "build-manifest.json", self.build)

    def prepared(self, name="preparation"):
        args = argparse.Namespace(
            build=self.assets, consumer=self.consumer, output=self.root / name,
            build_manifest_sha256=self.build_digest, consumer_manifest_sha256=self.consumer_digest)
        return args, prepare.prepare(args, profile=PROFILE)

    def extracted(self):
        args, report = self.prepared()
        output = self.root / "extracted"
        result = bundle.extract(args.output / PROFILE.archive, output,
                                report["archiveSha256"], report["manifestSha256"], profile=PROFILE)
        self.assertEqual("mapping_runtime_extracted_not_executed", result["result"])
        self.assertEqual(report["bundleFiles"], result["bundleFiles"])
        return output, report

    def test_mapping_and_read_only_build_profiles_cannot_be_interchanged(self):
        with self.assertRaisesRegex(ValueError, "source_bound"):
            bundle.verify_build(self.assets, self.build_digest)
        bundle.verify_build(self.assets, self.build_digest, profile=PROFILE)
        self.build["purpose"] = bundle.LIVE.build_purpose
        digest = save(self.assets / "build-manifest.json", self.build)
        with self.assertRaisesRegex(ValueError, "source_bound"):
            bundle.verify_build(self.assets, digest, profile=PROFILE)

    def test_mapping_preparation_is_deterministic_and_independently_reverified(self):
        with patch.object(prepare.consumer.subprocess, "run", side_effect=AssertionError("no execution")):
            args, first = self.prepared()
            second_args, second = self.prepared("second")
        self.assertEqual(first, second)
        self.assertEqual((args.output / PROFILE.archive).read_bytes(),
                         (second_args.output / PROFILE.archive).read_bytes())
        self.assertEqual("mapping_runtime_prepared_not_target_executed", first["result"])
        self.assertTrue(all(value is False for value in first["boundaries"].values()))
        self.assertEqual(first["manifestSha256"],
                         prepare.evidence(args.output, profile=PROFILE)["manifestSha256"])

    def test_extracted_package_rechecks_mirrored_sources_and_refuses_tampered_api(self):
        root, report = self.extracted()
        bundle.verify_bundle(root, report["manifestSha256"], project=root / "source", profile=PROFILE)
        with self.assertRaises((ValueError, KeyError)):
            bundle.verify_archive(self.root / "preparation" / PROFILE.archive, report["manifestSha256"])
        (root / "api/main.mjs").write_bytes(b"changed synthetic mapping entrypoint")
        with self.assertRaisesRegex(ValueError, "complete_live_runtime"):
            bundle.verify_bundle(root, report["manifestSha256"], project=root / "source", profile=PROFILE)

    def test_missing_builder_changed_purpose_and_acceptance_flags_are_rejected(self):
        args, report = self.prepared()
        root = args.output / "bundle"
        original = json.loads((root / PROFILE.manifest).read_bytes())
        for name, value in (("purpose", bundle.LIVE.purpose),
                            ("boundaries", {**original["boundaries"], "mappingReady": True})):
            modified = deepcopy(original)
            modified[name] = value
            digest = save(root / PROFILE.manifest, modified)
            with self.subTest(name=name), self.assertRaises(ValueError):
                bundle.verify_bundle(root, digest, profile=PROFILE)
        save(root / PROFILE.manifest, original)
        (root / "source/robot-runtime/build-mapping.mjs").unlink()
        with self.assertRaises(ValueError):
            bundle.verify_bundle(root, bundle.sha256(root / PROFILE.manifest), profile=PROFILE)

    def launch_fixture(self):
        root, report = self.extracted()
        self.config = self.root / "mapping.json"
        self.config.write_text("{}")
        environment = {"LINGXI_PORT": "3443", "LINGXI_PAIRING_CODE_SHA256": "a" * 64}
        for name in ("LINGXI_TLS_KEY", "LINGXI_TLS_CERT"):
            file = self.root / (name + ".synthetic")
            file.write_text("synthetic TLS input; never loaded")
            environment[name] = str(file)
        self.trial = argparse.Namespace(bundle=root, config=self.config, output=self.root / "run",
                                        manifest_sha256=report["manifestSha256"], seconds=30)
        return root, environment

    def arm64(self, root):
        patch.object(bundle, "ROOT", root / "source").start()
        patch.object(launcher.platform, "machine", return_value="aarch64").start()
        patch.object(launcher.sys, "version_info", (3, 10, 0)).start()

    def test_launcher_requires_packaged_executing_sources_and_correct_target(self):
        root, environment = self.launch_fixture()
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(launcher.managed_mapping, "run") as run:
            with self.assertRaisesRegex(ValueError, "extracted_mapping"):
                launcher.launch(self.trial)
            with patch.object(bundle, "ROOT", root / "source"), \
                    patch.object(launcher.platform, "machine", return_value="x86_64"):
                with self.assertRaisesRegex(ValueError, "arm64_humble"):
                    launcher.launch(self.trial)
            run.assert_not_called()
        self.assertFalse(self.trial.output.exists())

    def test_mapping_launcher_uses_owner_and_packaged_assets_without_read_only_override(self):
        root, environment = self.launch_fixture()
        self.arm64(root)
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(launcher.managed_mapping, "run") as run:
            def inspect(args):
                self.assertEqual(root / "api/main.mjs", args.api_bundle)
                self.assertEqual(root / "node/bin/node", args.node)
                self.assertEqual(bundle.sha256(args.api_bundle), args.api_sha256)
                self.assertEqual(str(root / "web"), os.environ["LINGXI_WEB_ROOT"])
                self.assertNotIn("LINGXI_POINT_READ_ONLY", os.environ)
                self.assertEqual(30, args.seconds)
                return 0
            run.side_effect = inspect
            self.assertEqual(0, launcher.launch(self.trial))
            self.assertEqual(environment, dict(os.environ))

    def test_launcher_rejects_default_port_web_data_tls_and_output_overlap_before_owner(self):
        root, environment = self.launch_fixture()
        self.arm64(root)
        changes = [{"LINGXI_PORT": value} for value in ("", "0", "3000", "65536", "-1", "abc")]
        changes += [{"LINGXI_WEB_ROOT": "/other"}, {"LINGXI_DATA_DIR": "/existing"},
                    {"LINGXI_POINT_READ_ONLY": "0"}, {"LINGXI_TLS_KEY": str(root / "api/main.mjs")}]
        with patch.object(launcher.managed_mapping, "run") as run:
            for change in changes:
                with self.subTest(change=change), patch.dict(os.environ, {**environment, **change}, clear=True):
                    with self.assertRaises(ValueError):
                        launcher.launch(self.trial)
            self.trial.output = root / "new"
            with patch.dict(os.environ, environment, clear=True), self.assertRaises(ValueError):
                launcher.launch(self.trial)
            run.assert_not_called()
        self.assertFalse((root / "new").exists())

    def test_extractor_cli_loads_without_inherited_pythonpath(self):
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(ROOT / "robot-runtime/python/prepare_mapping_bundle.py"), "--help"],
            cwd=self.root, env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
            capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("extract", result.stdout)


if __name__ == "__main__":
    unittest.main()
