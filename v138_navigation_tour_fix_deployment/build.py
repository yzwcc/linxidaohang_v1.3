"""Build a cumulative, source-reproduced navigation/tour routing patch."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "v138_complete_r15_deployment"
PREVIOUS_DIR = HERE.parent / "v138_control_reply_fix_deployment"
sys.path.insert(0, str(BASE))
from archive_utils import normalize_text, put, read_archive, tar_data, verify_sums, write_sums

NAME = "sparkrobot-lingxi-v1.3.8-control-reply-fix-1.0.6-20261019"
PREVIOUS = "sparkrobot-lingxi-v1.3.8-control-reply-fix-1.0.5-20261019"
CHANGED_CLASSES = ("LingxiNavAgentApp", "PresetMotionCatalog", "DoubaoArkClient", "NavigationCommandFallback")
NEW_CLASSES = ("LingxiNavAgentApp", "NavigationCommandFallback")
JAVA_TESTS = ("VoiceGateTest", "WakeSessionGateTest", "Soc2WakeEvidenceProviderTest",
              "AgentSdkInterruptWireTest", "VoiceRegression", "ModelRecoveryTest",
              "FunctionalControlTest", "AgentConnectionStateTest", "ControlReplyRegressionTest",
              "ControlOwnershipFaultTest", "LoggedModelTimeoutTest", "FieldVoiceReplayTest",
              "ControlReplyTextWireTest", "ReferenceControlChannelTest", "NavigationTourRoutingTest")
DOCS = ("部署说明.md", "验证记录.md")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def class_family(name, families=CHANGED_CLASSES):
    return any(name == "com/agibot/aiem/nav/" + family + ".class"
               or name.startswith("com/agibot/aiem/nav/" + family + "$") and name.endswith(".class")
               for family in families)


def command(args, check=True):
    args = list(map(str, args))
    if Path(args[0]).name == "javac.exe":
        args[1:1] = ["-J-Dfile.encoding=UTF-8", "-J-Duser.language=en"]
    elif Path(args[0]).name == "java.exe":
        args[1:1] = ["-Dfile.encoding=UTF-8", "-Duser.language=en"]
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    if check and result.returncode:
        raise RuntimeError(result.stdout[-16000:] + result.stderr[-16000:])
    return result


def changed_entries(before, after):
    with zipfile.ZipFile(io.BytesIO(before)) as old, zipfile.ZipFile(io.BytesIO(after)) as new:
        return sorted(name for name in set(old.namelist()) | set(new.namelist())
                      if name not in old.namelist() or name not in new.namelist()
                      or old.read(name) != new.read(name))


def main():
    work = HERE / "work"
    work.mkdir(exist_ok=True)
    java = Path(os.environ.get("JAVA_HOME", r"C:\Program Files\Java\jdk-17")) / "bin"
    baseline = BASE / "reference/java/robot-local-navigation-java-all.jar"
    sdk = BASE / "reference/java/linksoul-agentsdk-1.4.0.jar"
    source = BASE / "candidate/source/java/src"
    snapshot = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*.java")}
    previous_files = read_archive((PREVIOUS_DIR / "dist" / (PREVIOUS + ".tar.gz")).read_bytes(), PREVIOUS)
    verify_sums(previous_files, "SHA256SUMS")
    previous_jar = previous_files["payload/java/robot-local-navigation-java-all.jar"][1]
    assert digest(previous_jar) == "183daa4eeb9a658e15b7d157e5c22847206683a53e8c606622e5e2ad3275486b"
    assert digest(baseline.read_bytes()) == "4f6e2540fe098eddd30b2e18fd182e416eb270984d8d04d74bdd5370190834dd"
    assert digest(sdk.read_bytes()) == "bd419749b4a3dc635170ce67f33aedcc93cb4298bf931f582b0eaf83004ebe50"
    with tempfile.TemporaryDirectory(prefix="compile-", dir=work) as temporary:
        classes, tests = Path(temporary) / "main", Path(temporary) / "tests"
        classes.mkdir()
        tests.mkdir()
        cp = os.pathsep.join(map(str, (baseline, sdk)))
        command([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp", cp,
                 "-d", classes, *sorted((source / "main/java").rglob("*.java"))])
        compiled = {p.relative_to(classes).as_posix(): p.read_bytes() for p in classes.rglob("*.class")}
        jar = work / "robot-local-navigation-java-all.jar"
        with zipfile.ZipFile(baseline) as old, zipfile.ZipFile(jar, "w", zipfile.ZIP_DEFLATED) as new:
            for entry in old.infolist():
                if class_family(entry.filename):
                    continue
                data = old.read(entry)
                if entry.filename in compiled:
                    assert data == compiled[entry.filename], "Unrelated compiled class differs: " + entry.filename
                new.writestr(entry, data)
            for name, data in sorted(compiled.items()):
                if class_family(name):
                    info = zipfile.ZipInfo(name, (2026, 10, 19, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    new.writestr(info, data)
                else:
                    assert name in old.namelist(), "Unexpected class: " + name
        with zipfile.ZipFile(jar) as archive:
            assert archive.testzip() is None
            assert all(archive.read(name) == data for name, data in compiled.items())
        changed = changed_entries(previous_jar, jar.read_bytes())
        assert changed and all(class_family(name, NEW_CLASSES) for name in changed), changed
        cp = os.pathsep.join(map(str, (jar, sdk)))
        command([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp", cp,
                 "-d", tests, *sorted((source / "test/java").rglob("*.java"))])
        previous_path = Path(temporary) / "previous.jar"
        previous_path.write_bytes(previous_jar)
        old_result = command([java / "java.exe", "-cp", os.pathsep.join(map(str, (tests, previous_path, sdk))),
                              "com.agibot.aiem.nav.NavigationTourRoutingTest"], check=False)
        old_log = old_result.stdout + old_result.stderr
        (work / "previous-routing.log").write_text(old_log, encoding="utf-8")
        assert old_result.returncode != 0 and "Routing failures " in old_log, old_log
        assert "NullPointerException" not in old_log and "NoSuchMethod" not in old_log, old_log
        headline = next(line.strip() for line in old_log.splitlines() if "Routing failures " in line)
        print("Actual shipped 1.0.5: " + headline, flush=True)
        comparison = {"package": PREVIOUS, "jar_sha256": digest(previous_jar),
                      "suite": "NavigationTourRoutingTest", "result": headline,
                      "test_source_sha256": digest(snapshot["test/java/com/agibot/aiem/nav/NavigationTourRoutingTest.java"])}
        results = {}
        for test in JAVA_TESTS:
            output = command([java / "java.exe", "-cp", os.pathsep.join(map(str, (tests, jar, sdk))),
                              "com.agibot.aiem.nav." + test]).stdout
            results[test] = [line for line in output.splitlines() if "passed" in line.lower()]
            assert results[test], test + ": no passing result"
            print(test + ": " + "; ".join(results[test]), flush=True)
        assert snapshot == {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*.java")}
    metadata = {
        "package": NAME, "version": "1.0.6-20261019", "product": "V1.3.8",
        "baseline_jar_sha256": digest(baseline.read_bytes()), "patched_jar_sha256": digest(jar.read_bytes()),
        "superseded_jar_sha256": list(dict.fromkeys(
            json.loads(previous_files["release.json"][1])["superseded_jar_sha256"] + [digest(previous_jar)])),
        "sdk_sha256": digest(sdk.read_bytes()),
        "allowed_releases": ["1.0.19-20261017-r" + str(n) for n in range(11, 16)]
                            + ["1.0.19-20261019-r16"],
        "java_tests": results, "previous_comparison": comparison,
        "changed_runtime_files": ["java/robot-local-navigation-java-all.jar"],
        "changed_java_classes": list(CHANGED_CLASSES),
        "changed_jar_entries_since_1_0_5": changed, "hardware_validation": False,
        "independent_native_speech_interception_verified": False,
    }
    files = {}
    for name in ("install.py", "install.sh", "control.sh", "test_install.py", *DOCS):
        put(files, name, normalize_text(name, (HERE / name).read_bytes()))
    put(files, "payload/java/robot-local-navigation-java-all.jar", jar.read_bytes(), 0o644)
    for group, folder in (("main", "source"), ("test", "tests")):
        for path in sorted((source / group / "java").rglob("*.java")):
            name = folder + "/" + path.relative_to(source / group / "java").as_posix()
            put(files, name, normalize_text(name, path.read_bytes()), 0o644)
    put(files, "release.json", (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode(), 0o644)
    write_sums(files, "SHA256SUMS")
    verify_sums(files, "SHA256SUMS")
    dist = HERE / "dist"
    dist.mkdir(exist_ok=True)
    output = dist / (NAME + ".tar.gz")
    output.write_bytes(tar_data(files, NAME))
    output.with_suffix(output.suffix + ".sha256").write_bytes(
        (digest(output.read_bytes()) + "  " + output.name + "\n").encode("ascii"))
    for name in DOCS:
        (dist / name).write_bytes(files[name][1])
    (work / "java-results.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(output), flush=True)


if __name__ == "__main__":
    main()
