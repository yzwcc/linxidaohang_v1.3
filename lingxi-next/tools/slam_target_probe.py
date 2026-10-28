"""Target-side, synthetic-only ARM64 loading probe. No ROS or motion APIs."""
import argparse
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import subprocess
import time


MODULES = (
    "libodometry_estimation_cpu.so", "libsub_mapping_passthrough.so",
    "libglobal_mapping_pose_graph.so", "libglobal_mapping.so",
)


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def verify_bundle(root):
    manifest = json.loads((root / "bundle-manifest.json").read_text())
    if (manifest.get("purpose") != "synthetic_arm64_loading_trial"
            or manifest.get("inputClassification") != "synthetic_fixture"
            or manifest.get("navigationValidated") is not False):
        raise ValueError("explicit_synthetic_bundle_required")
    declared = manifest["files"]
    for name, entry in declared.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise ValueError("relative_bundle_member_required")
        path = root / name
        if (path.is_symlink() or not path.is_file() or path.resolve().parent != path.parent.resolve()
                or not path.resolve().is_relative_to(root.resolve())
                or path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]):
            raise ValueError("bundle_member_integrity_mismatch")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual != set(declared) | {"bundle-manifest.json"}:
        raise ValueError("undeclared_bundle_member")
    profile = json.loads((root / "config/trial-manifest.json").read_text())
    if profile.get("inputClassification") != "synthetic_fixture":
        raise ValueError("real_sensor_profile_not_allowed")
    return manifest


def mapped_libraries(root, manifest, text):
    expected = {name.split("/")[-1] for name in manifest["files"] if name.startswith("lib/")}
    found = {}
    for line in text.splitlines():
        fields = line.split(None, 5)
        if len(fields) < 6 or not fields[5].startswith("/"):
            continue
        path = Path(fields[5])
        if path.name not in expected:
            continue
        if path.parent.resolve() != (root / "lib").resolve():
            raise ValueError("trial_library_loaded_from_outside_isolated_bundle")
        found[path.name] = sha256(path)
    if not set(MODULES).issubset(found) or "libglim.so" not in found:
        raise ValueError("expected_cpu_modules_not_observed_in_process_maps")
    return found


def probe(root):
    root = root.resolve(strict=True)
    if (platform.system() != "Linux" or platform.machine() != "aarch64" or os.geteuid() == 0
            or os.environ.get("LD_LIBRARY_PATH") != str(root / "lib")
            or any(name in os.environ for name in (
                "ROS_DISTRO", "AMENT_PREFIX_PATH", "LD_PRELOAD", "PYTHONPATH",
            ))):
        raise ValueError("ordinary_user_clean_arm64_process_required")
    manifest = verify_bundle(root)
    started = time.monotonic()
    libraries = [ctypes.CDLL(str(root / "lib" / name), mode=os.RTLD_NOW | os.RTLD_LOCAL)
                 for name in MODULES]
    observed = mapped_libraries(root, manifest, Path("/proc/self/maps").read_text())
    command = [str(root / "bin/lingxi_glim_time_smoke"), str(root / "config")]
    command.extend(str(root / "lib" / name) for name in MODULES[:3])
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if completed.returncode:
        raise RuntimeError("synthetic_target_program_failed")
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    if len(lines) != 1:
        raise ValueError("one_synthetic_program_result_required")
    result = json.loads(lines[0])
    if (result.get("inputClassification") != "synthetic_fixture"
            or result.get("nativeModulesLoaded") != 3
            or result.get("robotCalibrationValidated") is not False
            or result.get("navigationValidated") is not False
            or any(not math.isclose(result.get(name, float("nan")), value, rel_tol=0, abs_tol=1e-12)
                   for name, value in (
                       ("frameSeconds", 1000), ("firstSeconds", 0.000332), ("lastSeconds", 0.095181),
                   ))):
        raise ValueError("synthetic_target_program_contract_mismatch")
    # Keep references alive until after inspecting this process's loaded libraries.
    if len(libraries) != len(MODULES):
        raise ValueError("incomplete_module_load")
    return {
        "purpose": "synthetic_arm64_target_loading_not_slam_acceptance",
        "result": "arm64_modules_loaded_and_synthetic_program_executed",
        "target": {"machine": platform.machine(), "python": platform.python_version(),
                   "libc": list(platform.libc_ver())},
        "bundleManifestSha256": sha256(root / "bundle-manifest.json"),
        "mappedBundleLibrariesSha256": observed, "syntheticProgramResult": result,
        "probeSeconds": time.monotonic() - started,
        "checks": {"fourCpuModulesLoaded": True, "bundleIntegrityPassed": True,
                   "isolatedLibrariesObserved": True, "syntheticExecutablePassed": True},
        "boundaries": {name: False for name in (
            "systemPackagesInstalled", "rosStarted", "motionCommandsSent",
            "realDataProcessedOnTarget", "robotCalibrationValidated", "relocalizationValidated",
            "targetRealtimePerformanceValidated", "rosWrapperValidated", "navigationValidated",
            "deploymentApproved", "rawDataPublished",
        )},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = probe(args.root)
    except BaseException as error:
        print(json.dumps({"result": "target_loading_failed", "errorType": type(error).__name__}))
        raise
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
