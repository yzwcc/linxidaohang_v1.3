#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


HERE = Path(__file__).resolve().parent
VERSION = "1.0.4-20261023"
NAME = f"sparkrobot-lingxi-consecutive-nav-fix-{VERSION}"
DIST = HERE / "dist"
REQUIRED = {
    "install.sh",
    "control.sh",
    "README.md",
    "release-info.txt",
    "manifest.json",
    "docs/连续导航卡住修复包部署说明.md",
    "payload/lingxi_nav_bridge.py",
    "payload/motion_option_policy.py",
    "payload/sdk_motion_controller.py",
    "payload/navigation_client.py",
    "payload/pnc_response.py",
    "payload/voice_preset_motion.py",
    "payload/point_catalog.py",
    "payload/tts_stream_protocol.py",
}
EXECUTABLES = {"install.sh", "control.sh"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != NAME:
        raise RuntimeError(f"归档包含不安全路径：{name}")
    return PurePosixPath(*path.parts[1:]).as_posix()


def verify_contents(contents: dict[str, bytes], modes: dict[str, int], label: str) -> None:
    if set(contents) != REQUIRED:
        raise RuntimeError(
            f"{label} 文件清单不一致；缺失={sorted(REQUIRED-set(contents))}，额外={sorted(set(contents)-REQUIRED)}"
        )
    manifest = json.loads(contents["manifest.json"].decode("utf-8"))
    if manifest.get("version") != VERSION or manifest.get("payload_count") != 8:
        raise RuntimeError(f"{label} manifest 版本或 payload 数量错误")
    listed = {item["path"]: item for item in manifest.get("files", [])}
    if set(listed) != REQUIRED - {"manifest.json"}:
        raise RuntimeError(f"{label} manifest 文件项不完整")
    for name, item in listed.items():
        if sha256(contents[name]) != item["sha256"] or len(contents[name]) != item["size"]:
            raise RuntimeError(f"{label} manifest 校验失败：{name}")
    for name, data in contents.items():
        if b"\r" in data:
            raise RuntimeError(f"{label} 含 CR/CRLF，可能在 Linux 解析失败：{name}")
        expected = 0o755 if name in EXECUTABLES else 0o644
        if stat.S_IMODE(modes[name]) != expected:
            raise RuntimeError(
                f"{label} 权限错误：{name}={oct(stat.S_IMODE(modes[name]))}，应为={oct(expected)}"
            )

    bridge = contents["payload/lingxi_nav_bridge.py"].decode("utf-8")
    policy = contents["payload/motion_option_policy.py"].decode("utf-8")
    client = contents["payload/navigation_client.py"].decode("utf-8")
    voice_motion = contents["payload/voice_preset_motion.py"].decode("utf-8")
    point_catalog = contents["payload/point_catalog.py"].decode("utf-8")
    tts_stream_protocol = contents["payload/tts_stream_protocol.py"].decode("utf-8")
    control = contents["control.sh"].decode("utf-8")
    installer = contents["install.sh"].decode("utf-8")
    for forbidden in (
        '_stop_arrival_motion("task_arrival_done")',
        '_stop_arrival_motion("arrival_action_done")',
        '_stop_arrival_motion("before_next_task_step")',
    ):
        if forbidden in bridge:
            raise RuntimeError(f"{label} 仍包含危险普通到点中断：{forbidden}")
    for marker in (
        "_finish_arrival_motion_after_wait",
        "Tour task motion release wait started",
        "Arrival empty motion ignored",
        'self.path == "/motion/play"',
    ):
        if marker not in bridge:
            raise RuntimeError(f"{label} 缺少连续导航到点动作保护：{marker}")
    if "CONTINUOUS_MOTION_IDS = frozenset({6001})" not in policy:
        raise RuntimeError(f"{label} 缺少 6001 持续动作策略")
    if "PNC_RESPONSE_MAX_RETRIES = 15" not in client:
        raise RuntimeError(f"{label} 缺少 PNC 有界重试策略")
    for marker in (
        "def resolve_voice_command",
        "has_negation_or_quoted_speech",
        "def submit_voice_preset_motion",
    ):
        if marker not in voice_motion:
            raise RuntimeError(f"{label} 缺少语音预置动作拒识策略：{marker}")
    for marker in ("def list_ambiguous_voice_names", "def _normalize_voice_name"):
        if marker not in point_catalog:
            raise RuntimeError(f"{label} 缺少歧义语音点位名解析：{marker}")
    for marker in ("class TtsStreamProtocol", "def claim(", "def close("):
        if marker not in tts_stream_protocol:
            raise RuntimeError(f"{label} 缺少流式 TTS 序号协议：{marker}")
    if '"ambiguous_point_names"' not in bridge:
        raise RuntimeError(f"{label} 没有向 Java Agent 提供活动地图歧义别名")
    if "def _fail_async" not in client or "def _fail(" not in client:
        raise RuntimeError(f"{label} PNC 失败回调缺少异步失败处理方法")
    if "is_bridge_process" not in control or "绝不会停止该进程" not in control:
        raise RuntimeError(f"{label} 缺少陈旧 PID 身份保护")
    for marker in (
        'RUNTIME_GROUP="${SPARKROBOT_RUNTIME_GROUP:-sparkrobot-nav}"',
        "resolve_bridge_run_user",
        "repair_runtime_permissions",
        'install -d -o "${run_user}" -g "${runtime_group}" -m 0770',
        '拒绝以 root 运行导航 bridge',
    ):
        if marker not in control:
            raise RuntimeError(f"{label} 缺少运行目录权限修复：{marker}")
    if "repair_runtime_permissions" not in installer:
        raise RuntimeError(f"{label} 安装器没有修复运行目录权限")
    if 'mktemp -d "${BACKUP_ROOT}/${TIMESTAMP}.XXXXXX"' not in installer:
        raise RuntimeError(f"{label} 备份目录没有使用唯一名称")
    forbidden_secret_markers = (b"BEGIN PRIVATE KEY", b"APPSECRET=", b"ARK_API_KEY=")
    for name, data in contents.items():
        for marker in forbidden_secret_markers:
            if marker in data.upper():
                raise RuntimeError(f"{label} 疑似包含密钥：{name}")

    with tempfile.TemporaryDirectory(prefix="lingxi-consecutive-audit-") as temp:
        root = Path(temp)
        for name in sorted(item for item in REQUIRED if item.startswith("payload/")):
            source = root / PurePosixPath(name).name
            source.write_bytes(contents[name])
            output = root / f"{source.stem}.pyc"
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import py_compile,sys; py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)",
                    str(source),
                    str(output),
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode:
                raise RuntimeError(f"{label} Python 编译失败：{name}\n{result.stderr}")


