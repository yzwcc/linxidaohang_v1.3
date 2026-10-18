"""Compile, verify and package the V1.3.8 control reply hotfix."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "v138_complete_r15_deployment"
sys.path.insert(0, str(BASE))
from archive_utils import normalize_text, put, read_archive, tar_data, verify_sums, write_sums

NAME = "sparkrobot-lingxi-v1.3.8-control-reply-fix-1.0.2-20261018"
PREVIOUS = "sparkrobot-lingxi-v1.3.8-control-reply-fix-1.0.1-20261018"
JAVA_TESTS = ("VoiceGateTest", "WakeSessionGateTest", "Soc2WakeEvidenceProviderTest",
              "AgentSdkInterruptWireTest", "VoiceRegression", "ModelRecoveryTest",
              "FunctionalControlTest", "AgentConnectionStateTest", "ControlReplyRegressionTest",
              "ControlOwnershipFaultTest", "LoggedModelTimeoutTest")


def run(args):
    args = list(map(str, args))
    if Path(args[0]).name == "javac.exe":
        args[1:1] = ["-J-Dfile.encoding=UTF-8", "-J-Duser.language=en"]
    elif Path(args[0]).name == "java.exe":
        args[1:1] = ["-Dfile.encoding=UTF-8", "-Duser.language=en"]
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(result.stdout[-12000:] + result.stderr[-6000:])
    return result.stdout


def main():
    work = HERE / "work"
    work.mkdir(exist_ok=True)
    # Empty compiler outputs prevent removed/renumbered inner classes leaking into a release.
    classes_temp = tempfile.TemporaryDirectory(prefix="classes-", dir=work)
    tests_temp = tempfile.TemporaryDirectory(prefix="tests-", dir=work)
    classes, tests = Path(classes_temp.name), Path(tests_temp.name)
    java = Path(os.environ.get("JAVA_HOME", r"C:\Program Files\Java\jdk-17")) / "bin"
    baseline = BASE / "reference/java/robot-local-navigation-java-all.jar"
    sdk = BASE / "reference/java/linksoul-agentsdk-1.4.0.jar"
    source = BASE / "candidate/source/java/src"
    source_snapshot = {p.relative_to(source).as_posix(): p.read_bytes()
                       for p in source.rglob("*.java")}
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
    previous_files = read_archive((HERE / "dist" / (PREVIOUS + ".tar.gz")).read_bytes(), PREVIOUS)
    previous_jar = previous_files["payload/java/robot-local-navigation-java-all.jar"][1]
    if "--check-previous" in sys.argv:
        previous_path = work / "previous-1.0.1.jar"
        previous_path.write_bytes(previous_jar)
        old_cp = os.pathsep.join(map(str, (tests, previous_path, sdk)))
        previous_result = subprocess.run(
            [str(java / "java.exe"), "-Dfile.encoding=UTF-8", "-Duser.language=en", "-cp", old_cp,
             "com.agibot.aiem.nav.ControlOwnershipFaultTest"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        (work / "previous-audit.log").write_text(previous_result.stdout + previous_result.stderr, encoding="utf-8")
        assert previous_result.returncode != 0, "The previous package must reproduce the audit regressions"
        print(next(line for line in previous_result.stderr.splitlines() if "failures:" in line))
    results = {}
    selected = ("ControlOwnershipFaultTest",) if "--fault-only" in sys.argv else JAVA_TESTS
    for name in selected:
        output = run([java / "java.exe", "-cp", str(tests) + os.pathsep + cp, "com.agibot.aiem.nav." + name])
        results[name] = [line for line in output.splitlines() if "passed" in line.lower()]
        print(name + ": " + "; ".join(results[name]))
    assert source_snapshot == {p.relative_to(source).as_posix(): p.read_bytes()
                               for p in source.rglob("*.java")}, "Java sources changed during validation; rebuild required"
    if "--java-only" in sys.argv or "--fault-only" in sys.argv:
        return
    files = {}
    for name in ("install.sh", "install.py", "control.sh", "部署说明.md", "日志核验说明.md", "验证记录.md", "test_install.py"):
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
        "package": NAME, "version": "1.0.2-20261018", "product": "V1.3.8",
        "baseline_jar_sha256": digest(baseline), "patched_jar_sha256": digest(jar),
        "superseded_jar_sha256": ["8cdf57a8f7c384b913d9aa8e47d1232075232f336937453170387955f55ada9e",
                                hashlib.sha256(previous_jar).hexdigest()],
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
    (dist / "日志核验说明.md").write_bytes(files["日志核验说明.md"][1])
    (dist / "验证记录.md").write_bytes(files["验证记录.md"][1])
    (work / "java-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    classes_temp.cleanup()
    tests_temp.cleanup()


if __name__ == "__main__":
    main()
