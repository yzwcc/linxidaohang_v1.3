#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import tarfile
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    archive = Path(sys.argv[1]) if len(sys.argv) > 1 else next(Path(__file__).parent.joinpath("dist").glob("*.tar.gz"))
    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getnames()
        top = sorted({name.split("/", 1)[0] for name in members if "/" in name})
        if len(top) != 1:
            raise SystemExit(f"invalid top-level directory: {top}")
        manifest_name = f"{top[0]}/manifest.json"
        manifest = json.load(handle.extractfile(manifest_name))
        for item in manifest["files"]:
            member = f"{top[0]}/{item['path']}"
            data = handle.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise SystemExit(f"checksum mismatch: {member}")
    print(f"通过：{archive.name}, SHA-256={digest(archive)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
