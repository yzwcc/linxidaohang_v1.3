"""Verify the final archive and unchanged 1.0.8 behavior, without contacting a robot."""
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build import BASE, DOCS, JAVA_TESTS, NAME, command, digest, prior, read_archive, verify_sums


def main():
    work = HERE / "work"
    work.mkdir(exist_ok=True)
    archive = HERE / "dist" / (NAME + ".tar.gz")
    raw = archive.read_bytes()
    assert archive.with_name(archive.name + ".sha256").read_bytes() == (
        digest(raw) + "  " + archive.name + "\n").encode("ascii")
    files = read_archive(raw, NAME)
    verify_sums(files, "SHA256SUMS")
    meta = json.loads(files["release.json"][1])
    baseline_raw = (HERE.parent / "v138_silent_control_fix_deployment/dist" / (prior.NAME + ".tar.gz")).read_bytes()
    assert digest(baseline_raw) == "6a7b596ee572ddf4741635a50f21e8564585f697beae3f9990654a01e36fb07e"
    baseline = read_archive(baseline_raw, prior.NAME)
    assert meta["runtime_identical_to_1_0_8"] is True
    for name, expected in meta["payload_sha256"].items():
        assert digest(files["payload/" + name][1]) == expected
        assert files["payload/" + name][1] == baseline["payload/" + name][1], name
    for name, item in baseline.items():
        if name.startswith("source/"):
            assert files[name][1] == item[1], name
    texts = 0
    for name, (_, data) in files.items():
        if name.endswith((".py", ".sh", ".java", ".md", ".json")) or name == "SHA256SUMS":
            assert b"\r" not in data, name
            texts += 1
        if name.endswith(".py"):
            compile(data, name, "exec")
    scripts = [data for name, (_, data) in files.items() if name.endswith(".sh")]
    for doc in DOCS:
        scripts += [block.encode() for block in re.findall(
            r"```bash\n(.*?)```", files[doc][1].decode("utf-8"), re.S)]
    for data in scripts:
        result = subprocess.run(["wsl", "-d", "Ubuntu", "--", "bash", "-n"],
                                input=data, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr
    java_results = {}
    with tempfile.TemporaryDirectory(prefix="native-tour-final-") as temporary:
        root = Path(temporary)
        for name, (entry, data) in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(entry.mode)
        linux_root = "/mnt/" + root.drive[0].lower() + root.as_posix()[2:]
        result = subprocess.run(["wsl", "-d", "Ubuntu", "-u", "root", "--", "python3", "-m",
            "unittest", "discover", "-s", linux_root, "-p", "test_*.py", "-v"],
            capture_output=True, timeout=90)
        log = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        (work / "linux-installer-tests.log").write_text(log, encoding="utf-8")
        assert result.returncode == 0 and "\nOK" in log, log[-24000:]
        count = int(re.search(r"Ran (\d+) tests", log).group(1))
        print("Packaged Linux tests passed: " + str(count), flush=True)
        result = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", "-q", "test"],
            cwd=root / "source/navigation", capture_output=True, timeout=90, text=True, encoding="utf-8")
        (work / "navigation-tests.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        assert result.returncode == 0, result.stdout + result.stderr
        python_summary = result.stdout.strip().splitlines()[-1]
        assert "183 passed" in python_summary
        print("Navigation tests: " + python_summary, flush=True)
        classes, tests = root / "recompiled", root / "java-tests"
        classes.mkdir()
        tests.mkdir()
        java = Path(os.environ.get("JAVA_HOME", r"C:\Program Files\Java\jdk-17")) / "bin"
        sdk = BASE / "reference/java/linksoul-agentsdk-1.4.0.jar"
        original = BASE / "reference/java/robot-local-navigation-java-all.jar"
        assert digest(sdk.read_bytes()) == meta["sdk_sha256"]
        assert digest(original.read_bytes()) == meta["baseline_jar_sha256"]
        jar_path = root / "payload/java/robot-local-navigation-java-all.jar"
        command([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp",
                 os.pathsep.join(map(str, (original, sdk))), "-d", classes,
                 *sorted((root / "source/java/main").rglob("*.java"))])
        compiled = {p.relative_to(classes).as_posix(): p.read_bytes() for p in classes.rglob("*.class")}
        with zipfile.ZipFile(jar_path) as jar:
            assert jar.testzip() is None
            assert all(jar.read(name) == data for name, data in compiled.items())
        command([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp",
                 os.pathsep.join(map(str, (jar_path, sdk))), "-d", tests,
                 *sorted((root / "source/java/test").rglob("*.java"))])
        for test in JAVA_TESTS:
            result = command([java / "java.exe", "-cp", os.pathsep.join(map(str, (tests, jar_path, sdk))),
                              "com.agibot.aiem.nav." + test], check=False)
            log = result.stdout + result.stderr
            (work / (test + ".log")).write_text(log, encoding="utf-8")
            passed = [line for line in result.stdout.splitlines() if "passed" in line.lower()]
            assert result.returncode == 0 and passed, test + ": " + log[-4000:]
            java_results[test] = passed
            print(test + ": " + "; ".join(passed), flush=True)
    report = dict(archive=archive.name, sha256=digest(raw), size_bytes=len(raw),
                  checked_files=len(files), lf_text_files=texts, bash_blocks=len(scripts),
                  runtime_payload_byte_identical_to_shipped_1_0_8=True,
                  source_byte_identical_to_shipped_1_0_8=True,
                  java_regression=java_results, source_runtime_class_count=len(compiled),
                  navigation_tests=python_summary, packaged_linux_tests=count,
                  native_repair="disable only start_fixed_line_intro/exhibit_commentary enabled entries",
                  hardware_validation=False, independent_native_speech_interception_verified=False)
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    for path in (HERE / "dist/交付验证结果.json", HERE / "交付验证结果.json"):
        path.write_text(output, encoding="utf-8", newline="\n")
    print(json.dumps({"sha256": report["sha256"], "bytes": report["size_bytes"],
                      "java_suites": len(java_results), "linux_tests": count,
                      "runtime_identical_to_1_0_8": True}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
