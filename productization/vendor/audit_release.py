#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from build_release import SECRET_PATTERNS, elf_machine
from deploy_robot import resolve_openssl, safe_extract, verify_release


FORBIDDEN_SUFFIXES = {".c", ".cc", ".cpp", ".h", ".hpp", ".java", ".kt", ".pyx", ".ts", ".tsx"}
FORBIDDEN_NAMES = {
    ".env", "id_ed25519", "id_rsa", "java-protection-mapping.txt", "mapping.txt",
    "license-signing-private.pem", "secrets.env",
}
CONFIG_SECRET_PATTERNS = (
    re.compile(
        rb"^(?:lingxi\.appSecret|doubao\.apiKey)\s*=\s*(?![$<{])[^\r\n ]+",
        re.I | re.MULTILINE,
    ),
    re.compile(
        rb"^(?:ROBOT_SSH_PASSWORD|MINIPROGRAM_SECRET|DOUBAO_API_KEY)\s*=\s*(?![$<{])[^\r\n ]+",
        re.I | re.MULTILINE,
    ),
)


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def audit_archive_modes(archive: Path, top: str, manifest: dict[str, object]) -> None:
    declared_modes = {
        str(entry["path"]): int(entry["mode"])
        for entry in manifest.get("files", [])
        if isinstance(entry, dict) and "path" in entry and "mode" in entry
    }
    require(declared_modes, "发布清单没有签名文件权限")
    with tarfile.open(archive, "r:gz") as handle:
        members = {member.name: member for member in handle.getmembers()}
    for relative, expected in declared_modes.items():
        require(expected & 0o022 == 0, f"发布清单包含组或其他用户可写文件：{relative}")
        member_name = f"{top}/rootfs/{relative}"
        member = members.get(member_name)
        require(member is not None and member.isfile(), f"归档文件条目缺失：{relative}")
        require(stat.S_IMODE(member.mode) == expected, f"归档权限与签名清单不一致：{relative}")


def audit_sources_and_secrets(release: Path, version_root: Path) -> tuple[int, int]:
    files = [path for path in release.rglob("*") if path.is_file()]
    for path in files:
        relative = path.relative_to(release).as_posix()
        suffix = path.suffix.lower()
        if suffix in FORBIDDEN_SUFFIXES:
            raise RuntimeError(f"发布包包含禁止源码：{relative}")
        if suffix == ".py":
            allowed = (
                relative.endswith("/navigation/robot_local_navigation_v3/__init__.py")
                or "/system/usr/lib/sparkrobot-license/" in relative
            )
            require(allowed, f"发布包包含业务 Python 源码：{relative}")
        require(path.name.lower() not in FORBIDDEN_NAMES, f"发布包包含禁止文件：{relative}")
        require(path.suffix.lower() != ".map", f"发布包包含源码映射：{relative}")
        if path.stat().st_size > 32 * 1024 * 1024:
            continue
        data = path.read_bytes()
        for pattern in (*SECRET_PATTERNS, *CONFIG_SECRET_PATTERNS):
            require(pattern.search(data) is None, f"发布包疑似包含密钥、口令或令牌：{relative}")

    navigation = version_root / "navigation" / "robot_local_navigation_v3"
    native_modules = sorted(navigation.glob("*.so"))
    require(len(native_modules) == 19, f"原生导航模块数量异常：{len(native_modules)}")
    for module in native_modules:
        require("cpython-310" in module.name, f"原生模块不是 CPython 3.10 ABI：{module.name}")
        require(elf_machine(module) == 183, f"原生模块不是 ARM64：{module.name}")
    return len(files), len(native_modules)


def audit_runtimes(version_root: Path) -> dict[str, object]:
    node = version_root / "runtime" / "node"
    java = version_root / "runtime" / "java" / "bin" / "java"
    java_release = version_root / "runtime" / "java" / "release"
    require(elf_machine(node) == 183, "Node 运行时不是 ARM64")
    require(elf_machine(java) == 183, "Java 运行时不是 ARM64")
    release_text = java_release.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'^JAVA_VERSION="([^"]+)"', release_text, re.MULTILINE)
    require(match is not None and match.group(1).startswith("17"), "Java 运行时不是 JRE 17")

    app_jar = version_root / "java" / "robot-local-navigation-java-all.jar"
    sdk_jar = version_root / "java" / "linksoul-agentsdk-1.4.0.jar"
    require(app_jar.is_file() and sdk_jar.is_file(), "Java 应用或 AgentSDK 缺失")
    with zipfile.ZipFile(app_jar) as archive:
        marker = json.loads(archive.read("META-INF/sparkrobot-protection.json").decode("utf-8"))
        require(marker.get("obfuscated") is True, "Java 应用没有混淆保护标记")
        class_data = archive.read("com/agibot/aiem/nav/LingxiNavAgentApp.class")
    class_major = int.from_bytes(class_data[6:8], "big")
    require(class_major == 61, f"Java 主类版本异常：major={class_major}")
    return {
        "node_architecture": "aarch64",
        "java_architecture": "aarch64",
        "java_version": match.group(1),
        "java_class_major": class_major,
        "java_obfuscated": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="独立审计灵犀导航正式发布归档")
    parser.add_argument("--archive", required=True)
    parser.add_argument("--report")
    parser.add_argument("--openssl")
    args = parser.parse_args()
    archive = Path(args.archive).resolve()
    require(archive.is_file(), "发布归档不存在")

    temp_parent = Path(archive.anchor) if os.name == "nt" and archive.anchor else None
    with tempfile.TemporaryDirectory(prefix=".sparkrobot-audit-", dir=temp_parent) as temp_name:
        release = safe_extract(archive, Path(temp_name) / "release")
        manifest = verify_release(release, resolve_openssl(args.openssl))
        version = str(manifest["version"])
        version_root = release / "rootfs" / "opt" / "sparkrobot" / "lingxi-nav" / "releases" / version
        require(version_root.is_dir(), "发布版本根目录缺失")
        audit_archive_modes(archive, release.name, manifest)
        file_count, native_count = audit_sources_and_secrets(release, version_root)
        runtime = audit_runtimes(version_root)
        report = {
            "format": "sparkrobot-release-audit-v1",
            "audited_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "archive": archive.name,
            "archive_sha256": sha256(archive),
            "product_id": manifest.get("product_id"),
            "version": version,
            "license_id": manifest.get("license_id"),
            "device_fingerprint": manifest.get("device_fingerprint"),
            "manifest_files": len(manifest.get("files", [])),
            "archive_files": file_count,
            "native_module_count": native_count,
            "source_scan": "passed",
            "secret_scan": "passed",
            "signature_and_hashes": "passed",
            "archive_modes": "passed",
            **runtime,
        }
        if args.report:
            report_path = Path(args.report).resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError, zipfile.BadZipFile, tarfile.TarError) as exc:
        print(f"发布归档审计失败：{exc}", file=os.sys.stderr)
        raise SystemExit(1)
