"""Synthetic bundle/config fixtures only; no ELF execution, sensors or robot."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-runtime/python"))
import async_mapping_bundle as bundle
from mapping_platform import MappingPlatform, configuration


class FullMappingBundleContracts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.baseline, self.root = self.parent / "baseline", self.parent / "full"
        for path in (self.baseline / "config", self.root / "lib"):
            path.mkdir(parents=True)
        self.write(self.baseline / "config/config.json", {"global": {
            "config_sub_mapping": "old-local.json", "config_global_mapping": "old-global.json",
            "config_sensors": "sensors.json"}})
        self.write(self.baseline / "config/sensors.json", {"synthetic": True})
        (self.baseline / "config/UPSTREAM_LICENSE.txt").write_text("synthetic license fixture")
        self.write(self.baseline / "private-profile.json", {"synthetic": True})
        shutil.copytree(self.baseline / "config", self.root / "config")
        self.write(self.root / "config/config.json", {"global": {
            "config_sub_mapping": "config_sub_mapping_cpu.json",
            "config_global_mapping": "config_global_mapping_cpu.json", "config_sensors": "sensors.json"}})
        options = {"enable_imu": True, "enable_optimization": True, "registration_error_factor_type": "VGICP"}
        self.write(self.root / "config/config_sub_mapping_cpu.json", {"sub_mapping": options})
        self.write(self.root / "config/config_global_mapping_cpu.json", {
            "global_mapping": {**options, "randomsampling_rate": 1.0}})
        self.library = self.root / "lib" / bundle.LIBRARY
        self.library.write_bytes(b"\x7fELF\x02\x01" + b"\0" * 12 + (183).to_bytes(2, "little"))
        sources = bundle.source_hashes()
        native = {key: {name.removeprefix(prefix): sources[name] for name in sources if name.startswith(prefix)}
                  for key, prefix in (("nativeSources", "tools/glim-full-mapping/"),
                                      ("asyncSources", "tools/glim-async-mapping/"))}
        self.report = {"result": "arm64_bounded_async_mapper_linked_not_target_executed",
                       "artifact": {"sha256": bundle.sha256(self.library)}, "privateEofSymbolsVerified": True,
                       "inputs": {"mapper": native}}
        self.write(self.root / "build-report.json", self.report)
        first, last = bundle.verify_configuration(self.root, self.baseline)
        self.core = dict.fromkeys(bundle.CORE_LIBRARIES, "b" * 64)
        self.manifest = {"schemaVersion": 1, "engine": bundle.ENGINE, "platform": "linux-aarch64",
                         "sourceSha256": sources, "baselineConfigurationSha256": first,
                         "configurationSha256": last,
                         "profileSha256": bundle.sha256(self.baseline / "private-profile.json"),
                         "coreManifestSha256": "a" * 64, "coreLibrariesSha256": self.core,
                         "targetExecutionValidated": False, "geometryValidated": False, "navigationValidated": False}
        self.refresh()

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def refresh(self):
        self.manifest["files"] = bundle.inventory(self.root)
        self.write(self.root / bundle.MANIFEST, self.manifest)
        self.digest = bundle.sha256(self.root / bundle.MANIFEST)

    def verify(self, **options):
        return bundle.verify_bundle(self.root, self.digest, **{
            "baseline": self.baseline, "core_manifest": "a" * 64, "core_libraries": self.core,
            "platform": "linux-aarch64", **options})

    def test_complete_explicit_fixture_is_verified_without_loading_elf(self):
        self.assertEqual(bundle.ENGINE, self.verify()["engine"])
        self.assertIn("config/UPSTREAM_LICENSE.txt", self.manifest["files"])

    def test_library_or_config_replacement_is_not_accepted(self):
        for path in (self.library, self.root / "config/sensors.json", self.root / "build-report.json"):
            before = path.read_bytes()
            path.write_bytes(before + b" ")
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                self.verify()
            path.write_bytes(before)
        self.verify()

    def test_unrelated_core_or_architecture_or_changed_source_is_rejected(self):
        for options in ({"core_manifest": "c" * 64}, {"core_libraries": {}}, {"platform": "linux-x86_64"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.verify(**options)
        self.manifest["sourceSha256"][bundle.SOURCES[0]] = "d" * 64
        self.refresh()
        with self.assertRaises(ValueError):
            self.verify()

    def test_rehashed_manifest_cannot_disguise_wrong_build_sources(self):
        self.report["inputs"]["mapper"]["asyncSources"]["bounded_worker.hpp"] = "e" * 64
        self.write(self.root / "build-report.json", self.report)
        self.refresh()
        with self.assertRaisesRegex(ValueError, "native_sources"):
            self.verify()

    def test_sensor_changes_and_extra_files_are_not_mapping_configuration(self):
        self.write(self.root / "config/sensors.json", {"synthetic": False})
        with self.assertRaisesRegex(ValueError, "sensor_and_odometry"):
            bundle.verify_configuration(self.root, self.baseline)
        (self.root / "unexpected.txt").write_text("synthetic")
        with self.assertRaises(ValueError):
            bundle.inventory(self.root)

    def test_full_mapper_cannot_be_selected_implicitly_or_by_partial_configuration(self):
        config = {"platform": "arm64-humble", "inputMode": "live", "maxSeconds": 60,
                  **dict.fromkeys(("root", "coreRoot", "mappingRoot", "pointAdapter"), str(self.parent)),
                  "pointManifestSha256": "a" * 64, "hypothesis": "header_is_scan_end"}
        file = self.parent / "runtime.json"
        for selection in (None, {}, {"root": str(self.root)}, {"root": str(self.root), "manifestSha256": "wrong"}):
            self.write(file, {**config, "fullMapping": selection})
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                configuration(file)
        selected = {**config, "fullMapping": {"root": str(self.root), "manifestSha256": self.digest}}
        self.write(file, selected)
        with patch("live_points.environment", return_value={}), patch.object(MappingPlatform, "verify", return_value={}):
            value, _ = configuration(file)
            binding = MappingPlatform(value)
        self.assertTrue(binding.full_mapping)
        self.assertEqual(self.root / "config", binding.config_dir)
        self.assertEqual(self.root / "lib" / bundle.LIBRARY, binding.mapping_library)
        self.assertEqual(self.parent / "config", binding.baseline_config_dir)
        self.assertEqual(self.parent / "lib/liblingxi_glim_mapping.so", binding.legacy_mapping_library)


if __name__ == "__main__":
    unittest.main()