def run_shell_and_install_smoke(contents: dict[str, bytes], modes: dict[str, int]) -> None:
    with tempfile.TemporaryDirectory(prefix="lingxi-consecutive-install-") as temp:
        root = Path(temp)
        package = root / NAME
        fixture = root / "fixture"
        nav_dir = fixture / "nav"
        package.mkdir()
        nav_dir.mkdir(parents=True)

        for name, data in contents.items():
            path = package / PurePosixPath(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            os.chmod(path, stat.S_IMODE(modes[name]))

        for script in ("install.sh", "control.sh"):
            result = subprocess.run(
                ["bash", "-n", script],
                cwd=package,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode:
                raise RuntimeError(f"{script} Bash 语法失败：\n{result.stderr}")

        original: dict[str, bytes] = {}
        for name in (
            "lingxi_nav_bridge.py",
            "motion_option_policy.py",
            "sdk_motion_controller.py",
            "navigation_client.py",
            "point_catalog.py",
        ):
            data = contents[f"payload/{name}"] + b"\n# audit-old-fixture\n"
            original[name] = data
            (nav_dir / name).write_bytes(data)
            os.chmod(nav_dir / name, 0o640)

        shell_environment = (
            "export SPARKROBOT_FIX_TEST_MODE=true "
            "NAV_DIR=../fixture/nav "
            "FIX_STATE_ROOT=../fixture/state "
            "BACKUP_ROOT=../fixture/backups "
            "SPARKROBOT_STATE_ROOT=../fixture/runtime "
            "SPARKROBOT_LOG_ROOT=../fixture/logs;"
        )
        result = subprocess.run(
            ["bash", "-c", f"{shell_environment} bash ./install.sh --no-restart"],
            cwd=package,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode:
            raise RuntimeError(
                "安装器沙箱冒烟测试失败：\n"
                + (result.stdout or "")
                + (result.stderr or "")
            )

        for name in (
            "lingxi_nav_bridge.py",
            "motion_option_policy.py",
            "sdk_motion_controller.py",
            "navigation_client.py",
            "pnc_response.py",
            "voice_preset_motion.py",
            "point_catalog.py",
            "tts_stream_protocol.py",
        ):
            if (nav_dir / name).read_bytes() != contents[f"payload/{name}"]:
                raise RuntimeError(f"安装器没有正确替换 {name}")
        if not (fixture / "state" / "installed-manifest.json").is_file():
            raise RuntimeError("安装器没有写入已安装清单")
        # Windows/NTFS does not expose the chmod result with Linux semantics.
        # The exact 0770 install invocation is asserted above; this smoke test
        # verifies that the installer actually executes the repair path.
        for directory in (fixture / "runtime" / "pids", fixture / "logs", fixture / "logs" / "ros"):
            if not directory.is_dir():
                raise RuntimeError(f"安装器没有创建运行目录：{directory}")

        backups = sorted((fixture / "backups").iterdir())
        if len(backups) != 1:
            raise RuntimeError(f"安装器备份数量错误：{len(backups)}")
        backup = backups[0]
        if (backup / "existed-pnc_response.py").read_text(encoding="utf-8").strip() != "false":
            raise RuntimeError("安装器没有记录新增 pnc_response.py")

        backup_argument = f"../fixture/backups/{backup.name}"
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"{shell_environment} "
                'source ./control.sh; '
                f'restore_backup_files "{backup_argument}"; '
                f'restore_install_state_from_backup "{backup_argument}"',
            ],
            cwd=package,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode:
            raise RuntimeError(
                "备份恢复沙箱冒烟测试失败：\n"
                + (result.stdout or "")
                + (result.stderr or "")
            )
        for name, data in original.items():
            if (nav_dir / name).read_bytes() != data:
                raise RuntimeError(f"备份恢复没有还原 {name}")
        if (nav_dir / "pnc_response.py").exists():
            raise RuntimeError("备份恢复没有删除原本不存在的 pnc_response.py")
        if (nav_dir / "voice_preset_motion.py").exists():
            raise RuntimeError("备份恢复没有删除原本不存在的 voice_preset_motion.py")
        if (nav_dir / "tts_stream_protocol.py").exists():
            raise RuntimeError("备份恢复没有删除原本不存在的 tts_stream_protocol.py")
        if (fixture / "state" / "installed-manifest.json").exists():
            raise RuntimeError("备份恢复没有还原安装状态")


def read_tar(path: Path) -> tuple[dict[str, bytes], dict[str, int]]:
    contents: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                raise RuntimeError(f"tar.gz 不允许符号链接：{member.name}")
            if not member.isfile():
                continue
            relative = relative_name(member.name)
            handle = archive.extractfile(member)
            if handle is None:
                raise RuntimeError(f"tar.gz 无法读取：{member.name}")
            contents[relative] = handle.read()
            modes[relative] = member.mode
    return contents, modes


def read_zip(path: Path) -> tuple[dict[str, bytes], dict[str, int]]:
    contents: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            relative = relative_name(info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise RuntimeError(f"zip 不允许符号链接：{info.filename}")
            contents[relative] = archive.read(info)
            modes[relative] = mode
    return contents, modes


def main() -> int:
    tar_path = DIST / f"{NAME}.tar.gz"
    zip_path = DIST / f"{NAME}.zip"
    for path in (tar_path, zip_path):
        if not path.is_file():
            raise RuntimeError(f"缺少归档：{path}")
        checksum_path = path.with_suffix(path.suffix + ".sha256")
        line = checksum_path.read_text(encoding="ascii").strip()
        expected_hash, expected_name = line.split(None, 1)
        if expected_name.strip() != path.name or sha256(path.read_bytes()) != expected_hash:
            raise RuntimeError(f"外部 SHA-256 文件错误：{checksum_path}")

    tar_contents, tar_modes = read_tar(tar_path)
    zip_contents, zip_modes = read_zip(zip_path)
    verify_contents(tar_contents, tar_modes, "tar.gz")
    verify_contents(zip_contents, zip_modes, "zip")
    if tar_contents != zip_contents:
        raise RuntimeError("tar.gz 与 zip 文件内容不一致")
    run_shell_and_install_smoke(tar_contents, tar_modes)
    print(
        "增量包审计通过：双格式内容一致、路径安全、LF、权限、manifest、"
        "SHA-256、Python/Bash 语法、安装和备份恢复均正常"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
