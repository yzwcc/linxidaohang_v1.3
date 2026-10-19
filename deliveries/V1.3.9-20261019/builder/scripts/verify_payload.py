#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    payload = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    failures = []
    for entry in payload.get("files", []):
        relative = PurePosixPath(str(entry["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            failures.append(f"非法路径: {relative}")
            continue
        path = root.joinpath(*relative.parts)
        if not path.is_file():
            failures.append(f"缺少: {relative}")
        elif path.stat().st_size != int(entry["size"]) or digest(path) != entry["sha256"]:
            failures.append(f"被修改: {relative}")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"已校验 {len(payload.get('files', []))} 个运行文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
