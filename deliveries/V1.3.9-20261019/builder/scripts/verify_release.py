#!/usr/bin/env python3
"""Read-only runtime/configuration provenance checks; never send robot commands."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import release_config

CURRENT = Path("/opt/sparkrobot/lingxi-nav/current")
VERSION = "1.3.9-complete-20261019-r4"
NAV_VERSION = "1.0.22-20261019"
PROFILE = {
    "enabled": True, "target_pose_radius": 0.45, "position_tolerance_m": 0.45,
    "yaw_tolerance_rad": 0.5235987755982988, "stable_duration_sec": 1.0,
    "require_low_speed": True, "low_speed_threshold_mps": 0.08,
    "max_forward_speed": 0.30, "timeout_sec": 150.0,
}
FAILURE_POLICY = {
    "continue_after_failure": True, "max_retries_per_step": 1,
    "retry_delay_sec": 1.0, "announce_skip": False,
}


def matches(actual, expected):
    if isinstance(expected, bool):
        return actual is expected
    return not isinstance(actual, bool) and actual == expected


def verify_config(info, state, data):
    if info.get("version") != NAV_VERSION or state.get("navigation_version") != NAV_VERSION or state.get("version") != VERSION:
        raise ValueError("运行版本与完整安装记录不同；请按完整备份恢复配置")
    if not isinstance(state.get("pnc_policy_skipped"), bool):
        raise ValueError("窄通道策略安装记录无效")
    navigation = data.get("navigation", {})
    for name, expected in (("dense_crowd", PROFILE), ("task_failure_policy", FAILURE_POLICY)):
        actual = navigation.get(name, {})
        if not isinstance(actual, dict) or any(not matches(actual.get(k), v) for k, v in expected.items()):
            raise ValueError("导航配置与本发布不一致：" + name)


def verify_java_args(args, current=CURRENT):
    expected = [
        (current / "runtime/java/bin/java").as_posix(), "-cp",
        (current / "java/robot-local-navigation-java-all.jar").as_posix() + ":" +
        (current / "java/linksoul-agentsdk-1.4.0.jar").as_posix(),
        "com.agibot.aiem.nav.LingxiNavAgentApp",
        "--config", "/etc/sparkrobot/lingxi-nav/lingxi-nav.properties",
    ]
    if args != expected:
        raise ValueError("Java 启动参数未精确指向本发布的 JRE、classpath、主类和配置")


def parse_execution(execution):
    if execution.count("argv[]=") != 1:
        raise ValueError("Java systemd ExecStart 格式无效")
    return shlex.split(execution.split("argv[]=", 1)[1].split(" ;", 1)[0])


def main():
    info = json.loads((CURRENT / "release-info.json").read_text(encoding="utf-8"))
    state = json.loads(Path("/var/lib/sparkrobot/lingxi-complete/installed.json").read_text(encoding="utf-8"))
    data = json.loads(Path("/var/lib/sparkrobot/lingxi-nav/unified_nav_config.json").read_text(encoding="utf-8"))
    verify_config(info, state, data)
    jar = CURRENT / "java/robot-local-navigation-java-all.jar"
    if hashlib.sha256(jar.read_bytes()).hexdigest() != info["java_sha256"]:
        raise ValueError("Java JAR 与本发布构建记录不同")
    print("navigation_version=" + info["version"])
    print(json.dumps(release_config.run("/", "verify", state["pnc_policy_skipped"]), ensure_ascii=False))
    execution = subprocess.check_output(
        ["systemctl", "show", "-p", "ExecStart", "--value", "lingxi-java-agent-robot.service"], text=True)
    verify_java_args(parse_execution(execution))
    pid = subprocess.check_output(
        ["systemctl", "show", "-p", "MainPID", "--value", "lingxi-java-agent-robot.service"], text=True).strip()
    if not pid.isdigit() or int(pid) <= 0:
        raise ValueError("Java Agent 未启动；先在网页开启服务，再执行 doctor")
    args = Path(f"/proc/{pid}/cmdline").read_bytes().rstrip(b"\0").split(b"\0")
    verify_java_args([value.decode("utf-8") for value in args])
    if Path(f"/proc/{pid}/cwd").resolve() != (CURRENT / "java").resolve():
        raise ValueError("Java 进程仍属于升级前目录；请重启服务")
    print("java_process=current_release")
    print("merged_release=verified")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError) as error:
        sys.exit(str(error))
