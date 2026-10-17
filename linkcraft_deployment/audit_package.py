#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


HERE = Path(__file__).resolve().parent
VERSION = "1.0.3-20260902"
NAME = f"sparkrobot-lingxi-linkcraft-addon-{VERSION}"
ARCHIVES = (HERE / "dist" / f"{NAME}.zip", HERE / "dist" / f"{NAME}.tar.gz")
REQUIRED = {
    "install.sh",
    "control.sh",
    "README.md",
    "release-info.txt",
    "manifest.json",
    "docs/灵创独立更新包部署教程.md",
    "payload/robot_local_navigation_v3/lingxi_nav_bridge.py",
    "payload/robot_local_navigation_v3/sdk_motion_controller.py",
    "payload/robot_local_navigation_v3/linkcraft_resources.py",
    "payload/robot_local_navigation_v3/tour_step_actions.py",
    "payload/robot_local_navigation_v3/tour_interaction_policy.py",
    "payload/console/dist/linkcraftResources.js",
}


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def audit_archive(path: Path) -> None:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not sidecar.is_file():
        raise RuntimeError(f"缺少校验文件：{sidecar}")
    expected = sidecar.read_text(encoding="ascii").split()[0]
    if digest_bytes(path.read_bytes()) != expected:
        raise RuntimeError(f"SHA-256 不匹配：{path.name}")

    if path.suffix == ".zip":
        archive = zipfile.ZipFile(path)
        names = archive.namelist()
        read_member = lambda name: archive.read(f"{NAME}/{name}")
    else:
        archive = tarfile.open(path, "r:gz")
        names = [member.name for member in archive.getmembers() if member.isfile()]

        def read_member(name: str) -> bytes:
            member = archive.extractfile(f"{NAME}/{name}")
            if member is None:
                raise RuntimeError(f"无法读取：{name}")
            return member.read()

    try:
        members = set()
        for name in names:
            if not name.startswith(NAME + "/"):
                continue
            relative = PurePosixPath(name[len(NAME) + 1:])
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"包含不安全路径：{name}")
            members.add(relative.as_posix())
        if not REQUIRED <= members:
            raise RuntimeError("部署包缺少：" + ", ".join(sorted(REQUIRED - members)))

        manifest = json.loads(read_member("manifest.json"))
        if manifest.get("version") != VERSION:
            raise RuntimeError("manifest 版本不正确")
        for item in manifest.get("files", []):
            name = str(item["path"])
            if name not in members:
                raise RuntimeError(f"manifest 文件不存在：{name}")
            if digest_bytes(read_member(name)) != item["sha256"]:
                raise RuntimeError(f"manifest 校验失败：{name}")

        forbidden_markers = (
            b"St87591540",
            b"ROBOT_SSH_PASSWORD=1",
            b"APP_SECRET=",
            b"DOUBAO_API_KEY=",
        )
        for member in members:
            if any(marker in read_member(member) for marker in forbidden_markers):
                raise RuntimeError(f"包中疑似包含秘密：{member}")
    finally:
        archive.close()


def main() -> int:
    missing = [str(path) for path in ARCHIVES if not path.is_file()]
    if missing:
        raise RuntimeError("请先运行 build_package.py：" + ", ".join(missing))
    for path in ARCHIVES:
        audit_archive(path)
        print(f"审计通过：{path.name}")
    print("LINKCRAFT_PACKAGE_AUDIT_OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"审计失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
