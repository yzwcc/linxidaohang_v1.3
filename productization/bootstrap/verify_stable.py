#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath


def verify_stable(root: Path, file_list: Path) -> int:
    data = json.loads(file_list.read_text(encoding="utf-8"))
    entries = data.get("files")
    if data.get("format") != "sparkrobot-stable-files-v1" or not isinstance(entries, list):
        raise RuntimeError("稳定文件清单格式无效")
    checked = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("稳定文件记录无效")
        relative = str(entry.get("path") or "")
        posix_path = PurePosixPath(relative)
        if not relative or posix_path.is_absolute() or ".." in posix_path.parts:
            raise RuntimeError(f"稳定文件路径不安全：{relative}")
        target = root.joinpath(*posix_path.parts)
        if not target.is_file() or target.is_symlink():
            raise RuntimeError(f"稳定文件缺失或类型错误：{relative}")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != entry.get("sha256"):
            raise RuntimeError(f"稳定文件已被修改：{relative}")
        checked += 1
    return checked


def main() -> int:
    parser = argparse.ArgumentParser(description="验证灵犀导航稳定系统文件")
    parser.add_argument("--root", default="/")
    parser.add_argument("--list", required=True)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    try:
        checked = verify_stable(Path(args.root).resolve(), Path(args.list).resolve())
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"稳定文件校验失败：{exc}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"稳定文件有效：files={checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
