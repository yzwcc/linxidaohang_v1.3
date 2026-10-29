"""Isolated synthetic bindings/process peers only; no ARM64, ROS or robot execution."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import glim_point_supervised_target as target


class FakeOutput:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeDecorator:
    def __init__(self, factory, output, on_points):
        self.output, self.on_points, self.closed = output, on_points, False
        self.native = factory()

    def close(self):
        self.closed = True
        self.native.close()
        self.output.close()
        return 3


def arguments(root):
    args = argparse.Namespace(
        operator_confirmed=True, root=root / "recorded", core_root=root / "core",
        reference=root / "reference", point_bundle=root / "points", consumer_root=root / "consumer",
        output=root / ".local/new", point_manifest_sha256="a" * 64, consumer_manifest_sha256="b" * 64,
        hypothesis="header_is_scan_start", rate=1., point_socket=root / "point.sock")
    for name in ("root", "core_root", "reference", "point_bundle", "consumer_root"):
        getattr(args, name).mkdir()
    return args


class TargetPointFactoryTests(unittest.TestCase):
    def test_actual_target_paths_separate_channel_and_single_owner_close(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = arguments(Path(temporary))
            output, native, summary, channel = FakeOutput(), Mock(), Mock(), Mock()
            channel.writer.sent, channel.writer.replaced, channel.writer.failed = 2, 0, False
            channel.writer.thread.is_alive.return_value = False
            output_factory = Mock(return_value=output)
            native_factory = Mock(return_value=native)
            transport = {}
            modules = {
                "lingxi_slam.point_output": types.SimpleNamespace(
                    NativePointOutput=output_factory, PointOutputNative=FakeDecorator),
                "run_glim_offline_trial": types.SimpleNamespace(Native=native_factory),
            }
            with patch.dict(sys.modules, modules), \
                    patch.dict(os.environ, {"LINGXI_SLAM_SESSION": "a" * 32}), \
                    patch.object(target.pipe, "connected_output", return_value=channel) as connected:
                wrapped = target.native_factory(args, summary, transport)()
                packet = object()
                wrapped.on_points(packet)
                self.assertEqual(3, wrapped.close())
                self.assertIsNone(wrapped.close())
            output_factory.assert_called_once_with(args.point_bundle / "lib/liblingxi_glim_point_output.so")
            native_factory.assert_called_once_with(args.root / "lib/liblingxi_glim_offline.so",
                                                   args.root / "config",
                                                   args.core_root / "lib/libodometry_estimation_cpu.so")
            connected.assert_called_once_with(args, "a" * 32)
            summary.begin.assert_called_once()
            summary.accept.assert_called_once_with(packet)
            summary.finish.assert_called_once_with(output)
            channel.assert_called_once_with(packet)
            channel.close.assert_called_once()
            native.close.assert_called_once()
            self.assertEqual({"sent": 2, "replaced": 0, "failed": False, "threadAlive": False}, transport)

    def test_failed_native_constructor_closes_point_output_and_channel(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = arguments(Path(temporary))
            output, channel = FakeOutput(), Mock()
            modules = {
                "lingxi_slam.point_output": types.SimpleNamespace(
                    NativePointOutput=Mock(return_value=output), PointOutputNative=FakeDecorator),
                "run_glim_offline_trial": types.SimpleNamespace(
                    Native=Mock(side_effect=RuntimeError("synthetic constructor failure"))),
            }
            with patch.dict(sys.modules, modules), \
                    patch.dict(os.environ, {"LINGXI_SLAM_SESSION": "a" * 32}), \
                    patch.object(target.pipe, "connected_output", return_value=channel):
                with self.assertRaises(RuntimeError):
                    target.native_factory(args, Mock(), {})()
            self.assertTrue(output.closed)
            channel.close.assert_called_once()


class TargetPointIntegrityTests(unittest.TestCase):
    def test_same_core_extension_and_verified_consumer_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = arguments(root)
            (root / "docs").mkdir()
            for name in ("slam-point-arm64-target-evidence.json", "slam-native-point-pipe-evidence.json"):
                (root / "docs" / name).write_bytes(b"synthetic reference")
            frozen = {"coreBundleManifestSha256": "c" * 64, "verifiedCoreLibrariesSha256": {"core.so": "d" * 64}}
            points = {"files": {"lib/" + target.probe.LIBRARY: {"sha256": "e" * 64}},
                      "expectedCoreLibrariesSha256": {"core.so": "d" * 64}}
            evidence = {
                "result": target.probe.RESULT, "bundleManifestSha256": args.point_manifest_sha256,
                "coreManifestSha256": frozen["coreBundleManifestSha256"],
                "actualLoadedLibrariesSha256": {target.probe.LIBRARY: "e" * 64},
            }
            runtime = {
                "sourceSha256": {"synthetic.ts": "f" * 64},
                "referenceEvidenceSha256": target.sha256(root / "docs/slam-native-point-pipe-evidence.json"),
                "consumerBundleSha256": "1" * 64, "files": {"node/bin/node": {"sha256": "2" * 64}},
            }
            with patch.object(target, "ROOT", root), \
                    patch.object(target.target, "inputs", return_value=frozen), \
                    patch.object(target.probe, "verify_bundle", return_value=points), \
                    patch.object(target.shared, "load", return_value=evidence), \
                    patch.object(target.consumer_bundle, "verify_bundle", return_value=runtime), \
                    patch.object(target.pipe, "source_hashes", return_value=runtime["sourceSha256"]), \
                    patch.object(target, "source_hashes", return_value={"binding.py": "3" * 64}):
                result = target.integrity(args)
                self.assertEqual("e" * 64, result["pointExtensionLibrarySha256"])
                self.assertEqual("2" * 64, result["nodeBinarySha256"])
                evidence["coreManifestSha256"] = "4" * 64
                with self.assertRaises(ValueError):
                    target.integrity(args)

    def test_stage_adds_only_identical_extra_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "extra/source.py"
            source.parent.mkdir()
            source.write_bytes(b"# synthetic staging source\n")
            args, runtime = argparse.Namespace(), root / "runtime"
            copied = {}
            with patch.object(target, "ROOT", root), \
                    patch.object(target.target, "stage", return_value=(args, runtime, copied)), \
                    patch.object(target, "source_hashes", return_value={"extra/source.py": target.sha256(source)}):
                staged, _, result = target.stage(args)
                self.assertIs(staged, args)
                self.assertEqual(source.read_bytes(), (runtime / "extra/source.py").read_bytes())
                self.assertEqual({"extra/source.py": target.sha256(source)}, result)
                (runtime / "extra/source.py").write_bytes(b"changed synthetic stage")
                with self.assertRaises(ValueError):
                    target.stage(args)


class TargetPointChildTests(unittest.TestCase):
    def test_installed_humble_passive_decoder_binding_without_ros_initialization(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = arguments(root)
            args.output.mkdir(parents=True)
            frozen = {"referenceLibrariesSha256": {"native.so": "c" * 64}}
            imu, cloud, numpy = object(), object(), types.SimpleNamespace(__version__="synthetic")
            deserialize, check = Mock(return_value="synthetic decoded"), Mock()
            rclpy = types.SimpleNamespace(init=Mock(side_effect=AssertionError("must not initialize ROS")))
            modules = {
                "numpy": numpy, "rclpy": rclpy,
                "rclpy.serialization": types.SimpleNamespace(deserialize_message=deserialize),
                "rclpy.type_support": types.SimpleNamespace(check_for_type_support=check),
                "sensor_msgs.msg": types.SimpleNamespace(Imu=imu, PointCloud2=cloud),
                "glim_target_worker": types.SimpleNamespace(observed_libraries=Mock(
                    return_value=frozen["referenceLibrariesSha256"])),
                "slam_target_probe": types.SimpleNamespace(verify_bundle=Mock(return_value={})),
            }
            summary = Mock()
            summary.report.return_value = {"packets": 2}

            def factory(_args, _summary, transport):
                transport.update(sent=2, replaced=0, failed=False, threadAlive=False)
                return "synthetic factory"

            def recorded(_args, _sink, **kwargs):
                kwargs["decode"]("imu", b"synthetic imu")
                kwargs["decode"]("cloud", b"synthetic cloud")
                self.assertEqual(frozen["referenceLibrariesSha256"], kwargs["verify_loaded"]())
                (args.output / "child-report.json").write_text(json.dumps({
                    "worker": {"stream": {"counts": {"estimatedPoses": 3}}}}))
                return 0

            with ExitStack() as stack:
                for context in (
                    patch.dict(sys.modules, modules), patch.object(target, "integrity", return_value=frozen),
                    patch.object(target.probe, "verify_bundle", return_value={}),
                    patch.object(target.probe, "mapped_libraries", return_value={target.probe.LIBRARY: "d" * 64}),
                    patch.object(target.Path, "read_text", return_value="synthetic maps"),
                    patch.object(target.points, "PacketSummary", return_value=summary),
                    patch.object(target.points, "verified_points", return_value={"packets": 2}),
                    patch.object(target, "native_factory", side_effect=factory),
                    patch.object(target.shared, "recorded_child", side_effect=recorded),
                    patch.object(target.shared, "load", side_effect=lambda path: (
                        {"worker": {"stream": {"counts": {"estimatedPoses": 3}}}}
                        if path.name == "child-report.json" else {})),
                ):
                    stack.enter_context(context)
                self.assertEqual(0, target.child(args, object()))
            self.assertEqual([unittest.mock.call(imu), unittest.mock.call(cloud)], check.call_args_list)
            self.assertEqual([unittest.mock.call(b"synthetic imu", imu),
                              unittest.mock.call(b"synthetic cloud", cloud)], deserialize.call_args_list)
            rclpy.init.assert_not_called()
            report = json.loads((args.output / "point-report.json").read_text())
            self.assertEqual("recorded", report["inputMode"])
            self.assertFalse(report["writer"]["threadAlive"])


class TargetPointParentTests(unittest.TestCase):
    def test_operator_gate_precedes_all_native_input_checks(self):
        with patch.object(target, "integrity") as inputs, patch.object(target.shared, "ProcessSupervisor") as owner:
            with self.assertRaisesRegex(ValueError, "confirmation"):
                target.run(argparse.Namespace(operator_confirmed=False))
            inputs.assert_not_called()
            owner.assert_not_called()

    def test_input_overlap_rejected_before_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = arguments(Path(temporary))
            args.output = args.point_bundle / "new"
            with patch.object(target.points, "private_output", return_value=args.output), \
                    patch.object(target, "integrity") as inputs:
                with self.assertRaisesRegex(ValueError, "overlap"):
                    target.run(args)
            inputs.assert_not_called()
            self.assertFalse(args.output.exists())

    def parent_case(self, root, *, fail=False, wrong_arch=False):
        args = arguments(root)
        frozen = {"journal": {"records": 3}, "pointExtensionLibrarySha256": "c" * 64}
        owner = Mock()
        owner.session = "a" * 32
        owner.stop.return_value = {"state": "stopped", "exitCode": 0, "processAlive": False}
        consumer = Mock()
        consumer.wait.return_value = 0

        def coordinate(*_args):
            if fail:
                raise RuntimeError("synthetic owned child failure")
            reports = {
                "child-report.json": {
                    "machine": "aarch64", "session": owner.session, "inputIntegrity": frozen,
                    "result": target.shared.CHILD_RESULT, "versions": {"decoder": "installed_humble_rclpy"},
                    "worker": {"stream": {"counts": {"estimatedPoses": 3}}}},
                "point-report.json": {"actualLoadedExtensionSha256": {target.probe.LIBRARY: "c" * 64}},
                "consumer-report.json": {"host": {
                    "machine": "x64" if wrong_arch else "arm64", "node": "v" + target.consumer_bundle.VERSION}},
            }
            for name, report in reports.items():
                (args.output / name).write_text(json.dumps(report))
            return owner.stop.return_value

        with patch.object(target.points, "private_output", return_value=args.output), \
                patch.object(target, "integrity", return_value=frozen), \
                patch.object(target, "stage", return_value=(args, root / "runtime", {})), \
                patch.object(target.shared, "ProcessSupervisor", return_value=owner) as supervisor, \
                patch.object(target.subprocess, "Popen", return_value=consumer) as popen, \
                patch.object(target.pipe, "wait_ready"), \
                patch.object(target.pipe, "stop_consumer", return_value={"exitCode": 0}) as stop, \
                patch.object(target.shared, "coordinate", side_effect=coordinate), \
                patch.object(target.shared, "complete_child"), patch.object(target.points, "verified_points"), \
                patch.object(target.pipe, "verify_consumer"):
            if fail or wrong_arch:
                with self.assertRaises((RuntimeError, ValueError)):
                    target.run(args)
            else:
                self.assertEqual(target.RESULT, target.run(args)["result"])
        owner.stop.assert_called_once()
        stop.assert_called_once_with(consumer)
        self.assertEqual(args.consumer_root, popen.call_args.kwargs["cwd"])
        self.assertIn(str(args.consumer_root / "node/bin/node"), popen.call_args.args[0])
        environment = supervisor.call_args.kwargs["environment"]
        self.assertEqual("2", environment["OMP_NUM_THREADS"])
        self.assertEqual("2", environment["OPENBLAS_NUM_THREADS"])
        report = json.loads((args.output / "report.json").read_text())
        self.assertFalse(report["healthChannelContainsPoints"])
        self.assertFalse(any(report["boundaries"].values()))
        return report

    def test_synthetic_parent_composition_is_recorded_and_separate_from_health(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual("recorded", self.parent_case(Path(temporary))["inputMode"])

    def test_native_failure_still_closes_both_owned_peers_and_preserves_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual("failed_or_interrupted", self.parent_case(Path(temporary), fail=True)["result"])

    def test_x64_consumer_cannot_be_accepted_as_target_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual("failed_or_interrupted", self.parent_case(Path(temporary), wrong_arch=True)["result"])


if __name__ == "__main__":
    unittest.main()
