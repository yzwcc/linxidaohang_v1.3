#!/usr/bin/env python3
"""Build the pinned X2 native components in a new, isolated Humble directory."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import struct
import subprocess
import sys
import sysconfig


REVISION = "90bbc8096c23a5660fc6c5e26dd99ec1ed79da11"
PACKAGES = ("fast_lio", "robot_nav_recorder", "robot_nav_costmap", "pointcloud_to_laserscan")
INTEGRATIONS = [f"integrations/{name}" for name in (*PACKAGES, "grid_export")]
EXECUTABLES = (
    "overlay/lib/fast_lio/fastlio_mapping",
    "overlay/lib/robot_nav_recorder/mapping_recorder",
    "overlay/lib/pointcloud_to_laserscan/pointcloud_to_laserscan_node",
)
LIBRARIES = ("overlay/lib/librobot_nav_costmap.so", "overlay/lib/libpointcloud_to_laserscan.so")
REQUIRED_ASSETS = (
    "overlay/local_setup.bash",
    "overlay/share/robot_nav_costmap/plugins.xml",
    "overlay/share/ament_index/resource_index/nav2_costmap_2d__pluginlib__plugin/robot_nav_costmap",
    *(f"overlay/share/ament_index/resource_index/packages/{name}" for name in PACKAGES),
)
MACHINES = {"aarch64": 183, "x86_64": 62}


def git(source, *args):
    return subprocess.check_output(["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(source), *args])


def checked_source(source):
    source = source.resolve(strict=True)
    root = Path(os.fsdecode(git(source, "rev-parse", "--show-toplevel").strip())).resolve()
    if root != source or git(source, "rev-parse", "HEAD").strip().decode("ascii") != REVISION:
        raise ValueError("source must be the root of the pinned reference checkout")
    if git(source, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("use a clean reference checkout; do not build from a patched tree")
    for name in (*INTEGRATIONS, "deploy", "robot_nav_tasks"):
        if not (source / name).is_dir() or (source / name).is_symlink():
            raise ValueError(f"missing reference checkout directory: {name}")
    for name in (*(f"{directory}/CMakeLists.txt" for directory in INTEGRATIONS),
                 *(f"integrations/{package}/package.xml" for package in PACKAGES),
                 "integrations/grid_export/vendor/patchwork-plusplus/cpp/CMakeLists.txt",
                 "deploy/nav2_services.launch.py", "LICENSE"):
        file = source / name
        if not file.is_file() or file.resolve(strict=True) != file:
            raise ValueError(f"missing or linked reference build input: {name}")
    return source


def sha256(file):
    digest = hashlib.sha256()
    with file.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_install(output, machine, suffix):
    libraries = (*LIBRARIES, f"grid/grid_export_native{suffix}", f"grid/nav_ground_native{suffix}")
    checks = {}
    for name in (*EXECUTABLES, *libraries, *REQUIRED_ASSETS):
        file = output / name
        # Reject missing/symlinked build artifacts and checkout-dependent links.
        if not file.is_file() or file.resolve(strict=True) != file:
            raise ValueError(f"missing or linked native output: {name}")
        mode = file.stat().st_mode
        if name in EXECUTABLES and not (mode & stat.S_IXUSR):
            raise ValueError(f"native output is not executable: {name}")
        if name in (*EXECUTABLES, *libraries):
            with file.open("rb") as stream:
                head = stream.read(64)
            if len(head) != 64 or head[:7] != b"\x7fELF\x02\x01\x01":
                raise ValueError(f"native output is not little-endian ELF64: {name}")
            kind, actual = struct.unpack_from("<HH", head, 16)
            if actual != MACHINES[machine] or kind not in ((2, 3) if name in EXECUTABLES else (3,)):
                raise ValueError(f"native output type or architecture mismatch: {name}")
        checks[name] = sha256(file)
    return checks


def copy_notices(source, output):
    names = git(source, "ls-tree", "-rz", "--name-only", REVISION, "--", "LICENSE", *INTEGRATIONS)
    notices = {}
    for raw in names.split(b"\0"):
        if not raw:
            continue
        name = Path(os.fsdecode(raw))
        if not (name.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")) or name.name == "package.xml"):
            continue
        file = output / "notices" / name
        file.parent.mkdir(parents=True, exist_ok=True)
        # Copy the tracked bytes, not any ignored files or linked local paths.
        file.write_bytes(git(source, "show", f"{REVISION}:{name.as_posix()}"))
        notices[file.relative_to(output).as_posix()] = sha256(file)
    if "notices/LICENSE" not in notices:
        raise ValueError("reference license missing")
    return notices


def build(source, output, jobs):
    machine = platform.machine()
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if (not sys.platform.startswith("linux") or machine not in MACHINES or sys.version_info[:2] != (3, 10)
            or not suffix or not suffix.startswith(".cpython-310-")):
        raise ValueError("use native Linux x86_64 or aarch64 with Humble's CPython 3.10")
    if os.environ.get("ROS_DISTRO") != "humble" or not os.environ.get("AMENT_PREFIX_PATH"):
        raise ValueError("source the prepared ROS 2 Humble and build dependency overlays first")
    if not all(shutil.which(tool) for tool in ("git", "cmake", "colcon")):
        raise ValueError("git, cmake and colcon must already be installed")
    source = checked_source(source)
    if not output.is_absolute() or output.is_symlink():
        raise ValueError("output must be a new absolute directory")
    output = output.parent.resolve(strict=True) / output.name
    if output == source or source in output.parents:
        raise ValueError("place the build outside of the reference checkout")
    output.mkdir(mode=0o700)
    env = dict(os.environ, CMAKE_BUILD_PARALLEL_LEVEL=str(jobs), COLCON_HOME=str(output / "colcon-home"))
    env.pop("COLCON_DEFAULTS_FILE", None)

    def run(*args):
        subprocess.run(args, cwd=output, env=env, check=True)

    run("colcon", "--log-base", str(output / "logs"), "build", "--executor", "sequential",
        "--base-paths", *(str(source / name) for name in INTEGRATIONS[:-1]),
        "--packages-select", *PACKAGES,
        "--build-base", str(output / "build"), "--install-base", str(output / "overlay"), "--merge-install",
        "--cmake-args", "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_TESTING=OFF")
    run("cmake", "-S", str(source / "integrations/grid_export"), "-B", str(output / "grid-build"),
        "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_INSTALL_PREFIX={output / 'grid'}",
        f"-DPython3_EXECUTABLE={sys.executable}")
    run("cmake", "--build", str(output / "grid-build"), "--parallel", str(jobs),
        "--target", "grid_export_native", "nav_ground_native")
    run("cmake", "--install", str(output / "grid-build"))
    checked_source(source)
    files = inspect_install(output, machine, suffix)
    files.update(copy_notices(source, output))
    manifest = {"schema": 1, "referenceRevision": REVISION, "machine": machine, "rosDistro": "humble",
                "pythonExtensionSuffix": suffix, "files": files, "dependencyClosureVerified": False,
                "targetAccepted": False}
    with (output / "SHA256SUMS.native").open("x", encoding="utf-8", newline="\n") as stream:
        for name, digest in sorted(files.items()):
            stream.write(f"{digest}  {name}\n")
    with (output / "native-build.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return output / "native-build.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--jobs", type=int, default=2, choices=range(1, 9), metavar="1-8")
    args = parser.parse_args()
    try:
        print(build(args.source, args.output, args.jobs))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Native build did not complete: {error}\n")


if __name__ == "__main__":
    main()
