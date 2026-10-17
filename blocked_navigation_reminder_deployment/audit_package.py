from __future__ import annotations

import hashlib
import json
import sys
import tarfile
from pathlib import Path


def main() -> int:
    archive = Path(sys.argv[1])
    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getnames()
        tops = sorted({name.split("/", 1)[0] for name in members if "/" in name})
        if len(tops) != 1:
            raise SystemExit(f"顶层目录无效：{tops}")
        top = tops[0]
        manifest = json.load(handle.extractfile(f"{top}/manifest.json"))
        for item in manifest["files"]:
            data = handle.extractfile(f"{top}/{item['path']}").read()
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise SystemExit(f"清单校验失败：{item['path']}")
    print(f"通过：{archive.name}, SHA-256={hashlib.sha256(archive.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
