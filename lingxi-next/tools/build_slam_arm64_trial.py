"""Cross-build CPU SLAM libraries for Jammy ARM64, without ROS or robot access."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import struct
import subprocess
import time

from build_slam_cpu_trial import PINS, commands, package_inventory, verify_sources
from glim_trial_config import generate


ROOT = Path(__file__).resolve().parents[1]


def toolchain_text(work):
    root = PurePosixPath(work)
    if (any(character in str(root) for character in ('"', "$", "\\", ";"))
            or any(character.isspace() for character in str(root))):
        raise ValueError("unsupported_cmake_path")
    values = {
        "CMAKE_SYSTEM_NAME": "Linux",
        "CMAKE_SYSTEM_PROCESSOR": "aarch64",
        "CMAKE_SYSROOT": root / "sysroot",
        "CMAKE_C_COMPILER": root / "toolchain/usr/bin/aarch64-linux-gnu-gcc-11",
        "CMAKE_CXX_COMPILER": root / "toolchain/usr/bin/aarch64-linux-gnu-g++-11",
        "CMAKE_FIND_ROOT_PATH": str(root / "prefix") + ";" + str(root / "sysroot"),
        "CMAKE_FIND_ROOT_PATH_MODE_PROGRAM": "NEVER",
        "CMAKE_FIND_ROOT_PATH_MODE_LIBRARY": "ONLY",
        "CMAKE_FIND_ROOT_PATH_MODE_INCLUDE": "ONLY",
        "CMAKE_FIND_ROOT_PATH_MODE_PACKAGE": "ONLY",
    }
    library_flags = (
        "-L" + str(root / "sysroot/usr/lib/aarch64-linux-gnu")
        + " -L" + str(root / "sysroot/lib/aarch64-linux-gnu")
    )
    for kind in ("EXE", "SHARED", "MODULE"):
        values["CMAKE_" + kind + "_LINKER_FLAGS_INIT"] = library_flags
    if any(any(character in str(value) for character in ('"', "\n", "\r", "$"))
           for value in values.values()):
        raise ValueError("unsupported_cmake_path")
    return "".join('set(' + key + ' "' + str(value) + '")\n' for key, value in values.items())


def cross_commands(sources, work, jobs):
    root = Path(work)
    plan = commands(sources, root, jobs)
    for name, command in plan:
        if name.endswith("_configure"):
            command.append("-DCMAKE_TOOLCHAIN_FILE=" + str(root / "arm64-toolchain.cmake"))
    return plan


def cross_environment(work, native):
    work, native = Path(work), Path(native)
    env = os.environ.copy()
    for name in (
        "ROS_VERSION", "ROS_DISTRO", "AMENT_PREFIX_PATH", "COLCON_PREFIX_PATH",
        "CMAKE_PREFIX_PATH", "CPATH", "CPLUS_INCLUDE_PATH", "LIBRARY_PATH",
        "GCC_EXEC_PREFIX", "COMPILER_PATH", "CC", "CXX",
        "PKG_CONFIG_PATH", "PKG_CONFIG_LIBDIR", "PKG_CONFIG_SYSROOT_DIR",
    ):
        env.pop(name, None)
    env["PATH"] = os.pathsep.join((
        str(native / "sysroot/usr/bin"), str(work / "toolchain/usr/bin"), env["PATH"],
    ))
    env["LD_LIBRARY_PATH"] = os.pathsep.join((
        str(native / "sysroot/usr/lib/x86_64-linux-gnu"),
        str(work / "host-linker-libs"),
    ))
    return env


def prepare_host_linker_libraries(work):
    work = Path(work)
    source = work / "toolchain/usr/lib/x86_64-linux-gnu"
    destination = work / "host-linker-libs"
    destination.mkdir()
    # Do not put Jammy's older host libstdc++ ahead of the native CMake runtime.
    for pattern in ("libbfd-*.so", "libopcodes-*.so", "libctf*.so*"):
        for library in source.glob(pattern):
            resolved = library.resolve()
            if not resolved.is_relative_to((work / "toolchain").resolve()):
                raise ValueError("unexpected_cross_linker_library_path")
            (destination / library.name).symlink_to(resolved)


def inspect_elf(path, readelf, env):
    path = Path(path)
    with path.open("rb") as stream:
        header = stream.read(20)
    if len(header) != 20 or header[:6] != b"\x7fELF\x02\x01":
        raise ValueError("expected_little_endian_elf64")
    if struct.unpack_from("<H", header, 18)[0] != 183:
        raise ValueError("expected_aarch64_machine")
    versions = subprocess.check_output(
        [str(readelf), "--version-info", str(path)], text=True, env=env,
    )
    def requirements(prefix):
        return sorted({
            tuple(int(part) for part in match.split("."))
            for match in re.findall(r"\b" + prefix + r"_([0-9]+(?:\.[0-9]+)+)", versions)
        })

    glibc = requirements("GLIBC")
    if glibc and glibc[-1] > (2, 35):
        raise ValueError("glibc_requirement_newer_than_jammy")
    result = {
        "file": path.name, "machine": "AArch64", "class": "ELF64",
        "maxGlibcRequirement": ".".join(map(str, glibc[-1])) if glibc else None,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    for prefix in ("GLIBCXX", "CXXABI"):
        values = requirements(prefix)
        result["max" + prefix + "Requirement"] = ".".join(map(str, values[-1])) if values else None
    return result


def run(sources, cache, native, work, jobs):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("linux_x86_64_build_host_required")
    if os.geteuid() == 0:
        raise ValueError("ordinary_user_required")
    sources, cache, native, work = (
        Path(path).resolve() for path in (sources, cache, native, work)
    )
    if work.exists():
        raise ValueError("existing_work_is_preserved_use_new_directory")
    if any(parent == work or parent in work.parents for parent in (sources, cache, native)):
        raise ValueError("work_must_be_outside_input_caches")
    plan = cross_commands(sources, work, jobs)
    text = toolchain_text(work)
    verify_sources(sources)
    download = json.loads((cache / "download-report.json").read_text(encoding="utf-8"))
    if download.get("result") != "downloaded_not_installed":
        raise ValueError("successful_download_report_required")
    inventories = {}
    for architecture in ("amd64", "arm64"):
        packages = package_inventory(cache / architecture / "archives")
        if any(item["architecture"] not in (architecture, "all") for item in packages):
            raise ValueError("wrong_cache_architecture")
        if packages != download.get("caches", {}).get(architecture):
            raise ValueError("package_cache_changed_since_download")
        inventories[architecture] = packages
    core = json.loads((native / "build-report.json").read_text(encoding="utf-8"))
    if (core.get("result") != "core_libraries_built_not_target_acceptance"
            or core.get("hostMachine") != platform.machine()
            or core.get("sourceCommits") != PINS):
        raise ValueError("successful_native_tooling_baseline_required")
    work.mkdir(mode=0o700, parents=True)
    (work / "logs").mkdir()
    (work / "arm64-toolchain.cmake").write_text(text, encoding="utf-8")
    report = {
        "schemaVersion": 1, "purpose": "jammy_arm64_cpu_cross_build",
        "hostMachine": platform.machine(), "targetMachine": "aarch64",
        "sourceCommits": PINS, "packages": inventories, "stages": [], "artifacts": [],
        "systemPackagesInstalled": False, "targetExecutionValidated": False,
        "rosWrapperValidated": False, "robotCalibrationValidated": False,
        "navigationValidated": False, "containsMotionAdapter": False,
        "allTransitiveDependenciesPinned": False, "result": "running",
    }
    env = cross_environment(work, native)

    def checkpoint():
        (work / "cross-build-report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8",
        )

    def execute(name, command):
        print("Starting ARM64 " + name, flush=True)
        stage = {"name": name, "command": command, "result": "running"}
        report["stages"].append(stage)
        checkpoint()
        started = time.monotonic()
        with (work / "logs" / (name + ".log")).open("w") as log:
            completed = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        stage.update(
            exitCode=completed.returncode, seconds=time.monotonic() - started,
            result="passed" if completed.returncode == 0 else "failed",
        )
        checkpoint()
        if completed.returncode:
            raise RuntimeError("arm64_" + name + "_failed_see_log")

    checkpoint()
    try:
        for architecture, destination in (("amd64", "toolchain"), ("arm64", "sysroot")):
            for package in inventories[architecture]:
                execute("extract_" + architecture + "_" + package["package"], [
                    "dpkg-deb", "--extract",
                    str(cache / architecture / "archives" / package["file"]),
                    str(work / destination),
                ])
        prepare_host_linker_libraries(work)
        for name, command in plan:
            execute(name, command)
        profile = json.loads(
            (ROOT / "tools/glim-smoke/synthetic-profile.json").read_text(encoding="utf-8")
        )
        generate(sources / "glim", work / "synthetic-config", profile)
        execute("synthetic_link_configure", [
            "cmake", "-S", str(ROOT / "tools/glim-smoke"), "-B", str(work / "link-smoke"),
            "-G", "Ninja", "-DCMAKE_TOOLCHAIN_FILE=" + str(work / "arm64-toolchain.cmake"),
            "-DCMAKE_PREFIX_PATH=" + str(work / "prefix") + ";" + str(work / "sysroot/usr"),
            "-DGLIM_TRIAL_CONFIG=" + str(work / "synthetic-config"),
        ])
        execute("synthetic_link_build", [
            "cmake", "--build", str(work / "link-smoke"), "--parallel", str(jobs),
        ])
        readelf = work / "toolchain/usr/bin/aarch64-linux-gnu-readelf"
        for name in (
            "libgtsam.so", "libgtsam_unstable.so", "libgtsam_points.so",
            "libglim.so", "libodometry_estimation_cpu.so",
            "libsub_mapping_passthrough.so", "libglobal_mapping_pose_graph.so",
        ):
            report["artifacts"].append(inspect_elf(work / "prefix/lib" / name, readelf, env))
        report["artifacts"].append(
            inspect_elf(work / "link-smoke/lingxi_glim_time_smoke", readelf, env)
        )
        verify_sources(sources)
        report["result"] = "arm64_cross_built_and_linked_not_executed"
    except BaseException:
        report["result"] = "failed_or_interrupted"
        raise
    finally:
        checkpoint()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-cache", "package-cache", "native-work-root", "work-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()
    report = run(
        args.source_cache, args.package_cache, args.native_work_root, args.work_root, args.jobs,
    )
    print(json.dumps({key: value for key, value in report.items() if key != "packages"}, indent=2))


if __name__ == "__main__":
    main()
