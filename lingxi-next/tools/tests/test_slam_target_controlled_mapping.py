"""Synthetic target control contracts; no SSH, ROS, or native SLAM execution."""
import ast
import importlib.util
import json
from pathlib import Path, PurePosixPath
import shlex
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        spec.loader.exec_module(module)
    return module


launcher = load("run_glim_controlled_target")
AVAILABLE = (sys.platform == "linux" and importlib.util.find_spec("numpy") is not None
             and importlib.util.find_spec("json5") is not None)
if AVAILABLE:
    runtime = load("glim_target_controlled_mapping")
    builder = load("prepare_glim_controlled_target")
    host = load("run_glim_controlled_mapping")


class ControlledTargetCommand(unittest.TestCase):
    def test_every_predeclared_condition_has_a_distinct_result(self):
        self.assertEqual(12, len(launcher.CONDITIONS))
        self.assertEqual(12, len({launcher.label(*condition) for condition in launcher.CONDITIONS}))
        for dataset in ("static", "dynamic"):
            for timing in ("header_is_scan_start", "header_is_scan_end"):
                self.assertEqual([1, 2, 3], [repeat for kind, hypothesis, repeat in launcher.CONDITIONS
                                           if (kind, hypothesis) == (dataset, timing)])

    def test_command_keeps_clean_humble_environment_and_bounded_low_priority(self):
        home = PurePosixPath("/home/synthetic")
        root, core = home / "lingxi-next-validation-arm64-control-test", home / "lingxi-next-validation-arm64-core-test"
        command = shlex.split(launcher.command(root, core, home, "dynamic", "header_is_scan_end", root / "result"))
        self.assertEqual(["timeout", "--signal=INT", "--kill-after=10s", "300s", "nice", "-n", "10"], command[:7])
        self.assertIn("-i", command)
        self.assertIn("AMENT_PREFIX_PATH=/opt/ros/humble", command)
        self.assertIn("OMP_NUM_THREADS=2", command)
        self.assertIn("OPENBLAS_NUM_THREADS=2", command)
        index = command.index("python3")
        self.assertEqual([str(root / "tools/glim_target_controlled_mapping.py"), "run"],
                         command[index + 1:index + 3])
        self.assertNotIn("ROS_DOMAIN_ID", " ".join(command))
        for token in ("cmd_vel", "McLocomotion", "systemctl", "sudo", "ros2 run", "ros2 launch"):
            self.assertNotIn(token, " ".join(command))

    def test_bad_conditions_and_foreign_output_are_rejected(self):
        home = PurePosixPath("/home/synthetic")
        root, core = home / "trial", home / "core"
        for dataset, hypothesis, output in (
                ("other", "header_is_scan_start", root / "result"),
                ("static", "automatic", root / "result"),
                ("static", "header_is_scan_start", home / "result")):
            with self.assertRaisesRegex(ValueError, "home_scoped"):
                launcher.command(root, core, home, dataset, hypothesis, output)

    def test_existing_output_or_root_user_is_rejected_before_ssh(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(launcher, "connect") as connect:
            for args in (NS(output=Path(directory), user="ordinary"),
                         NS(output=Path(directory) / "new", user="root")):
                with self.assertRaisesRegex(ValueError, "ordinary_user"):
                    launcher.run(args)
            connect.assert_not_called()

    def test_local_bundle_rejects_tampering_and_relative_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "synthetic.txt").write_text("synthetic")
            entry = {"sha256": launcher.sha256(root / "synthetic.txt"),
                     "bytes": (root / "synthetic.txt").stat().st_size}
            manifest = {"purpose": launcher.PURPOSE, "files": {"synthetic.txt": entry}}
            path = root / "replay-manifest.json"
            path.write_text(json.dumps(manifest))
            self.assertEqual(manifest, launcher.verify_local_bundle(root))
            (root / "synthetic.txt").write_text("changed")
            with self.assertRaisesRegex(ValueError, "unchanged_direct"):
                launcher.verify_local_bundle(root)
            manifest["files"] = {"../outside": entry}
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "unchanged_direct"):
                launcher.verify_local_bundle(root)


