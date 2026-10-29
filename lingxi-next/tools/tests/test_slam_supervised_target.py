"""Synthetic ARM64 binding contracts; never connect to or start a robot process."""
import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import glim_supervised_target as target
from slam_callback_journal import Writer
from verify_slam_capture import sha256
from test_slam_supervised_replay import EXPECTED, child_report, stopped


CONFIGURATION = {"explicit_synthetic_config": "a" * 64}
MANIFEST = "b" * 64


def fixture(root):
    root.mkdir()
    writer = Writer(root / "private-callbacks.cdrjournal")
    for number, kind in enumerate(("imu", "cloud", "imu")):
        writer.append(kind, number * 1_000_000, b"explicitly synthetic, not CDR")
    journal = writer.finish()
    (root / "private-native.log").write_bytes(b"explicitly synthetic native log")
    (root / "private-results.npz").write_bytes(b"explicitly synthetic hash fixture, not native arrays")
    report = {
        "result": target.REFERENCE_RESULT, "mode": "live", "target": {"machine": "aarch64"},
        "timingHypothesis": "header_is_scan_start", "replayManifestSha256": MANIFEST,
        "effectiveConfiguration": CONFIGURATION, "journal": journal,
        "filesSha256": {name: sha256(root / name) for name in target.REFERENCE_FILES},
        "worker": {
            "state": "stopped", "failure": None, "threadAlive": False,
            "queuedMessages": 0, "inFlightMessages": 0,
            "counts": {"completedMessages": 3, "acceptedImu": 2, "acceptedClouds": 1,
                       "discardedMessages": 0, "rejectedMessages": 0},
        },
    }
    (root / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return report


class TargetBindings(unittest.TestCase):
    @unittest.skipIf(sys.platform == "linux" and platform.machine() == "aarch64",
                     "wrong-platform check is for the non-target test host")
    def test_non_target_is_rejected_before_loading_target_dependencies_or_paths(self):
        with self.assertRaisesRegex(ValueError, "arm64_humble"):
            target.inputs(argparse.Namespace(rate=1.))

    def test_child_environment_is_explicit_and_omits_dds_and_parent_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {"SYNTHETIC_SECRET": "never-inherit", "ROS_DOMAIN_ID": "99"}):
                env = target.child_environment(root, root)
        self.assertNotIn("SYNTHETIC_SECRET", env)
        for name in ("ROS_DISTRO", "ROS_DOMAIN_ID", "RMW_IMPLEMENTATION", "LD_PRELOAD"):
            self.assertNotIn(name, env)
        self.assertEqual("2", env["OMP_NUM_THREADS"])
        self.assertEqual("2", env["OPENBLAS_NUM_THREADS"])
        self.assertEqual("/opt/ros/humble", env["AMENT_PREFIX_PATH"])
        self.assertIn("python3.10", env["PYTHONPATH"])
        self.assertIn("aarch64-linux-gnu", env["LD_LIBRARY_PATH"])

    def test_operator_confirmation_is_required_before_input_loading_or_any_process(self):
        for value in (None, False, 1, "true"):
            args = argparse.Namespace(operator_confirmed=value)
            with patch.object(target, "inputs") as inputs, patch.object(target.shared, "ProcessSupervisor") as owner:
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "confirmation"):
                    target.run(args)
            inputs.assert_not_called()
            owner.assert_not_called()

    def test_reference_requires_complete_journal_counts_not_only_a_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "reference"
            original = fixture(root)
            self.assertEqual(original, target.reference_integrity(
                root, MANIFEST, CONFIGURATION, "header_is_scan_start"))
            changes = (
                ("completedMessages", 2), ("acceptedImu", 1), ("acceptedClouds", True),
                ("discardedMessages", 1), ("rejectedMessages", 1),
            )
            for name, value in changes:
                report = json.loads(json.dumps(original))
                report["worker"]["counts"][name] = value
                (root / "report.json").write_text(json.dumps(report), encoding="utf-8")
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "full_journal"):
                    target.reference_integrity(root, MANIFEST, CONFIGURATION, "header_is_scan_start")

    def test_reference_rejects_wrong_machine_configuration_hypothesis_or_private_file_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "reference"
            original = fixture(root)
            for field, value in (("target", {"machine": "x86_64"}), ("mode", "replay"),
                                  ("effectiveConfiguration", {}), ("timingHypothesis", "header_is_scan_end"),
                                  ("result", "failed_or_interrupted")):
                report = {**original, field: value}
                (root / "report.json").write_text(json.dumps(report), encoding="utf-8")
                with self.subTest(field=field), self.assertRaises(ValueError):
                    target.reference_integrity(root, MANIFEST, CONFIGURATION, "header_is_scan_start")
            (root / "report.json").write_text(json.dumps(original), encoding="utf-8")
            (root / "private-native.log").write_bytes(b"changed synthetic log")
            with self.assertRaises(ValueError):
                target.reference_integrity(root, MANIFEST, CONFIGURATION, "header_is_scan_start")

    def test_journal_footer_is_verified_even_if_a_report_checksum_is_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "reference"
            report = fixture(root)
            journal = root / "private-callbacks.cdrjournal"
            journal.write_bytes(journal.read_bytes()[:-1])
            report["filesSha256"][journal.name] = sha256(journal)
            (root / "report.json").write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(ValueError):
                target.reference_integrity(root, MANIFEST, CONFIGURATION, "header_is_scan_start")

    def test_staging_copies_exact_runtime_and_reference_without_changing_frozen_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, reference, output = (root / name for name in ("source", "reference", "output"))
            names = ("tools/glim_supervised_target.py", "robot-slam/lingxi_slam/worker.py")
            for name in names:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"# explicitly synthetic source\n")
            fixture(reference)
            args = argparse.Namespace(reference=reference, output=output, root=root / "frozen",
                                      core_root=root / "core")
            with patch.object(target, "ROOT", source), patch.object(target, "SOURCES", names):
                staged, runtime, copied = target.stage(args)
            self.assertEqual(set(names), set(copied))
            self.assertEqual(args.root, staged.root)
            self.assertEqual(args.core_root, staged.core_root)
            self.assertEqual(reference, args.reference)
            self.assertNotEqual(reference, staged.reference)
            for name in names:
                self.assertEqual((source / name).read_bytes(), (runtime / name).read_bytes())
            for name in ("report.json", *target.REFERENCE_FILES):
                self.assertEqual((reference / name).read_bytes(), (staged.reference / name).read_bytes())

    def test_source_has_no_ros_initialization_network_coordinator_or_application_controls(self):
        text = (ROOT / "tools/glim_supervised_target.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, (
                    "create_node", "init", "create_publisher", "publish", "create_client",
                    "create_service", "call_async", "connect",
                ))
        self.assertIn('os.fdopen(os.dup(1), "wb", buffering=0)', text)
        self.assertIn("os.dup2(2, 1)", text)
        self.assertNotIn("subprocess", text)

    def synthetic_run(self, directory, *, mutate=None, coordination_error=None):
        root = Path(directory)
        args = argparse.Namespace(
            operator_confirmed=True, root=root / "frozen", core_root=root / "core",
            reference=root / "reference", output=root / "project/.local/trial",
            hypothesis="header_is_scan_start", rate=1.,
        )
        args.root.mkdir()
        args.core_root.mkdir()
        owner = Mock()
        terminal = {**stopped(), "session": "c" * 32}
        owner.stop.return_value = terminal
        integrity = {"journal": EXPECTED, "explicitlySynthetic": True}

        def synthetic_stage(value):
            report = {
                **child_report(), "result": target.shared.CHILD_RESULT,
                "machine": "aarch64", "session": terminal["session"], "replayRate": 1.,
                "inputIntegrity": integrity, "explicitlySynthetic": True,
            }
            if mutate:
                mutate(report)
            target.shared.private_json(value.output / "child-report.json", report)
            (value.output / "private-child.log").write_bytes(b"explicitly synthetic log; no process started")
            return value, value.output / "runtime-source", {}

        coordinate = Mock(side_effect=coordination_error, return_value=terminal)
        with patch.object(target, "ROOT", root / "project"), \
                patch.object(target, "inputs", return_value=integrity), \
                patch.object(target, "stage", side_effect=synthetic_stage), \
                patch.object(target.shared, "ProcessSupervisor", return_value=owner) as constructor, \
                patch.object(target.shared, "coordinate", coordinate):
            if mutate or coordination_error:
                with self.assertRaises((ValueError, RuntimeError)):
                    target.run(args)
                self.assertEqual("failed_or_interrupted", target.shared.load(args.output / "report.json")["result"])
            else:
                result = target.run(args)
                self.assertEqual(target.RESULT, result["result"])
                values = constructor.call_args
                self.assertEqual(["/usr/bin/nice", "-n", "10", sys.executable, "-B"], values.args[0][:5])
                self.assertIn("--core-root", values.args[0])
                self.assertEqual(target.child_environment(args.root, args.core_root), values.kwargs["environment"])
                self.assertEqual(target.shared.LIMITS, values.kwargs["limits"])
                self.assertFalse(any(result["boundaries"].values()))
        owner.stop.assert_called_once()
        coordinate.assert_called_once_with(owner, EXPECTED["records"], 110.)

    def test_parent_wiring_uses_nice_explicit_environment_and_shared_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            self.synthetic_run(directory)

    def test_parent_rejects_wrong_machine_or_session_even_with_complete_counters(self):
        for key, value in (("machine", "x86_64"), ("session", "d" * 32), ("replayRate", .5)):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                self.synthetic_run(directory, mutate=lambda report: report.update({key: value}))

    def test_parent_coordination_failure_still_writes_failure_and_cleans_owned_process(self):
        with tempfile.TemporaryDirectory() as directory:
            self.synthetic_run(directory, coordination_error=RuntimeError("explicitly_synthetic_timeout"))


