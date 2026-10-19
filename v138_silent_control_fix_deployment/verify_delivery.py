"""Verify the shipped archive, Linux transaction, and reproducible runtime bytecode."""
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
from build import BASE, DOCS, JAVA_TESTS, NAME, PREFIX, PY_FILES, command, digest, read_archive, verify_sums


def main():
    archive = HERE / "dist" / (NAME + ".tar.gz")
    raw = archive.read_bytes()
    assert archive.with_name(archive.name + ".sha256").read_bytes() == (
        digest(raw) + "  " + archive.name + "\n").encode("ascii")
    files = read_archive(raw, NAME)
    verify_sums(files, "SHA256SUMS")
    meta = json.loads(files["release.json"][1])
    assert set(meta["java_tests"]) == set(JAVA_TESTS) and all(meta["java_tests"].values())
    assert meta["hardware_validation"] is False
    assert meta["silent_start_control"] is True
    text_count = 0
    for name, (_, data) in files.items():
        if name.endswith((".py", ".sh", ".java", ".md", ".json")) or name == "SHA256SUMS":
            assert b"\r" not in data, name
            text_count += 1
        if name.endswith(".py"):
            compile(data, name, "exec")
    for name, expected in meta["payload_sha256"].items():
        assert digest(files["payload/" + name][1]) == expected, name
    for name in PY_FILES:
        assert files["payload/" + PREFIX + name][1] == files[
            "source/navigation/robot_local_navigation_v3/" + name][1]
    scripts = [data for name, (_, data) in files.items() if name.endswith(".sh")]
    for doc in DOCS:
        scripts += [block.encode() for block in re.findall(
            r"```bash\n(.*?)```", files[doc][1].decode("utf-8"), re.S)]
    for data in scripts:
        result = subprocess.run(["wsl", "-d", "Ubuntu", "--", "bash", "-n"],
                                input=data, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr
    with tempfile.TemporaryDirectory(prefix="silent-control-final-") as temporary:
        root = Path(temporary)
        for name, (entry, data) in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(entry.mode)
        linux_root = "/mnt/" + root.drive[0].lower() + root.as_posix()[2:]
        result = subprocess.run(["wsl", "-d", "Ubuntu", "-u", "root", "--",
            "python3", "-m", "unittest", "discover", "-s", linux_root, "-p", "test_*.py", "-v"],
            capture_output=True, timeout=90)
        log = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        (HERE / "work/linux-installer-tests.log").write_text(log, encoding="utf-8")
        assert result.returncode == 0 and "\nOK" in log, log[-16000:]
        installer_count = int(re.search(r"Ran (\d+) tests", log).group(1))
        python = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", "-q", "test"],
            cwd=root / "source/navigation", capture_output=True, timeout=90, text=True, encoding="utf-8")
        assert python.returncode == 0, python.stdout + python.stderr
        python_summary = python.stdout.strip().splitlines()[-1]
        assert "183 passed" in python_summary, python_summary
        classes = root / "recompiled"
        classes.mkdir()
        java = Path(os.environ.get("JAVA_HOME", r"C:\Program Files\Java\jdk-17")) / "bin"
        sdk = BASE / "reference/java/linksoul-agentsdk-1.4.0.jar"
        command([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp",
                 os.pathsep.join(map(str, (BASE / "reference/java/robot-local-navigation-java-all.jar", sdk))),
                 "-d", classes, *sorted((root / "source/java/main").rglob("*.java"))])
        compiled = {p.relative_to(classes).as_posix(): p.read_bytes() for p in classes.rglob("*.class")}
        with zipfile.ZipFile(io.BytesIO(files["payload/java/robot-local-navigation-java-all.jar"][1])) as jar:
            assert jar.testzip() is None
            assert all(jar.read(name) == data for name, data in compiled.items())
            assert int.from_bytes(jar.read("com/agibot/aiem/nav/LingxiNavAgentApp.class")[6:8], "big") == 61
    report = {
        "archive": archive.name, "sha256": digest(raw), "size_bytes": len(raw),
        "checked_files": len(files), "lf_text_files": text_count,
        "bash_scripts_and_guide_blocks": len(scripts),
        "java_regression": meta["java_tests"], "final_packaged_python_tests": python_summary,
        "packaged_linux_installer_tests": installer_count, "source_runtime_class_count": len(compiled),
        "cumulative_through": meta["cumulative_through"],
        "previous_silent_comparison": meta["previous_silent_comparison"],
        "changed_runtime_files": meta["changed_runtime_files"],
        "hardware_validation": False, "independent_native_speech_interception_verified": False,
    }
    for target in (HERE / "交付验证结果.json", HERE / "dist/交付验证结果.json"):
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
