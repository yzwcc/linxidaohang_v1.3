#!/usr/bin/env python3
"""Apply and verify the fixed, versioned A2 PNC YAML profile."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any


KEY_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<key>[A-Za-z_][A-Za-z0-9_-]*):(?P<rest>.*)$"
)


def load_policy(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise ValueError("policy must contain a files object")
    return data


def _numeric(value: str) -> float | None:
    try:
        return float(value.strip())
    except ValueError:
        return None


def _same_value(actual: str, expected: str) -> bool:
    left = _numeric(actual)
    right = _numeric(expected)
    if left is not None and right is not None:
        return abs(left - right) < 1e-9
    return actual.strip() == expected.strip()


def _walk_scalar_lines(lines: list[str]):
    stack: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        content = line.rstrip("\r\n")
        match = KEY_RE.match(content)
        if not match or content.lstrip().startswith("#"):
            continue
        indent = len(match.group("indent").replace("\t", "    "))
        while stack and indent <= stack[-1][0]:
            stack.pop()
        key = match.group("key")
        path = tuple(item[1] for item in stack) + (key,)
        yield index, match, path
        if not match.group("rest").split("#", 1)[0].strip():
            stack.append((indent, key))


def _updates_by_path(updates: list[dict[str, Any]]) -> dict[tuple[str, ...], str]:
    result: dict[tuple[str, ...], str] = {}
    for item in updates:
        path = tuple(str(part) for part in item.get("path", []))
        value = str(item.get("value", "")).strip()
        if not path or not value:
            raise ValueError("every policy update needs a non-empty path and value")
        if path in result:
            raise ValueError(f"duplicate policy path: {'.'.join(path)}")
        result[path] = value
    return result


def apply_file(path: Path, updates: list[dict[str, Any]], *, verify: bool = False) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    targets = _updates_by_path(updates)
    found: dict[str, str] = {}
    replaced = 0
    for index, match, full_path in _walk_scalar_lines(lines):
        if full_path not in targets:
            continue
        name = ".".join(full_path)
        if name in found:
            raise ValueError(f"ambiguous YAML path: {path}:{name}")
        rest = match.group("rest")
        comment = ""
        if "#" in rest:
            comment = "  " + rest[rest.index("#") :].rstrip()
        actual = rest.split("#", 1)[0].strip()
        found[name] = actual
        expected = targets[full_path]
        if not verify:
            newline = "\r\n" if lines[index].endswith("\r\n") else "\n" if lines[index].endswith("\n") else ""
            lines[index] = f"{match.group('indent')}{match.group('key')}: {expected}{comment}{newline}"
            replaced += 1
    missing = [
        ".".join(path_value)
        for path_value in targets
        if ".".join(path_value) not in found
    ]
    if missing:
        raise ValueError(f"missing YAML paths in {path}: {', '.join(missing)}")
    if verify:
        mismatched = [
            f"{name}={found[name]} (expected {targets[tuple(name.split('.'))]})"
            for name in found
            if not _same_value(found[name], targets[tuple(name.split('.'))])
        ]
        if mismatched:
            raise ValueError(f"policy mismatch in {path}: {'; '.join(mismatched)}")
        return found
    if replaced != len(targets):
        raise ValueError(f"updated {replaced} of {len(targets)} paths in {path}")
    original_stat = path.stat()
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text("".join(lines), encoding="utf-8", newline="")
        os.chmod(temporary, stat.S_IMODE(original_stat.st_mode))
        chown = getattr(os, "chown", None)
        if chown is not None:
            try:
                chown(temporary, original_stat.st_uid, original_stat.st_gid)
            except PermissionError:
                pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return found


def apply_policy(policy_path: Path, app2: Path, mppi: Path, *, verify: bool) -> dict[str, dict[str, str]]:
    policy = load_policy(policy_path)
    files = policy["files"]
    return {
        "app2.yaml": apply_file(app2, files["app2.yaml"], verify=verify),
        "mppi_controller.yaml": apply_file(mppi, files["mppi_controller.yaml"], verify=verify),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--app2", type=Path, required=True)
    parser.add_argument("--mppi", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if sum(bool(value) for value in (args.apply, args.verify, args.status)) != 1:
        parser.error("choose exactly one of --apply, --verify, --status")
    result = apply_policy(args.policy, args.app2, args.mppi, verify=args.verify or args.status)
    label = "changed" if args.apply else "verified" if args.verify else "status"
    print(json.dumps({"ok": True, label: result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
