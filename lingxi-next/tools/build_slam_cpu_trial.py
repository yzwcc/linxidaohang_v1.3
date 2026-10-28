"""Build pinned CPU-only SLAM core libraries in an isolated user directory.

Linux only. Requires an existing source cache and APT-downloaded .deb files.
Does not download software, install system packages, start ROS or contact a robot.
This is a host build smoke check, not ARM64, ROS or navigation acceptance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import time


PINS = {
    "gtsam": "3ad4b4c3cb28394c9597f48fa02dad361c8450e3",
    "gtsam_points": "9d32e7dbecf6015560d84b4901d6b0a6f483ec46",
    "glim": "1a197c999e4865f5cab7c9cd25474128413ebbca",
}


def commands(sources, work, jobs):
    if type(jobs) is not int or not 1 <= jobs <= 4:
        raise ValueError("jobs_must_be_between_1_and_4")
    sources, work = Path(sources), Path(work)
    common = [
        "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_CXX_FLAGS_RELEASE=-O1 -DNDEBUG",
        "-DCMAKE_INSTALL_PREFIX=" + str(work / "prefix"),
        "-DCMAKE_INSTALL_LIBDIR=lib",
        "-DCMAKE_PREFIX_PATH=" + str(work / "prefix") + ";" + str(work / "sysroot/usr"),
    ]
    options = {
        "gtsam": [
            "-DGTSAM_BUILD_TESTS=OFF", "-DGTSAM_BUILD_EXAMPLES_ALWAYS=OFF",
            "-DGTSAM_BUILD_UNSTABLE=ON", "-DGTSAM_BUILD_PYTHON=OFF",
            "-DGTSAM_WITH_TBB=OFF", "-DGTSAM_USE_SYSTEM_EIGEN=ON",
            "-DGTSAM_BUILD_WITH_MARCH_NATIVE=OFF",
            "-DGTSAM_COMPILE_OPTIONS_PRIVATE_RELEASE=-O1",
        ],
        "gtsam_points": [
            "-DBUILD_WITH_CUDA=OFF", "-DBUILD_WITH_TBB=OFF",
            "-DBUILD_WITH_MARCH_NATIVE=OFF", "-DBUILD_TESTS=OFF",
            "-DBUILD_DEMO=OFF", "-DBUILD_EXAMPLE=OFF", "-DBUILD_TOOLS=OFF",
        ],
        "glim": [
            "-DBUILD_WITH_CUDA=OFF", "-DBUILD_WITH_VIEWER=OFF",
            "-DBUILD_WITH_OPENCV=OFF", "-DBUILD_WITH_MARCH_NATIVE=OFF",
        ],
    }
    result = []
    for name in PINS:
        build = work / "build" / name
        result.extend((
            (name + "_configure", ["cmake", "-S", str(sources / name), "-B", str(build)]
             + common + options[name]),
            (name + "_build", ["cmake", "--build", str(build), "--parallel", str(jobs)]),
            (name + "_install", ["cmake", "--install", str(build)]),
        ))
    return result


def verify_sources(sources):
    for name, expected in PINS.items():
        source = Path(sources) / name
        git = ["git", "-c", "core.fsmonitor=false", "-c", "core.autocrlf=true", "-C", str(source)]
        actual = subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip()
        if actual != expected:
            raise ValueError("unexpected_source_revision_" + name)
        dirty = subprocess.check_output(
            git + ["status", "--porcelain", "--untracked-files=all"], text=True,
        )
        if dirty.strip():
            raise ValueError("dirty_upstream_source_" + name)


def package_inventory(cache):
    packages = []
    names = set()
    for path in sorted(Path(cache).glob("*.deb")):
        if path.is_symlink() or not path.is_file():
            raise ValueError("indirect_package_archive")
        description = subprocess.check_output(
            ["dpkg-deb", "--show", "--showformat=${Package}\\t${Version}\\t${Architecture}", str(path)],
            text=True,
        ).split("\t")
        if len(description) != 3:
            raise ValueError("invalid_package_metadata")
        if description[0] in names:
            raise ValueError("duplicate_package_archives_use_a_clean_cache")
        names.add(description[0])
        packages.append({
            "file": path.name, "package": description[0], "version": description[1],
            "architecture": description[2], "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    if not packages:
        raise ValueError("no_downloaded_packages")
    return packages


def run(sources, cache, work, jobs):
    if platform.system() != "Linux":
        raise ValueError("linux_build_environment_required")
    sources, cache, work = (Path(path).resolve() for path in (sources, cache, work))
    if work.exists():
        raise ValueError("use_a_new_work_directory_existing_results_are_preserved")
    if any(parent == work or parent in work.parents for parent in (sources, cache)):
        raise ValueError("work_directory_must_be_outside_input_caches")
    plan = commands(sources, work, jobs)
    verify_sources(sources)
    packages = package_inventory(cache)
    architecture = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    if any(item["architecture"] not in ("all", architecture) for item in packages):
        raise ValueError("package_architecture_does_not_match_build_host")
    work.mkdir(mode=0o700, parents=True)
    logs = work / "logs"
    logs.mkdir()
    report = {
        "schemaVersion": 1, "purpose": "isolated_cpu_core_build_smoke",
        "hostMachine": platform.machine(), "hostOS": platform.freedesktop_os_release(),
        "sourceCommits": PINS, "packages": packages, "stages": [],
        "optimization": "O1_smoke_only_not_a_performance_benchmark",
        "targetArm64Validated": False, "rosWrapperValidated": False,
        "robotCalibrationValidated": False, "navigationValidated": False,
        "systemPackagesInstalled": False, "containsMotionAdapter": False,
        "allTransitiveDependenciesPinned": False, "result": "running",
    }

    def checkpoint():
        (work / "build-report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8",
        )

    env = os.environ.copy()
    for name in ("ROS_VERSION", "ROS_DISTRO", "AMENT_PREFIX_PATH", "COLCON_PREFIX_PATH"):
        env.pop(name, None)
    env["PATH"] = str(work / "sysroot/usr/bin") + os.pathsep + env["PATH"]
    # Debian's dpkg architecture calls x86_64 "amd64"; library directories use the GNU triplet.
    triplet = subprocess.check_output(["gcc", "-dumpmachine"], text=True).strip()
    env["LD_LIBRARY_PATH"] = os.pathsep.join((
        str(work / "prefix/lib"), str(work / "sysroot/usr/lib" / triplet),
    ))
    checkpoint()
    try:
        print("Extracting downloaded packages into " + str(work / "sysroot"), flush=True)
        with (logs / "package-extraction.log").open("w", encoding="utf-8") as log:
            for package in packages:
                subprocess.run(
                    ["dpkg-deb", "--extract", str(cache / package["file"]), str(work / "sysroot")],
                    check=True, stdout=log, stderr=subprocess.STDOUT,
                )
        for name, command in plan:
            print("Starting " + name, flush=True)
            stage = {"name": name, "command": command, "result": "running"}
            report["stages"].append(stage)
            checkpoint()
            started = time.monotonic()
            with (logs / (name + ".log")).open("w", encoding="utf-8") as log:
                result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
            stage.update(exitCode=result.returncode, seconds=time.monotonic() - started)
            stage["result"] = "passed" if result.returncode == 0 else "failed"
            checkpoint()
            if result.returncode:
                raise RuntimeError(name + "_failed_see_log")
        verify_sources(sources)
        report["result"] = "core_libraries_built_not_target_acceptance"
    except BaseException:
        report["result"] = "failed_or_interrupted"
        raise
    finally:
        checkpoint()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-cache", type=Path, required=True)
    parser.add_argument("--package-cache", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()
    report = run(args.source_cache, args.package_cache, args.work_root, args.jobs)
    print(json.dumps({key: value for key, value in report.items() if key != "packages"}, indent=2))


if __name__ == "__main__":
    main()
