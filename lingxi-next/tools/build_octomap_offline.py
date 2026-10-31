"""Build the existing adapter against explicitly selected local OctoMap libraries."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from evaluate_recorded_clearance import ROOT, digest


def build(sysroot, output):
    from refine_saved_map import private_output
    sysroot = Path(sysroot).resolve(strict=True)
    output = private_output(output)
    include, libraries = sysroot / "usr/include", sysroot / "usr/lib/x86_64-linux-gnu"
    dependencies = {name: (libraries / name).resolve(strict=True)
                    for name in ("liboctomap.so.1.9.7", "liboctomath.so.1.9.7")}
    if not (include / "octomap/octomap.h").is_file():
        raise ValueError("explicit_local_octomap_headers_required")
    output.mkdir(mode=0o700)
    (output / "lib").mkdir(mode=0o700)
    headers = {path.relative_to(include / "octomap").as_posix(): digest(path)
               for path in (include / "octomap").rglob("*") if path.is_file()}
    # Import only OctoMap headers, never another distribution's libc headers.
    shutil.copytree(include / "octomap", output / "include/octomap")
    for name, source in dependencies.items():
        shutil.copyfile(source, output / "lib" / name)
        (output / "lib" / name.removesuffix(".7")).symlink_to(name)
    source = ROOT / "robot-navigation/native-octomap/octomap_bridge.cpp"
    source_hash = digest(source)
    command = ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Wpedantic", "-shared", "-fPIC",
        '-DLINGXI_OCTOMAP_VERSION="1.9.7"', "-I" + str(output / "include"), str(source),
        *(str(output / "lib" / name) for name in dependencies), "-Wl,-rpath,$ORIGIN/lib",
        "-o", str(output / "liblingxi_octomap.so")]
    with (output / "private-build.log").open("x") as stream:
        subprocess.run(command, stdout=stream, stderr=stream, check=True, timeout=90)
        subprocess.run([sys.executable, "-c", "import ctypes, sys; native = ctypes.CDLL(sys.argv[1]); "
            "assert native.lingxi_octomap_abi_version() == 1", str(output / "liblingxi_octomap.so")],
            stdout=stream, stderr=stream, check=True, timeout=10,
            env={key: value for key, value in os.environ.items() if key != "LD_LIBRARY_PATH"})
    if digest(source) != source_hash or any(digest(source) != digest(output / "lib" / name)
                                          for name, source in dependencies.items()) or any(
            digest(include / "octomap" / name) != before or digest(output / "include/octomap" / name) != before
            for name, before in headers.items()):
        raise ValueError("unchanged_offline_adapter_sources_and_dependencies_required")
    report = {"schemaVersion": 1, "purpose": "offline_native_build_not_target_acceptance",
        "sourceSha256": source_hash, "nativeSha256": digest(output / "liblingxi_octomap.so"),
        "headersSha256": headers,
        "dependencies": {name: digest(path) for name, path in dependencies.items()},
        "compiler": subprocess.check_output(["g++", "--version"], text=True).splitlines()[0],
        "octomapVersion": "1.9.7", "freshProcessLoadPassed": True,
        "robotContacted": False, "navigationReady": False}
    with (output / "build-report.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sysroot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        print(json.dumps(build(args.sysroot, args.output)))
    finally:
        os.umask(previous)
