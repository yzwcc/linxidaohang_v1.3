"""Isolated synthetic cross-build contracts; mocks are never ARM64 runtime evidence."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy"))
if AVAILABLE:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import build_glim_observations_arm64 as build


@unittest.skipUnless(AVAILABLE, "requires isolated numerical Python runtime")
class ObservationCrossBuild(unittest.TestCase):
    def setUp(self):
        private = build.native.ROOT / ".local"
        private.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="isolated-observation-cross-test-", dir=private)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).absolute()
        self.args = SimpleNamespace(**{name: self.root / name for name in
            ("cross_work", "native_work", "upstream", "native_adapter", "output")})
        for name in ("cross_work", "native_work", "upstream", "native_adapter"):
            getattr(self.args, name).mkdir()
        (self.args.native_adapter / "source").mkdir()
        (self.args.native_adapter / "source/synthetic.cpp").write_text("// isolated synthetic source\n")
        self.fixture = self.root / "synthetic-fixture.cpp"
        self.fixture.write_text("// isolated synthetic fixture\n")
        self.frozen = {"fixtureSha256": build.native.asynchronous.full.sha256(self.fixture),
                       "syntheticInputsNotRealBuildEvidence": True}
        for replacement in (
            patch.object(build, "inputs", return_value=self.frozen),
            patch.object(build, "FIXTURE_SOURCE", self.fixture),
            patch.object(build.platform, "system", return_value="Linux"),
            patch.object(build.platform, "machine", return_value="x86_64"),
            patch.object(build.os, "geteuid", return_value=1000, create=True),
            patch.object(build, "cross_environment", return_value={}),
            patch.object(build, "exported_abi"),
            patch.object(build, "inspect_elf", side_effect=self.inspect),
            patch.object(build.subprocess, "run", side_effect=self.run_command),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def inspect(self, path, readelf, environment):
        return {"file": path.name, "machine": "AArch64", "class": "ELF64",
                "sha256": build.native.asynchronous.full.sha256(path)}

    def run_command(self, command, **options):
        if command[:2] == ["cmake", "--build"]:
            (self.args.output / "build").mkdir()
            for name in (build.LIBRARY, build.FIXTURE):
                (self.args.output / "build" / name).write_bytes(b"ISOLATED SYNTHETIC ARTIFACT NOT AN ELF")
        return SimpleNamespace(returncode=0)

    def test_cross_plan_links_without_executing_a_target_or_sensor(self):
        plan = build.commands(self.args)
        self.assertEqual(["configure", "build"], [name for name, _ in plan])
        self.assertIn("-DCMAKE_BUILD_TYPE=Release", plan[0][1])
        self.assertIn("-DCMAKE_TOOLCHAIN_FILE=" + str(self.args.cross_work / "arm64-toolchain.cmake"), plan[0][1])
        for _, command in plan:
            self.assertEqual("cmake", command[0])
            for forbidden in ("ctest", "qemu-aarch64", "ros2", "sudo", build.FIXTURE):
                self.assertNotIn(forbidden, command)

    def test_source_frozen_fixture_build_and_independent_verification_agree(self):
        report = build.build(self.args)
        self.assertEqual(build.RESULT, report["result"])
        self.assertEqual(report, build.verify(self.args))
        self.assertTrue(all(value is False for value in report["boundaries"].values()))
        self.assertEqual([build.LIBRARY, build.FIXTURE], [row["file"] for row in report["artifacts"]])
        self.assertEqual((self.args.native_adapter / "source/synthetic.cpp").read_bytes(),
                         (self.args.output / "source/synthetic.cpp").read_bytes())

    def test_existing_build_and_input_overlap_are_preserved(self):
        self.args.output.mkdir()
        marker = self.args.output / "preserved"
        marker.write_text("keep")
        with self.assertRaises(ValueError):
            build.build(self.args)
        self.assertEqual("keep", marker.read_text())
        self.args.output = self.args.native_adapter / "child"
        with self.assertRaisesRegex(ValueError, "outside_inputs"):
            build.build(self.args)
        self.assertFalse(self.args.output.exists())

    def test_failed_compilation_leaves_a_failed_report_not_an_accepted_build(self):
        with patch.object(build.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(RuntimeError):
                build.build(self.args)
        report = json.loads((self.args.output / "build-report.json").read_bytes())
        self.assertEqual("failed_or_interrupted", report["result"])
        self.assertFalse(report["privateEofSymbolsVerified"])
        with self.assertRaises(ValueError):
            build.verify(self.args)

    def test_source_fixture_artifact_and_readiness_changes_are_not_verified(self):
        build.build(self.args)
        for relative in ("source/synthetic.cpp", "contract/test_observation_capture.cpp", "build/" + build.LIBRARY):
            path = self.args.output / relative
            before = path.read_bytes()
            path.write_bytes(before + b"changed synthetic bytes")
            with self.subTest(path=relative), self.assertRaises(ValueError):
                build.verify(self.args)
            path.write_bytes(before)
        path = self.args.output / "build-report.json"
        report = json.loads(path.read_bytes())
        report["boundaries"]["navigationValidated"] = True
        path.write_text(json.dumps(report))
        with self.assertRaises(ValueError):
            build.verify(self.args)


if __name__ == "__main__":
    unittest.main()
