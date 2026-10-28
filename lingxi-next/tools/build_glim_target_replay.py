"""Cross-build the existing replay C ABI and prepare a private recorded-data trial."""
import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from build_slam_arm64_trial import cross_environment, inspect_elf
from build_slam_cpu_trial import PINS
from glim_trial_config import generate
from run_glim_offline_trial import explicit_diagnostic_profile
from slam_target_probe import sha256, verify_bundle


ROOT = Path(__file__).resolve().parents[1]
PYTHON_SOURCES = (
    "glim_target_replay.py", "run_glim_offline_trial.py", "run_kiss_offline_trial.py",
    "diagnose_slam_sensors.py", "slam_bag_audit.py", "verify_slam_capture.py",
    "build_slam_cpu_trial.py", "glim_trial_config.py", "slam_target_probe.py",
    "slam_preflight.py",
)


def validate_python_sources(directory, names=PYTHON_SOURCES):
    declared = set(names)
    for name in names:
        source = directory / name
        if source.is_symlink() or not source.is_file():
            raise ValueError("direct_python_source_required_" + name)
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports = [node.module]
            for imported in imports:
                dependency = imported.split(".")[0] + ".py"
                if (directory / dependency).is_file() and dependency not in declared:
                    raise ValueError("missing_private_bundle_dependency_" + dependency)


def prepare(args):
    if any(path.exists() for path in (args.build_output, args.bundle)):
        raise ValueError("existing_target_replay_build_or_bundle_is_preserved")
    validate_python_sources(ROOT / "tools")
    core = json.loads((args.cross_work / "cross-build-report.json").read_text())
    if core["result"] != "arm64_cross_built_and_linked_not_executed" or core["sourceCommits"] != PINS:
        raise ValueError("verified_arm64_core_required")
    verify_bundle(args.core_bundle)
    profile = json.loads(args.profile.read_text())
    diagnostic = json.loads(args.diagnostic.read_text())
    for hypothesis in ("header_is_scan_start", "header_is_scan_end"):
        explicit_diagnostic_profile(profile, diagnostic, hypothesis)
    args.build_output.mkdir(parents=True, mode=0o700)
    source = args.build_output / "source"
    source.mkdir()
    for name in ("offline.cpp", "CMakeLists.txt"):
        shutil.copyfile(ROOT / "tools/glim-replay" / name, source / name)
    env = cross_environment(args.cross_work, args.native_work)
    report = {
        "purpose": "arm64_recorded_replay_adapter_cross_build", "sourceCommits": PINS,
        "crossCoreReportSha256": sha256(args.cross_work / "cross-build-report.json"),
        "sourceSha256": {name: sha256(source / name) for name in ("offline.cpp", "CMakeLists.txt")},
        "stages": [], "targetExecutionValidated": False,
    }
    try:
        for name, command in (
            ("configure", [
                "cmake", "-S", str(source), "-B", str(args.build_output / "build"), "-G", "Ninja",
                "-DCMAKE_TOOLCHAIN_FILE=" + str(args.cross_work / "arm64-toolchain.cmake"),
                "-DCMAKE_PREFIX_PATH=" + str(args.cross_work / "prefix") + ";" + str(args.cross_work / "sysroot/usr"),
                "-DCMAKE_CXX_FLAGS=-O1",
            ]),
            ("build", ["cmake", "--build", str(args.build_output / "build"), "--parallel", "2"]),
        ):
            with (args.build_output / (name + ".log")).open("x") as log:
                result = subprocess.run(command, env=env, stdout=log, stderr=log)
            report["stages"].append({"name": name, "exitCode": result.returncode})
            if result.returncode:
                raise RuntimeError("target_replay_adapter_build_failed_see_logs")
        library = args.build_output / "build/liblingxi_glim_offline.so"
        report["artifact"] = inspect_elf(
            library, args.cross_work / "toolchain/usr/bin/aarch64-linux-gnu-readelf", env,
        )
        report["result"] = "arm64_adapter_cross_built_not_executed"
    except BaseException:
        report["result"] = "failed_or_interrupted"
        raise
    finally:
        (args.build_output / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
    args.bundle.mkdir(parents=True, mode=0o700)
    for name in ("tools", "lib"):
        (args.bundle / name).mkdir(mode=0o700)
    for name in PYTHON_SOURCES:
        shutil.copyfile(ROOT / "tools" / name, args.bundle / "tools" / name)
    # A fresh interpreter must not resolve missing modules from the checkout.
    subprocess.run([
        sys.executable, "-I", "-B", "-c",
        "import sys; sys.path.insert(0, sys.argv[1]); import glim_target_replay",
        str(args.bundle / "tools"),
    ], check=True, cwd=args.bundle)
    shutil.copyfile(library, args.bundle / "lib/liblingxi_glim_offline.so")
    for source_path, name in (
        (args.profile, "private-profile.json"), (args.diagnostic, "private-sensor-diagnostic.json"),
        (ROOT / "docs/slam-capture-evidence.json", "capture-manifest.json"),
    ):
        shutil.copyfile(source_path, args.bundle / name)
    generate(args.upstream, args.bundle / "config", profile)
    manifest = {
        "schemaVersion": 1, "purpose": "arm64_recorded_glim_odometry_trial",
        "timingHypothesesOnlyNotCalibration": True, "sourceCommits": PINS,
        "adapterBuildReportSha256": sha256(args.build_output / "build-report.json"),
        "coreBundleManifestSha256": sha256(args.core_bundle / "bundle-manifest.json"),
        "captureManifestSha256": sha256(args.bundle / "capture-manifest.json"),
        "files": {}, "navigationValidated": False,
    }
    for path in sorted(args.bundle.rglob("*")):
        if path.is_file():
            path.chmod(0o600)
            manifest["files"][path.relative_to(args.bundle).as_posix()] = {
                "sha256": sha256(path), "bytes": path.stat().st_size,
            }
    (args.bundle / "replay-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return {"result": "private_arm64_recorded_replay_bundle_prepared",
            "files": len(manifest["files"]) + 1,
            "manifestSha256": sha256(args.bundle / "replay-manifest.json"),
            "artifact": report["artifact"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cross-work", "native-work", "core-bundle", "profile", "diagnostic", "upstream",
                 "build-output", "bundle"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        setattr(args, name, value.resolve())
    old_umask = os.umask(0o077)
    try:
        print(json.dumps(prepare(args), indent=2))
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
