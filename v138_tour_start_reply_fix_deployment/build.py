"""Build cumulative 1.0.9 against the immutable shipped 1.0.8 package."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
spec = importlib.util.spec_from_file_location("silent_builder", ROOT / "v138_silent_control_fix_deployment/build.py")
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
BASE, PREFIX, PY_FILES = prior.BASE, prior.PREFIX, prior.PY_FILES
command, digest, read_archive = prior.command, prior.digest, prior.read_archive
put, normalize_text, write_sums = prior.put, prior.normalize_text, prior.write_sums
verify_sums, tar_data, json_data = prior.verify_sums, prior.tar_data, prior.json_data
NAME = "sparkrobot-lingxi-v1.3.8-tour-start-reply-fix-1.0.9-20261019"
DOCS = ("部署说明.md", "修复与验证说明.md")
JAVA_TESTS = (*prior.JAVA_TESTS, "TourStartTakeoverTest")


def main():
    work = HERE / "work"
    work.mkdir(exist_ok=True)
    raw = (ROOT / "v138_silent_control_fix_deployment/dist" / (prior.NAME + ".tar.gz")).read_bytes()
    assert digest(raw) == "6a7b596ee572ddf4741635a50f21e8564585f697beae3f9990654a01e36fb07e"
    old = read_archive(raw, prior.NAME)
    verify_sums(old, "SHA256SUMS")
    old_meta = json.loads(old["release.json"][1])
    source = BASE / "candidate/source"
    java_source = source / "java/src"
    snapshot = {p.relative_to(java_source).as_posix(): p.read_bytes() for p in java_source.rglob("*.java")}
    baseline = BASE / "reference/java/robot-local-navigation-java-all.jar"
    sdk = BASE / "reference/java/linksoul-agentsdk-1.4.0.jar"
    assert digest(baseline.read_bytes()) == old_meta["baseline_jar_sha256"]
    assert digest(sdk.read_bytes()) == old_meta["sdk_sha256"]
    java = Path(os.environ.get("JAVA_HOME", r"C:\Program Files\Java\jdk-17")) / "bin"
    results, failures = {}, []
    with tempfile.TemporaryDirectory(prefix="compile-", dir=work) as temporary:
        classes, tests = Path(temporary) / "main", Path(temporary) / "tests"
        classes.mkdir()
        tests.mkdir()
        command([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp",
                 os.pathsep.join(map(str, (baseline, sdk))), "-d", classes,
                 *sorted((java_source / "main/java").rglob("*.java"))])
        compiled = {p.relative_to(classes).as_posix(): p.read_bytes() for p in classes.rglob("*.class")}
        jar = work / "robot-local-navigation-java-all.jar"
        family = ("LingxiNavAgentApp", "PresetMotionCatalog", "DoubaoArkClient", "NavigationCommandFallback", "NavBridgeClient")
        with zipfile.ZipFile(baseline) as original, zipfile.ZipFile(jar, "w", zipfile.ZIP_DEFLATED) as output:
            for entry in original.infolist():
                if prior.prior.class_family(entry.filename, family):
                    continue
                data = original.read(entry)
                if entry.filename in compiled:
                    assert compiled[entry.filename] == data, entry.filename
                output.writestr(entry, data)
            for name, data in sorted(compiled.items()):
                if prior.prior.class_family(name, family):
                    entry = zipfile.ZipInfo(name, (2026, 10, 19, 0, 0, 0))
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    entry.external_attr = 0o100644 << 16
                    output.writestr(entry, data)
        jar_data = jar.read_bytes()
        changes = prior.prior.changed_entries(old["payload/java/robot-local-navigation-java-all.jar"][1], jar_data)
        assert all(prior.prior.class_family(name, ("LingxiNavAgentApp",)) for name in changes)
        with zipfile.ZipFile(jar) as archive:
            assert archive.testzip() is None
            assert all(archive.read(name) == data for name, data in compiled.items())
        command([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp",
                 os.pathsep.join(map(str, (jar, sdk))), "-d", tests,
                 *sorted((java_source / "test/java").rglob("*.java"))])
        for test in JAVA_TESTS:
            result = command([java / "java.exe", "-cp", os.pathsep.join(map(str, (tests, jar, sdk))),
                              "com.agibot.aiem.nav." + test], check=False)
            log = result.stdout + result.stderr
            (work / (test + ".log")).write_text(log, encoding="utf-8")
            results[test] = [line for line in result.stdout.splitlines() if "passed" in line.lower()]
            if result.returncode or not results[test]:
                failures.append(test)
                print("FAILED " + test + ": " + log[-3000:], flush=True)
            else:
                print(test + ": " + "; ".join(results[test]), flush=True)
        previous = Path(temporary) / "previous.jar"
        previous.write_bytes(old["payload/java/robot-local-navigation-java-all.jar"][1])
        comparison = command([java / "java.exe", "-cp", os.pathsep.join(map(str, (tests, previous, sdk))),
                              "com.agibot.aiem.nav.TourStartTakeoverTest"], check=False)
        old_log = comparison.stdout + comparison.stderr
        (work / "previous-tour-start.log").write_text(old_log, encoding="utf-8")
        assert comparison.returncode != 0 and "Tour takeover failures " in old_log, old_log[-3000:]
        assert "NoSuchMethod" not in old_log and "NoSuchField" not in old_log
    assert not failures, failures
    assert snapshot == {p.relative_to(java_source).as_posix(): p.read_bytes() for p in java_source.rglob("*.java")}
    python = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", "-q", "test"],
        cwd=source / "navigation", capture_output=True, timeout=90, text=True, encoding="utf-8")
    assert python.returncode == 0, python.stdout + python.stderr
    (work / "python-tests.log").write_text(python.stdout + python.stderr, encoding="utf-8")
    payload = {name: old["payload/" + name][1] for name in old_meta["payload_sha256"]}
    payload["java/robot-local-navigation-java-all.jar"] = jar_data
    accepted = {name: list(dict.fromkeys(values + [old_meta["payload_sha256"][name]]))
                for name, values in old_meta["python_accepted_sha256"].items()}
    meta = {**old_meta, "package": NAME, "version": "1.0.9-20261019",
        "patched_jar_sha256": digest(jar_data),
        "superseded_jar_sha256": list(dict.fromkeys(old_meta["superseded_jar_sha256"] + [old_meta["patched_jar_sha256"]])),
        "python_accepted_sha256": accepted,
        "payload_sha256": {name: digest(data) for name, data in payload.items()},
        "java_tests": results, "python_tests": python.stdout.strip().splitlines()[-1],
        "changed_jar_entries_since_1_0_8": changes,
        "cumulative_through": "silent-control-fix-1.0.8-20261019",
        "tour_neutral_receipt": "收到", "tour_response_completes_before_dispatch": True,
        "previous_tour_comparison": next(line.strip() for line in old_log.splitlines()
                                         if "Tour takeover failures " in line)}
    files = {}
    for name, data in payload.items():
        put(files, "payload/" + name, data, 0o644)
    for name in ("install.py", "install.sh", *DOCS):
        put(files, name, normalize_text(name, (HERE / name).read_bytes()))
    for name in ("java_runtime.py", "test_java_runtime.py", "test_install.py", "test_silent_install.py", "arrival_runtime.py"):
        put(files, name, old[name][1])
    for group in ("main", "test"):
        for path in sorted((java_source / group / "java").rglob("*.java")):
            put(files, "source/java/" + group + "/" + path.relative_to(java_source / group / "java").as_posix(),
                normalize_text(path.name, path.read_bytes()), 0o644)
    for name, item in old.items():
        if name.startswith("source/navigation/"):
            put(files, name, item[1], 0o644)
    put(files, "release.json", json_data(meta), 0o644)
    write_sums(files, "SHA256SUMS")
    verify_sums(files, "SHA256SUMS")
    dist = HERE / "dist"
    dist.mkdir(exist_ok=True)
    archive = dist / (NAME + ".tar.gz")
    archive.write_bytes(tar_data(files, NAME))
    archive.with_name(archive.name + ".sha256").write_bytes(
        (digest(archive.read_bytes()) + "  " + archive.name + "\n").encode("ascii"))
    for doc in DOCS:
        (dist / doc).write_bytes(files[doc][1])
    print(archive, flush=True)


if __name__ == "__main__":
    main()
