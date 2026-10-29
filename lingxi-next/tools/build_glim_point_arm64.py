"""Build the isolated corrected-point extension, never execute it on a robot."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

from build_slam_arm64_trial import cross_environment, inspect_elf
from build_slam_cpu_trial import PINS
import run_glim_point_output_trial as points
from verify_slam_capture import sha256


RESULT = "arm64_corrected_point_extension_linked_not_target_executed"


def inputs(args):
    core = points.replay.load(args.cross_work / "cross-build-report.json")
    native = points.replay.load(args.native_work / "build-report.json")
    if (core["result"] != "arm64_cross_built_and_linked_not_executed"
            or core["sourceCommits"] != PINS or core["targetMachine"] != "aarch64"
            or native["result"] != "core_libraries_built_not_target_acceptance"
            or native["sourceCommits"] != PINS or native["hostMachine"] != "x86_64"):
        raise ValueError("verified_pinned_cross_core_and_host_tools_required")
    reference = points.replay.load(points.ROOT / "docs/slam-supervised-arm64-evidence.json")
    libraries = {}
    for name, digest in reference["actualLoadedLibrariesSha256"].items():
        if name == "liblingxi_glim_offline.so":
            continue
        path = args.cross_work / "prefix/lib" / name
        if sha256(path) != digest:
            raise ValueError("same_previously_loaded_arm64_core_required")
        libraries[name] = digest
    for artifact in core["artifacts"]:
        if artifact["file"].endswith(".so") and sha256(
                args.cross_work / "prefix/lib" / artifact["file"]) != artifact["sha256"]:
            raise ValueError("unchanged_cross_core_artifacts_required")
    return {
        "crossCoreBuildSha256": sha256(args.cross_work / "cross-build-report.json"),
        "nativeToolsBuildSha256": sha256(args.native_work / "build-report.json"),
        "toolchainSha256": sha256(args.cross_work / "arm64-toolchain.cmake"),
        "sourceCommits": PINS, "referenceCoreLibrariesSha256": libraries,
        "nativePointSourceSha256": {name: sha256(points.ROOT / name) for name in points.NATIVE_NAMES},
        "builderSourceSha256": sha256(__file__),
    }


def commands(args, source):
    return (
        ("configure", [
            "cmake", "-S", str(source), "-B", str(args.output / "build"), "-G", "Ninja",
            "-DCMAKE_TOOLCHAIN_FILE=" + str(args.cross_work / "arm64-toolchain.cmake"),
            "-DCMAKE_PREFIX_PATH=" + str(args.cross_work / "prefix") + ";" + str(args.cross_work / "sysroot/usr"),
            "-DCMAKE_NM=" + str(args.cross_work / "toolchain/usr/bin/aarch64-linux-gnu-nm"),
            "-DCMAKE_BUILD_TYPE=Release",
        ]),
        ("build", ["cmake", "--build", str(args.output / "build"), "--parallel", "2"]),
        # This inspection uses the host CMake and cross nm, never the ARM64 executable.
        ("exported_abi_only", ["ctest", "--test-dir", str(args.output / "build"),
                               "-R", "^point_output_exported_abi$", "--output-on-failure"]),
    )


def build(args):
    if platform.system() != "Linux" or platform.machine() != "x86_64" or os.geteuid() == 0:
        raise ValueError("ordinary_user_linux_cross_build_host_required")
    output = points.private_output(args.output)
    if any(output == path or path in output.parents for path in (args.cross_work, args.native_work)):
        raise ValueError("new_output_outside_frozen_inputs_required")
    frozen = inputs(args)
    output.mkdir(mode=0o700, parents=True)
    source = output / "source"
    source.mkdir(mode=0o700)
    for name in points.NATIVE_NAMES:
        destination = source / Path(name).name
        shutil.copyfile(points.ROOT / name, destination)
        if sha256(destination) != frozen["nativePointSourceSha256"][name]:
            raise ValueError("identical_point_source_snapshot_required")
    report = {
        "schemaVersion": 1, "result": "running", "inputs": frozen,
        "buildType": "Release", "stages": [], "artifacts": [],
        "hostAbiInspectionExecuted": False, "nativeContractExecutionsOnTarget": 0,
        "targetExecutionValidated": False, "robotContacted": False,
        "runtimePackageBuilt": False, "deploymentApproved": False,
    }
    environment = cross_environment(args.cross_work, args.native_work)
    try:
        for name, command in commands(args, source):
            with (output / (name + ".log")).open("x") as log:
                result = subprocess.run(command, stdout=log, stderr=log, env=environment, timeout=180)
            report["stages"].append({"name": name, "exitCode": result.returncode})
            if result.returncode:
                raise RuntimeError("arm64_point_extension_build_failed_see_private_log")
        readelf = args.cross_work / "toolchain/usr/bin/aarch64-linux-gnu-readelf"
        for name in (points.LIBRARY, "point_output_contract"):
            report["artifacts"].append(inspect_elf(output / "build" / name, readelf, environment))
        if inputs(args) != frozen:
            raise ValueError("cross_inputs_changed_during_point_build")
        report.update(result=RESULT, hostAbiInspectionExecuted=True)
    except BaseException:
        report["result"] = "failed_or_interrupted"
        raise
    finally:
        points.replay.private_json(output / "build-report.json", report)
    return report


def evidence(output):
    report = points.replay.load(output / "build-report.json")
    if (report["result"] != RESULT or report["buildType"] != "Release"
            or report["inputs"]["sourceCommits"] != PINS
            or report["inputs"]["builderSourceSha256"] != sha256(__file__)
            or report["inputs"]["nativePointSourceSha256"]
            != {name: sha256(points.ROOT / name) for name in points.NATIVE_NAMES}
            or report["stages"] != [{"name": name, "exitCode": 0}
                                    for name in ("configure", "build", "exported_abi_only")]
            or report["hostAbiInspectionExecuted"] is not True
            or type(report["nativeContractExecutionsOnTarget"]) is not int
            or report["nativeContractExecutionsOnTarget"] != 0
            or any(report[key] is not False for key in (
                "targetExecutionValidated", "robotContacted", "runtimePackageBuilt", "deploymentApproved"))
            or [item["file"] for item in report["artifacts"]] != [points.LIBRARY, "point_output_contract"]):
        raise ValueError("complete_nonexecuted_arm64_point_build_required")
    for artifact in report["artifacts"]:
        if (artifact["machine"] != "AArch64" or artifact["class"] != "ELF64"
                or artifact["sha256"] != sha256(output / "build" / artifact["file"])):
            raise ValueError("unchanged_arm64_point_build_artifact_required")
    return {
        "schemaVersion": 1, "date": "2026-10-29", "result": RESULT,
        "privateBuildReportSha256": sha256(output / "build-report.json"),
        "inputs": report["inputs"], "buildType": "Release", "stages": report["stages"],
        "artifacts": report["artifacts"], "exportedCFunctions": [
            "lingxi_points_close", "lingxi_points_counts", "lingxi_points_error",
            "lingxi_points_open", "lingxi_points_take",
        ],
        "hostAbiInspectionExecuted": True, "nativeContractExecutionsOnTarget": 0,
        "targetExecutionValidated": False, "robotContacted": False,
        "runtimePackageBuilt": False, "deploymentApproved": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands_parser = parser.add_subparsers(dest="command", required=True)
    builder = commands_parser.add_parser("build")
    for name in ("cross-work", "native-work", "output"):
        builder.add_argument("--" + name, type=Path, required=True)
    exporter = commands_parser.add_parser("export")
    for name in ("build", "output"):
        exporter.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    previous = os.umask(0o077)
    try:
        if args.command == "export":
            report = evidence(args.build)
            points.replay.private_json(args.output, report)
        else:
            report = build(args)
        print(json.dumps({"result": report["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
