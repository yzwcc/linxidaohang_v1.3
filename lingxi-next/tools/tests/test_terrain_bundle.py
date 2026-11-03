"""Synthetic receipts and ELF headers only; no ARM64 or robot acceptance."""
from copy import deepcopy
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "tools"), str(Path(__file__).parent)]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy", "json5"))
if AVAILABLE:
    import terrain_bundle as component
    import live_acquisition as live
    import test_live_acquisition as live_fixture
    import test_terrain_target_trial as target_fixture
    from test_ground import spec


class TargetFixture:
    def prepare_target(self):
        trial = target_fixture.TerrainTargetTrial()
        trial.setUp()
        self.addCleanup(trial.doCleanups)
        self.trial = trial
        for name in component.CURRENT_SOURCES:
            trial.manifest["sourceSha256"][name] = live.digest((ROOT / name).read_bytes())
        trial.write_manifest()
        self.selection = dict(root=str(trial.root), manifestSha256=live.digest((trial.root / component.probe.MANIFEST).read_bytes()),
            report=str(trial.home / "receipt.json"))
        self.receipt = dict(result=component.probe.RESULT, trialManifestSha256=self.selection["manifestSha256"],
            privateRemoteDirectory="/home/synthetic/lingxi-next-validation-arm64-terrain-fixture",
            probeExitCode=0, privateOutputSha256="b" * 64, targetProbe=trial.result())
        self.write_receipt()
        self.ground, self.octomap = [dict(library=str(trial.root / "native" / name),
            sha256=trial.manifest["files"]["native/" + name]["sha256"]) for name in component.probe.LIBRARIES]
        self.ground["declaration"] = dict(sensor_height_metres=.6, ground_z_metres=0.)
        self.octomap["dependencies"] = {name: trial.manifest["files"]["native/lib/" + name]["sha256"]
            for name in component.probe.DEPENDENCIES}

    def write_receipt(self):
        path = Path(self.selection["report"])
        path.write_text(json.dumps(self.receipt), encoding="utf-8")
        self.selection["reportSha256"] = live.digest(path.read_bytes())

    def verify(self):
        return component.verify_selection(self.selection, self.ground, self.octomap)

    def target_terrain(self):
        return dict(geometry=dict(metres_per_raw_unit=1., declared_from_raw=[
            1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]),
            projection=asdict(spec()), ground=self.ground, octomap=self.octomap, targetTrial=self.selection)


