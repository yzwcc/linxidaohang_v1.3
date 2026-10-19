"""Audit the final deliverable, including its extracted Linux installer."""
import hashlib
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
from build import BASE, CHANGED_CLASSES, JAVA_TESTS, NAME, NEW_CLASSES, PREVIOUS, PREVIOUS_DIR, class_family, command
sys.path.insert(0, str(BASE))
from archive_utils import read_archive, verify_sums


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    archive = HERE / "dist" / (NAME + ".tar.gz")
    raw = archive.read_bytes()
    assert archive.with_suffix(archive.suffix + ".sha256").read_bytes() == (
        sha(raw) + "  " + archive.name + "\n").encode("ascii")
    files = read_archive(raw, NAME)
    verify_sums(files, "SHA256SUMS")
    text_count = 0
    for name, (_, data) in files.items():
        if name.endswith((".java", ".py", ".sh", ".md", ".json")) or name == "SHA256SUMS":
            assert b"\r" not in data, "CRLF: " + name
            text_count += 1
        if name.endswith(".py"):
            compile(data, name, "exec")
    meta = json.loads(files["release.json"][1])
    assert set(meta["java_tests"]) == set(JAVA_TESTS)
    assert len(JAVA_TESTS) == 15
    assert all(meta["java_tests"].values())
    assert meta["changed_runtime_files"] == ["java/robot-local-navigation-java-all.jar"]
    assert meta["changed_java_classes"] == list(CHANGED_CLASSES)
    assert meta["allowed_releases"] == ["1.0.19-20261017-r" + str(n) for n in range(11, 16)] + [
        "1.0.19-20261019-r16"]
    assert meta["hardware_validation"] is False
    assert meta["independent_native_speech_interception_verified"] is False
    previous = read_archive((PREVIOUS_DIR / "dist" / (PREVIOUS + ".tar.gz")).read_bytes(), PREVIOUS)
    old_jar = previous["payload/java/robot-local-navigation-java-all.jar"][1]
    assert sha(old_jar) in meta["superseded_jar_sha256"]
    comparison = meta["previous_comparison"]
    assert comparison["package"] == PREVIOUS and comparison["jar_sha256"] == sha(old_jar)
    assert "Routing failures " in comparison["result"]
    assert comparison["test_source_sha256"] == sha(
        files["tests/com/agibot/aiem/nav/NavigationTourRoutingTest.java"][1])
    jar_data = files["payload/java/robot-local-navigation-java-all.jar"][1]
    assert sha(jar_data) == meta["patched_jar_sha256"]
    with zipfile.ZipFile(io.BytesIO(jar_data)) as jar, zipfile.ZipFile(io.BytesIO(old_jar)) as old:
        assert jar.testzip() is None
        assert int.from_bytes(jar.read("com/agibot/aiem/nav/LingxiNavAgentApp.class")[6:8], "big") == 61
        changed = sorted(name for name in set(jar.namelist()) | set(old.namelist())
                         if name not in jar.namelist() or name not in old.namelist()
                         or jar.read(name) != old.read(name))
        assert changed == meta["changed_jar_entries_since_1_0_5"]
        assert all(class_family(name, NEW_CLASSES) for name in changed)
    blocks = re.findall(r"```bash\n(.*?)```", files["部署说明.md"][1].decode(), re.S)
    scripts = [data for name, (_, data) in files.items() if name.endswith(".sh")]
    scripts.extend(block.encode() for block in blocks)
    for data in scripts:
        args = ["wsl", "-d", "Ubuntu", "--", "bash", "-n"] if os.name == "nt" else ["bash", "-n"]
        result = subprocess.run(args, input=data, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
    with tempfile.TemporaryDirectory(prefix="nav-tour-final-") as temporary:
        root = Path(temporary)
        for name, (entry, data) in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(entry.mode)
        import importlib.util
        spec = importlib.util.spec_from_file_location("final_installer", root / "install.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        installer.verify_package(root)
        if os.name == "nt":
            linux_root = "/mnt/" + root.drive[0].lower() + root.as_posix()[2:]
            args = ["wsl", "-d", "Ubuntu", "-u", "root", "--", "python3", linux_root + "/test_install.py"]
        else:
            args = [sys.executable, str(root / "test_install.py")]
        result = subprocess.run(args, capture_output=True, timeout=60)
        installer_log = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        assert result.returncode == 0 and "Ran 11 tests" in installer_log and "\nOK" in installer_log, installer_log
        java = Path(os.environ.get("JAVA_HOME", r"C:\Program Files\Java\jdk-17")) / "bin"
        classes = root / "recompiled"
        classes.mkdir()
        baseline = BASE / "reference/java/robot-local-navigation-java-all.jar"
        sdk = BASE / "reference/java/linksoul-agentsdk-1.4.0.jar"
        command([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp",
                 os.pathsep.join(map(str, (baseline, sdk))), "-d", classes, *sorted((root / "source").rglob("*.java"))])
        compiled = {p.relative_to(classes).as_posix(): p.read_bytes() for p in classes.rglob("*.class")}
        with zipfile.ZipFile(io.BytesIO(jar_data)) as jar:
            assert all(jar.read(name) == data for name, data in compiled.items()), "Final source/runtime mismatch"
    report = {
        "archive": archive.name, "sha256": sha(raw), "size_bytes": len(raw),
        "checked_files": len(files), "lf_text_files": text_count,
        "bash_scripts_and_guide_blocks": len(scripts), "java_target": 17,
        "java_regression": meta["java_tests"], "previous_comparison": comparison,
        "changed_jar_entries_since_1_0_5": changed,
        "packaged_linux_installer_tests": 11, "source_runtime_class_count": len(compiled),
        "hardware_validation": False, "independent_native_speech_interception_verified": False,
    }
    (HERE / "dist/交付验证结果.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Keep the verified report in Git; binary deliverables remain ignored.
    (HERE / "交付验证结果.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
