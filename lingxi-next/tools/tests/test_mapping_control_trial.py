"""Architecture and collection contracts; no robot contact or native calls."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from run_mapping_control_trial import SOURCE_NAMES, recorded_inputs, stage_runtime
from run_mapping_arm64_trial import validate_result
from run_saved_map_arm64_localization import validate_check


class MappingTrialInputs(unittest.TestCase):
    def test_saved_map_localization_requires_current_sources_and_complete_cleanup(self):
        names = ("robot-runtime/python/controlled_localization.py",
                 "robot-runtime/python/managed_localization.py",
                 "robot-runtime/python/localization_owner.py",
                 "tools/check_managed_localization_runtime.py")
        collection = {"uploadedFiles": {name: {"sha256": "a" * 64} for name in names},
                      "summary": {"runs": [{"completedMessages": 12}]}}
        check = {"result": "managed_recorded_localization_https_checked",
                 "receiptSha256": "b" * 64, "apiBundleSha256": "c" * 64,
                 "sourceSha256": {name: "a" * 64 for name in names},
                 "pauseConfirmed": True, "stopConfirmed": True, "staleGenerationRejected": True,
                 "singleNativeWorker": True, "motionCommandsSent": False,
                 "localizationReady": False, "navigationReady": False,
                 "mapProfileAssociationVerified": False, "completedMessages": 12, "seedRequests": 1,
                 "apiProcess": {"exitCode": 0, "forcedTermination": False,
                                "processAlive": False, "groupAlive": False},
                 "nativeProcess": {"state": "stopped", "exitCode": 0, "forcedTermination": False,
                                   "cleanupComplete": True, "processAlive": False, "monitorAlive": False}}
        validate_check(check, collection, "c" * 64, "b" * 64)
        for changes in ({"sourceSha256": {}}, {"nativeProcess": {}}, {"apiProcess": {}}, {"completedMessages": 0},
                        {"navigationReady": True}, {"receiptSha256": "d" * 64},
                        {"apiBundleSha256": "e" * 64}, {"seedRequests": 0},
                        {"sourceSha256": {name: "f" * 64 for name in names}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_check({**check, **changes}, collection, "c" * 64, "b" * 64)

    def test_arm64_staging_preserves_required_point_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            for name in ("tools", "robot-slam", "robot-runtime/python", "docs"):
                (source / name).mkdir(parents=True)
            for name in ("slam-stream-input-evidence.json", "slam-point-arm64-target-evidence.json"):
                (source / "docs" / name).write_bytes(b'{"synthetic":true}')
            for arm64 in (False, True):
                target = Path(temporary) / str(arm64)
                stage_runtime(source, target, arm64=arm64)
                self.assertEqual(b'{"synthetic":true}', (target / "docs/slam-stream-input-evidence.json").read_bytes())
                self.assertEqual(arm64, (target / "docs/slam-point-arm64-target-evidence.json").exists())

    def test_architecture_requires_explicit_matching_recorded_configuration(self):
        x64 = {"inputMode": "recorded", **dict.fromkeys(
            ("work", "adapter", "mappingAdapter", "bundle", "reference"), "/synthetic")}
        arm = {"platform": "arm64-humble", "inputMode": "recorded", **dict.fromkeys(
            ("root", "coreRoot", "mappingRoot", "reference", "pointAdapter"), "/synthetic")}
        for config, machine, count in ((x64, "x86_64", 5), (arm, "aarch64", 5)):
            roots, profile = recorded_inputs(config, machine)
            self.assertEqual(count, len(roots))
            self.assertEqual(Path("/synthetic/private-profile.json"), profile)
            with self.assertRaises(ValueError):
                recorded_inputs({**config, "inputMode": "live"}, machine)
            with self.assertRaises(ValueError):
                recorded_inputs(config, "other")

    def test_collected_result_requires_current_sources_architecture_and_provenance(self):
        files = {name: {"sha256": "a" * 64} for name in SOURCE_NAMES}
        report = {
            "result": "recorded_native_service_api_save_discard_verified",
            "machine": "aarch64", "inputMode": "recorded", "serviceExitCode": 0,
            "socketRemoved": True, "probeSha256": "b" * 64,
            "savedMapDirectory": "c" * 32, "motionCommandsSent": False,
            "sourceSha256": {name: "a" * 64 for name in SOURCE_NAMES},
            "sensorProvenance": {"receiptSchemaVersion": 2, "freshReloadMatches": True,
                                 "currentConfigurationMatches": True, "missingOrMismatchedRejected": 4},
        }
        validate_result(report, files, "b" * 64)
        for changes in ({"machine": "x86_64"}, {"socketRemoved": False},
                        {"sourceSha256": {"source.py": "d" * 64}}, {"sourceSha256": {}},
                        {"sensorProvenance": {}}, {"savedMapDirectory": "../elsewhere"},
                        {"motionCommandsSent": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_result({**report, **changes}, files, "b" * 64)


if __name__ == "__main__":
    unittest.main()
