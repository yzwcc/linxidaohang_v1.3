"""Build 1.0.8 from immutable 1.0.7 and freshly compiled application sources."""
import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BASE = ROOT / "v138_complete_r15_deployment"
sys.path.insert(0, str(BASE))
from archive_utils import digest, json_data, normalize_text, put, read_archive, tar_data, verify_sums, write_sums

spec = importlib.util.spec_from_file_location("arrival_builder", ROOT / "v138_arrival_tts_fix_deployment/build.py")
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
NAME = "sparkrobot-lingxi-v1.3.8-silent-control-fix-1.0.8-20261019"
DOCS = ("部署说明.md", "修复与验证说明.md")
PY_FILES, PREFIX = prior.PY_FILES, prior.PREFIX
JAVA_TESTS = (*prior.JAVA_TESTS, "SilentStartControlTest")
command = prior.command


def main():
    work = HERE / "work"
    work.mkdir(exist_ok=True)
    raw = (ROOT / "v138_arrival_tts_fix_deployment/dist" / (prior.NAME + ".tar.gz")).read_bytes()
    assert digest(raw) == "6fbe5bcac4834ede2bb1643570e20d38afa1129b020234c1396755d45b6e084a"
    previous = read_archive(raw, prior.NAME)
    verify_sums(previous, "SHA256SUMS")
    old_meta = json.loads(previous["release.json"][1])
    source = BASE / "candidate/source"
    java_source = source / "java/src"
    snapshot = {p.relative_to(java_source).as_posix(): p.read_bytes() for p in java_source.rglob("*.java")}
    baseline = BASE / "reference/java/robot-local-navigation-java-all.jar"
    sdk = BASE / "reference/java/linksoul-agentsdk-1.4.0.jar"
    assert digest(baseline.read_bytes()) == old_meta["baseline_jar_sha256"]
    assert digest(sdk.read_bytes()) == old_meta["sdk_sha256"]
    java = Path(os.environ.get("JAVA_HOME", r"C:\Program Files\Java\jdk-17")) / "bin"
    allowed = ("LingxiNavAgentApp", "PresetMotionCatalog", "DoubaoArkClient", "NavigationCommandFallback", "NavBridgeClient")
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
        with zipfile.ZipFile(baseline) as old, zipfile.ZipFile(jar, "w", zipfile.ZIP_DEFLATED) as new:
            for entry in old.infolist():
                if prior.class_family(entry.filename, allowed):
                    continue
                data = old.read(entry)
                if entry.filename in compiled:
                    assert compiled[entry.filename] == data, entry.filename
                new.writestr(entry, data)
            for name, data in sorted(compiled.items()):
                if prior.class_family(name, allowed):
                    info = zipfile.ZipInfo(name, (2026, 10, 19, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    new.writestr(info, data)
        jar_data = jar.read_bytes()
        changed = prior.changed_entries(previous["payload/java/robot-local-navigation-java-all.jar"][1], jar_data)
        assert all(prior.class_family(name, ("LingxiNavAgentApp", "NavBridgeClient", "NavigationCommandFallback")) for name in changed)
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
                print("FAILED " + test + ": " + log[-1500:], flush=True)
            else:
                print(test + ": " + "; ".join(results[test]), flush=True)
        old = Path(temporary) / "previous.jar"
        old.write_bytes(previous["payload/java/robot-local-navigation-java-all.jar"][1])
        comparison = command([java / "java.exe", "-cp", os.pathsep.join(map(str, (tests, old, sdk))),
                              "com.agibot.aiem.nav.SilentStartControlTest"], check=False)
        old_log = comparison.stdout + comparison.stderr
        (work / "previous-silent-start.log").write_text(old_log, encoding="utf-8")
        assert comparison.returncode != 0 and "Silent start failures " in old_log, old_log[-1500:]
        assert "NoSuchMethod" not in old_log and "NoSuchField" not in old_log, old_log[-1500:]
    assert not failures, failures
    assert snapshot == {p.relative_to(java_source).as_posix(): p.read_bytes() for p in java_source.rglob("*.java")}
    python = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", "-q", "test"],
        cwd=source / "navigation", capture_output=True, timeout=90, text=True, encoding="utf-8")
    (work / "python-tests.log").write_text(python.stdout + python.stderr, encoding="utf-8")
    assert python.returncode == 0, python.stdout + python.stderr
    payload = {"java/robot-local-navigation-java-all.jar": jar_data}
    payload.update({PREFIX + name: normalize_text(name, (source / "navigation/robot_local_navigation_v3" / name).read_bytes())
                    for name in PY_FILES})
    accepted_python = {name: list(dict.fromkeys([baseline_hash, old_meta["payload_sha256"][name]]))
                       for name, baseline_hash in old_meta["python_baselines"].items()}
    meta = {**old_meta, "package": NAME, "version": "1.0.8-20261019",
        "patched_jar_sha256": digest(jar_data),
        "superseded_jar_sha256": list(dict.fromkeys(old_meta["superseded_jar_sha256"] + [old_meta["patched_jar_sha256"]])),
        "python_accepted_sha256": accepted_python,
        "payload_sha256": {name: digest(data) for name, data in payload.items()},
        "java_tests": results, "python_tests": python.stdout.strip().splitlines()[-1],
        "changed_jar_entries_since_1_0_7": changed,
        "cumulative_through": "arrival-tts-fix-1.0.7-20261019",
        "silent_start_control": True, "hardware_validation": False,
        "independent_native_speech_interception_verified": False,
        "previous_silent_comparison": next(line.strip() for line in old_log.splitlines()
                                          if "Silent start failures " in line)}
    files = {}
    for name, data in payload.items():
        put(files, "payload/" + name, data, 0o644)
    for name in ("install.py", "install.sh", "test_silent_install.py", *DOCS):
        put(files, name, normalize_text(name, (HERE / name).read_bytes()))
    for name in ("java_runtime.py", "test_java_runtime.py", "test_install.py"):
        data = previous[name][1]
        if name == "test_install.py":
            data = data.replace(b'patch.object(m, "NAV_USER"', b'patch.object(m.runtime, "NAV_USER"')
            data = data.replace(b'patch.object(m, "write_atomic"', b'patch.object(m.runtime, "write_atomic"')
        put(files, name, data)
    put(files, "arrival_runtime.py", previous["install.py"][1])
    for group in ("main", "test"):
        for path in sorted((java_source / group / "java").rglob("*.java")):
            put(files, "source/java/" + group + "/" + path.relative_to(java_source / group / "java").as_posix(),
                normalize_text(path.name, path.read_bytes()), 0o644)
    for path in sorted((source / "navigation").rglob("*.py")):
        if "__pycache__" not in path.parts:
            put(files, "source/navigation/" + path.relative_to(source / "navigation").as_posix(),
                normalize_text(path.name, path.read_bytes()), 0o644)
    put(files, "release.json", json_data(meta), 0o644)
    write_sums(files, "SHA256SUMS")
    verify_sums(files, "SHA256SUMS")
    dist = HERE / "dist"
    dist.mkdir(exist_ok=True)
    output = dist / (NAME + ".tar.gz")
    output.write_bytes(tar_data(files, NAME))
    output.with_name(output.name + ".sha256").write_bytes(
        (digest(output.read_bytes()) + "  " + output.name + "\n").encode("ascii"))
    for doc in DOCS:
        (dist / doc).write_bytes(files[doc][1])
    print(output, flush=True)


if __name__ == "__main__":
    main()
