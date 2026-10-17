#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
VERSION = "1.0.2-20260902"
NAME = f"sparkrobot-lingxi-kb-{VERSION}"
ARCHIVE = HERE / "dist" / f"{NAME}.zip"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    sidecar = ARCHIVE.with_suffix(ARCHIVE.suffix + ".sha256")
    if not ARCHIVE.is_file() or not sidecar.is_file():
        raise RuntimeError("缺少部署包或 SHA-256 文件")
    expected = sidecar.read_text(encoding="ascii").split()[0]
    if digest(ARCHIVE) != expected:
        raise RuntimeError("部署包 SHA-256 不一致")
    with zipfile.ZipFile(ARCHIVE) as archive:
        names = set(archive.namelist())
        required = {
            f"{NAME}/install.sh", f"{NAME}/install_voice_agent.sh", f"{NAME}/control.sh", f"{NAME}/release-info.txt",
            f"{NAME}/manifest.json", f"{NAME}/service/retriever_service.py",
            f"{NAME}/service/kb-search", f"{NAME}/systemd/sparkrobot-lingxi-kb.service",
            f"{NAME}/templates/retriever.env.example", f"{NAME}/agent/robot-local-navigation-java-all-kb.jar",
            f"{NAME}/docs/灵心知识库独立部署教程.md",
        }
        if not required <= names:
            raise RuntimeError("部署包缺少：" + ", ".join(sorted(required - names)))
        for member in names:
            path = PurePosixPath(member)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"ZIP 包含不安全路径：{member}")
        manifest = json.loads(archive.read(f"{NAME}/manifest.json"))
        if manifest["version"] != VERSION:
            raise RuntimeError("版本清单不正确")
        for item in manifest["files"]:
            member = f"{NAME}/{item['path']}"
            with archive.open(member) as handle:
                if hashlib.sha256(handle.read()).hexdigest() != item["sha256"]:
                    raise RuntimeError(f"文件校验失败：{item['path']}")
        for marker in (b"St87591540", b"ROBOT_SSH_PASSWORD=1", b"1.0.6"):
            for member in names:
                if member.endswith((".zip", ".gz", ".7z")):
                    continue
                if marker in archive.read(member):
                    raise RuntimeError(f"套件包含不应出现的内容：{member}")
    print("KB_PACKAGE_AUDIT_OK")
    print(f"archive={ARCHIVE}")
    print(f"sha256={expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