@unittest.skipUnless(AVAILABLE, "requires isolated numerical Python runtime")
class TerrainBundleTests(TargetFixture, unittest.TestCase):
    def setUp(self):
        self.prepare_target()

    def test_read_only_selection_never_loads_native_or_executes_bundled_tests(self):
        original = {path: path.read_bytes() for path in self.trial.root.rglob("*") if path.is_file()}
        with patch.object(component.probe.ctypes, "CDLL") as native, patch.object(component.probe, "run_contracts") as contracts:
            self.verify()
        native.assert_not_called()
        contracts.assert_not_called()
        self.assertEqual(original, {path: path.read_bytes() for path in original})
        self.assertTrue(all(value is False for value in self.receipt["targetProbe"]["boundaries"].values()))

    def test_missing_report_or_changed_report_bytes_rejected(self):
        path = Path(self.selection["report"])
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.verify()
        path.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "pinned_bounded"):
            self.verify()

    def test_manifest_digest_and_bundled_library_mutation_rejected(self):
        original = self.selection["manifestSha256"]
        self.selection["manifestSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "pinned_bounded"):
            self.verify()
        self.selection["manifestSha256"] = original
        with Path(self.ground["library"]).open("ab") as stream:
            stream.write(b"mutation")
        with self.assertRaisesRegex(ValueError, "unchanged_terrain_trial"):
            self.verify()

    def test_failed_running_partial_or_boolean_exit_receipts_rejected(self):
        original = deepcopy(self.receipt)
        for changes in ({"result": "running"}, {"result": "failed_or_interrupted"}, {"probeExitCode": 1},
                {"probeExitCode": False}, {"trialManifestSha256": "0" * 64},
                {"privateOutputSha256": ""}, {"errorType": "TimeoutError"},
                {"privateRemoteDirectory": "/root/trial"}):
            with self.subTest(changes=changes):
                self.receipt = {**original, **changes}
                self.write_receipt()
                with self.assertRaisesRegex(ValueError, "successful_terrain_target"):
                    self.verify()

    def test_incomplete_target_results_and_physical_claims_rejected(self):
        original = deepcopy(self.receipt["targetProbe"])
        for changes in ({"executedContracts": original["executedContracts"][:-1]},
                {"target": {"machine": "x86_64"}}, {"checks": {**original["checks"], "all21Contracts": 1}},
                {"boundaries": {**original["boundaries"], "navigationReady": True}},
                {"mappedLibrariesSha256": {}}, {"probeSeconds": True}):
            with self.subTest(changes=changes):
                self.receipt["targetProbe"] = {**original, **changes}
                self.write_receipt()
                with self.assertRaisesRegex(ValueError, "complete_source_bound"):
                    self.verify()

    def test_only_current_production_sources_are_accepted(self):
        name = component.CURRENT_SOURCES[-2]
        self.trial.manifest["sourceSha256"][name] = "0" * 64
        self.trial.write_manifest()
        self.selection["manifestSha256"] = live.digest((self.trial.root / component.probe.MANIFEST).read_bytes())
        with self.assertRaisesRegex(ValueError, "current_production_terrain"):
            self.verify()

    def test_selected_paths_and_dependencies_must_be_the_tested_bundle(self):
        original = self.ground.copy()
        foreign = self.trial.home / "same-bytes.so"
        shutil.copyfile(self.ground["library"], foreign)
        for changes in ({"library": str(foreign)}, {"sha256": "0" * 64}):
            self.ground = {**original, **changes}
            with self.assertRaisesRegex(ValueError, "same_target_tested_terrain_libraries"):
                self.verify()
        self.ground = original
        self.octomap["dependencies"][component.probe.DEPENDENCIES[0]] = "0" * 64
        with self.assertRaisesRegex(ValueError, "same_target_tested_terrain_dependencies"):
            self.verify()

    def test_symlinked_report_rejected(self):
        alias = self.trial.home / "report-alias.json"
        alias.symlink_to(self.selection["report"])
        self.selection["report"] = str(alias)
        with self.assertRaisesRegex(ValueError, "direct_absolute"):
            self.verify()

    def test_selection_shape_rejected_before_native_or_report_load(self):
        original = self.selection
        for value in (None, {}, {**original, "navigationReady": True}):
            self.selection = value
            with self.assertRaisesRegex(ValueError, "explicit_terrain_target_selection"):
                self.verify()

    def test_source_change_during_preflight_is_rejected(self):
        sources = component.source_hashes()
        with patch.object(component, "source_hashes", side_effect=[sources, {**sources, "mutated": "0" * 64}]):
            with self.assertRaisesRegex(ValueError, "unchanged_terrain_target_selection"):
                self.verify()


if AVAILABLE:
    @unittest.skipUnless(AVAILABLE, "requires isolated numerical Python runtime")
    class LiveTargetTerrainTests(live_fixture.SettingsFixture, TargetFixture, unittest.TestCase):
        def setUp(self):
            self.prepare()
            self.prepare_target()
            self.value["terrain"] = self.target_terrain()
            self.write()

        def test_selected_inputs_reach_same_passive_native_paths_without_host_reports(self):
            with patch("check_ground_native.verified_build") as host_build:
                settings = self.settings()
            host_build.assert_not_called()
            self.assertEqual(self.ground, settings.value["terrain"]["ground"])
            self.assertEqual(self.octomap, settings.value["terrain"]["octomap"])
            self.assertEqual((1., .6), (settings.terrain[0].metres_per_raw_unit, settings.terrain[2].sensor_height_metres))
            self.assertEqual(self.selection["manifestSha256"], settings.frozen["terrainTrialManifestSha256"])
            self.assertEqual(self.selection["reportSha256"], settings.frozen["terrainTargetReportSha256"])
            self.assertFalse((self.trial.root / "native/build-report.json").exists())
            self.assertEqual(settings.frozen, settings.verify())

        def test_parent_child_preflight_rejects_changed_receipt(self):
            settings = self.settings()
            self.assertIn("--acquisition-integrity-sha256", settings.child_arguments())
            with Path(self.selection["report"]).open("ab") as stream:
                stream.write(b" ")
            with self.assertRaisesRegex(ValueError, "pinned_bounded"):
                settings.child_arguments()

        def test_mixed_host_and_target_provenance_or_missing_receipt_rejected(self):
            self.value["terrain"]["ground"]["buildReportSha256"] = "0" * 64
            self.write()
            with self.assertRaisesRegex(ValueError, "explicit_declared_terrain_native_builds"):
                self.settings()
            del self.value["terrain"]["ground"]["buildReportSha256"]
            Path(self.selection["report"]).unlink()
            self.write()
            with self.assertRaises(FileNotFoundError):
                self.settings()

        def test_ros_selection_stays_private_and_requires_same_terrain_receipt(self):
            self.value["rosOutput"] = {"mode": "private_declared_terrain"}
            self.write()
            settings = self.settings()
            self.assertEqual("private_declared_terrain", settings.frozen["rosOutputMode"])
            self.assertIn("robot-runtime/python/terrain_bundle.py", settings.frozen["sourceSha256"])
            self.assertNotIn("navigationReady", settings.frozen)
            self.assertEqual(spec(), settings.terrain[1])

        def test_live_factory_consumes_target_paths_and_pause_retires_terrain(self):
            case = live_fixture.CompositionTests()
            case.setUp()
            self.addCleanup(case.doCleanups)
            case.value["terrain"] = self.target_terrain()
            case.write()
            calls = []
            class Process(live_fixture.FakeProcess):
                failure = "synthetic_terrain_failure"
                @property
                def cleanup_complete(self):
                    return self.closed
            def process(**kwargs):
                calls.append(kwargs)
                return Process(case.release, case.terrain_calls)
            self.enterContext(patch.object(live, "TerrainProcess", side_effect=process))
            case.composition = live.AcquisitionComposition(case.settings(), case.session, case.root)
            self.addCleanup(case.composition.finish)
            self.addCleanup(case.release.set)
            case.native = case.composition.factory(case.Native)
            self.addCleanup(case.native.close)
            live_fixture.wait_for(lambda: case.composition.lane.snapshot().state == "ready")
            case.frame()
            case.frame()
            case.command("seed")
            for _ in range(3):
                case.frame()
            live_fixture.wait_for(lambda: case.composition.source.snapshot().grid is not None)
            self.assertEqual(1, len(calls))
            self.assertEqual(Path(self.ground["library"]), calls[0]["ground_library"])
            self.assertEqual(Path(self.octomap["library"]), calls[0]["octomap_library"])
            self.assertEqual(self.octomap["dependencies"], calls[0]["dependencies"])
            case.command("pause")
            self.assertIsNone(case.composition.terrain_snapshot().grid)
            case.native.close()
            report = case.composition.finish()
            live.verify_terminal(report, case.composition.settings.frozen, estimated_poses=case.frames)
            self.assertTrue(report["terrainChildCleaned"])
            self.assertFalse(report["navigationReady"])


if __name__ == "__main__":
    unittest.main()
