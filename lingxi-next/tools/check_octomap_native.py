"""Run real OctoMap and NavFn on labeled synthetic observations, without ROS nodes."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import unittest


def check(args):
    library = args.library.resolve(strict=True)
    lock = json.loads(args.lock.read_bytes())
    if (hashlib.sha256(library.read_bytes()).hexdigest() != args.sha256 or args.output.exists()
            or lock["version"] != "1.9.7"):
        raise ValueError("verified_octomap_library_lock_and_new_report_required")
    os.environ.update(LINGXI_TEST_OCTOMAP_LIBRARY=str(library), LINGXI_TEST_OCTOMAP_SHA256=args.sha256,
        LINGXI_TEST_OCTOMAP_DEPENDENCIES=json.dumps(lock["dependencies"]),
        LINGXI_TEST_NAVFN_LIBRARY=str(args.navfn_library.resolve(strict=True)),
        LINGXI_TEST_NAVFN_SHA256=args.navfn_sha256)
    spec = importlib.util.spec_from_file_location("native_octomap_contracts",
                                                Path(__file__).parent / "tests/test_octomap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(module))
    report = {"schemaVersion": 1, "result": "native_synthetic_occupancy_passed" if result.wasSuccessful()
        and not result.skipped else "failed_or_skipped", "machine": platform.machine(),
        "python": platform.python_version(), "nativeSha256": args.sha256,
        "octomapVersion": lock["version"], "loadedDependenciesSha256": lock["dependencies"],
        "navfnSha256": args.navfn_sha256, "testsRun": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
        "inputClassification": "isolated_synthetic_rays", "sensorsSubscribed": False,
        "motionCommandsSent": False, "navigationReady": False}
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("library", "lock", "output", "navfn-library"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("sha256", "navfn-sha256"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    report = check(args)
    print(json.dumps(report))
    raise SystemExit(0 if report["result"] == "native_synthetic_occupancy_passed" else 1)
