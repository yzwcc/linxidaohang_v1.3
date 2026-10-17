"""Compile candidate Java and console without modifying the immutable r13 package."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JAVA = Path(os.environ.get("JAVA_HOME", r"C:\Program Files\Java\jdk-17")) / "bin"
JAVA_TESTS = (
    "VoiceGateTest", "WakeSessionGateTest", "Soc2WakeEvidenceProviderTest",
    "AgentSdkInterruptWireTest", "VoiceRegression", "ModelRecoveryTest",
    "FunctionalControlTest", "AgentConnectionStateTest",
)


def run(args, cwd=None, timeout=180):
    result = subprocess.run(list(map(str, args)), cwd=cwd, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{args[0]} exited {result.returncode}\n{result.stdout[-10000:]}\n{result.stderr[-10000:]}")
    return result.stdout + result.stderr


def main():
    reference = HERE / "reference"
    classes = reference / "classes"
    classes.mkdir(exist_ok=True)
    java = HERE / "candidate/source/java"
    jar = reference / "java/robot-local-navigation-java-all.jar"
    sdk = reference / "java/linksoul-agentsdk-1.4.0.jar"
    cp = os.pathsep.join(map(str, (jar, sdk)))
    javac = JAVA / "javac.exe" if os.name == "nt" else Path(shutil.which("javac"))
    sources = sorted((java / "src/main/java").rglob("*.java"))
    run([javac, "-encoding", "UTF-8", "--release", "17", "-cp", cp, "-d", classes, *sources])
    target = reference / "robot-local-navigation-java-all.jar"
    with zipfile.ZipFile(jar) as baseline, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as output:
        for entry in baseline.infolist():
            if entry.filename.startswith("com/agibot/aiem/nav/") and entry.filename.endswith(".class"):
                continue
            output.writestr(entry, baseline.read(entry.filename))
        for source in sorted(classes.rglob("*.class")):
            output.write(source, source.relative_to(classes).as_posix())
    tests = reference / "test-classes"
    tests.mkdir(exist_ok=True)
    cp = os.pathsep.join(map(str, (target, sdk)))
    run([javac, "-encoding", "UTF-8", "--release", "17", "-cp", cp, "-d", tests,
         *sorted((java / "src/test/java").rglob("*.java"))])
    reports = HERE / "qa"
    reports.mkdir(exist_ok=True)
    results = {}
    executable = javac.with_name("java.exe" if os.name == "nt" else "java")
    for name in JAVA_TESTS:
        result = run([executable, "-cp", str(tests) + os.pathsep + cp, "com.agibot.aiem.nav." + name])
        (reports / (name + ".log")).write_text(result, encoding="utf-8")
        results[name] = [line for line in result.splitlines() if "passed" in line.lower() or "assertions" in line.lower()][-2:]
        print(name + ": passed", flush=True)
    (reports / "java-regression.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
