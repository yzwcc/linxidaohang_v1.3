"""Synthetic bundle and host-identity contracts; no robot or network in tests."""
import ast
import importlib.util
import json
from pathlib import Path, PurePosixPath
import shlex
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        spec.loader.exec_module(value)
    return value


probe = module("slam_target_probe")
runner = module("run_slam_target_trial")


class TargetBundle(unittest.TestCase):
    def fixture(self, root):
        (root / "config").mkdir()
        profile = root / "config/trial-manifest.json"
        profile.write_text(json.dumps({"inputClassification": "synthetic_fixture"}))
        manifest = {"purpose": "synthetic_arm64_loading_trial", "inputClassification": "synthetic_fixture",
                    "navigationValidated": False, "files": {
                        "config/trial-manifest.json": {"bytes": profile.stat().st_size,
                                                     "sha256": probe.sha256(profile)},
                    }}
        (root / "bundle-manifest.json").write_text(json.dumps(manifest))
        return manifest

    def test_synthetic_bundle_integrity_is_checked_before_upload_or_load(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.fixture(root)
            self.assertEqual(manifest, probe.verify_bundle(root))

    def test_changed_content_is_not_silently_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            (root / "config/trial-manifest.json").write_text("different")
            with self.assertRaisesRegex(ValueError, "integrity_mismatch"):
                probe.verify_bundle(root)

    def test_real_sensor_classification_is_not_allowed_in_target_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.fixture(root)
            manifest["inputClassification"] = "target_sensor_profile"
            (root / "bundle-manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "explicit_synthetic"):
                probe.verify_bundle(root)

    def test_undeclared_data_file_is_not_transferred(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            (root / "unexpected-map.bin").write_bytes(b"synthetic-test-data")
            with self.assertRaisesRegex(ValueError, "undeclared"):
                probe.verify_bundle(root)

    def test_trial_member_path_must_stay_relative(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.fixture(root)
            manifest["files"]["../outside"] = {"bytes": 0, "sha256": "unused"}
            (root / "bundle-manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "relative_bundle"):
                probe.verify_bundle(root)

    def test_target_probe_does_not_execute_on_a_non_arm64_machine(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(probe.platform, "machine", return_value="x86_64"):
            with self.assertRaisesRegex(ValueError, "clean_arm64"):
                probe.probe(Path(directory))

    def test_foreign_library_provider_is_not_reported_as_isolated_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {"files": {"lib/libglim.so": {}}}
            with self.assertRaisesRegex(ValueError, "outside_isolated"):
                probe.mapped_libraries(root, manifest, "000-111 r-xp 0 0 1 /usr/lib/libglim.so\n")

    def test_absent_modules_do_not_count_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "not_observed"):
                probe.mapped_libraries(Path(directory), {"files": {}}, "")


class TargetLauncher(unittest.TestCase):
    def test_explicit_user_directory_is_required(self):
        self.assertEqual("/home/synthetic/lingxi-next-validation-arm64-synthetic-a",
                         str(runner.target_directory("/home/synthetic", "lingxi-next-validation-arm64-synthetic-a")))
        for name in ("/tmp/trial", "../trial", "generic-folder", "lingxi-next-validation-arm64-a b"):
            with self.assertRaises(ValueError):
                runner.target_directory("/home/synthetic", name)
        for home in ("/", "/root", "relative"):
            with self.assertRaises(ValueError):
                runner.target_directory(home, "lingxi-next-validation-arm64-synthetic-a")

    def test_remote_command_has_bounded_runtime_and_clean_library_environment(self):
        command = shlex.split(runner.probe_command(PurePosixPath("/home/synthetic/lingxi-next-validation-arm64-synthetic-a")))
        self.assertEqual(["timeout", "--signal=TERM", "--kill-after=5s", "120s", "env", "-i"], command[:6])
        self.assertIn("LD_LIBRARY_PATH=/home/synthetic/lingxi-next-validation-arm64-synthetic-a/lib", command)
        self.assertNotIn("sudo", command)

    def test_known_identity_is_required_even_for_a_previously_known_current_ip(self):
        key = NS(get_name=lambda: "ssh-ed25519")
        keys = NS(lookup=lambda identity: {"ssh-ed25519": key} if identity == "synthetic-prior-robot" else None)
        self.assertTrue(runner.matches_identity(keys, "synthetic-prior-robot", key))
        self.assertFalse(runner.matches_identity(keys, "different-robot", key))
        self.assertFalse(runner.matches_identity(keys, "synthetic-prior-robot", NS(get_name=lambda: "ssh-ed25519")))

    def test_existing_local_report_prevents_connection_and_mutation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, "verify_bundle"), patch.object(runner, "connect") as connect:
            path = Path(directory)
            report = path / "report.json"
            report.write_text("existing-result")
            with self.assertRaisesRegex(ValueError, "existing_target_report"):
                runner.run(NS(bundle=path, output=report))
            connect.assert_not_called()
            self.assertEqual("existing-result", report.read_text())

    def test_probe_contains_no_ros_motion_or_network_apis(self):
        text = (ROOT / "tools/slam_target_probe.py").read_text()
        for token in ("rclpy", "cmd_vel", "McLocomotion", "create_publisher", "socket"):
            self.assertNotIn(token, text)
        tree = ast.parse(text)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module)
        self.assertNotIn("paramiko", imports)
        self.assertNotIn("requests", imports)

    def test_public_evidence_does_not_promote_synthetic_loading_to_slam(self):
        evidence = json.loads((ROOT / "docs/slam-arm64-target-evidence.json").read_text())
        self.assertEqual("aarch64", evidence["target"]["machine"])
        self.assertEqual(4, len(probe.MODULES))
        self.assertTrue(set(probe.MODULES).issubset(evidence["mappedBundleLibrariesSha256"]))
        self.assertTrue(all(value is True for value in evidence["checks"].values()))
        self.assertTrue(all(value is False for value in evidence["boundaries"].values()))
        self.assertFalse(evidence["boundaries"]["realDataProcessedOnTarget"])
        self.assertFalse(evidence["boundaries"]["rosWrapperValidated"])
        self.assertNotIn("host", evidence)
        self.assertNotIn("password", evidence)

    def test_exporter_rechecks_synthetic_time_conversion_not_only_success_label(self):
        exporter = module("export_slam_target_evidence")
        actual = {"syntheticProgramResult": {
            "inputClassification": "synthetic_fixture", "nativeModulesLoaded": 3,
            "robotCalibrationValidated": False, "navigationValidated": False,
            "frameSeconds": 1000, "firstSeconds": 0.000332, "lastSeconds": 0.095181,
        }}
        exporter.validate_recorded_probe(actual)
        for key, value in (("nativeModulesLoaded", 0), ("lastSeconds", 95181000),
                           ("robotCalibrationValidated", True), ("navigationValidated", True)):
            changed = json.loads(json.dumps(actual))
            changed["syntheticProgramResult"][key] = value
            with self.assertRaisesRegex(ValueError, "synthetic_execution_contract"):
                exporter.validate_recorded_probe(changed)


if __name__ == "__main__":
    unittest.main()
