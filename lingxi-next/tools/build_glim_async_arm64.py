"""Cross-link the verified full async mapper for ARM64; never execute on a robot."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
from types import SimpleNamespace

from build_glim_point_arm64 import inputs as verified_cross_inputs
from build_slam_arm64_trial import cross_environment, inspect_elf
import run_glim_async_mapping as asynchronous

ABI = ("lingxi_full_error", "lingxi_full_open", "lingxi_full_imu", "lingxi_full_finish",
       "lingxi_full_shutdown", "lingxi_full_reload", "lingxi_async_status", "lingxi_async_snapshot")


def inputs(args):
    cross = verified_cross_inputs(args)
    cross.pop("nativePointSourceSha256")
    cross.pop("builderSourceSha256")
    return {"crossCore": cross, "mapper": asynchronous.inputs(SimpleNamespace(
        work=args.native_work, upstream=args.upstream)),
        "builderSha256": asynchronous.full.sha256(Path(__file__).resolve())}


def build(args):
    if platform.system() != "Linux" or platform.machine() != "x86_64" or os.geteuid() == 0:
        raise ValueError("ordinary_user_linux_cross_build_host_required")
    output = asynchronous.full.private_output(args.output)
    if any(output == path or path in output.parents for path in (args.cross_work, args.native_work, args.upstream)):
        raise ValueError("new_async_cross_output_outside_inputs_required")
    frozen = inputs(args)
    output.mkdir(mode=0o700)
    source = output / "source"
    source.mkdir()
    for name in asynchronous.NATIVE_SOURCES:
        shutil.copyfile(asynchronous.SOURCE / name, source / name)
    (source / "sync").mkdir()
    shutil.copyfile(asynchronous.full.SOURCE / "full_mapping.cpp", source / "sync/full_mapping.cpp")
    for name in asynchronous.full.UPSTREAM_INPUTS:
        target = source / "upstream" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.upstream / name, target)
    patch = asynchronous.full.SOURCE / "submapping-eof.patch"
    plan = (
        ("patch-check", ["git", "-C", str(source / "upstream"), "apply", "--check", "--recount", str(patch)]),
        ("patch", ["git", "-C", str(source / "upstream"), "apply", "--recount", str(patch)]),
        ("configure", ["cmake", "-S", str(source), "-B", str(output / "build"), "-G", "Ninja",
                       "-DCMAKE_TOOLCHAIN_FILE=" + str(args.cross_work / "arm64-toolchain.cmake"),
                       "-DCMAKE_PREFIX_PATH=" + str(args.cross_work / "prefix") + ";" + str(args.cross_work / "sysroot/usr"),
                       "-DCMAKE_NM=" + str(args.cross_work / "toolchain/usr/bin/aarch64-linux-gnu-nm"),
                       "-DCMAKE_BUILD_TYPE=Release"]),
        ("build", ["cmake", "--build", str(output / "build"), "--parallel", "1"]),
    )
    environment = cross_environment(args.cross_work, args.native_work)
    for name, command in plan:
        with (output / (name + ".private.log")).open("x") as log:
            result = subprocess.run(command, stdout=log, stderr=log, env=environment, timeout=600)
        if result.returncode:
            raise RuntimeError("async_arm64_build_failed_see_private_log_" + name)
    library = output / "build" / asynchronous.full.LIBRARY
    artifact = inspect_elf(library, args.cross_work / "toolchain/usr/bin/aarch64-linux-gnu-readelf", environment)
    symbols = subprocess.run([str(args.cross_work / "toolchain/usr/bin/aarch64-linux-gnu-nm"),
                              "-D", "--defined-only", str(library)], env=environment,
                             check=True, capture_output=True, text=True, timeout=30).stdout
    names = {line.split()[-1] for line in symbols.splitlines() if line.split()}
    if not set(ABI).issubset(names):
        raise ValueError("complete_arm64_async_c_abi_required")
    demangled = subprocess.run(["c++filt"], input=symbols, check=True,
                              capture_output=True, text=True, timeout=30).stdout
    if ("glim::SubMapping::" in demangled or "glim::LingxiSubMappingEof::insert_finalized_frame(" not in demangled
            or "glim::LingxiSubMappingEof::submit_end_of_sequence()" not in demangled):
        raise ValueError("private_arm64_eof_implementation_required")
    if inputs(args) != frozen:
        raise ValueError("async_cross_inputs_changed")
    report = {"result": "arm64_bounded_async_mapper_linked_not_target_executed", "inputs": frozen,
              "artifact": artifact, "exportedCFunctions": list(ABI), "privateEofSymbolsVerified": True,
              "copiedSourcesSha256": {path.relative_to(source).as_posix(): asynchronous.full.sha256(path)
                                      for path in sorted(source.rglob("*")) if path.is_file()},
              "boundaries": dict.fromkeys(asynchronous.full.BOUNDARIES, False)}
    asynchronous.full.save(output / "build-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cross-work", "native-work", "upstream", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        setattr(args, name, value.absolute())
    previous = os.umask(0o077)
    try:
        report = build(args)
        print(json.dumps({"result": report["result"], "machine": report["artifact"]["machine"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
