"""Synthetic build contracts only; never load or execute an ARM64 library."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

try:
    import build_glim_point_arm64 as builder
except ImportError:
    builder = None


@unittest.skipIf(builder is None, "Cross-build helper dependencies run in isolated Linux")
class CrossBuildTests(unittest.TestCase):
    def test_plan_only_executes_host_export_inspection(self):
        args = argparse.Namespace(output=Path("/new"), cross_work=Path("/frozen"),
                                  native_work=Path("/native"))
        plan = dict(builder.commands(args, Path("/source")))
        self.assertIn("-DCMAKE_BUILD_TYPE=Release", plan["configure"])
        self.assertIn("-DCMAKE_NM=" + str(args.cross_work / "toolchain/usr/bin/aarch64-linux-gnu-nm"),
                      plan["configure"])
        self.assertEqual("^point_output_exported_abi$", plan["exported_abi_only"][-2])
        self.assertTrue(all("point_output_contract" not in " ".join(command) for command in plan.values()))

    def fixture(self, directory):
        root = Path(directory)
        (root / "build").mkdir()
        artifacts = []
        for name in (builder.points.LIBRARY, "point_output_contract"):
            (root / "build" / name).write_bytes(b"synthetic not executable artifact")
            artifacts.append({
                "file": name, "machine": "AArch64", "class": "ELF64",
                "sha256": builder.sha256(root / "build" / name),
            })
        return {
            "result": builder.RESULT, "buildType": "Release",
            "inputs": {
                "sourceCommits": builder.PINS, "builderSourceSha256": builder.sha256(builder.__file__),
                "nativePointSourceSha256": {
                    name: builder.sha256(ROOT / name) for name in builder.points.NATIVE_NAMES},
            },
            "stages": [{"name": name, "exitCode": 0}
                       for name in ("configure", "build", "exported_abi_only")],
            "artifacts": artifacts, "hostAbiInspectionExecuted": True,
            "nativeContractExecutionsOnTarget": 0, "targetExecutionValidated": False,
            "robotContacted": False, "runtimePackageBuilt": False, "deploymentApproved": False,
        }

    def test_evidence_cannot_claim_target_execution_or_product_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self.fixture(directory)
            for key, value in ((None, None), ("targetExecutionValidated", True),
                               ("robotContacted", True), ("runtimePackageBuilt", True),
                               ("nativeContractExecutionsOnTarget", 1), ("hostAbiInspectionExecuted", False)):
                current = deepcopy(report)
                if key is not None:
                    current[key] = value
                (root / "build-report.json").write_text(json.dumps(current), encoding="utf-8")
                with self.subTest(key=key):
                    if key is None:
                        self.assertFalse(builder.evidence(root)["targetExecutionValidated"])
                    else:
                        with self.assertRaises(ValueError):
                            builder.evidence(root)

    def test_changed_binary_cannot_reuse_old_build_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self.fixture(directory)
            (root / "build-report.json").write_text(json.dumps(report), encoding="utf-8")
            (root / "build" / builder.points.LIBRARY).write_bytes(b"different synthetic artifact")
            with self.assertRaisesRegex(ValueError, "unchanged_arm64"):
                builder.evidence(root)


if __name__ == "__main__":
    unittest.main()
