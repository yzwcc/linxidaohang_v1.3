#!/usr/bin/env python3
"""Read-only runtime/configuration provenance checks; never send robot commands."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import release_config

CURRENT = Path("/opt/sparkrobot/lingxi-nav/current")

def main():
    info = json.loads((CURRENT / "release-info.json").read_text(encoding="utf-8"))
    state = json.loads(Path("/var/lib/sparkrobot/lingxi-complete/installed.json").read_text(encoding="utf-8"))
    if info["version"] != state["navigation_version"]:
        raise SystemExit("运行版本与完整安装记录不同；如果只回滚了运行程序，请按完整备份恢复配置")
    print("navigation_version=" + info["version"])
    data = json.loads(Path("/var/lib/sparkrobot/lingxi-nav/unified_nav_config.json").read_text(encoding="utf-8"))
    profile = data["navigation"]["dense_crowd"]
    expected = {"enabled": True, "target_pose_radius": 0.45, "position_tolerance_m": 0.45,
                "max_forward_speed": 0.30, "require_low_speed": True}
    if any(profile.get(k) != v for k,v in expected.items()):
        raise SystemExit("密集人流配置与本发布不一致")
    policy = data["navigation"]["task_failure_policy"]
    if policy.get("max_retries_per_step") != 1 or policy.get("continue_after_failure") is not True:
        raise SystemExit("讲解失败重试/跳点策略不一致")
    print(json.dumps(release_config.run("/", "verify", state["pnc_policy_skipped"]), ensure_ascii=False))
    execution = subprocess.check_output(
        ["systemctl", "show", "-p", "ExecStart", "--value", "lingxi-java-agent-robot.service"], text=True)
    jar = str(CURRENT / "java/robot-local-navigation-java-all.jar")
    if jar not in execution or "/overrides/" in execution:
        raise SystemExit("Java systemd ExecStart 仍指向旧覆盖包")
    pid = subprocess.check_output(
        ["systemctl", "show", "-p", "MainPID", "--value", "lingxi-java-agent-robot.service"], text=True).strip()
    if pid.isdigit() and int(pid) > 0:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        if not any(jar.encode() in value for value in args) or any(b"/overrides/" in value for value in args):
            raise SystemExit("Java 实际进程尚未加载完整包 JAR")
        print("java_process=current_release")
    else:
        raise SystemExit("Java Agent 未启动；先在网页开启服务，再执行 doctor")
    print("merged_release=verified")
    return 0

if __name__ == "__main__":
    sys.exit(main())
