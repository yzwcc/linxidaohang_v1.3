"""Compile, verify and package the V1.3.8 control reply hotfix."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "v138_complete_r15_deployment"
sys.path.insert(0, str(BASE))
from archive_utils import normalize_text, put, tar_data, verify_sums, write_sums

NAME = "sparkrobot-lingxi-v1.3.8-control-reply-fix-1.0.0-20261018"
JAVA_TESTS = ("VoiceGateTest", "WakeSessionGateTest", "Soc2WakeEvidenceProviderTest",
              "AgentSdkInterruptWireTest", "VoiceRegression", "ModelRecoveryTest",
              "FunctionalControlTest", "AgentConnectionStateTest", "ControlReplyRegressionTest")


def run(args):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(result.stdout[-12000:] + result.stderr[-6000:])
    return result.stdout


def main():
    work = HERE / "work"
    classes, tests = work / "classes", work / "tests"
    classes.mkdir(parents=True, exist_ok=True)
    tests.mkdir(parents=True, exist_ok=True)
    java = Path(os.environ.get("JAVA_HOME", r"C:\Program Files\Java\jdk-17")) / "bin"
    baseline = BASE / "reference/java/robot-local-navigation-java-all.jar"
    sdk = BASE / "reference/java/linksoul-agentsdk-1.4.0.jar"
    source = BASE / "candidate/source/java/src"
    cp = os.pathsep.join(map(str, (baseline, sdk)))
    run([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp", cp,
         "-d", classes, *sorted((source / "main/java").rglob("*.java"))])
    compiled = {p.relative_to(classes).as_posix(): p.read_bytes() for p in classes.rglob("*.class")}
    jar = work / "robot-local-navigation-java-all.jar"
    with zipfile.ZipFile(baseline) as old, zipfile.ZipFile(jar, "w", zipfile.ZIP_DEFLATED) as new:
        original = set(old.namelist())
        for entry in old.infolist():
            if entry.filename.startswith("com/agibot/aiem/nav/LingxiNavAgentApp") and entry.filename.endswith(".class"):
                continue
            data = old.read(entry)
            if entry.filename in compiled:
                assert data == compiled[entry.filename], "Unrelated bytecode changed: " + entry.filename
            new.writestr(entry, data)
        for name, data in sorted(compiled.items()):
            if name.startswith("com/agibot/aiem/nav/LingxiNavAgentApp"):
                info = zipfile.ZipInfo(name, (2026, 10, 18, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                new.writestr(info, data)
            else:
                assert name in original, "Unexpected unrelated class: " + name
    with zipfile.ZipFile(jar) as archive:
        assert archive.testzip() is None
        assert all(archive.read(n) == d for n, d in compiled.items())
    cp = os.pathsep.join(map(str, (jar, sdk)))
    run([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp", cp,
         "-d", tests, *sorted((source / "test/java").rglob("*.java"))])
    results = {}
    for name in JAVA_TESTS:
        output = run([java / "java.exe", "-cp", str(tests) + os.pathsep + cp, "com.agibot.aiem.nav." + name])
        results[name] = [line for line in output.splitlines() if "passed" in line.lower()]
        print(name + ": " + "; ".join(results[name]))
    if "--java-only" in sys.argv:
        return
    files = {}
    for name in ("install.sh", "install.py", "control.sh", "部署说明.md", "test_install.py"):
        put(files, name, normalize_text(name, (HERE / name).read_bytes()))
    put(files, "payload/java/robot-local-navigation-java-all.jar", jar.read_bytes(), 0o644)
    for path in sorted((source / "main/java").rglob("*.java")):
        name = "source/" + path.relative_to(source / "main/java").as_posix()
        put(files, name, normalize_text(name, path.read_bytes()), 0o644)
    for path in sorted((source / "test/java").rglob("*.java")):
        name = "tests/" + path.relative_to(source / "test/java").as_posix()
        put(files, name, normalize_text(name, path.read_bytes()), 0o644)
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    metadata = {
        "package": NAME, "version": "1.0.0-20261018", "product": "V1.3.8",
        "baseline_jar_sha256": digest(baseline), "patched_jar_sha256": digest(jar),
        "sdk_sha256": digest(sdk),
        "allowed_releases": ["1.0.19-20261017-r" + str(n) for n in range(11, 16)],
        "java_tests": results,
        "changed_runtime_files": ["java/robot-local-navigation-java-all.jar"],
        "hardware_validation": False,
    }
    put(files, "release.json", (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), 0o644)
    write_sums(files, "SHA256SUMS")
    verify_sums(files, "SHA256SUMS")
    dist = HERE / "dist"
    dist.mkdir(exist_ok=True)
    output = dist / (NAME + ".tar.gz")
    output.write_bytes(tar_data(files, NAME))
    output.with_suffix(output.suffix + ".sha256").write_bytes(
        (digest(output) + "  " + output.name + "\n").encode("ascii"))
    (dist / "部署说明.md").write_bytes(files["部署说明.md"][1])
    (work / "java-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
