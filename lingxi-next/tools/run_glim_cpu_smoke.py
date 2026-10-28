"""Run the synthetic GLIM native-library smoke test without ROS or robot access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import time

from build_slam_cpu_trial import PINS
from glim_trial_config import generate


ROOT = Path(__file__).resolve().parents[1]


def run(work, upstream):
    if platform.system() != "Linux":
        raise ValueError("linux_build_environment_required")
    work = Path(work).resolve()
    core = json.loads((work / "build-report.json").read_text(encoding="utf-8"))
    if core.get("result") != "core_libraries_built_not_target_acceptance":
        raise ValueError("successful_core_build_required")
    if core.get("sourceCommits") != PINS or core.get("hostMachine") != platform.machine():
        raise ValueError("unexpected_build_baseline_or_machine")
    output = work / "native-smoke"
    if output.exists():
        raise ValueError("existing_smoke_results_are_preserved")
    output.mkdir(mode=0o700)
    result = {
        "purpose": "synthetic_native_glim_smoke",
        "inputClassification": "synthetic_fixture", "stages": [],
        "robotCalibrationValidated": False, "navigationValidated": False,
        "rosWrapperValidated": False, "result": "running",
    }
    env = os.environ.copy()
    triplet = subprocess.check_output(["gcc", "-dumpmachine"], text=True).strip()
    env["PATH"] = str(work / "sysroot/usr/bin") + os.pathsep + env["PATH"]
    env["LD_LIBRARY_PATH"] = os.pathsep.join((
        str(work / "prefix/lib"), str(work / "sysroot/usr/lib" / triplet),
    ))
    env["CMAKE_PREFIX_PATH"] = os.pathsep.join((str(work / "prefix"), str(work / "sysroot/usr")))
    for name in ("ROS_VERSION", "ROS_DISTRO", "AMENT_PREFIX_PATH", "COLCON_PREFIX_PATH"):
        env.pop(name, None)
    try:
        profile = json.loads(
            (ROOT / "tools/glim-smoke/synthetic-profile.json").read_text(encoding="utf-8")
        )
        generate(upstream, output / "config", profile)
        commands = (
            ("configure", ["cmake", "-S", str(ROOT / "tools/glim-smoke"),
                           "-B", str(output / "build"),
                           "-DGLIM_TRIAL_CONFIG=" + str(output / "config")]),
            ("build", ["cmake", "--build", str(output / "build"), "--parallel", "2"]),
            ("ctest", ["ctest", "--test-dir", str(output / "build"), "--verbose"]),
        )
        for name, command in commands:
            started = time.monotonic()
            print("Starting native smoke " + name, flush=True)
            with (output / (name + ".log")).open("w", encoding="utf-8") as log:
                completed = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
            result["stages"].append({
                "name": name, "exitCode": completed.returncode,
                "seconds": time.monotonic() - started,
            })
            if completed.returncode:
                raise RuntimeError("native_smoke_" + name + "_failed_see_log")
        result["result"] = "passed_synthetic_only"
    except BaseException:
        result["result"] = "failed_or_interrupted"
        raise
    finally:
        result["logs"] = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in output.glob("*.log")
        }
        (output / "smoke-report.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8",
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--upstream-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.work_root, args.upstream_root), indent=2))


if __name__ == "__main__":
    main()
