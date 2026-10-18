"""Verify the actual shipped archive and its Linux commands."""
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
sys.path.insert(0, str(HERE.parent / "v138_complete_r15_deployment"))
from archive_utils import read_archive, verify_sums

NAME = "sparkrobot-lingxi-v1.3.8-control-reply-fix-1.0.4-20261018"
PREVIOUS = "sparkrobot-lingxi-v1.3.8-control-reply-fix-1.0.3-20261018"
CHANGED_CLASSES = ("LingxiNavAgentApp", "PresetMotionCatalog", "DoubaoArkClient")


def main():
    archive = HERE / "dist" / (NAME + ".tar.gz")
    raw = archive.read_bytes()
    sidecar = archive.with_suffix(archive.suffix + ".sha256").read_bytes()
    assert sidecar == (hashlib.sha256(raw).hexdigest() + "  " + archive.name + "\n").encode("ascii")
    files = read_archive(raw, NAME)
    verify_sums(files, "SHA256SUMS")
    text_count = 0
    for name, (_, data) in files.items():
        if name.endswith((".java", ".py", ".sh", ".md", ".json")) or name == "SHA256SUMS":
            assert b"\r" not in data, name + ": CRLF detected"
            text_count += 1
        if name.endswith(".py"):
            compile(data, name, "exec")
    with zipfile.ZipFile(io.BytesIO(files["payload/java/robot-local-navigation-java-all.jar"][1])) as jar:
        assert jar.testzip() is None
        app = jar.read("com/agibot/aiem/nav/LingxiNavAgentApp.class")
        assert int.from_bytes(app[6:8], "big") == 61, "Must support Java 17"
        baseline = HERE.parent / "v138_complete_r15_deployment/reference/java/robot-local-navigation-java-all.jar"
        with zipfile.ZipFile(baseline) as original:
            changed = sorted(name for name in set(jar.namelist()) | set(original.namelist())
                             if name not in jar.namelist() or name not in original.namelist()
                             or jar.read(name) != original.read(name))
            assert changed
            assert all(any(name == "com/agibot/aiem/nav/" + cls + ".class"
                           or name.startswith("com/agibot/aiem/nav/" + cls + "$") and name.endswith(".class")
                           for cls in CHANGED_CLASSES) for name in changed), changed
    meta = json.loads(files["release.json"][1])
    assert meta["changed_runtime_files"] == ["java/robot-local-navigation-java-all.jar"]
    assert meta["changed_java_classes"] == list(CHANGED_CLASSES)
    assert hashlib.sha256(files["payload/java/robot-local-navigation-java-all.jar"][1]).hexdigest() == meta["patched_jar_sha256"]
    assert len(meta["java_tests"]) == 13 and meta["java_tests"]["ControlReplyTextWireTest"]
    assert meta["previous_comparison"] == {
        "package": PREVIOUS, "suite": "ControlReplyTextWireTest", "assertions": 712, "expected_failures": 28}
    previous = read_archive((HERE / "dist" / (PREVIOUS + ".tar.gz")).read_bytes(), PREVIOUS)
    previous_jar = previous["payload/java/robot-local-navigation-java-all.jar"][1]
    assert hashlib.sha256(previous_jar).hexdigest() in meta["superseded_jar_sha256"]
    with zipfile.ZipFile(io.BytesIO(previous_jar)) as old, zipfile.ZipFile(
            io.BytesIO(files["payload/java/robot-local-navigation-java-all.jar"][1])) as new:
        changed_previous = sorted(name for name in set(old.namelist()) | set(new.namelist())
                                  if name not in old.namelist() or name not in new.namelist()
                                  or old.read(name) != new.read(name))
    assert changed_previous == meta["changed_jar_entries_since_1_0_3"]
    assert all(name == "com/agibot/aiem/nav/LingxiNavAgentApp.class"
               or name.startswith("com/agibot/aiem/nav/LingxiNavAgentApp$") and name.endswith(".class")
               for name in changed_previous)
    bash_blocks = re.findall(r"```bash\n(.*?)```", files["部署说明.md"][1].decode("utf-8"), re.S)
    commands = [data for name, (_, data) in files.items() if name.endswith(".sh")]
    commands += [block.encode("utf-8") for block in bash_blocks]
    for command in commands:
        args = ["wsl", "-d", "Ubuntu", "--", "bash", "-n"] if os.name == "nt" else ["bash", "-n"]
        result = subprocess.run(args, input=command, capture_output=True)
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    # Extract only validated regular entries to a private temporary directory.
    with tempfile.TemporaryDirectory(prefix="reply-delivery-") as temporary:
        root = Path(temporary)
        for name, (entry, data) in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(entry.mode)
        import importlib.util
        spec = importlib.util.spec_from_file_location("packaged_reply_installer", root / "install.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        installer.verify_package(root)
        # Execute the installer tests extracted from the actual deliverable,
        # rather than silently testing a different workspace copy.
        if os.name == "nt":
            linux_root = "/mnt/" + root.drive[0].lower() + root.as_posix()[2:]
            args = ["wsl", "-d", "Ubuntu", "-u", "root", "--", "python3", linux_root + "/test_install.py"]
        else:
            args = [sys.executable, str(root / "test_install.py")]
        installed_tests = subprocess.run(args, capture_output=True, timeout=60)
        installer_output = (installed_tests.stdout + installed_tests.stderr).decode("utf-8", errors="replace")
        assert installed_tests.returncode == 0, installer_output
        assert "Ran 10 tests" in installer_output and "\nOK" in installer_output, installer_output
    report = {
        "archive": archive.name, "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw), "checked_files": len(files), "lf_text_files": text_count,
        "bash_scripts_and_guide_blocks": len(commands), "java_target": 17,
        "java_regression": meta["java_tests"], "changed_jar_entries": changed,
        "previous_comparison": meta["previous_comparison"],
        "changed_jar_entries_since_1_0_3": changed_previous,
        "packaged_linux_installer_tests": 10, "hardware_validation": False,
    }
    output = HERE / "dist" / "交付验证结果.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
