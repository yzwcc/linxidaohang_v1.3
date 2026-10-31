"""Offline product admission and publication; synthetic fixtures are not mapping evidence."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam")]
import async_mapping_bundle as bundle
from mapping_platform import configuration, MappingPlatform
import test_async_mapping_bundle as bundles
import test_slam_mapping_session as sessions

NUMERICAL = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy"))


class ObservedProductBundle(unittest.TestCase):
    def setUp(self):
        self.fixture = bundles.FullMappingBundleContracts()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        fixture = self.fixture
        fixture.write(fixture.baseline / "config/config_odometry_cpu.json", {
            "odometry_estimation": {"save_imu_rate_trajectory": False, "syntheticUnchanged": 7}})
        fixture.write(fixture.root / "config/config_odometry_cpu.json", {
            "odometry_estimation": {"save_imu_rate_trajectory": True, "syntheticUnchanged": 7}})
        sources = bundle.source_hashes(observed=True)
        native = fixture.report["inputs"]["mapper"]
        acquisition = {"mapping": native, "observations": {
            name.removeprefix("tools/glim-observations/"): sources[name]
            for name in sources if name.startswith("tools/glim-observations/")}}
        fixture.report = {"result": "arm64_observed_mapper_and_fixture_linked_not_target_executed",
            "privateEofSymbolsVerified": True, "artifacts": [{"file": bundle.LIBRARY,
                "sha256": bundle.sha256(fixture.library)}], "inputs": {"observations": acquisition}}
        fixture.write(fixture.root / "build-report.json", fixture.report)
        first, last = bundle.verify_configuration(fixture.root, fixture.baseline, observed=True)
        fixture.manifest.update(engine=bundle.OBSERVED_ENGINE, sourceSha256=sources,
            baselineConfigurationSha256=first, configurationSha256=last)
        fixture.refresh()

    def verify(self, **options):
        return self.fixture.verify(observed=True, **options)

    def test_explicit_observed_arm64_bundle_keeps_all_readiness_false(self):
        value = self.verify()
        self.assertEqual(bundle.OBSERVED_ENGINE, value["engine"])
        for key in ("targetExecutionValidated", "geometryValidated", "navigationValidated"):
            self.assertIs(value[key], False)
        with self.assertRaises(ValueError):
            self.fixture.verify()

    def test_host_report_uses_the_same_source_and_trajectory_contract(self):
        fixture = self.fixture
        fixture.library.write_bytes(b"\x7fELF\x02\x01" + b"\0" * 12 + (62).to_bytes(2, "little"))
        fixture.report = {"result": "observed_mapping_adapter_built_not_geometry_acceptance",
            "librarySha256": bundle.sha256(fixture.library),
            "inputs": fixture.report["inputs"]["observations"]}
        fixture.write(fixture.root / "build-report.json", fixture.report)
        fixture.manifest["platform"] = "linux-x86_64"
        fixture.refresh()
        self.assertEqual(bundle.OBSERVED_ENGINE, self.verify(platform="linux-x86_64")["engine"])

    def test_capture_sources_cannot_be_substituted_by_rehashing_the_outer_manifest(self):
        fixture = self.fixture
        fixture.report["inputs"]["observations"]["observations"]["observation_capture.cpp"] = "e" * 64
        fixture.write(fixture.root / "build-report.json", fixture.report)
        fixture.refresh()
        with self.assertRaisesRegex(ValueError, "capture_native_sources"):
            self.verify()

    def test_trajectory_capture_is_required_without_changing_other_odometry_settings(self):
        fixture = self.fixture
        file = fixture.root / "config/config_odometry_cpu.json"
        for options in ({"save_imu_rate_trajectory": False, "syntheticUnchanged": 7},
                        {"save_imu_rate_trajectory": True, "syntheticUnchanged": 8}):
            fixture.write(file, {"odometry_estimation": options})
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "trajectory_configuration"):
                bundle.verify_configuration(fixture.root, fixture.baseline, observed=True)

    def test_upstream_commented_configuration_is_parsed_without_weakening_strict_metadata(self):
        fixture = self.fixture
        for directory in (fixture.baseline, fixture.root):
            for path in (directory / "config").glob("*.json"):
                path.write_text("// upstream configuration\n" + path.read_text(), encoding="utf-8")
        first, last = bundle.verify_configuration(fixture.root, fixture.baseline, observed=True)
        fixture.manifest.update(baselineConfigurationSha256=first, configurationSha256=last)
        fixture.refresh()
        self.verify()
        with self.assertRaises(json.JSONDecodeError):
            bundle.read_json(fixture.baseline / "config/config_odometry_cpu.json")

    def test_duplicate_configuration_keys_cannot_hide_an_override(self):
        path = self.fixture.root / "config/config_odometry_cpu.json"
        path.write_text('{"odometry_estimation": {}, "odometry_estimation": {}}', encoding="utf-8")
        with self.assertRaises(ValueError):
            bundle.verify_configuration(self.fixture.root, self.fixture.baseline, observed=True)

    def test_configuration_requires_true_explicit_capture_and_does_not_load_a_library(self):
        fixture = self.fixture
        value = {"platform": "arm64-humble", "inputMode": "live", "maxSeconds": 60,
            **dict.fromkeys(("root", "coreRoot", "mappingRoot", "pointAdapter"), str(fixture.parent)),
            "pointManifestSha256": "a" * 64, "hypothesis": "header_is_scan_end",
            "fullMapping": {"root": str(fixture.root), "manifestSha256": fixture.digest,
                            "captureObservations": True}}
        file = fixture.parent / "runtime.json"
        for flag in (False, None, 1, "true"):
            fixture.write(file, {**value, "fullMapping": {**value["fullMapping"], "captureObservations": flag}})
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                configuration(file)
        fixture.write(file, value)
        with patch("live_points.environment", return_value={}), patch.object(MappingPlatform, "verify", return_value={}):
            checked, _ = configuration(file)
            platform = MappingPlatform(checked)
        self.assertTrue(platform.full_mapping)
        self.assertTrue(platform.observed_mapping)

    def test_preparation_cannot_write_inside_any_frozen_input(self):
        from prepare_async_mapping_bundle import prepare
        parent = self.fixture.parent
        args = dict.fromkeys(("build", "native_work", "upstream", "baseline", "cross_work",
                              "core_root", "native_observed_adapter"), parent / "unrelated")
        for name in args:
            options = {**args, name: parent, "output": parent / "new-output", "capture_observations": True}
            with self.subTest(input=name), self.assertRaisesRegex(ValueError, "outside_frozen_inputs"):
                prepare(SimpleNamespace(**options))


@unittest.skipUnless(NUMERICAL, "requires isolated numerical Python runtime")
class ObservedProductPublication(unittest.TestCase):
    def setUp(self):
        self.fixture = sessions.MappingSaveLifecycle()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.session = self.fixture.make(with_provenance=True)
        self.mapper = self.session.mapping
        self.mapper.engine = bundle.ENGINE
        self.mapper.observation_implementation_sha256 = "d" * 64
        self.mapper.observation_report = {"syntheticPublicationFixtureOnly": True}
        self.observations = self.mapper.observation_report
        previous = self.session.reload_map
        self.session.reload_map = lambda saved: {**previous(saved), "observations": self.observations}

    def close(self):
        self.fixture.decision.request_save()
        self.session.close()

    def test_binding_is_complete_before_the_single_ready_receipt_is_published(self):
        def bind(saved, inventory, provenance, implementation, *, pending=False):
            self.assertTrue(pending)
            self.assertFalse((saved / "map-ready.json").exists())
            receipt = json.loads((saved / "map-ready.pending").read_bytes())
            self.assertEqual("d" * 64, receipt["observationImplementationSha256"])
            self.assertEqual(receipt["mapInventory"], inventory)
            self.assertEqual(receipt["sensorProvenance"], provenance)
            self.assertEqual("d" * 64, implementation)
            return {"observations": self.observations}
        with patch("lingxi_slam.observation_binding.bind_observations", side_effect=bind) as binding:
            self.close()
        binding.assert_called_once()
        self.assertEqual("d" * 64, self.session.receipt["observationImplementationSha256"])
        self.assertFalse((self.fixture.output / "map-ready.pending").exists())
        self.assertTrue((self.fixture.output / "map-ready.json").is_file())

    def test_missing_or_different_independent_observations_cannot_publish(self):
        self.observations = None
        with self.assertRaisesRegex(ValueError, "independent_acquisition"):
            self.close()
        self.assertIsNone(self.session.receipt)
        self.assertFalse((self.fixture.output / "map-ready.json").exists())

    def test_binding_failure_cannot_publish_a_map_receipt(self):
        with patch("lingxi_slam.observation_binding.bind_observations", side_effect=ValueError("synthetic")):
            with self.assertRaises(ValueError):
                self.close()
        self.assertFalse((self.fixture.output / "map-ready.json").exists())

    def test_archive_changed_during_binding_cannot_publish(self):
        with patch("lingxi_slam.observation_binding.bind_observations", return_value={"observations": {}}):
            with self.assertRaisesRegex(ValueError, "before_publication"):
                self.close()
        self.assertFalse((self.fixture.output / "map-ready.json").exists())

    def test_discard_never_creates_a_binding_or_ready_receipt(self):
        with patch("lingxi_slam.observation_binding.bind_observations") as binding:
            self.session.close()
        binding.assert_not_called()
        self.assertIsNone(self.session.receipt)


if __name__ == "__main__":
    unittest.main()
