#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


HERE = Path(__file__).resolve().parent
SOURCE_ROOT = (
    HERE.parent
    / "robot_local_navigation_v3"
    / "robot_local_navigation_v3-new-auto-return-interaction-end"
    / "robot_local_navigation_v3"
)
VERSION = "1.0.3-20260902"
NAME = f"sparkrobot-lingxi-linkcraft-addon-{VERSION}"
DIST = HERE / "dist"
SOURCE_FILES = (
    "lingxi_nav_bridge.py",
    "sdk_motion_controller.py",
    "linkcraft_resources.py",
    "tour_step_actions.py",
    "tour_interaction_policy.py",
)
CONSOLE_SOURCE = HERE.parent / "server" / "dist" / "linkcraftResources.js"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def validate_sources() -> None:
    missing = [name for name in SOURCE_FILES if not (SOURCE_ROOT / name).is_file()]
    if missing:
        raise RuntimeError("灵创源码缺失: " + ", ".join(missing))
    if not CONSOLE_SOURCE.is_file():
        raise RuntimeError("缺少已编译网页后端文件，请先在 server 目录执行 npm run build: server/dist/linkcraftResources.js")
    with tempfile.TemporaryDirectory(prefix=".linkcraft-pycompile-") as temp:
        for name in SOURCE_FILES:
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", str(SOURCE_ROOT / name)],
                cwd=temp,
                capture_output=True,
                text=True,
            )
            if result.returncode:
                raise RuntimeError(f"灵创源码无法编译: {name}\n{result.stderr}")


def copy_tree(stage: Path) -> None:
    (stage / "payload" / "robot_local_navigation_v3").mkdir(parents=True)
    for name in SOURCE_FILES:
        shutil.copy2(SOURCE_ROOT / name, stage / "payload" / "robot_local_navigation_v3" / name)
    console_target = stage / "payload" / "console" / "dist"
    console_target.mkdir(parents=True)
    shutil.copy2(CONSOLE_SOURCE, console_target / "linkcraftResources.js")
    for name in ("install.sh", "control.sh", "README.md", "release-info.txt"):
        shutil.copy2(HERE / name, stage / name)
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
            {"format": "sparkrobot-lingxi-linkcraft-v1", "version": VERSION, "files": files},
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
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{digest(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    validate_sources()
    DIST.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".linkcraft-build-", dir=DIST) as temp:
        stage = Path(temp) / NAME
        stage.mkdir()
        copy_tree(stage)
        write_manifest(stage)
        zip_path, tar_path = write_archives(stage)
    write_checksum(zip_path)
    write_checksum(tar_path)
    print(f"已生成: {zip_path}")
    print(f"已生成: {tar_path}")
    print(f"SHA-256: {digest(tar_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
