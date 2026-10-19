"""Reproduce the cumulative Java patch and bind Python changes to shipped r16."""
import io
import importlib.util
import argparse
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
spec = importlib.util.spec_from_file_location("routing_builder", ROOT / "v138_navigation_tour_fix_deployment/build.py")
routing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routing)
command, class_family, changed_entries = routing.command, routing.class_family, routing.changed_entries

NAME = "sparkrobot-lingxi-v1.3.8-arrival-tts-fix-1.0.7-20261019"
PREVIOUS = "sparkrobot-lingxi-v1.3.8-control-reply-fix-1.0.6-20261019"
FULL = "lingxi-v1.3.8-complete-20261019-r16-deployment"
JAVA_TESTS = (*routing.JAVA_TESTS, "TtsAcknowledgementTest")
PY_FILES = ("navigation_client.py", "lingxi_nav_bridge.py", "tts_speaker.py",
            "near_goal_arrival.py", "tts_receipt.py")
DOCS = ("部署说明.md", "修复与验证说明.md")
PREFIX = "navigation/robot_local_navigation_v3/"


def main(reuse_java_tests=False):
    work = HERE / "work"
    work.mkdir(exist_ok=True)
    previous_raw = (ROOT / "v138_navigation_tour_fix_deployment/dist" / (PREVIOUS + ".tar.gz")).read_bytes()
    assert digest(previous_raw) == "c65d76143ee4db799e23a06af2028798839f7091bf8ae645586a5c11c77bccae"
    previous = read_archive(previous_raw, PREVIOUS)
    verify_sums(previous, "SHA256SUMS")
    full_raw = (ROOT / "v138_complete_r16_deployment/dist" / (FULL + ".tar.gz")).read_bytes()
    assert digest(full_raw) == "1f17375157f06334ac95ec5836020136005dc178aba4be9de573080b30857069"
    full = read_archive(full_raw, FULL)
    verify_sums(full, "SHA256SUMS.txt")
    component = "sparkrobot-lingxi-nav-internal-1.0.19-20261019-r16"
    runtime = read_archive(full["components/" + component + ".tar.gz"][1], component)
    verify_sums(runtime, "SHA256SUMS")
    del full, full_raw
    source = BASE / "candidate/source"
    py_root = source / "navigation/robot_local_navigation_v3"
    for path in py_root.glob("*.py"):
        if path.name not in PY_FILES:
            assert path.read_bytes().replace(b"\r\n", b"\n") == runtime["payload/" + PREFIX + path.name][1], path.name
    python_baselines = {PREFIX + name: digest(runtime["payload/" + PREFIX + name][1])
                        if "payload/" + PREFIX + name in runtime else None for name in PY_FILES}
    python_results = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "pytest", "-q", "test"],
        cwd=source / "navigation", capture_output=True, timeout=90, text=True, encoding="utf-8")
    (work / "python-tests.log").write_text(python_results.stdout + python_results.stderr, encoding="utf-8")
    assert python_results.returncode == 0, python_results.stdout + python_results.stderr
    print(python_results.stdout.strip().splitlines()[-1], flush=True)
    java = Path(os.environ.get("JAVA_HOME", r"C:\Program Files\Java\jdk-17")) / "bin"
    baseline, sdk = (BASE / "reference/java" / name for name in (
        "robot-local-navigation-java-all.jar", "linksoul-agentsdk-1.4.0.jar"))
    prior_meta = json.loads(previous["release.json"][1])
    assert digest(baseline.read_bytes()) == prior_meta["baseline_jar_sha256"]
    assert digest(sdk.read_bytes()) == prior_meta["sdk_sha256"]
    java_source = source / "java/src"
    verified_java = None
    if reuse_java_tests:
        cached = HERE / "dist" / (NAME + ".tar.gz")
        cached_raw = cached.read_bytes()
        assert cached.with_name(cached.name + ".sha256").read_bytes() == (
            digest(cached_raw) + "  " + cached.name + "\n").encode("ascii")
        verified_java = read_archive(cached_raw, NAME)
        verify_sums(verified_java, "SHA256SUMS")
        for group in ("main", "test"):
            for path in (java_source / group / "java").rglob("*.java"):
                key = "source/java/" + group + "/" + path.relative_to(java_source / group / "java").as_posix()
                assert verified_java[key][1] == normalize_text(path.name, path.read_bytes()), key
        assert set(json.loads(verified_java["release.json"][1])["java_tests"]) == set(JAVA_TESTS)
    files, results = {}, {}
    with tempfile.TemporaryDirectory(prefix="java-", dir=work) as temporary:
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
                if class_family(entry.filename):
                    continue
                data = old.read(entry)
                if entry.filename in compiled:
                    assert data == compiled[entry.filename], "Unrelated class changed: " + entry.filename
                new.writestr(entry, data)
            for name, data in sorted(compiled.items()):
                if class_family(name):
                    info = zipfile.ZipInfo(name, (2026, 10, 19, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    new.writestr(info, data)
        jar_data = jar.read_bytes()
        with zipfile.ZipFile(jar) as archive:
            assert archive.testzip() is None
            assert all(archive.read(name) == data for name, data in compiled.items())
        changed = changed_entries(previous["payload/java/robot-local-navigation-java-all.jar"][1], jar_data)
        assert changed and all(class_family(name, ("LingxiNavAgentApp",)) for name in changed)
        cp = os.pathsep.join(map(str, (jar, sdk)))
        command([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp", cp,
                 "-d", tests, *sorted((java_source / "test/java").rglob("*.java"))])
        if verified_java:
            assert jar_data == verified_java["payload/java/robot-local-navigation-java-all.jar"][1]
            results = json.loads(verified_java["release.json"][1])["java_tests"]
            assert all(results.values())
            print("Java artifact and all test sources unchanged; reused verified 16-suite results", flush=True)
        for test in (() if verified_java else JAVA_TESTS):
            result = command([java / "java.exe", "-cp", os.pathsep.join(map(str, (tests, jar, sdk))),
                              "com.agibot.aiem.nav." + test])
            (work / (test + ".log")).write_text(result.stdout + result.stderr, encoding="utf-8")
            results[test] = [line for line in result.stdout.splitlines() if "passed" in line.lower()]
            assert results[test], test
            print(test + ": " + "; ".join(results[test]), flush=True)
    payload = {"java/robot-local-navigation-java-all.jar": jar_data}
    payload.update({PREFIX + name: normalize_text(name, (py_root / name).read_bytes()) for name in PY_FILES})
    meta = {
        "package": NAME, "version": "1.0.7-20261019", "product": "V1.3.8",
        "baseline_jar_sha256": prior_meta["baseline_jar_sha256"],
        "patched_jar_sha256": digest(jar_data), "sdk_sha256": prior_meta["sdk_sha256"],
        "superseded_jar_sha256": prior_meta["superseded_jar_sha256"] + [prior_meta["patched_jar_sha256"]],
        "allowed_releases": ["1.0.19-20261017-r15", "1.0.19-20261019-r16"],
        "python_baselines": python_baselines,
        "navigation_control_sha256": digest(runtime["scripts/navigation_control.sh"][1]),
        "payload_sha256": {name: digest(data) for name, data in payload.items()},
        "changed_runtime_files": list(payload), "java_tests": results,
        "python_tests": python_results.stdout.strip().splitlines()[-1],
        "changed_jar_entries_since_1_0_6": changed,
        "cumulative_through": "control-reply-fix-1.0.6-20261019",
        "baseline_full_sha256": "1f17375157f06334ac95ec5836020136005dc178aba4be9de573080b30857069",
        "source_runtime_class_count": len(compiled),
        "hardware_validation": False, "independent_native_speech_interception_verified": False,
    }
    for name, data in payload.items():
        put(files, "payload/" + name, data, 0o644)
    for name in ("install.py", "install.sh", "test_install.py", *DOCS):
        put(files, name, normalize_text(name, (HERE / name).read_bytes()))
    put(files, "java_runtime.py", previous["install.py"][1])
    # Run the original validator's Linux scenarios against the original helper.
    original_tests = previous["test_install.py"][1].replace(b'HERE / "install.py"', b'HERE / "java_runtime.py"')
    put(files, "test_java_runtime.py", original_tests)
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-java-tests", action="store_true")
    main(parser.parse_args().reuse_java_tests)
