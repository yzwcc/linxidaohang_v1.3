#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


HERE = Path(__file__).resolve().parent
NAME = "lingxi-v1.3.1-full-deployment-20260901"
ARCHIVE = HERE / "dist" / f"{NAME}.zip"
SIDECAR = ARCHIVE.with_suffix(ARCHIVE.suffix + ".sha256")
PREFIX = f"{NAME}/"
AIMDK = "packages/aimdk-aarch64-1bde262f-artifacts.7z"
NAV_VERSION = "1.0.7-20260901"
NAV_ROOT = f"sparkrobot-lingxi-nav-internal-{NAV_VERSION}"
NAV = f"packages/{NAV_ROOT}.tar.gz"
NAV_SHA256 = "35acbc60fb6135fda778b65f58ae0a4c71e8806dd3a0213649a25bdc80e07c94"


def stream_digest(handle) -> str:
    value = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        value.update(chunk)
    return value.hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as handle:
        return stream_digest(handle)


def main() -> int:
    if not ARCHIVE.is_file() or not SIDECAR.is_file():
        raise RuntimeError("缺少最终 ZIP 或 SHA-256 文件")
    expected_archive_hash = SIDECAR.read_text(encoding="ascii").split()[0]
    if file_digest(ARCHIVE) != expected_archive_hash:
        raise RuntimeError("最终 ZIP 的 SHA-256 不一致")

    with zipfile.ZipFile(ARCHIVE) as archive:
        infos = archive.infolist()
        for info in infos:
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"ZIP 包含不安全路径：{info.filename}")
        names = {info.filename for info in infos}
        required = {
            PREFIX + "README-先看我.md",
            PREFIX + "VERSION",
            PREFIX + "bundle-manifest.json",
            PREFIX + "SHA256SUMS.txt",
            PREFIX + AIMDK,
            PREFIX + NAV,
            PREFIX + "scripts/Windows-校验并上传全部文件.ps1",
            PREFIX + "scripts/robot/00-install-environment.sh",
            PREFIX + "scripts/robot/01-replace-aimdk.sh",
            PREFIX + "scripts/robot/02-install-lingxi-navigation.sh",
            PREFIX + "scripts/robot/03-verify-after-web-start.sh",
            PREFIX + "scripts/robot/04-check-localization.sh",
            PREFIX + "docs/灵犀-v1.3.1-完整部署教程.md",
            PREFIX + "docs/新机器人验收清单.md",
            PREFIX + "templates/secrets.env.example",
        }
        missing = sorted(required - names)
        if missing:
            raise RuntimeError("ZIP 缺少文件：" + ", ".join(missing))

        manifest = json.loads(archive.read(PREFIX + "bundle-manifest.json"))
        if manifest["bundle_version"] != "1.3.1" or manifest["contains_real_secrets"]:
            raise RuntimeError("套件版本或秘密标记不正确")
        nav_manifest = manifest["components"]["lingxi_navigation"]
        if nav_manifest["version"] != NAV_VERSION or nav_manifest["sha256"] != NAV_SHA256:
            raise RuntimeError("导航组件版本或 SHA-256 不正确")

        checksum_lines = archive.read(PREFIX + "SHA256SUMS.txt").decode("utf-8").splitlines()
        for line in checksum_lines:
            expected, relative = line.split("  ", 1)
            member = PREFIX + relative
            if member not in names:
                raise RuntimeError(f"校验清单成员不存在：{relative}")
            with archive.open(member) as handle:
                if stream_digest(handle) != expected:
                    raise RuntimeError(f"成员 SHA-256 不一致：{relative}")

        ps1 = archive.read(PREFIX + "scripts/Windows-校验并上传全部文件.ps1")
        if not ps1.startswith(b"\xef\xbb\xbf"):
            raise RuntimeError("PowerShell 脚本缺少 UTF-8 BOM")
        powershell = shutil.which("powershell") or shutil.which("powershell.exe")
        if powershell:
            with tempfile.TemporaryDirectory(prefix="lingxi-v131-audit-") as temp:
                script_path = Path(temp) / "upload.ps1"
                script_path.write_bytes(ps1)
                parser_command = (
                    "& { param([string]$Target) "
                    "$errors = $null; "
                    "[System.Management.Automation.Language.Parser]::ParseFile($Target, [ref]$null, [ref]$errors) | Out-Null; "
                    "if ($errors.Count) { $errors | Format-List; exit 1 } "
                    "}"
                )
                subprocess.run(
                    [powershell, "-NoProfile", "-Command", parser_command, str(script_path)],
                    check=True,
                )

        nav_required = {
            f"{NAV_ROOT}/payload/console/dist/public/index.html",
            f"{NAV_ROOT}/payload/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py",
            f"{NAV_ROOT}/payload/java/robot-local-navigation-java-all.jar",
            f"{NAV_ROOT}/source/navigation/setup.py",
            f"{NAV_ROOT}/source/java/pom.xml",
            f"{NAV_ROOT}/source/server/package.json",
            f"{NAV_ROOT}/install.sh",
        }
        nav_names: set[str] = set()
        with archive.open(PREFIX + NAV) as nav_handle:
            with tarfile.open(fileobj=nav_handle, mode="r|gz") as nav_archive:
                for member in nav_archive:
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or ".." in path.parts:
                        raise RuntimeError(f"导航 TAR 包含不安全路径：{member.name}")
                    nav_names.add(member.name)
        nav_missing = sorted(nav_required - nav_names)
        if nav_missing:
            raise RuntimeError("导航组件缺少运行文件或源码：" + ", ".join(nav_missing))

    print("BUNDLE_AUDIT_OK")
    print(f"archive={ARCHIVE}")
    print(f"sha256={expected_archive_hash}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        OSError,
        RuntimeError,
        KeyError,
        ValueError,
        subprocess.CalledProcessError,
        zipfile.BadZipFile,
        tarfile.TarError,
    ) as exc:
        print(f"BUNDLE_AUDIT_FAILED: {exc}")
        raise SystemExit(1)
