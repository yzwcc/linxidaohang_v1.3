"""Run actual native planning checks on synthetic maps without ROS nodes or actuators."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import unittest


def check(library, expected, output):
    library = Path(library).resolve(strict=True)
    if hashlib.sha256(library.read_bytes()).hexdigest() != expected or Path(output).exists():
        raise ValueError("verified_native_library_and_new_report_required")
    os.environ["LINGXI_TEST_NAVFN_LIBRARY"] = str(library)
    os.environ["LINGXI_TEST_NAVFN_SHA256"] = expected
    filename = Path(__file__).resolve().parent / "tests/test_navfn.py"
    spec = importlib.util.spec_from_file_location("native_navfn_contracts", filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(module))
    report = {"schemaVersion": 1, "result": "native_synthetic_planning_passed" if result.wasSuccessful()
              and not result.skipped else "failed_or_skipped", "machine": platform.machine(),
              "python": platform.python_version(), "nativeSha256": expected,
              "upstreamCommit": module.UPSTREAM_COMMIT, "testsRun": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
              "inputClassification": "isolated_synthetic_costmaps", "sensorsSubscribed": False,
              "motionCommandsSent": False, "navigationReady": False}
    with Path(output).open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = check(args.library, args.sha256, args.output)
    print(json.dumps(report))
    raise SystemExit(0 if report["result"] == "native_synthetic_planning_passed" else 1)
