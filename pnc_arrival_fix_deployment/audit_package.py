#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import tarfile
import zipfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
VERSION = "1.0.2-20260906"
NAME = f"sparkrobot-lingxi-pnc-arrival-fix-{VERSION}"
DIST = HERE / "dist"
REQUIRED = {
    "install.sh",
    "control.sh",
    "README.md",
    "release-info.txt",
    "manifest.json",
    "payload/navigation_client.py",
    "payload/pnc_response.py",
    "docs/导航到达判定修复包部署说明.md",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit_names(names: set[str], label: str) -> None:
    names = {name.rstrip("/") for name in names}
    expected = {f"{NAME}/{item}" for item in REQUIRED}
    missing = sorted(expected - names)
    forbidden = sorted(
        name for name in names
        if name.startswith(f"{NAME}/")
        and not name.endswith("/")
        and name.removeprefix(f"{NAME}/") not in REQUIRED
    )
    if missing or forbidden:
        raise RuntimeError(f"{label} 清单不符合预期；缺失={missing}，额外={forbidden}")


def main() -> int:
    tar_path = DIST / f"{NAME}.tar.gz"
    zip_path = DIST / f"{NAME}.zip"
    for path in (tar_path, zip_path):
        if not path.is_file():
            raise RuntimeError(f"缺少归档：{path}")
    with tarfile.open(tar_path, "r:gz") as archive:
        audit_names({item.name for item in archive.getmembers() if item.isfile()}, "tar.gz")
    with zipfile.ZipFile(zip_path) as archive:
        audit_names({name for name in archive.namelist() if not name.endswith("/")}, "zip")
    print("增量包审计通过：只包含导航到达判定修复文件、安装脚本和部署说明")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
