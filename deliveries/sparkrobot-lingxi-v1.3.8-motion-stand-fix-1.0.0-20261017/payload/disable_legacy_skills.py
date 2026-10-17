#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: disable_legacy_skills.py SKILLS_JSON", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)

    changed = 0

    def visit(value: object) -> None:
        nonlocal changed
        if isinstance(value, dict):
            if (
                value.get("value") == "exhibit_commentary"
                or value.get("skill_name") == "start_fixed_line_intro"
                or value.get("skillName") == "start_fixed_line_intro"
            ) and value.get("enabled") is not False:
                value["enabled"] = False
                changed += 1
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(data)
    if changed:
        fd, temporary = tempfile.mkstemp(
            prefix="skills.json.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    print(f"技能重复映射处理：{path} changed={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
