"""Cross-build the unchanged preprocessing adapter; prepare a private ARM64 bundle."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from build_glim_target_replay import PYTHON_SOURCES, validate_python_sources
from build_slam_arm64_trial import cross_environment, inspect_elf
from build_slam_cpu_trial import PINS
from glim_target_preprocess import PURPOSE, verify_preprocess_bundle
from run_glim_preprocess_trial import SOURCE_PINS, new_output, save
from slam_target_probe import verify_bundle
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
SOURCES = PYTHON_SOURCES + (
    "glim_target_preprocess.py", "run_glim_preprocess_trial.py", "compare_glim_architectures.py",
    "export_glim_mapping_evidence.py", "run_glim_mapping_trial.py", "glim_target_mapping.py",
)


def prepare(args):
    for path in (args.build_output, args.bundle):
        new_output(path, (args.cross_work, args.native_work, args.core_bundle, args.config, args.upstream_root))
    if args.build_output in args.bundle.parents or args.bundle in args.build_output.parents:
        raise ValueError("separate_new_build_and_bundle_required")
    validate_python_sources(ROOT / "tools", SOURCES)
    cross = json.loads((args.cross_work / "cross-build-report.json").read_text())
    core = verify_bundle(args.core_bundle)
    host = json.loads((ROOT / "docs/slam-preprocess-repeatability-evidence.json").read_text())
    if (cross["result"] != "arm64_cross_built_and_linked_not_executed"
            or cross["sourceCommits"] != PINS
            or sha256(args.cross_work / "cross-build-report.json") != core["crossBuildReportSha256"]
            or sha256(args.config / "trial-manifest.json") != host["unchangedConfigurationManifestSha256"]
            or sha256(ROOT / "tools/run_glim_preprocess_trial.py") != host["runnerSourceSha256"]):
        raise ValueError("verified_existing_core_and_unchanged_host_configuration_required")
    for name, digest in SOURCE_PINS.items():
        if sha256(args.upstream_root / name) != digest:
            raise ValueError("pinned_actual_preprocessing_source_required")
    args.build_output.mkdir(parents=True, mode=0o700)
    source = args.build_output / "source"
    source.mkdir(mode=0o700)
    for name, digest in host["adapterSourcesSha256"].items():
        original = ROOT / "tools/glim-preprocess" / name
        if sha256(original) != digest:
            raise ValueError("unchanged_host_native_adapter_source_required")
        shutil.copyfile(original, source / name)
    env = cross_environment(args.cross_work, args.native_work)
    report = {
        "purpose": "arm64_unchanged_native_preprocessing_adapter_cross_build",
        "sourceCommits": PINS, "upstreamSourcesSha256": SOURCE_PINS,
        "crossCoreReportSha256": sha256(args.cross_work / "cross-build-report.json"),
        "sourceSha256": host["adapterSourcesSha256"], "stages": [],
        "targetExecutionValidated": False,
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
                result = subprocess.run(command, env=env, stdout=log, stderr=log, timeout=180)
            report["stages"].append({"name": name, "exitCode": result.returncode})
            if result.returncode:
                raise RuntimeError("arm64_preprocessing_build_failed_see_private_log")
        library = args.build_output / "build/liblingxi_glim_preprocess.so"
        report["artifact"] = inspect_elf(
            library, args.cross_work / "toolchain/usr/bin/aarch64-linux-gnu-readelf", env)
        report["result"] = "arm64_preprocessing_adapter_built_not_executed"
    except BaseException:
        report["result"] = "failed_or_interrupted"
        raise
    finally:
        save(args.build_output / "build-report.json", report)
    args.bundle.mkdir(parents=True, mode=0o700)
    for name in ("tools", "lib", "source"):
        (args.bundle / name).mkdir(mode=0o700)
    for name in SOURCES:
        shutil.copyfile(ROOT / "tools" / name, args.bundle / "tools" / name)
    subprocess.run([
        sys.executable, "-I", "-B", "-c",
        "import sys; sys.path.insert(0, sys.argv[1]); import glim_target_preprocess",
        str(args.bundle / "tools"),
    ], check=True, cwd=args.bundle, timeout=30)
    shutil.copytree(args.config, args.bundle / "config")
    for path, name in (
        (library, "lib/liblingxi_glim_preprocess.so"),
        (args.build_output / "build-report.json", "adapter-build-report.json"),
        (ROOT / "docs/slam-capture-evidence.json", "capture-manifest.json"),
        (ROOT / "docs/slam-preprocess-repeatability-evidence.json", "host-repeatability-evidence.json"),
    ):
        shutil.copyfile(path, args.bundle / name)
    for name in host["adapterSourcesSha256"]:
        shutil.copyfile(source / name, args.bundle / "source" / name)
    manifest = {
        "schemaVersion": 1, "purpose": PURPOSE, "sourceCommits": PINS,
        "timingHypothesis": "header_is_scan_end", "controlIsNotProductionConfiguration": True,
        "navigationValidated": False,
        "coreBundleManifestSha256": sha256(args.core_bundle / "bundle-manifest.json"),
        "adapterBuildReportSha256": sha256(args.build_output / "build-report.json"),
        "files": {},
    }
    for path in sorted(args.bundle.rglob("*")):
        if path.is_symlink():
            raise ValueError("only_direct_private_bundle_files_supported")
        if path.is_dir():
            path.chmod(0o700)
        elif path.is_file():
            path.chmod(0o600)
            manifest["files"][path.relative_to(args.bundle).as_posix()] = {
                "sha256": sha256(path), "bytes": path.stat().st_size,
            }
    save(args.bundle / "preprocess-manifest.json", manifest)
    verify_preprocess_bundle(args.bundle)
    return {"result": "private_arm64_preprocessing_bundle_prepared",
            "files": len(manifest["files"]) + 1, "artifact": report["artifact"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cross-work", "native-work", "core-bundle", "config", "upstream-root", "build-output", "bundle"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    old_umask = os.umask(0o077)
    try:
        print(json.dumps(prepare(args)))
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
