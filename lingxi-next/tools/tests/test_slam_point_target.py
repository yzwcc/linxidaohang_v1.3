"""Clearly synthetic artifacts and peers only; no ARM64 execution or robot contact."""
import argparse
from copy import deepcopy
from contextlib import ExitStack
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import glim_point_target_probe as probe
import prepare_glim_point_target as preparer


def synthetic_elf():
    header = bytearray(20)
    header[:6], header[18:20] = b"\x7fELF\x02\x01", (183).to_bytes(2, "little")
    return bytes(header) + b"explicit synthetic nonexecutable fixture"


def fixture(root):
    manifest = {
        "schemaVersion": 1, "purpose": probe.PURPOSE, "inputClassification": "isolated_synthetic_contracts",
        "sourceCommits": probe.PINS, "scenarios": list(probe.SCENARIOS), "files": {},
        "crossBuildReportSha256": "a" * 64, "crossBuildEvidenceSha256": "b" * 64,
        "expectedCoreLibrariesSha256": dict.fromkeys(probe.CORE_LIBRARIES, "c" * 64),
        "targetExecutionValidated": False, "navigationValidated": False, "deploymentApproved": False,
    }
    for name in probe.MEMBERS:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(synthetic_elf() if name.startswith(("lib/", "bin/")) else b"# synthetic source\n")
        manifest["files"][name] = {
            "sha256": probe.sha256(path), "bytes": path.stat().st_size, "executable": name.startswith("bin/")}
    return manifest


