"""Synthetic trial preparation/transport checks, without robot or native loading."""
import ast
import copy
import json
from pathlib import Path, PurePosixPath
import shlex
import shutil
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import terrain_target_probe as probe
import run_terrain_target_trial as runner


class TerrainTargetTrial(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.root, self.source = self.home / "trial", self.home / "source"
        self.root.mkdir()
        files, sources = {}, {}
        for name in probe.SOURCES + ("tools/run_terrain_target_trial.py",):
            source = self.source / name
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, source)
            sources[name] = probe.sha256(source)
        members = set(probe.SOURCES) | {"native/" + name for name in probe.LIBRARIES + probe.NOTICES}
        members.update("native/lib/" + name for name in probe.DEPENDENCIES)
        for name in members:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if name in probe.SOURCES:
                shutil.copyfile(self.source / name, path)
            else:
                header = bytearray(20)
                header[:6], header[18:20] = b"\x7fELF\x02\x01", b"\xb7\x00"
                path.write_bytes(bytes(header) if ".so" in name else b"synthetic notice")
            files[name] = dict(bytes=path.stat().st_size, sha256=probe.sha256(path))
        for name, target in probe.aliases().items():
            try:
                (self.root / name).symlink_to(target)
            except OSError:
                self.skipTest("Linux private SONAME symlink fixture required")
        contracts = probe.contract_ids(probe.load_contracts(ROOT))
        self.manifest = dict(schemaVersion=1, purpose="synthetic_arm64_terrain_trial",
            platform="linux-aarch64", inputClassification="synthetic_fixture", buildReportSha256="a" * 64,
            sourceSha256=sources, contracts=contracts, files=files, symlinks=probe.aliases(),
            boundaries=dict(probe.BOUNDARIES))
        self.write_manifest()

    def write_manifest(self):
        (self.root / probe.MANIFEST).write_text(json.dumps(self.manifest), encoding="utf-8")

    def args(self, output=None):
        return NS(bundle=self.root, output=output or self.home / "result.json", user="synthetic",
            host="synthetic-host", known_identity="synthetic-identity",
            directory="lingxi-next-validation-arm64-terrain-fixture",
            manifest_sha256=probe.sha256(self.root / probe.MANIFEST))

    def result(self):
        return dict(result=probe.RESULT, inputClassification="synthetic_fixture", target=dict(machine="aarch64"),
            trialManifestSha256=probe.sha256(self.root / probe.MANIFEST),
            buildReportSha256=self.manifest["buildReportSha256"], executedContracts=self.manifest["contracts"],
            mappedLibrariesSha256={Path(name).name: entry["sha256"] for name, entry in self.manifest["files"].items()
                if name.startswith("native/") and ".so" in name}, probeSeconds=.1,
            checks=dict(probe.CHECKS), boundaries=dict(probe.BOUNDARIES))

    def client(self):
        client = MagicMock()
        sftp = client.open_sftp.return_value.__enter__.return_value
        sftp.normalize.return_value = "/home/synthetic"
        return client, sftp, client.get_transport.return_value.open_session.return_value.__enter__.return_value

    def test_exact_bundle_and_21_current_contract_ids(self):
        self.assertEqual(21, len(self.manifest["contracts"]))
        self.assertEqual(self.manifest, probe.verify_bundle(self.root))

    def test_mutated_binary_or_source_rejected(self):
        for name in ("native/" + probe.LIBRARIES[0], probe.SOURCES[0]):
            path = self.root / name
            original = path.read_bytes()
            path.write_bytes(original + b"mutation")
            with self.assertRaisesRegex(ValueError, "unchanged_terrain"):
                probe.verify_bundle(self.root)
            path.write_bytes(original)

    def test_wrong_architecture_rejected_even_with_updated_hash(self):
        name = "native/" + probe.LIBRARIES[0]
        path = self.root / name
        data = bytearray(path.read_bytes())
        data[18:20] = b"\x3e\x00"
        path.write_bytes(data)
        self.manifest["files"][name]["sha256"] = probe.sha256(path)
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "aarch64_terrain"):
            probe.verify_bundle(self.root)

    def test_extra_file_or_empty_directory_rejected(self):
        extra = self.root / "saved-map.bin"
        extra.write_bytes(b"not synthetic")
        with self.assertRaisesRegex(ValueError, "undeclared"):
            probe.verify_bundle(self.root)
        extra.unlink()
        extra.mkdir()
        with self.assertRaisesRegex(ValueError, "undeclared"):
            probe.verify_bundle(self.root)

    def test_only_exact_relative_soname_aliases_allowed(self):
        name, target = next(iter(probe.aliases().items()))
        alias = self.root / name
        alias.unlink()
        alias.symlink_to(self.root / "native/lib" / target)
        with self.assertRaisesRegex(ValueError, "relative_terrain_soname"):
            probe.verify_bundle(self.root)

    def test_regular_file_cannot_replace_dependency_alias(self):
        name, target = next(iter(probe.aliases().items()))
        alias = self.root / name
        alias.unlink()
        shutil.copyfile(self.root / "native/lib" / target, alias)
        with self.assertRaisesRegex(ValueError, "relative_terrain_soname"):
            probe.verify_bundle(self.root)

    def test_real_data_navigation_or_missing_contract_claim_rejected(self):
        original = copy.deepcopy(self.manifest)
        for field, value in (("inputClassification", "recorded"),
                ("boundaries", {**probe.BOUNDARIES, "navigationReady": True}),
                ("contracts", original["contracts"][:-1]), ("contracts", [original["contracts"][0]] * 21)):
            self.manifest = {**original, field: value}
            self.write_manifest()
            with self.assertRaises(ValueError):
                probe.verify_bundle(self.root)

    def test_process_maps_require_all_four_libraries_in_private_bundle(self):
        paths = [self.root / name for name in self.manifest["files"] if ".so" in name]
        maps = "\n".join("000-111 r--p 0 00:00 1 " + str(path) for path in paths)
        self.assertEqual(self.result()["mappedLibrariesSha256"], probe.mapped_libraries(self.root, self.manifest, maps))
        with self.assertRaisesRegex(ValueError, "must_be_observed"):
            probe.mapped_libraries(self.root, self.manifest, "\n".join(maps.splitlines()[:-1]))
        foreign = self.home / paths[0].name
        shutil.copyfile(paths[0], foreign)
        with self.assertRaisesRegex(ValueError, "outside_isolated"):
            probe.mapped_libraries(self.root, self.manifest, maps.replace(str(paths[0]), str(foreign)))

    def test_result_requires_complete_target_contracts_and_explicit_boundaries(self):
        original = self.result()
        identity = original["trialManifestSha256"]
        runner.validate_result(original, self.manifest, identity)
        for field, value in (("executedContracts", original["executedContracts"][:-1]),
                ("mappedLibrariesSha256", {}), ("trialManifestSha256", "b" * 64),
                ("buildReportSha256", "c" * 64), ("target", None), ("target", dict(machine="x86_64")),
                ("checks", {**probe.CHECKS, "all21Contracts": 1}),
                ("boundaries", {**probe.BOUNDARIES, "navigationReady": 0}),
                ("probeSeconds", float("nan")), ("probeSeconds", 121)):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "complete_source_bound"):
                runner.validate_result({**original, field: value}, self.manifest, identity)

    def test_existing_or_bundle_report_does_not_connect_or_overwrite(self):
        existing = self.home / "existing.json"
        existing.write_bytes(b"preserved")
        for path in (existing, self.root / "result.json", self.home / "missing/result.json"):
            with patch.object(runner, "connect") as connect, self.assertRaises((ValueError, FileNotFoundError)):
                runner.run(self.args(path))
            connect.assert_not_called()
        self.assertEqual(b"preserved", existing.read_bytes())
        self.assertEqual(self.manifest, probe.verify_bundle(self.root))

    def test_source_changed_before_run_does_not_connect(self):
        (self.source / probe.SOURCES[0]).write_bytes(b"changed")
        with patch.object(runner, "ROOT", self.source), patch.object(runner, "connect") as connect:
            with self.assertRaisesRegex(ValueError, "current_source_bound"):
                runner.run(self.args())
            connect.assert_not_called()

    def test_connection_failure_has_new_private_report(self):
        args = self.args()
        with patch.object(runner, "ROOT", self.source), patch.object(runner, "connect", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                runner.run(args)
        report = json.loads(args.output.read_bytes())
        self.assertEqual("failed_or_interrupted", report["result"])
        self.assertEqual("OSError", report["errorType"])
        self.assertNotIn("host", report)
        self.assertEqual(0o600, args.output.stat().st_mode & 0o777)

    def test_mocked_success_uploads_exact_files_aliases_and_closes(self):
        args, result = self.args(), self.result()
        client, sftp, channel = self.client()
        with patch.object(runner, "ROOT", self.source), patch.object(runner, "connect", return_value=client), \
                patch.object(runner, "receive", return_value=(0, json.dumps(result).encode(), b"")):
            value = runner.run(args)
        self.assertEqual(probe.RESULT, value["result"])
        self.assertEqual(21, value["executedContracts"])
        self.assertEqual(len(self.manifest["files"]) + 1, sftp.put.call_count)
        self.assertEqual(2, sftp.symlink.call_count)
        self.assertTrue(all(call.args[1] == 0o600 for call in sftp.chmod.call_args_list))
        client.close.assert_called_once()
        channel.exec_command.assert_called_once_with(runner.command(PurePosixPath("/home/synthetic") / args.directory))
        self.assertEqual(result, json.loads(args.output.read_bytes())["targetProbe"])

    def test_remote_errors_or_timeout_preserve_failure_and_close(self):
        for index, failure in enumerate((RuntimeError("probe failed"), TimeoutError("bounded"))):
            args = self.args(self.home / (str(index) + ".json"))
            client, _, _ = self.client()
            response = dict(return_value=(1, b"private failure", b"")) if index == 0 else dict(side_effect=failure)
            with patch.object(runner, "ROOT", self.source), patch.object(runner, "connect", return_value=client), \
                    patch.object(runner, "receive", **response):
                with self.assertRaises(type(failure)):
                    runner.run(args)
            self.assertEqual("failed_or_interrupted", json.loads(args.output.read_bytes())["result"])
            client.close.assert_called_once()

    def test_source_mutation_during_remote_run_rejects_success(self):
        args, result = self.args(), self.result()
        client, _, _ = self.client()
        def received(channel):
            (self.source / probe.SOURCES[0]).write_bytes(b"changed during target run")
            return 0, json.dumps(result).encode(), b""
        with patch.object(runner, "ROOT", self.source), patch.object(runner, "connect", return_value=client), \
                patch.object(runner, "receive", side_effect=received):
            with self.assertRaisesRegex(ValueError, "changed_during"):
                runner.run(args)
        self.assertNotIn("targetProbe", json.loads(args.output.read_bytes()))
        client.close.assert_called_once()

    def test_prepare_uses_real_builder_file_field_and_preserves_prior_trial(self):
        import build_terrain_arm64 as builder
        build = self.home / "build"
        shutil.copytree(self.root / "native", build / "build")
        (build / "build-report.json").write_bytes(b"synthetic verified report")
        report = dict(inputs=dict(sourceSha256={}, hostContractSourceSha256={}),
            artifacts=[dict(file=name, sha256=probe.sha256(build / "build" / name)) for name in probe.LIBRARIES])
        args = NS(build=build, output=self.home / "prepared", cross_work=self.home / "cross",
            native_work=self.home / "native", ground_host=self.home / "ground", octomap_host=self.home / "octomap",
            octomap_arm64=self.home / "dependencies")
        with patch.object(builder, "verify", return_value=report), patch.object(runner, "ROOT", self.source):
            value = runner.prepare(args)
            self.assertEqual(21, value["contracts"])
            self.assertEqual(probe.BOUNDARIES, value["boundaries"])
            identity = probe.sha256(args.output / probe.MANIFEST)
            with self.assertRaisesRegex(ValueError, "new_direct"):
                runner.prepare(args)
            self.assertEqual(identity, probe.sha256(args.output / probe.MANIFEST))


class TerrainTrialProcessContracts(unittest.TestCase):
    def test_bounded_clean_command_has_no_ros_service_or_motion_control(self):
        remote = PurePosixPath("/home/synthetic/lingxi-next-validation-arm64-terrain")
        values = shlex.split(runner.command(remote))
        self.assertEqual(["timeout", "--signal=TERM", "--kill-after=5s", "120s", "env", "-i"], values[:6])
        self.assertIn("LD_LIBRARY_PATH=" + str(remote / "native/lib"), values)
        self.assertIn("-I", values)
        self.assertIn("-B", values)
        for token in ("sudo", "source", "bash", "ros2", "systemctl"):
            self.assertNotIn(token, values)

    def test_probe_imports_no_sensor_network_or_motion_modules(self):
        text = (ROOT / "tools/terrain_target_probe.py").read_text()
        modules = []
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                modules.extend(item.name for item in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module)
        for name in ("rclpy", "socket", "paramiko", "requests"):
            self.assertNotIn(name, modules)
        for name in ("cmd_vel", "McLocomotion", "create_publisher", "create_subscription"):
            self.assertNotIn(name, text)

    def test_target_probe_rejects_host_before_loading_any_library(self):
        with patch.object(probe.platform, "system", return_value="Linux"), \
                patch.object(probe.platform, "machine", return_value="x86_64"), patch.object(probe.ctypes, "CDLL") as loaded:
            with self.assertRaisesRegex(ValueError, "clean_arm64"):
                probe.probe(Path("/synthetic"))
            loaded.assert_not_called()

    def test_exact_success_not_skips_failures_or_partial_suite(self):
        methods = {"test_%02d" % index: lambda self: None for index in range(21)}
        case = type("SyntheticContracts", (unittest.TestCase,), methods)
        def suite():
            return unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(case)])
        contracts = probe.contract_ids(suite())
        with patch.object(probe, "load_contracts", side_effect=lambda root: suite()):
            self.assertEqual(contracts, probe.run_contracts(ROOT, contracts))
            with self.assertRaisesRegex(ValueError, "exact_current"):
                probe.run_contracts(ROOT, contracts[:-1])
            for replacement in (unittest.skip("synthetic skip")(lambda self: None), lambda self: self.fail("synthetic")):
                setattr(case, "test_00", replacement)
                with self.assertRaisesRegex(RuntimeError, "terrain_native_contract_failed"):
                    probe.run_contracts(ROOT, contracts)


if __name__ == "__main__":
    unittest.main()
