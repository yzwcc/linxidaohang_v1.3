#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


HERE = Path(__file__).resolve().parent
VERSION = "1.0.2-20260906"
NAME = f"sparkrobot-lingxi-pnc-arrival-fix-{VERSION}"
DIST = HERE / "dist"
PAYLOAD_FILES = ("navigation_client.py", "pnc_response.py")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def validate_sources() -> None:
    missing = [name for name in PAYLOAD_FILES if not (HERE / "payload" / name).is_file()]
    if missing:
        raise RuntimeError("增量补丁文件缺失：" + ", ".join(missing))
    with tempfile.TemporaryDirectory(prefix=".pnc-arrival-compile-") as temp:
        for name in PAYLOAD_FILES:
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", str(HERE / "payload" / name)],
                cwd=temp,
                capture_output=True,
                text=True,
            )
            if result.returncode:
                raise RuntimeError(f"无法编译 {name}:\n{result.stderr}")


def copy_tree(stage: Path) -> None:
    (stage / "payload").mkdir(parents=True)
    for name in PAYLOAD_FILES:
        shutil.copy2(HERE / "payload" / name, stage / "payload" / name)
    for name in ("install.sh", "control.sh", "README.md", "release-info.txt"):
        shutil.copy2(HERE / name, stage / name)
    os.chmod(stage / "install.sh", 0o755)
    os.chmod(stage / "control.sh", 0o755)
    shutil.copytree(HERE / "docs", stage / "docs")


def write_manifest(stage: Path) -> None:
    files = []
    for path in sorted(item for item in stage.rglob("*") if item.is_file()):
        files.append({
            "path": path.relative_to(stage).as_posix(),
            "sha256": digest(path),
            "size": path.stat().st_size,
        })
    (stage / "manifest.json").write_text(
        json.dumps(
            {"format": "sparkrobot-lingxi-pnc-arrival-fix-v1", "version": VERSION, "files": files},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_archives(stage: Path) -> tuple[Path, Path]:
    DIST.mkdir(parents=True, exist_ok=True)
    zip_path = DIST / f"{NAME}.zip"
    tar_path = DIST / f"{NAME}.tar.gz"
    zip_path.unlink(missing_ok=True)
    tar_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            rel = PurePosixPath(NAME) / PurePosixPath(path.relative_to(stage).as_posix())
            info = zipfile.ZipInfo(rel.as_posix())
            executable = path.name in {"install.sh", "control.sh"}
            info.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    shutil.make_archive(str(DIST / NAME), "gztar", root_dir=stage.parent, base_dir=NAME)
    return zip_path, tar_path


def write_checksum(path: Path) -> None:
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    # sha256sum treats CR from Windows line endings as part of the filename.
    with checksum_path.open("w", encoding="ascii", newline="\n") as handle:
        handle.write(f"{digest(path)}  {path.name}\n")


def main() -> int:
    validate_sources()
    with tempfile.TemporaryDirectory(prefix=".pnc-arrival-build-", dir=DIST if DIST.exists() else HERE) as temp:
        stage = Path(temp) / NAME
        stage.mkdir()
        copy_tree(stage)
        write_manifest(stage)
        zip_path, tar_path = write_archives(stage)
    write_checksum(zip_path)
    write_checksum(tar_path)
    print(f"已生成：{zip_path}")
    print(f"已生成：{tar_path}")
    print(f"SHA-256：{digest(tar_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