def write_manifest(root, manifest):
    (root / "point-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return probe.sha256(root / "point-manifest.json")


class BundleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest = fixture(self.root)
        self.digest = write_manifest(self.root, self.manifest)

    def test_exact_synthetic_bundle_can_be_verified_without_executing_anything(self):
        with patch.object(subprocess, "Popen", side_effect=AssertionError("must not execute")):
            self.assertEqual(self.manifest, probe.verify_bundle(self.root, self.digest))

    def test_changed_manifest_bytes_and_wrong_external_hash_are_rejected(self):
        for digest in ("", "0" * 64, "../input", True):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                probe.verify_bundle(self.root, digest)
        self.manifest["purpose"] = "changed"
        write_manifest(self.root, self.manifest)
        with self.assertRaises(ValueError):
            probe.verify_bundle(self.root, self.digest)

    def test_manifest_schema_claims_and_counter_aliases_are_rejected(self):
        original = deepcopy(self.manifest)
        for key, value in (("targetExecutionValidated", True), ("navigationValidated", True),
                           ("deploymentApproved", True), ("inputClassification", "recorded"),
                           ("schemaVersion", True), ("scenarios", list(reversed(probe.SCENARIOS))),
                           ("expectedCoreLibrariesSha256", {}), ("crossBuildReportSha256", "bad")):
            self.manifest = deepcopy(original)
            self.manifest[key] = value
            digest = write_manifest(self.root, self.manifest)
            with self.subTest(key=key), self.assertRaises(ValueError):
                probe.verify_bundle(self.root, digest)

    def test_changed_artifact_and_undeclared_file_rejected(self):
        path = self.root / probe.MEMBERS[0]
        original = path.read_bytes()
        path.write_bytes(original + b"changed")
        with self.assertRaises(ValueError):
            probe.verify_bundle(self.root, self.digest)
        path.write_bytes(original)
        (self.root / "private-extra").write_bytes(b"synthetic")
        with self.assertRaises(ValueError):
            probe.verify_bundle(self.root, self.digest)

    def test_other_architecture_not_accepted_even_with_updated_file_hash(self):
        path = self.root / probe.MEMBERS[0]
        data = bytearray(path.read_bytes())
        data[18:20] = (62).to_bytes(2, "little")
        path.write_bytes(data)
        self.manifest["files"][probe.MEMBERS[0]]["sha256"] = probe.sha256(path)
        digest = write_manifest(self.root, self.manifest)
        with self.assertRaisesRegex(ValueError, "aarch64"):
            probe.verify_bundle(self.root, digest)

    def test_scenarios_match_the_frozen_cmake_native_contract_list(self):
        source = (ROOT / "robot-slam/native-point-output/CMakeLists.txt").read_text(encoding="utf-8")
        names = re.search(r"foreach\(scenario IN ITEMS (.*?)\)", source, re.S).group(1).split()
        self.assertEqual(list(probe.SCENARIOS), names)
        self.assertNotIn("exported_abi", names)

    def test_fresh_operator_gate_precedes_all_file_reads_and_loads(self):
        with patch.object(probe, "verify_bundle") as verify, patch.object(probe.ctypes, "CDLL") as load:
            with self.assertRaisesRegex(ValueError, "confirmation"):
                probe.run(argparse.Namespace(operator_confirmed=False))
            verify.assert_not_called()
            load.assert_not_called()

    def test_environment_has_only_explicit_bundle_and_core_paths_no_ros_or_credentials(self):
        env = probe.environment(PurePosixPath("/new/bundle"), PurePosixPath("/preserved/core"))
        self.assertEqual("/new/bundle/lib:/preserved/core/lib", env["LD_LIBRARY_PATH"])
        for name in ("ROS_DISTRO", "AMENT_PREFIX_PATH", "PYTHONPATH", "LD_PRELOAD", "PASSWORD"):
            self.assertNotIn(name, env)

    def test_loaded_library_paths_and_every_expected_digest_must_match(self):
        bundle, core = self.root, self.root / "core"
        expected = self.manifest["expectedCoreLibrariesSha256"]
        for name in expected:
            path = core / "lib" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic core " + name.encode())
            expected[name] = probe.sha256(path)
        paths = [bundle / "lib" / probe.LIBRARY, *(core / "lib" / name for name in expected)]
        text = "\n".join("1-2 r--p 000000 00:00 1 " + str(path) for path in paths)
        self.assertEqual(set(expected) | {probe.LIBRARY},
                         set(probe.mapped_libraries(bundle, core, self.manifest, text)))
        with self.assertRaises(ValueError):
            probe.mapped_libraries(bundle, core, self.manifest, "\n".join(text.splitlines()[:-1]))
        (core / "lib/libglim.so").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            probe.mapped_libraries(bundle, core, self.manifest, text)


class AbiTests(unittest.TestCase):
    def library(self):
        return argparse.Namespace(**{
            "lingxi_points_" + name: Mock(return_value=0) for name in ("error", "open", "take", "counts", "close")})

    def test_empty_abi_resolves_five_symbols_and_closes_once(self):
        library = self.library()
        self.assertEqual({"open": 1, "close": 1, "callbacks": 0, "copiedPackets": 0}, probe.empty_abi(library))
        library.lingxi_points_open.assert_called_once_with(2)
        library.lingxi_points_close.assert_called_once()
        self.assertEqual(2, library.lingxi_points_counts.call_count)
        self.assertEqual([], library.lingxi_points_error.argtypes)

    def test_failed_take_still_closes_and_failed_open_does_not_double_close(self):
        for name, closed in (("take", 1), ("open", 0)):
            library = self.library()
            getattr(library, "lingxi_points_" + name).return_value = -1
            with self.subTest(name=name), self.assertRaises(ValueError):
                probe.empty_abi(library)
            self.assertEqual(closed, library.lingxi_points_close.call_count)


class SyntheticTargetRunTests(unittest.TestCase):
    def run_case(self, root, *, failed=False, existing=False):
        bundle, core, home = (root / name for name in ("bundle", "core", "home"))
        core.mkdir()
        (core / "bundle-manifest.json").write_bytes(b"explicit synthetic core manifest")
        output = home / ".cache/lingxi-next/point-probe"
        if existing:
            output.mkdir(parents=True)
            (output / "preserved").write_bytes(b"never overwrite")
        args = argparse.Namespace(bundle=bundle, core_root=core, output=output,
                                  manifest_sha256="a" * 64, operator_confirmed=True)
        manifest = {"expectedCoreLibrariesSha256": dict.fromkeys(probe.CORE_LIBRARIES, "b" * 64)}
        core_manifest = {"files": {"lib/" + name: {"sha256": value}
                                  for name, value in manifest["expectedCoreLibrariesSha256"].items()}}
        contract = Mock(side_effect=lambda _bundle, _core, _output, scenario: {
            "scenario": scenario, "exitCode": 5 if failed else 0,
            "forcedTermination": False, "processAlive": False, "timedOut": False})
        load = Mock(return_value=object())
        with ExitStack() as stack:
            for context in (
                patch.object(probe.sys, "platform", "linux"),
                patch.object(probe.sys, "version_info", (3, 10)),
                patch.object(probe.platform, "machine", return_value="aarch64"),
                patch.object(probe.platform, "python_version", return_value="3.10.0"),
                patch.object(probe.platform, "libc_ver", return_value=("glibc", "2.35")),
                patch.object(probe.os, "geteuid", return_value=1000, create=True),
                patch.object(probe.os, "RTLD_NOW", 2, create=True),
                patch.object(probe.os, "RTLD_LOCAL", 0, create=True),
                patch.dict(probe.os.environ, probe.environment(bundle, core), clear=True),
                patch.object(probe.Path, "home", return_value=home),
                patch.object(probe.Path, "read_text", return_value="explicit synthetic maps"),
                patch.object(probe, "verify_bundle", return_value=manifest),
                patch.object(probe, "verify_core", return_value=core_manifest),
                patch.object(probe.ctypes, "CDLL", load),
                patch.object(probe, "mapped_libraries", return_value={"synthetic.so": "b" * 64}),
                patch.object(probe, "empty_abi", return_value={"open": 1, "close": 1, "callbacks": 0, "copiedPackets": 0}),
                patch.object(probe, "contract", contract),
            ):
                stack.enter_context(context)
            if existing or failed:
                with self.assertRaises((RuntimeError, ValueError)):
                    probe.run(args)
            else:
                result = probe.run(args)
                self.assertEqual(probe.RESULT, result["result"])
                self.assertEqual(11, result["nativeContractExecutionsOnTarget"])
                self.assertFalse(any(result["boundaries"].values()))
        return output, load, contract

    def test_synthetic_complete_run_records_all_native_stages_without_readiness_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            output, load, contract = self.run_case(Path(directory))
            self.assertEqual(2, load.call_count)
            self.assertEqual(11, contract.call_count)
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(probe.RESULT, report["result"])

    def test_failed_stage_is_retained_and_later_stages_are_not_run(self):
        with tempfile.TemporaryDirectory() as directory:
            output, _, contract = self.run_case(Path(directory), failed=True)
            self.assertEqual(1, contract.call_count)
            report = json.loads((output / "report.json").read_text())
            self.assertEqual("failed_or_interrupted", report["result"])
            self.assertEqual(1, report["nativeContractExecutionsOnTarget"])
            self.assertEqual(5, report["stages"][0]["exitCode"])

    def test_existing_output_is_preserved_before_native_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            output, load, contract = self.run_case(Path(directory), existing=True)
            load.assert_not_called()
            contract.assert_not_called()
            self.assertEqual(b"never overwrite", (output / "preserved").read_bytes())
            self.assertFalse((output / "report.json").exists())


class PreparationTests(unittest.TestCase):
    def test_preparation_copies_only_declared_bytes_without_native_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build, output = root / "build", root / "new"
            (build / "build").mkdir(parents=True)
            for name in (probe.LIBRARY, "point_output_contract"):
                (build / "build" / name).write_bytes(synthetic_elf())
            (build / "build-report.json").write_text("synthetic private build report", encoding="utf-8")
            published = json.loads((ROOT / "docs/slam-native-point-arm64-build-evidence.json").read_text())
            args = argparse.Namespace(build=build, output=output)
            with patch.object(preparer.platform, "system", return_value="Linux"), \
                    patch.object(preparer.platform, "machine", return_value="x86_64"), \
                    patch.object(preparer.os, "geteuid", return_value=1000, create=True), \
                    patch.object(preparer.builder.points, "private_output", return_value=output), \
                    patch.object(preparer.builder, "evidence", return_value=published), \
                    patch.object(subprocess, "Popen", side_effect=AssertionError("must not execute")):
                report = preparer.prepare(args)
            self.assertEqual(0, report["nativeContractExecutionsOnTarget"])
            self.assertFalse(report["robotContacted"])
            self.assertFalse(report["runtimePackageBuilt"])
            self.assertEqual(11, report["preparedNativeScenarios"])
            bundle = output / "bundle"
            probe.verify_bundle(bundle, report["manifestSha256"])
            with tarfile.open(output / "point-target-validation.tar.gz", "r:gz") as archive:
                self.assertEqual({"point-target-validation/" + name for name in (*probe.MEMBERS, "point-manifest.json")},
                                 set(archive.getnames()))
                for member in archive:
                    self.assertTrue(member.isfile())
                    name = member.name.removeprefix("point-target-validation/")
                    self.assertEqual((bundle / name).read_bytes(), archive.extractfile(member).read())


@unittest.skipUnless(sys.platform == "linux", "Isolated Linux process lifecycle only")
class ActualSyntheticProcessTests(unittest.TestCase):
    def test_success_failure_and_timeout_close_the_owned_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            program = root / "bin/point_output_contract"
            shutil.copyfile(ROOT / "tools/tests/fixtures/point_contract_synthetic.py", program)
            program.chmod(0o700)
            for scenario, expected, forced, timed_out in (
                ("copy", 0, False, False), ("overflow", 5, False, False),
                ("pending_close", -15, True, True),
            ):
                result = probe.contract(root, root / "synthetic-core", root, scenario, timeout_seconds=.5)
                self.assertEqual({"scenario": scenario, "exitCode": expected, "forcedTermination": forced,
                                  "processAlive": False, "timedOut": timed_out}, result)


if __name__ == "__main__":
    unittest.main()
