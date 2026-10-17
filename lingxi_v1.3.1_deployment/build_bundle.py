#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
VERSION = "1.3.1"
RELEASE_DATE = "20260901"
BUNDLE_NAME = f"lingxi-v{VERSION}-full-deployment-{RELEASE_DATE}"

AIMDK_SOURCE = Path(r"E:\1资料\0火花机器人资料\内部资料\aimdk-aarch64-1bde262f-artifacts.7z")
AIMDK_NAME = AIMDK_SOURCE.name
AIMDK_SHA256 = "f13147a175bc3fb621fb118698aae0353c097adda38bba640f9a39c01433cc98"

NAV_NAME = "sparkrobot-lingxi-nav-internal-1.0.7-20260901.tar.gz"
NAV_SOURCE = ROOT / "internal_deployment" / "dist" / NAV_NAME
NAV_SHA256 = "35acbc60fb6135fda778b65f58ae0a4c71e8806dd3a0213649a25bdc80e07c94"

FULL_GUIDE_SOURCE = ROOT / "新机器人完整部署命令手册-AIMDK与灵犀导航-2026-09-01.md"
ACCEPTANCE_SOURCE = ROOT / "internal_deployment" / "docs" / "新机器人验收清单.md"
OUTPUT_DIR = HERE / "dist"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def require_file(path: Path, expected_sha256: str | None = None) -> None:
    if not path.is_file():
        raise RuntimeError(f"缺少构建输入：{path}")
    if expected_sha256 and digest(path) != expected_sha256:
        raise RuntimeError(f"文件校验失败：{path}")


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def build_stage(stage: Path) -> None:
    require_file(AIMDK_SOURCE, AIMDK_SHA256)
    require_file(NAV_SOURCE, NAV_SHA256)
    require_file(FULL_GUIDE_SOURCE)
    require_file(ACCEPTANCE_SOURCE)

    copy_file(HERE / "README-先看我.md", stage / "README-先看我.md")
    copy_file(HERE / "docs" / "组件版本与验收边界.md", stage / "docs" / "组件版本与验收边界.md")
    copy_file(ACCEPTANCE_SOURCE, stage / "docs" / "新机器人验收清单.md")
    copy_file(HERE / "templates" / "secrets.env.example", stage / "templates" / "secrets.env.example")
    shutil.copytree(HERE / "scripts", stage / "scripts")
    powershell_script = stage / "scripts" / "Windows-校验并上传全部文件.ps1"
    powershell_script.write_text(
        powershell_script.read_text(encoding="utf-8"),
        encoding="utf-8-sig",
        newline="\r\n",
    )

    guide_header = f"""# 灵犀 v{VERSION} 完整部署教程

套件版本：`v{VERSION}`  
套件发布日期：`2026-09-01`  
内含 AIMDK：`aimdk v0.0.0-g1bde262`  
内含导航运行组件：`1.0.7-20260901`

本文档是 v1.3.1 离线套件的完整教程。先按根目录《README-先看我》执行最短流程；需要逐步部署或排障时，按以下完整手册操作。

---

"""
    full_guide = FULL_GUIDE_SOURCE.read_text(encoding="utf-8")
    write_text(stage / "docs" / "灵犀-v1.3.1-完整部署教程.md", guide_header + full_guide)

    packages = stage / "packages"
    copy_file(AIMDK_SOURCE, packages / AIMDK_NAME)
    copy_file(NAV_SOURCE, packages / NAV_NAME)
    write_text(packages / f"{AIMDK_NAME}.sha256", f"{AIMDK_SHA256}  {AIMDK_NAME}\n")
    write_text(packages / f"{NAV_NAME}.sha256", f"{NAV_SHA256}  {NAV_NAME}\n")
    write_text(
        packages / "README.md",
        "# 安装组件\n\n"
        "- `aimdk-aarch64-1bde262f-artifacts.7z`：AIMDK ARM64 原始构建包。\n"
        "- `sparkrobot-lingxi-nav-internal-1.0.7-20260901.tar.gz`：导航、地图、语音、网页和完整源码。\n\n"
        "不要重命名组件文件；自动上传和安装脚本会校验固定文件名与 SHA-256。\n",
    )

    manifest = {
        "format": "sparkrobot-lingxi-full-deployment-v1",
        "bundle_name": BUNDLE_NAME,
        "bundle_version": VERSION,
        "release_date": "2026-09-01",
        "architecture": "aarch64",
        "target": "Agibot X2 Ultra / Ubuntu 22.04 / ROS 2 Humble",
        "contains_real_secrets": False,
        "components": {
            "aimdk": {
                "version": "aimdk v0.0.0-g1bde262",
                "file": f"packages/{AIMDK_NAME}",
                "sha256": AIMDK_SHA256,
            },
            "lingxi_navigation": {
                "version": "1.0.7-20260901",
                "file": f"packages/{NAV_NAME}",
                "sha256": NAV_SHA256,
                "includes_source": True,
            },
        },
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    write_text(stage / "bundle-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    write_text(stage / "VERSION", f"LINGXI_BUNDLE_VERSION={VERSION}\nRELEASE_DATE={RELEASE_DATE}\n")

    checksums = []
    for path in sorted(item for item in stage.rglob("*") if item.is_file()):
        if path.name == "SHA256SUMS.txt":
            continue
        checksums.append(f"{digest(path)}  {path.relative_to(stage).as_posix()}")
    write_text(stage / "SHA256SUMS.txt", "\n".join(checksums) + "\n")


def audit_stage(stage: Path) -> None:
    required = (
        "README-先看我.md",
        "VERSION",
        "bundle-manifest.json",
        "SHA256SUMS.txt",
        f"packages/{AIMDK_NAME}",
        f"packages/{NAV_NAME}",
        "scripts/Windows-校验并上传全部文件.ps1",
        "scripts/robot/00-install-environment.sh",
        "scripts/robot/01-replace-aimdk.sh",
        "scripts/robot/02-install-lingxi-navigation.sh",
        "scripts/robot/03-verify-after-web-start.sh",
        "scripts/robot/04-check-localization.sh",
        "templates/secrets.env.example",
        "docs/灵犀-v1.3.1-完整部署教程.md",
        "docs/新机器人验收清单.md",
    )
    for relative in required:
        if not (stage / relative).is_file():
            raise RuntimeError(f"套件缺少：{relative}")

    if digest(stage / "packages" / AIMDK_NAME) != AIMDK_SHA256:
        raise RuntimeError("套件内 AIMDK 校验失败")
    if digest(stage / "packages" / NAV_NAME) != NAV_SHA256:
        raise RuntimeError("套件内导航包校验失败")

    forbidden = ("sk-", "AKLT", "BEGIN OPENSSH PRIVATE KEY", "BEGIN RSA PRIVATE KEY")
    for path in stage.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".7z", ".gz", ".zip", ".jar"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(marker in text for marker in forbidden):
            raise RuntimeError(f"套件疑似包含真实秘密：{path.relative_to(stage)}")


def make_zip(stage: Path, output: Path) -> None:
    output.unlink(missing_ok=True)
    fixed_time = (2026, 9, 1, 0, 0, 0)
    with zipfile.ZipFile(output, "w", allowZip64=True) as archive:
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            relative = PurePosixPath(BUNDLE_NAME) / PurePosixPath(path.relative_to(stage).as_posix())
            info = zipfile.ZipInfo(relative.as_posix(), date_time=fixed_time)
            executable = path.suffix.lower() in {".sh", ".ps1"}
            info.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
            compressed_asset = path.suffix.lower() in {".7z", ".gz", ".zip", ".jar"}
            info.compress_type = zipfile.ZIP_STORED if compressed_asset else zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes(), compresslevel=None if compressed_asset else 6)


def verify_zip(output: Path) -> None:
    with zipfile.ZipFile(output) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP 成员损坏：{bad}")
        names = set(archive.namelist())
        prefix = f"{BUNDLE_NAME}/"
        for relative in ("README-先看我.md", "SHA256SUMS.txt", f"packages/{AIMDK_NAME}", f"packages/{NAV_NAME}"):
            if prefix + relative not in names:
                raise RuntimeError(f"ZIP 缺少：{relative}")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / f"{BUNDLE_NAME}.zip"
    with tempfile.TemporaryDirectory(prefix=".lingxi-v131-", dir=OUTPUT_DIR) as temp:
        stage = Path(temp) / BUNDLE_NAME
        stage.mkdir()
        build_stage(stage)
        audit_stage(stage)
        make_zip(stage, output)
    verify_zip(output)
    archive_sha256 = digest(output)
    write_text(output.with_suffix(output.suffix + ".sha256"), f"{archive_sha256}  {output.name}\n")
    print(f"部署包：{output}")
    print(f"大小：{output.stat().st_size} bytes")
    print(f"SHA-256：{archive_sha256}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"构建失败：{exc}", file=os.sys.stderr)
        raise SystemExit(1)
