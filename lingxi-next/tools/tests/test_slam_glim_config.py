"""Offline configuration fixtures, explicitly not real robot calibration."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
UPSTREAM = Path(os.environ.get(
    "LINGXI_GLIM_UPSTREAM_ROOT", ROOT / ".local" / "slam-sources" / "glim"
))
spec = importlib.util.spec_from_file_location("glim_trial_config", ROOT / "tools" / "glim_trial_config.py")
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


def synthetic_profile():
    return json.loads(
        (ROOT / "tools" / "glim-smoke" / "synthetic-profile.json").read_text(encoding="utf-8")
    )


class GlimTrialConfiguration(unittest.TestCase):
    def test_relative_nanoseconds_are_explicit_not_autodetected(self):
        result = config.settings(synthetic_profile())["config_sensors.json"]["sensors"]
        self.assertFalse(result["autoconf_perpoint_times"])
        self.assertTrue(result["perpoint_relative_time"])
        self.assertTrue(result["autoconf_prefer_frame_time"])
        self.assertEqual(1e-9, result["perpoint_time_scale"])
        self.assertFalse(result["global_shutter_lidar"])

    def test_unresolved_example_is_not_a_runnable_sensor_profile(self):
        profile = json.loads((ROOT / "docs" / "glim-sensor-profile.example.json").read_text())
        with self.assertRaisesRegex(ValueError, "not_reviewed"):
            config.settings(profile)

    def test_missing_scales_offsets_and_transform_do_not_get_defaults(self):
        for key in ("accelerationScale", "angularVelocityScale",
                    "imuTimeOffsetSeconds", "cloudTimeOffsetSeconds", "T_lidar_imu"):
            with self.subTest(key=key):
                profile = synthetic_profile()
                profile[key] = None
                with self.assertRaises(ValueError):
                    config.settings(profile)

    def test_unit_and_extrinsic_evidence_are_required(self):
        for key in ("timeEvidence", "imuUnitEvidence", "extrinsicEvidence"):
            profile = synthetic_profile()
            profile[key] = ""
            with self.assertRaises(ValueError):
                config.settings(profile)

    def test_wrong_time_reference_is_not_silently_converted(self):
        profile = synthetic_profile()
        profile["pointTime"]["reference"] = "absolute"
        with self.assertRaisesRegex(ValueError, "point_time_contract"):
            config.settings(profile)

    def test_nonfinite_boolean_zero_and_invalid_rotation_values_fail(self):
        for key, value in (("accelerationScale", float("nan")),
                           ("accelerationScale", 1e-9),
                           ("angularVelocityScale", True), ("angularVelocityScale", 0),
                           ("T_lidar_imu", [0] * 7)):
            profile = synthetic_profile()
            profile[key] = value
            with self.assertRaises(ValueError):
                config.settings(profile)

    def test_unclassified_profile_is_not_mislabeled_as_target_evidence(self):
        for value in (None, "", "validated_robot"):
            profile = synthetic_profile()
            profile["inputClassification"] = value
            with self.assertRaisesRegex(ValueError, "classification"):
                config.settings(profile)

    def test_profile_scope_is_structured_and_version_is_not_a_boolean(self):
        for profile in ([], None, {"schemaVersion": True, "purpose": "offline_glim_trial"}):
            with self.assertRaisesRegex(ValueError, "profile_scope"):
                config.settings(profile)

    def test_cpu_mode_has_no_viewer_extensions_or_native_frame_names(self):
        result = config.settings(synthetic_profile())
        self.assertEqual(
            "config_odometry_cpu.json", result["config.json"]["global"]["config_odometry"]
        )
        ros = result["config_ros.json"]["glim_ros"]
        self.assertEqual([], ros["extension_modules"])
        self.assertFalse(ros["publish_imu2lidar"])
        self.assertEqual("lingxi_trial_map", ros["map_frame_id"])
        self.assertEqual(1.0, ros["playback_speed"])

    def test_generation_does_not_modify_sources_and_does_not_claim_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "source", Path(directory) / "trial"
            (source / "config").mkdir(parents=True)
            for name in config.REQUIRED_CONFIGS:
                (source / "config" / name).write_text("{}\n")
            (source / "LICENSE").write_text("SYNTHETIC TEST LICENSE\n")
            before = {path: path.read_bytes() for path in source.rglob("*") if path.is_file()}
            with patch.object(config, "verify_source", return_value=config.pinned_commit()):
                manifest = config.generate(source, output, synthetic_profile())
            self.assertEqual(before, {path: path.read_bytes() for path in before})
            self.assertFalse(manifest["deploymentApproved"])
            self.assertFalse(manifest["navigationValidated"])
            self.assertFalse(manifest["startsProcesses"])
            self.assertEqual("synthetic_fixture", manifest["inputClassification"])
            self.assertIn("not_target_tuned", manifest["algorithmParameters"])
            self.assertIn("not_target_calibrated", manifest["imuNoiseParameters"])
            self.assertTrue(manifest["requiresNetworkIsolatedExecution"])
            self.assertEqual(9, len(manifest["files"]))

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trial"
            output.mkdir()
            marker = output / "existing"
            marker.write_text("keep")
            with self.assertRaisesRegex(ValueError, "already_exists"):
                config.generate(Path(directory) / "source", output, synthetic_profile())
            self.assertEqual("keep", marker.read_text())

    def test_output_cannot_be_inside_upstream(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            with self.assertRaisesRegex(ValueError, "outside_upstream"):
                config.generate(source, source / "trial", synthetic_profile())

    def test_wrong_upstream_revision_is_rejected(self):
        result = type("Result", (), {"stdout": "0" * 40 + "\n"})()
        with patch.object(config.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(ValueError, "review_baseline"):
                config.verify_source(Path("unused"))

    def test_dirty_upstream_configuration_is_rejected(self):
        result = type("Result", (), {"stdout": config.pinned_commit() + "\n"})()
        dirty = type("Result", (), {"stdout": " M config/config_sensors.json\n"})()
        with patch.object(config.subprocess, "run", side_effect=[result, dirty]):
            with self.assertRaisesRegex(ValueError, "not_clean"):
                config.verify_source(Path("unused"))

    @unittest.skipUnless(UPSTREAM.is_dir(), "optional pinned upstream checkout is not available")
    def test_real_pinned_config_files_with_an_isolated_synthetic_profile(self):
        revision = config.verify_source(UPSTREAM)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "synthetic-only"
            manifest = config.generate(UPSTREAM, output, synthetic_profile())
            self.assertEqual(revision, manifest["upstreamCommit"])
            self.assertEqual("synthetic_fixture", manifest["inputClassification"])
            self.assertFalse(manifest["robotCalibrationValidated"])
            for name in config.REQUIRED_CONFIGS:
                self.assertEqual(
                    (UPSTREAM / "config" / name).read_bytes(), (output / name).read_bytes()
                )
            self.assertEqual(
                (UPSTREAM / "LICENSE").read_bytes(), (output / "UPSTREAM_LICENSE.txt").read_bytes()
            )
            for name, expected in manifest["files"].items():
                self.assertEqual(expected, hashlib.sha256((output / name).read_bytes()).hexdigest())
        self.assertEqual(revision, config.verify_source(UPSTREAM))


if __name__ == "__main__":
    unittest.main()