@unittest.skipUnless(AVAILABLE, "requires isolated Linux NumPy and JSON5")
class ControlledTargetConfiguration(unittest.TestCase):
    def fixture(self, root):
        baseline = root / "baseline-config"
        baseline.mkdir()
        (baseline / "config_preprocess.json").write_text(
            "/* synthetic */\n" + json.dumps({"preprocess": {"num_threads": 2, "distance_far_thresh": 100}}))
        (baseline / "config_sensors.json").write_text('{"synthetic":true}')
        manifest = {"files": host.config_hashes(baseline), "deploymentApproved": False,
                    "robotCalibrationValidated": False, "navigationValidated": False}
        (baseline / "trial-manifest.json").write_text(json.dumps(manifest))
        with patch.object(host, "PREPROCESS_CONFIG_SHA256", host.sha256(baseline / "config_preprocess.json")):
            control = host.make_control(baseline, root / "config")
        (root / "control-manifest.json").write_text(json.dumps(control))
        self.addCleanup(patch.stopall)
        patch.object(runtime, "BASELINE_MANIFEST", host.sha256(baseline / "trial-manifest.json")).start()
        patch.object(runtime, "EFFECTIVE_MANIFEST", host.sha256(root / "config/trial-manifest.json")).start()
        return control

    def test_actual_control_manifest_constants_match_the_host_evidence(self):
        evidence = json.loads((ROOT / "docs/slam-controlled-mapping-evidence.json").read_text())
        self.assertEqual(runtime.BASELINE_MANIFEST, evidence["baselineConfigurationManifestSha256"])
        self.assertEqual(runtime.EFFECTIVE_MANIFEST, evidence["effectiveConfigurationManifestSha256"])

    def test_target_checks_exact_host_control_without_needing_json5_on_robot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = self.fixture(root)
            self.assertEqual(expected, runtime.verify_control(root))
            self.assertEqual(host.config_hashes(root / "config"), runtime.configuration_files(root / "config"))

    def test_changed_config_or_control_metadata_is_not_accepted(self):
        for field in ("config", "approval", "delta"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                control = self.fixture(root)
                if field == "config":
                    (root / "config/config_sensors.json").write_text('{"synthetic_changed":true}')
                else:
                    if field == "approval":
                        control["productionConfigurationSelected"] = True
                    else:
                        control["semanticParameterChanges"][0]["after"] = 2
                    (root / "control-manifest.json").write_text(json.dumps(control))
                with self.assertRaisesRegex(ValueError, "byte_identical_host"):
                    runtime.verify_control(root)

    def test_config_symlink_and_nested_directory_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            (root / "config/foreign").symlink_to(root / "outside")
            with self.assertRaisesRegex(ValueError, "flat_direct"):
                runtime.configuration_files(root / "config")

    def test_existing_trial_is_preserved_before_environment_or_native_access(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime, "_run") as runner:
            with self.assertRaisesRegex(ValueError, "preserved"):
                runtime.run(NS(output=Path(directory)))
            runner.assert_not_called()

    def test_builder_refuses_existing_or_nonprivate_output_before_reading_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "parent"
            source.mkdir()
            for output in (source, root / "public"):
                with patch.object(builder, "ROOT", root), patch.object(builder, "verify_replay_bundle") as verify:
                    with self.assertRaisesRegex(ValueError, "new_ignored"):
                        builder.prepare(NS(mapping_bundle=source, output=output))
                    verify.assert_not_called()

    def test_actual_library_provenance_includes_both_native_adapters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trial, core = root / "trial", root / "core"
            (trial / "lib").mkdir(parents=True)
            (core / "lib").mkdir(parents=True)
            paths = [core / "lib/libglim.so", core / "lib/libodometry_estimation_cpu.so",
                     core / "lib/libgtsam_points.so.1", trial / "lib/liblingxi_glim_offline.so",
                     trial / "lib/liblingxi_glim_mapping.so"]
            for path in paths:
                path.write_bytes(b"synthetic-not-an-ELF")
            manifest = {"files": {path.relative_to(core).as_posix(): {} for path in paths if path.is_relative_to(core)}}
            text = "\n".join("000-fff r-xp 0000 00:00 0 " + str(path) for path in paths)
            found = runtime.loaded_libraries(trial, core, manifest, text)
            self.assertEqual({path.name for path in paths}, set(found))
            with self.assertRaisesRegex(ValueError, "outside_verified"):
                runtime.loaded_libraries(trial, core, manifest, text.replace(str(paths[-1]), "/foreign/" + paths[-1].name))


class ControlledTargetBoundaries(unittest.TestCase):
    def test_runtime_reuses_humble_reader_and_existing_native_implementation(self):
        text = (ROOT / "tools/glim_target_controlled_mapping.py").read_text()
        ast.parse(text)
        for token in ("class NativeControl(replay.Native)", "replay.run(args, native_factory=NativeControl",
                      "super().close()", 'str(Path(__file__).resolve()), "reload"', "timeout=180"):
            self.assertIn(token, text)
        for token in ("rclpy.init", "create_node", "create_publisher", "create_subscription",
                      "cmd_vel", "McLocomotion", "publish("):
            self.assertNotIn(token, text)
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [item.name for item in node.names] if isinstance(node, ast.Import) else [node.module]
                for module in modules:
                    self.assertNotIn(module.split(".")[0], {"rclpy", "paramiko", "socket", "requests", "json5"})

    def test_previous_whole_map_runner_exporter_and_native_sources_remain_unchanged(self):
        evidence = json.loads((ROOT / "docs/slam-controlled-mapping-evidence.json").read_text())
        for filename, digest in evidence["sourceSha256"].items():
            self.assertEqual(digest, launcher.sha256(ROOT / "tools" / filename))
        self.assertEqual(evidence["exporterSourceSha256"],
                         launcher.sha256(ROOT / "tools/export_glim_controlled_mapping.py"))
        for filename, digest in evidence["nativeMappingSourcesSha256"].items():
            self.assertEqual(digest, launcher.sha256(ROOT / "tools/glim-mapping" / filename))


if __name__ == "__main__":
    unittest.main()
