#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath


def verify_signature(manifest: Path, signature: Path, public_key: Path) -> None:
    result = subprocess.run(
        [os.environ.get("OPENSSL_BIN", "openssl"), "dgst", "-sha256", "-verify", str(public_key), "-signature", str(signature), str(manifest)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or "Verified OK" not in result.stdout:
        raise RuntimeError("发布清单签名无效")


def verify_manifest(
    root: Path,
    manifest: Path,
    signature: Path,
    public_key: Path,
    *,
    apply_modes: bool = False,
    check_modes: bool = False,
) -> dict[str, object]:
    verify_signature(manifest, signature, public_key)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("format") != "sparkrobot-release-manifest-v1":
        raise RuntimeError("发布清单格式不受支持")
    entries = data.get("files")
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("发布清单没有文件记录")
    declared: set[str] = set()
    modes: list[tuple[Path, int, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("发布清单文件记录无效")
        relative = str(entry.get("path") or "")
        posix_path = PurePosixPath(relative)
        if not relative or posix_path.is_absolute() or ".." in posix_path.parts:
            raise RuntimeError(f"发布清单包含不安全路径：{relative}")
        if relative in declared:
            raise RuntimeError(f"发布清单包含重复路径：{relative}")
        declared.add(relative)
        target = root.joinpath(*posix_path.parts)
        if not target.is_file() or target.is_symlink():
            raise RuntimeError(f"发布文件缺失或类型错误：{relative}")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != entry.get("sha256"):
            raise RuntimeError(f"发布文件校验失败：{relative}")
        if target.stat().st_size != int(entry.get("size", -1)):
            raise RuntimeError(f"发布文件大小不匹配：{relative}")
        if "mode" in entry:
            expected_mode = int(entry["mode"])
            if expected_mode < 0 or expected_mode > 0o777 or expected_mode & 0o022:
                raise RuntimeError(f"发布文件权限不安全：{relative}")
            modes.append((target, expected_mode, relative))

    strict_roots = data.get("strict_roots", [])
    if not isinstance(strict_roots, list):
        raise RuntimeError("发布清单 strict_roots 无效")
    for relative_root in strict_roots:
        posix_root = PurePosixPath(str(relative_root))
        if posix_root.is_absolute() or ".." in posix_root.parts:
            raise RuntimeError(f"发布清单包含不安全保护目录：{relative_root}")
        protected = root.joinpath(*posix_root.parts)
        if not protected.is_dir() or protected.is_symlink():
            raise RuntimeError(f"发布保护目录缺失或类型错误：{relative_root}")
        for target in protected.rglob("*"):
            if target.is_symlink():
                raise RuntimeError(f"发布保护目录包含符号链接：{target}")
            if target.is_file():
                relative = target.relative_to(root).as_posix()
                if relative not in declared:
                    raise RuntimeError(f"发布保护目录包含未签名文件：{relative}")
    if apply_modes:
        for target, expected_mode, _ in modes:
            os.chmod(target, expected_mode)
    if check_modes or apply_modes:
        for target, expected_mode, relative in modes:
            actual_mode = stat.S_IMODE(target.stat().st_mode)
            if actual_mode != expected_mode:
                raise RuntimeError(
                    f"发布文件权限不匹配：{relative}，期望 {expected_mode:04o}，实际 {actual_mode:04o}"
                )
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="验证灵犀导航发布制品")
    parser.add_argument("--root", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--public-key", required=True)
    parser.add_argument("--apply-modes", action="store_true", help="验签和哈希通过后恢复清单中的文件权限")
    parser.add_argument("--check-modes", action="store_true", help="校验清单中的文件权限")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    try:
        data = verify_manifest(
            Path(args.root).resolve(),
            Path(args.manifest).resolve(),
            Path(args.signature).resolve(),
            Path(args.public_key).resolve(),
            apply_modes=args.apply_modes,
            check_modes=args.check_modes,
        )
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"发布制品校验失败：{exc}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"发布制品有效：version={data.get('version')}，files={len(data.get('files', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
