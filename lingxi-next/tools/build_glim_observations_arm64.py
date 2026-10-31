"""Cross-build the tested observed mapper and isolated native fixture, without target execution."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
from types import SimpleNamespace

from build_glim_point_arm64 import inputs as cross_inputs
from build_glim_async_arm64 import ABI as ASYNC_ABI
from build_slam_arm64_trial import cross_environment, inspect_elf
import build_glim_observations as native

RESULT = "arm64_observed_mapper_and_fixture_linked_not_target_executed"
LIBRARY = native.asynchronous.full.LIBRARY
FIXTURE = "lingxi_observation_contract"
ABI = (*ASYNC_ABI, "lingxi_observation_enable", "lingxi_observation_save",
       "lingxi_observation_shutdown", "lingxi_observation_error")
FIXTURE_SOURCE = native.ROOT / "tools/tests/native/test_observation_capture.cpp"
CMAKE = """cmake_minimum_required(VERSION 3.22)
project(lingxi_observed_contract LANGUAGES CXX)
find_package(glim REQUIRED)
add_subdirectory(../source mapper)
set_target_properties(lingxi_glim_full_mapping PROPERTIES LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR})
add_executable(lingxi_observation_contract test_observation_capture.cpp)
target_compile_features(lingxi_observation_contract PRIVATE cxx_std_17)
target_compile_options(lingxi_observation_contract PRIVATE -Wall -Wextra -Werror)
target_include_directories(lingxi_observation_contract PRIVATE ../source)
target_link_libraries(lingxi_observation_contract PRIVATE lingxi_glim_full_mapping glim::glim)
set_target_properties(lingxi_observation_contract PROPERTIES BUILD_RPATH_USE_ORIGIN ON)
"""


def native_args(args):
    return SimpleNamespace(work=args.native_work, upstream=args.upstream, mapping_adapter=args.native_adapter)


def inputs(args):
    native.verified_adapter(native_args(args))
    core = cross_inputs(args)
    core.pop("nativePointSourceSha256")
    core.pop("builderSourceSha256")
    return {"crossCore": core, "observations": native.inputs(native_args(args)),
            "nativeBuildReportSha256": native.asynchronous.full.sha256(args.native_adapter / "build-report.json"),
            "fixtureSha256": native.asynchronous.full.sha256(FIXTURE_SOURCE),
            "builderSha256": native.asynchronous.full.sha256(Path(__file__).resolve())}


def inventory(source):
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("ordinary_frozen_observation_sources_required")
    return {path.relative_to(source).as_posix(): native.asynchronous.full.sha256(path)
            for path in sorted(source.rglob("*")) if path.is_file()}


def exported_abi(args, environment):
    symbols = subprocess.run([str(args.cross_work / "toolchain/usr/bin/aarch64-linux-gnu-nm"),
        "-D", "--defined-only", str(args.output / "build" / LIBRARY)],
        check=True, capture_output=True, text=True, env=environment, timeout=30).stdout
    names = {line.split()[-1] for line in symbols.splitlines() if line.split()}
    if not set(ABI).issubset(names):
        raise ValueError("complete_arm64_observation_and_async_c_abi_required")
    demangled = subprocess.run(["c++filt"], input=symbols, check=True,
        capture_output=True, text=True, timeout=30).stdout
    if ("glim::SubMapping::" in demangled
            or "glim::LingxiSubMappingEof::insert_finalized_frame(" not in demangled
            or "glim::LingxiSubMappingEof::submit_end_of_sequence()" not in demangled):
        raise ValueError("private_arm64_observed_submapping_implementation_required")


def commands(args):
    return (("configure", ["cmake", "-S", str(args.output / "contract"), "-B", str(args.output / "build"),
        "-G", "Ninja", "-DCMAKE_TOOLCHAIN_FILE=" + str(args.cross_work / "arm64-toolchain.cmake"),
        "-DCMAKE_PREFIX_PATH=" + str(args.cross_work / "prefix") + ";" + str(args.cross_work / "sysroot/usr"),
        "-DCMAKE_NM=" + str(args.cross_work / "toolchain/usr/bin/aarch64-linux-gnu-nm"),
        "-DCMAKE_BUILD_TYPE=Release"]),
        ("build", ["cmake", "--build", str(args.output / "build"), "--parallel", "1"]))


def build(args):
    if platform.system() != "Linux" or platform.machine() != "x86_64" or os.geteuid() == 0:
        raise ValueError("ordinary_user_linux_cross_build_host_required")
    output = native.asynchronous.full.private_output(args.output)
    for path in (args.cross_work, args.native_work, args.upstream, args.native_adapter):
        if output == path or path in output.parents or output in path.parents:
            raise ValueError("new_observation_cross_output_outside_inputs_required")
    frozen = inputs(args)
    original = inventory(args.native_adapter / "source")
    output.mkdir(mode=0o700)
    source = output / "source"
    shutil.copytree(args.native_adapter / "source", source)
    if inventory(source) != original:
        raise ValueError("same_tested_native_observation_sources_required")
    contract = output / "contract"
    contract.mkdir()
    (contract / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    shutil.copyfile(FIXTURE_SOURCE, contract / "test_observation_capture.cpp")
    report = {"schemaVersion": 1, "result": "running", "inputs": frozen, "buildType": "Release",
              "stages": [], "artifacts": [], "copiedSourceSha256": original,
              "exportedCFunctions": list(ABI), "privateEofSymbolsVerified": False,
              "boundaries": dict.fromkeys(native.asynchronous.full.BOUNDARIES, False)}
    environment = cross_environment(args.cross_work, args.native_work)
    try:
        for name, command in commands(args):
            with (output / (name + ".private.log")).open("x") as log:
                result = subprocess.run(command, stdout=log, stderr=log, env=environment, timeout=600)
            report["stages"].append({"name": name, "exitCode": result.returncode})
            if result.returncode:
                raise RuntimeError("observed_arm64_" + name + "_failed_see_private_log")
        readelf = args.cross_work / "toolchain/usr/bin/aarch64-linux-gnu-readelf"
        report["artifacts"] = [inspect_elf(output / "build" / name, readelf, environment)
                               for name in (LIBRARY, FIXTURE)]
        exported_abi(args, environment)
        if (inputs(args) != frozen or inventory(source) != original
                or (contract / "CMakeLists.txt").read_text(encoding="utf-8") != CMAKE
                or native.asynchronous.full.sha256(contract / "test_observation_capture.cpp") != frozen["fixtureSha256"]):
            raise ValueError("observation_cross_build_inputs_changed")
        report.update(result=RESULT, privateEofSymbolsVerified=True)
    except BaseException:
        report["result"] = "failed_or_interrupted"
        raise
    finally:
        native.asynchronous.full.save(output / "build-report.json", report)
    return report


def verify(args):
    report = json.loads((args.output / "build-report.json").read_bytes())
    frozen = inputs(args)
    expected = inventory(args.native_adapter / "source")
    if (report.get("schemaVersion") != 1 or report.get("result") != RESULT or report.get("inputs") != frozen
            or report.get("buildType") != "Release" or report.get("privateEofSymbolsVerified") is not True
            or report.get("exportedCFunctions") != list(ABI)
            or report.get("copiedSourceSha256") != expected or inventory(args.output / "source") != expected
            or report.get("stages") != [{"name": name, "exitCode": 0} for name in ("configure", "build")]
            or report.get("boundaries") != dict.fromkeys(native.asynchronous.full.BOUNDARIES, False)
            or (args.output / "contract/CMakeLists.txt").read_text(encoding="utf-8") != CMAKE
            or native.asynchronous.full.sha256(args.output / "contract/test_observation_capture.cpp") != frozen["fixtureSha256"]):
        raise ValueError("unchanged_source_bound_arm64_observation_build_required")
    environment = cross_environment(args.cross_work, args.native_work)
    readelf = args.cross_work / "toolchain/usr/bin/aarch64-linux-gnu-readelf"
    actual = [inspect_elf(args.output / "build" / name, readelf, environment) for name in (LIBRARY, FIXTURE)]
    if report["artifacts"] != actual:
        raise ValueError("unchanged_arm64_observation_artifacts_required")
    exported_abi(args, environment)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "verify"))
    for name in ("cross-work", "native-work", "upstream", "native-adapter", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.absolute())
    previous = os.umask(0o077)
    try:
        report = {"build": build, "verify": verify}[args.command](args)
        print(json.dumps({"result": report["result"], "artifacts": report["artifacts"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
