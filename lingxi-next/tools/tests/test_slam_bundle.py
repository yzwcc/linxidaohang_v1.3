"""Shared-workspace packaging and launcher contracts using labeled synthetic assets."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
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
HOST_NODE = shutil.which("node")


class SlamBundleContracts(unittest.TestCase):
    def setUp(self):
        fixtures.LiveBundleContracts.setUp(self)
        for name in PROFILE.metadata:
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic source\n")
        for name in PROFILE.directories:
            path = self.project / name
            path.mkdir(parents=True, exist_ok=True)
            (path / "synthetic.txt").write_text("synthetic source\n")
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

    def test_extraction_invalidates_cached_missing_module_paths_before_source_verification(self):
        args, report = self.prepared()
        events, original = [], bundle.verify_bundle

        def verify(*values, **keywords):
            events.append("verified")
            return original(*values, **keywords)

        with patch.object(bundle.importlib, "invalidate_caches", side_effect=lambda: events.append("invalidated")), \
                patch.object(bundle, "verify_bundle", side_effect=verify):
            bundle.extract(args.output / PROFILE.archive, self.root / "cold-extracted",
                           report["archiveSha256"], report["manifestSha256"], profile=PROFILE)
        self.assertEqual(["invalidated", "verified"], events)

    def test_actual_source_inventory_includes_runtime_native_provenance(self):
        sources = bundle.source_hashes(ROOT, profile=PROFILE)
        native = ["tools/glim-replay/offline.cpp", "tools/glim-replay/CMakeLists.txt",
                  "tools/glim-mapping/mapping.cpp", "tools/glim-mapping/CMakeLists.txt"]
        native += ["robot-slam/native-point-output/" + name for name in (
            "point_output.h", "point_output.cpp", "point_output_contract.cpp", "CMakeLists.txt",
            "exports.map", "verify_exports.cmake")]
        native += ["robot-slam/native-acquisition-output/" + name for name in (
            "acquisition.h", "acquisition.cpp", "acquisition_contract.cpp", "CMakeLists.txt", "exports.map")]
        native += list(PROFILE.metadata) + ["robot-navigation/lingxi_navigation/terrain_worker.py",
            "robot-navigation/lingxi_navigation/terrain_process.py", "robot-runtime/python/live_acquisition.py"]
        for name in native:
            with self.subTest(name=name):
                self.assertEqual(bundle.sha256(ROOT / name), sources[name])
        self.assertNotIn("robot-navigation/native-ground/CMakeLists.txt", sources)

    @unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("numpy", "scipy", "json5")),
                         "requires isolated scientific Python dependencies")
    def test_actual_executable_sources_import_from_extracted_package_without_project_pythonpath(self):
        # Only dependency metadata and web/API assets remain synthetic in this package.
        synthetic = {"package-lock.json", "docs/slam-point-consumer-preparation-evidence.json"}
        for name in bundle.source_hashes(ROOT, profile=PROFILE):
            if name not in synthetic:
                destination = self.project / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, destination)
        self.build["sourceSha256"] = bundle.source_hashes(self.project, profile=PROFILE)
        self.build_digest = save(self.assets / "build-manifest.json", self.build)
        root, _ = self.extracted()
        source = root / "source"
        script = f"""
import hashlib, json, pathlib, runpy, sys
source = pathlib.Path({str(source)!r})
runpy.run_path(str(source / 'robot-runtime/python/launch_slam_bundle.py'))
import managed_slam, slam_service, localization_service, live_acquisition, acquisition_bundle, check_ground_native
from lingxi_navigation import terrain_worker, terrain_process
from lingxi_slam import acquisition_output
modules = (managed_slam, slam_service, localization_service, live_acquisition, acquisition_bundle,
           check_ground_native, terrain_worker, terrain_process, acquisition_output)
assert all(pathlib.Path(module.__file__).resolve().is_relative_to(source) for module in modules)
assert live_acquisition.ROOT == source
assert acquisition_bundle.ROOT == source
hashes = acquisition_bundle.source_hashes()
for name in live_acquisition.SOURCES:
    hashes[name] = hashlib.sha256((source / name).read_bytes()).hexdigest()
assert not any(str({str(ROOT)!r}) == entry for entry in sys.path)
print(json.dumps({{'modules': len(modules), 'sourceFiles': len(hashes), 'nativeExecuted': False}}))
"""
        result = subprocess.run([sys.executable, "-I", "-B", "-c", script], cwd=self.root,
            capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(9, report["modules"])
        self.assertGreater(report["sourceFiles"], 20)
        self.assertFalse(report["nativeExecuted"])

    @unittest.skipUnless(HOST_NODE, "requires a host Node interpreter, never ARM64 execution")
    def test_python_and_javascript_share_all_profile_source_inventories(self):
        builder = (ROOT / "robot-runtime/build-live.mjs").as_uri()
        for name, profile in (("point", bundle.LIVE), ("mapping", bundle.MAPPING), ("slam", PROFILE)):
            script = (f"import {{sourceHashes}} from {json.dumps(builder)}; "
                      f"console.log(JSON.stringify(await sourceHashes({json.dumps(str(ROOT))}, {json.dumps(name)})));")
            result = subprocess.run([HOST_NODE, "--input-type=module", "-e", script], cwd=self.root,
                capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(bundle.source_hashes(ROOT, profile=profile), json.loads(result.stdout))

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

    def test_launcher_forwards_explicit_external_sidecar_to_validation_without_copying_it(self):
        root, environment, trial = self.fixture()
        trial.acquisition_settings = self.root / "external-acquisition.json"
        trial.acquisition_settings.write_text("synthetic operator-only sidecar")
        trial.acquisition_settings_sha256 = bundle.sha256(trial.acquisition_settings)
        trial.check_only = True
        managed = SimpleNamespace(run=Mock(), validate=Mock())
        with patch.object(bundle, "ROOT", root / "source"), patch.object(launcher.platform, "machine", return_value="aarch64"), \
                patch.object(launcher.sys, "version_info", (3, 10, 0)), patch.dict(os.environ, environment, clear=True), \
                patch.dict(sys.modules, {"managed_slam": managed}):
            self.assertEqual(0, launcher.launch(trial))
            managed.validate.assert_called_once_with(trial)
            trial.acquisition_settings = root / "source/package.json"
            with self.assertRaisesRegex(ValueError, "outside_runtime"):
                launcher.launch(trial)
        managed.run.assert_not_called()
        self.assertFalse(trial.output.exists())
        self.assertFalse((root / "external-acquisition.json").exists())

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