AVAILABLE = sys.platform == "linux" and importlib.util.find_spec("numpy") is not None


@unittest.skipUnless(AVAILABLE, "requires isolated Linux NumPy for installed-decoder binding fixture")
class DecoderBinding(unittest.TestCase):
    def test_humble_decoder_and_type_support_are_injected_into_the_shared_session(self):
        imu, cloud = type("SyntheticImu", (), {}), type("SyntheticCloud", (), {})
        serialization, messages, support = (types.ModuleType(name) for name in (
            "rclpy.serialization", "sensor_msgs.msg", "rclpy.type_support"))
        serialization.deserialize_message = Mock(side_effect=lambda payload, kind: (payload, kind))
        messages.Imu, messages.PointCloud2 = imu, cloud
        support.check_for_type_support = Mock()
        integrity = {"referenceLibrariesSha256": {"synthetic.so": "a" * 64}}
        args = argparse.Namespace(root=Path("/synthetic/frozen"), core_root=Path("/synthetic/core"))
        modules = {"rclpy.serialization": serialization, "sensor_msgs.msg": messages,
                   "rclpy.type_support": support}
        with patch.dict(sys.modules, modules), patch.object(target, "inputs", return_value=integrity), \
                patch.object(target.shared, "load", return_value={}), \
                patch("slam_target_probe.verify_bundle", return_value={}), \
                patch("glim_target_worker.observed_libraries", return_value=integrity["referenceLibrariesSha256"]), \
                patch.object(target.shared, "recorded_child", return_value=0) as session:
            self.assertEqual(0, target.child(args, object()))
            bindings = session.call_args.kwargs
            self.assertEqual((b"synthetic-imu", imu), bindings["decode"]("imu", b"synthetic-imu"))
            self.assertEqual((b"synthetic-cloud", cloud), bindings["decode"]("cloud", b"synthetic-cloud"))
            self.assertEqual(integrity["referenceLibrariesSha256"], bindings["verify_loaded"]())
            self.assertEqual(integrity, bindings["verify_inputs"]())
        self.assertEqual([imu, cloud], [call.args[0] for call in support.check_for_type_support.call_args_list])


if __name__ == "__main__":
    unittest.main()
