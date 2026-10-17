#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
VERSION = "1.0.2-20260902"
NAME = f"sparkrobot-lingxi-kb-{VERSION}"
DIST = HERE / "dist"
JAVA_AGENT_JAR = (
    HERE.parent
    / "robot_local_nav_java"
    / "robot_local_nav_java-new-java-interaction-end-hook"
    / "target"
    / "robot-local-navigation-java-0.1.0-all-kb-20260902.jar"
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    DIST.mkdir(exist_ok=True)
    output = DIST / f"{NAME}.zip"
    with tempfile.TemporaryDirectory(prefix=".kb-build-", dir=DIST) as temp:
        stage = Path(temp) / NAME
        stage.mkdir()
        if not JAVA_AGENT_JAR.is_file():
            raise RuntimeError(f"缺少已编译的知识库语音接入 JAR：{JAVA_AGENT_JAR}")
        for relative in ("install.sh", "install_voice_agent.sh", "control.sh", "release-info.txt", "README.md"):
            shutil.copy2(HERE / relative, stage / relative)
        for directory in ("service", "systemd", "templates", "docs"):
            shutil.copytree(HERE / directory, stage / directory)
        (stage / "agent").mkdir()
        shutil.copy2(JAVA_AGENT_JAR, stage / "agent" / "robot-local-navigation-java-all-kb.jar")
        for path in stage.rglob("*"):
            if path.is_file() and path.suffix in {".sh", ""} and path.name in {"install.sh", "install_voice_agent.sh", "control.sh", "kb-search", "retriever_service.py"}:
                path.chmod(0o755)
        manifest = []
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            manifest.append({"path": path.relative_to(stage).as_posix(), "sha256": digest(path), "size": path.stat().st_size})
        (stage / "manifest.json").write_text(json.dumps({"format": "sparkrobot-lingxi-kb-v1", "version": VERSION, "files": manifest}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output.unlink(missing_ok=True)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(item for item in stage.rglob("*") if item.is_file()):
                relative = PurePosixPath(NAME) / PurePosixPath(path.relative_to(stage).as_posix())
                info = zipfile.ZipInfo(relative.as_posix())
                executable = path.name in {"install.sh", "install_voice_agent.sh", "control.sh", "kb-search", "retriever_service.py"}
                info.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
    checksum = digest(output)
    output.with_suffix(output.suffix + ".sha256").write_text(f"{checksum}  {output.name}\n", encoding="ascii")
    print(f"部署包：{output}")
    print(f"SHA-256：{checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
