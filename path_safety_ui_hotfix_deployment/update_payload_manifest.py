from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath


CHANGED_PATHS = {
    "console/dist/app.js",
    "console/dist/robotClient.js",
    "console/dist/public/app.js",
    "console/dist/public/index.html",
    "console/dist/public/styles.css",
}
PRESERVED_RUNTIME_PATHS = {
    "java/robot-local-navigation-java-all.jar",
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def update(root: Path, manifest: Path) -> None:
    root = root.resolve()
    manifest = manifest.resolve()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    entries = {str(item.get("path", "")): item for item in payload.get("files", [])}
    for relative in CHANGED_PATHS:
        if relative not in entries:
            payload.setdefault("files", []).append({"path": relative})
            entries[relative] = payload["files"][-1]
    missing_preserved = PRESERVED_RUNTIME_PATHS - set(entries)
    if missing_preserved:
        raise RuntimeError(f"preserved runtime files missing from payload manifest: {sorted(missing_preserved)}")

    updated: set[str] = set()
    for entry in payload.get("files", []):
        relative = PurePosixPath(str(entry.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"invalid payload manifest path: {relative}")
        name = relative.as_posix()
        if name not in CHANGED_PATHS | PRESERVED_RUNTIME_PATHS:
            continue
        target = root.joinpath(*relative.parts)
        if not target.is_file():
            raise RuntimeError(f"payload manifest target is missing: {name}")
        entry["sha256"] = digest(target)
        entry["size"] = target.stat().st_size
        updated.add(name)
    expected = CHANGED_PATHS | PRESERVED_RUNTIME_PATHS
    if updated != expected:
        raise RuntimeError(f"manifest update set mismatch: {sorted(updated)}")

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{manifest.name}.", dir=manifest.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, manifest.stat().st_mode & 0o777)
        os.replace(temporary_name, manifest)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    arguments = parser.parse_args()
    update(arguments.root, arguments.manifest)
