"""Run regressions against staged or extracted runtime files, never a robot."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys
import build_delivery as build
from check_java import TESTS


def run(package):
    package = package.resolve()
    report_dir = build.DELIVERY / "reports/packaged"
    report_dir.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, (
                           package / "payload/navigation", build.SOURCE / "navigation"))),
                       PYTHONDONTWRITEBYTECODE="1", FUNCTIONAL_CONSOLE=str(package / "payload/console"))
    nav = package / "payload/navigation"
    classpath = os.pathsep.join(map(str, (
        build.SOURCE / "java/target/test-classes",
        package / "payload/java/robot-local-navigation-java-all.jar",
        package / "payload/java/linksoul-agentsdk-1.4.0.jar",
    )))
    commands = [
        ("payload-manifest", [sys.executable, "-X", "utf8", str(package / "scripts/verify_payload.py"),
                              "--root", str(package / "payload"), "--manifest", str(package / "payload-manifest.json")]),
        ("navigation-import", [sys.executable, "-c",
                               "import robot_local_navigation_v3.models as m; "
                               "from pathlib import Path; "
                               "assert Path(m.__file__).resolve().is_relative_to(Path.cwd()); print(m.__file__)"]),
        ("navigation", [sys.executable, "-X", "utf8", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        str(build.SOURCE / "navigation/test")]),
        ("console", [shutil.which("node"), str(build.HERE / "check_console.mjs"), str(package / "payload/console")]),
        ("task-functional", [sys.executable, "-X", "utf8", "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider",
                             str(build.HERE / "functional_task_check.py")]),
    ]
    commands += [(name, [shutil.which("java"), "-Dfile.encoding=UTF-8", "-cp", classpath, "com.agibot.aiem.nav." + name]) for name in TESTS]
    records = []
    for name, command in commands:
        with (report_dir / (name + ".log")).open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=nav, env=environment,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=180)
        records.append({"name": name, "exit_code": result.returncode})
        print(name, "PASS" if result.returncode == 0 else "FAIL", flush=True)
        if result.returncode:
            print((report_dir / (name + ".log")).read_text(encoding="utf-8")[-6000:])
            break
    (report_dir / "results.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return records[-1]["exit_code"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("package", nargs="?", type=Path, default=build.INNER)
    args = parser.parse_args()
    sys.exit(run(args.package))
