#!/usr/bin/env python3
"""Read current-process connection evidence without interpreting historical logs."""
import argparse
import json
from pathlib import Path


def connection_fields(file: Path, pid: int, running: bool) -> dict[str, str]:
    fields = {"lingxi_connected": "false", "robot_online": "false",
              "last_auth_state": "等待当前进程认证状态"}
    if not running or pid <= 0:
        return fields
    try:
        state = json.loads(file.read_text(encoding="utf-8"))
        if type(state.get("pid")) is not int or state["pid"] != pid:
            return fields
        code = state.get("auth_code")
        if type(code) is int:
            fields["last_auth_state"] = f"code={code}"
            fields["lingxi_connected"] = "true" if code == 0 else "false"
        fields["robot_online"] = "true" if type(code) is int and code == 0 and state.get("robot_online") is True else "false"
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return fields


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--running", choices=("true", "false"), required=True)
    args = parser.parse_args()
    for key, value in connection_fields(args.file, args.pid, args.running == "true").items():
        print(f"{key}={value}")
