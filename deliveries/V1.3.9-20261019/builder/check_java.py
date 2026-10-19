"""Compile from the release snapshot and run the main-based Java regressions."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
JAVA = HERE.parent / "source/java"
REPORTS = HERE.parent / "reports"
TESTS = (
    "VoiceGateTest", "WakeSessionGateTest", "Soc2WakeEvidenceProviderTest",
    "AgentSdkInterruptWireTest", "VoiceRegression", "ControlAckRegression",
)


def run():
    REPORTS.mkdir(exist_ok=True)
    maven = shutil.which("mvn") or shutil.which("mvn.cmd")
    if not maven:
        maven = str(ROOT.with_name(ROOT.name + "-v1.3.9") / ".tools/apache-maven-3.9.9/bin/mvn.cmd")
    records = []
    commands = [("java-build", [maven, "-q", "-o", "-DskipTests", "package"])]
    classpath = os.pathsep.join(map(str, (
        JAVA / "target/test-classes",
        JAVA / "target/robot-local-navigation-java-0.1.0-all.jar",
        JAVA / "linksoul-agentsdk-1.4.0-SNAPSHOT.jar",
    )))
    java = shutil.which("java")
    if not java:
        raise RuntimeError("Java 17 is required")
    commands += [(test, [java, "-cp", classpath, "com.agibot.aiem.nav." + test]) for test in TESTS]
    for name, command in commands:
        with (REPORTS / (name + ".log")).open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=JAVA, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        records.append({"name": name, "exit_code": result.returncode})
        print(name, "PASS" if result.returncode == 0 else "FAIL", flush=True)
        if result.returncode:
            print((REPORTS / (name + ".log")).read_text(encoding="utf-8")[-5000:])
            break
    (REPORTS / "java-results.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return records[-1]["exit_code"]


if __name__ == "__main__":
    sys.exit(run())
