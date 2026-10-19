"""Check the final archive, Linux transactions, and source/runtime consistency."""
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
from build import BASE, DOCS, JAVA_TESTS, NAME, PREFIX, PY_FILES, command, digest, prior, read_archive, verify_sums


def main():
    archive = HERE / "dist" / (NAME + ".tar.gz")
    raw = archive.read_bytes()
    assert archive.with_name(archive.name + ".sha256").read_bytes() == (
        digest(raw) + "  " + archive.name + "\n").encode("ascii")
    files = read_archive(raw, NAME)
    verify_sums(files, "SHA256SUMS")
    meta = json.loads(files["release.json"][1])
    assert set(meta["java_tests"]) == set(JAVA_TESTS) and all(meta["java_tests"].values())
    assert meta["tour_neutral_receipt"] == "收到"
    assert meta["tour_response_completes_before_dispatch"] is True
    previous_path = HERE.parent / "v138_silent_control_fix_deployment/dist" / (prior.NAME + ".tar.gz")
    previous_raw = previous_path.read_bytes()
    assert digest(previous_raw) == "6a7b596ee572ddf4741635a50f21e8564585f697beae3f9990654a01e36fb07e"
    previous = read_archive(previous_raw, prior.NAME)
    verify_sums(previous, "SHA256SUMS")
    previous_meta = json.loads(previous["release.json"][1])
    assert previous_meta["patched_jar_sha256"] in meta["superseded_jar_sha256"]
    for name in PY_FILES:
        key = PREFIX + name
        assert previous_meta["payload_sha256"][key] in meta["python_accepted_sha256"][key]
        assert files["payload/" + key][1] == previous["payload/" + key][1], "Navigation runtime changed: " + name
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
    with tempfile.TemporaryDirectory(prefix="tour-start-final-") as temporary:
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
        (HERE / "work/linux-installer-tests.log").write_text(log, encoding="utf-8")
        assert result.returncode == 0 and "\nOK" in log, log[-16000:]
        count = int(re.search(r"Ran (\d+) tests", log).group(1))
        result = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", "-q", "test"],
            cwd=root / "source/navigation", capture_output=True, timeout=90, text=True, encoding="utf-8")
        assert result.returncode == 0, result.stdout + result.stderr
        python_summary = result.stdout.strip().splitlines()[-1]
        assert "183 passed" in python_summary
        classes = root / "recompiled"
        classes.mkdir()
        java = Path(os.environ.get("JAVA_HOME", r"C:\Program Files\Java\jdk-17")) / "bin"
        command([java / "javac.exe", "-encoding", "UTF-8", "--release", "17", "-cp",
                 os.pathsep.join(map(str, (BASE / "reference/java/robot-local-navigation-java-all.jar",
                                          BASE / "reference/java/linksoul-agentsdk-1.4.0.jar"))),
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
        "java_regression": meta["java_tests"], "packaged_python_tests": python_summary,
        "packaged_linux_installer_tests": count, "source_runtime_class_count": len(compiled),
        "cumulative_through": meta["cumulative_through"],
        "previous_tour_comparison": meta["previous_tour_comparison"],
        "changed_java_entries_since_1_0_8": meta["changed_jar_entries_since_1_0_8"],
        "tour_neutral_receipt": meta["tour_neutral_receipt"],
        "actual_shipped_1_0_8_upgrade_hashes_accepted": True,
        "python_navigation_payload_unchanged_since_1_0_8": True,
        "hardware_validation": False, "independent_native_speech_interception_verified": False,
    }
    for target in (HERE / "交付验证结果.json", HERE / "dist/交付验证结果.json"):
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
