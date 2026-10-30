"""Synthetic target-report validation; robot execution is never part of unit tests."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import check_base_pose_tf2 as probe
import run_base_pose_tf2_target as target


class ReportContract(unittest.TestCase):
    def fixture(self):
        return {"schemaVersion": 1, "result": probe.RESULT, "machine": "aarch64",
                "inputClassification": "synthetic_transforms_no_sensors", "sourceSha256": {"fixture": "a" * 64},
                "checks": dict.fromkeys(probe.CHECKS, True), "boundaries": dict.fromkeys(probe.BOUNDARIES, False),
                "maximumMatrixElementError": 1e-15}

    def test_source_bound_synthetic_arm64_result(self):
        report = self.fixture()
        target.validate(report, report["sourceSha256"])

    def test_missing_or_false_check_and_motion_claim_rejected(self):
        mutations = (
            lambda r: r["checks"].pop("integerExactTime"),
            lambda r: r["checks"].update(midpointInterpolation=False),
            lambda r: r["checks"].update(midpointInterpolation=1),
            lambda r: r["boundaries"].update(motionCommandsSent=True),
            lambda r: r["boundaries"].pop("navigationReady"),
            lambda r: r.update(machine="x86_64"),
            lambda r: r.update(schemaVersion=True),
            lambda r: r.update(sourceSha256={}),
            lambda r: r.update(maximumMatrixElementError=float("nan")),
            lambda r: r.update(maximumMatrixElementError=1e-3),
        )
        for mutate in mutations:
            report = deepcopy(self.fixture())
            mutate(report)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                target.validate(report, self.fixture()["sourceSha256"])

    def test_probe_has_no_context_node_sensor_or_actuation_calls(self):
        tree = ast.parse((ROOT / "tools/check_base_pose_tf2.py").read_text())
        forbidden = {"init", "create_node", "create_subscription", "create_publisher",
                     "create_client", "publish", "connect"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, forbidden)

    def test_published_evidence_matches_current_probe_sources(self):
        report = json.loads((ROOT / "docs/slam-base-tf2-arm64-evidence.json").read_text())
        hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in probe.SOURCES}
        target.validate(report, hashes)


if __name__ == "__main__":
    unittest.main()
