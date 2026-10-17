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
VERSION = "1.0.0-20260929"
NAME = f"sparkrobot-lingxi-v1.3.9-narrow-corridor-policy-{VERSION}"
DIST = HERE / "dist"
FILES = ("install.sh", "control.sh", "apply_policy.py", "policy.json", "README.md")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def validate() -> None:
    missing = [name for name in FILES if not (HERE / name).is_file()]
    if missing:
        raise RuntimeError("文件缺失：" + ", ".join(missing))
    with tempfile.TemporaryDirectory(prefix=".narrow-policy-compile-") as temp:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(HERE / "apply_policy.py")],
            cwd=temp,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise RuntimeError(result.stderr)
    json.loads((HERE / "policy.json").read_text(encoding="utf-8"))


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
            {"format": "sparkrobot-lingxi-narrow-corridor-policy-v1", "version": VERSION, "files": files},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def make_archives(stage: Path) -> tuple[Path, Path]:
    DIST.mkdir(parents=True, exist_ok=True)
    zip_path = DIST / f"{NAME}.zip"
    tar_path = DIST / f"{NAME}.tar.gz"
    zip_path.unlink(missing_ok=True)
    tar_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            rel = PurePosixPath(NAME) / PurePosixPath(path.relative_to(stage).as_posix())
            info = zipfile.ZipInfo(rel.as_posix())
            mode = 0o755 if path.name in {"install.sh", "control.sh"} else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    shutil.make_archive(str(DIST / NAME), "gztar", root_dir=stage.parent, base_dir=NAME)
    return zip_path, tar_path


def write_checksum(path: Path) -> None:
    with path.with_suffix(path.suffix + ".sha256").open("w", encoding="ascii", newline="\n") as handle:
        handle.write(f"{digest(path)}  {path.name}\n")


def main() -> int:
    validate()
    with tempfile.TemporaryDirectory(prefix=".narrow-policy-build-", dir=str(DIST if DIST.exists() else HERE)) as temp:
        stage = Path(temp) / NAME
        stage.mkdir()
        for name in FILES:
            shutil.copy2(HERE / name, stage / name)
        for name in ("install.sh", "control.sh"):
            os.chmod(stage / name, 0o755)
        write_manifest(stage)
        zip_path, tar_path = make_archives(stage)
    write_checksum(zip_path)
    write_checksum(tar_path)
    print(f"已生成：{tar_path}")
    print(f"SHA-256：{digest(tar_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
