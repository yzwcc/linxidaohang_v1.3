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

NAME = "sparkrobot-lingxi-v1.3.8-control-reply-fix-1.0.1-20261018"


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
    meta = json.loads(files["release.json"][1])
    assert meta["changed_runtime_files"] == ["java/robot-local-navigation-java-all.jar"]
    assert hashlib.sha256(files["payload/java/robot-local-navigation-java-all.jar"][1]).hexdigest() == meta["patched_jar_sha256"]
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
    report = {
        "archive": archive.name, "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw), "checked_files": len(files), "lf_text_files": text_count,
        "bash_scripts_and_guide_blocks": len(commands), "java_target": 17,
        "java_regression": meta["java_tests"], "hardware_validation": False,
    }
    output = HERE / "dist" / "交付验证结果.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
