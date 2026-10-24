#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


HERE = Path(__file__).resolve().parent
VERSION = "1.3.1-complete-20261024-r18"
NAME = f"lingxi-v{VERSION}-deployment"
DIST = HERE / "dist"
REQUIRED = {
    "README.md",
    "VERSION",
    "bundle-manifest.json",
    "SHA256SUMS.txt",
    "install_all.sh",
    "control_all.sh",
    "docs/灵犀完整部署教程-v1.3.1-complete.md",
    "docs/客户日常使用手册-v1.3.1.md",
    "docs/已有AIMDK部署说明-v1.3.1-r18.md",
    "components/lingxi-v1.3.1-full-deployment-20261024-r18.zip",
    "components/aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip",
    "components/sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18.tar.gz",
    "components/sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18.tar.gz.sha256",
    "components/sparkrobot-lingxi-kb-1.0.10-20261023.zip",
    "components/sparkrobot-lingxi-linkcraft-addon-1.0.3-20260902.tar.gz",
    "components/sparkrobot-lingxi-consecutive-nav-fix-1.0.8-20261024-r18.tar.gz",
}
LINUX_TEXT_FILES = {
    "SHA256SUMS.txt",
    "install_all.sh",
    "control_all.sh",
    "README.md",
    "docs/灵犀完整部署教程-v1.3.1-complete.md",
    "docs/客户日常使用手册-v1.3.1.md",
    "docs/已有AIMDK部署说明-v1.3.1-r18.md",
}
BASE_BUNDLE = "lingxi-v1.3.1-full-deployment-20261024-r18.zip"
BASE_ROOT = "lingxi-v1.3.1-full-deployment-20261024-r18"
AIMDK_NAME = "aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip"
AIMDK_SHA256 = "dc80fe5d2907881ed77beb230680867efac6bd28b458f83d2ff4e9479abe3615"
NAV_NAME = "sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18.tar.gz"
FIX_NAME = "sparkrobot-lingxi-consecutive-nav-fix-1.0.8-20261024-r18.tar.gz"
INSTALLER_REQUIRED_SNIPPETS = (
    "verify_aimdk_runtime",
    "sudo test -f /etc/sparkrobot/lingxi-nav/lingxi-nav.properties",
    'resolved="$(realpath -m -- "${WORK_ROOT}")"',
    '[[ "${resolved}" == /home/run/* ]]',
    'WORK_ROOT="${resolved}"',
    'BASE_ROOT="${WORK_ROOT}/base/lingxi-v1.3.1-full-deployment-20261024-r18"',
    'sudo rm -rf -- "${staging}"',
    'sudo bash "${LINKCRAFT_ROOT}/install.sh" --no-restart',
    'sudo bash "${FIX_ROOT}/install.sh"',
    'KB_NAME="sparkrobot-lingxi-kb-1.0.10-20261023.zip"',
    'KB_ROOT="${staging}/sparkrobot-lingxi-kb-1.0.10-20261023"',
    'FIX_NAME="sparkrobot-lingxi-consecutive-nav-fix-1.0.8-20261024-r18.tar.gz"',
    'FIX_ROOT="${staging}/sparkrobot-lingxi-consecutive-nav-fix-1.0.8-20261024-r18"',
    'test "$(cat /home/agi/aimdk/version)" = "aimdk v1.1.0-gfaf4c94"',
    "PncTaskRequest",
    "reference_line",
    "repair-permissions",
    "sudo systemctl restart lingxi-nav-console.service",
)
README_REQUIRED_SNIPPETS = (
    "v1.3.1-complete-20261024-r18",
    "aimdk v1.1.0-gfaf4c94",
    "BEST_EFFORT + VOLATILE",
    "BEST_EFFORT + TRANSIENT_LOCAL",
    "0.25 m",
    "sparkrobot-lingxi-kb-1.0.10-20261023",
    "普通常识、科学原理、语言、历史、观点和开放问题",
    "使用模型已有知识正常回答",
    "doubao.streamingReplies",
    "PlayTts",
    "stream ID",
    "owner token",
    "失去所有权",
    "call_async",
    "stream_action=end",
    "不具备撤回或停止能力",
    "已知点位导航快速路径",
    "原始音频仍需调用当前配置的转写/意图模型",
    "并行",
    "停止指令",
    "全局路径净空",
    "机器人实测外接半径",
    "额外障碍净空",
    "task_type=2、pnc_mode=0",
    "reference_line",
    "静态占用地图",
    "target_pose_radius",
    "`3000`",
    "`task_type`",
    "`8002`",
    "`18082`",
)
GUIDE_REQUIRED_SNIPPETS = (
    "v1.3.1-complete-20261024-r18",
    "aimdk v1.1.0-gfaf4c94",
    "BEST_EFFORT + VOLATILE",
    "BEST_EFFORT + TRANSIENT_LOCAL",
    "0.25 m",
    "sparkrobot-lingxi-kb-1.0.10-20261023",
    "### 10.5 已知点位导航快速路径",
    "r17 仍未实现端侧离线 ASR",
    "### 10.6 命令路由与并行检索",
    "### 10.7 可选流式回复",
    "### 10.8 流式会话所有权保护",
    "doubao.streamingReplies=false",
    "AIMDK PlayTts",
    "stream ID",
    "owner token",
    "call_async",
    "stream_action=end",
    "不具备撤回或停止能力",
    "该值会保留",
    "不要因为当前上下文或知识库没有提供常识答案而拒答",
    "### 13.4 R17 全局路径净空（R18 保留）",
    "所有地图、点位、任务和全局路径安全参数都只在机器人 `3000` 网页控制台配置",
    "无需打开 `8002` 或 `18082`",
    "所有地图、点位、任务和路径安全参数都只在 3000 网页控制台配置",
    "8002 和 18082 是内部服务端口，不是配置页面",
    "机器人实测外接半径",
    "额外障碍净空",
    "reference_line",
    "静态占用地图",
    "不会代替现场导航验收",
    "地图、点位、任务及全局路径安全参数都只在机器人 `3000` 网页控制台配置",
    "`8002` 和 `18082` 是内部服务端口",
)
AIMDK_GUIDE_REQUIRED_SNIPPETS = (
    "v1.3.1-complete-20261024-r18",
    "aimdk v1.1.0-gfaf4c94",
    "BEST_EFFORT + VOLATILE",
    "BEST_EFFORT + TRANSIENT_LOCAL",
    "AIMDK_R18_CONTRACT_OK",
    "保留现有 `/home/agi/aimdk`",
    "该脚本用于编译并替换 AIMDK",
    "sparkrobot-lingxi-kb-1.0.10-20261023",
    "sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18",
    "sparkrobot-lingxi-consecutive-nav-fix-1.0.8-20261024-r18",
    "普通常识、科学原理、语言、历史、观点和开放问题",
    "使用模型已有知识正常回答",
    "doubao.streamingReplies=false",
    'CONFIG="${AGENT_WORKDIR}/config/lingxi-nav.properties"',
    "AIMDK PlayTts 连续队列",
    "首段",
    "call_async",
    "stream_action=end",
    "不具备撤回或停止能力",
        "所有地图、点位、任务和路径安全参数都只在 3000 网页控制台配置",
        "8002 和 18082 是内部服务端口，不是配置页面",
    "### 7. R17 全局路径净空与导航安全",
    "R18 AIMDK 接口行为",
    "所有地图、点位、任务和路径安全参数都只在 3000 网页控制台配置",
    "8002 和 18082 是内部服务端口，不是配置页面",
    "pnc_mode=0",
    "target_pose_radius",
    "reference_line",
    "机器人实测外接半径",
    "额外障碍净空",
    "所有地图、点位、任务和路径安全参数都只在机器人 `3000` 网页控制台配置",
    "不需要也不应让用户打开 `8002` 或 `18082` 配置",
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def audit_names(names: set[str], label: str) -> None:
    names = {name.rstrip("/") for name in names}
    expected = {f"{NAME}/{item}" for item in REQUIRED}
    missing = sorted(expected - names)
    extras = sorted(name for name in names if name.startswith(f"{NAME}/") and name not in expected)
    if missing or extras:
        raise RuntimeError(f"{label} 清单不符合预期：缺少={missing}，额外={extras}")


def check_safe_names(names: set[str]) -> None:
    for name in names:
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"归档包含不安全路径：{name}")


def check_linux_text(name: str, value: bytes, label: str) -> None:
    if b"\r" in value:
        raise RuntimeError(f"{label} 中的 {name} 含有 CRLF/CR 换行，不能交付到 Linux")


def read_tar_member(archive: tarfile.TarFile, name: str) -> bytes:
    member = archive.getmember(name)
    handle = archive.extractfile(member)
    if handle is None:
        raise RuntimeError(f"TAR 无法读取成员：{name}")
    return handle.read()


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def audit_shell_syntax(script: bytes, label: str) -> None:
    bash = shutil.which("bash")
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            git_bash = Path(git).resolve().parent.parent / "bin" / "bash.exe"
            if git_bash.is_file():
                bash = str(git_bash)
    if not bash:
        raise RuntimeError(f"{label} Bash 语法测试缺少 Bash")
    with tempfile.TemporaryDirectory(prefix="lingxi-shell-audit-") as temp:
        path = Path(temp) / "script.sh"
        path.write_bytes(script)
        result = subprocess.run(
            [bash, "-n", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode:
            raise RuntimeError(
                f"{label} Bash 语法检查失败：\n"
                + (result.stdout or "")
                + (result.stderr or "")
            )


def digest_stream(handle) -> str:
    value = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        value.update(chunk)
    return value.hexdigest()


def audit_manifest_components(manifest: dict, reader) -> None:
    components = manifest.get("components")
    if not isinstance(components, dict):
        raise RuntimeError("组件清单格式错误")
    expected_names = {
        item.removeprefix("components/")
        for item in REQUIRED
        if item.startswith("components/")
    }
    if set(components) != expected_names:
        raise RuntimeError("组件清单与归档内容不一致")
    for name, expected in components.items():
        actual = digest_bytes(reader(f"{NAME}/components/{name}"))
        if actual != expected:
            raise RuntimeError(f"归档内组件 SHA-256 不一致：{name}")


def audit_internal_checksums(names: set[str], reader, label: str) -> None:
    checksum_name = f"{NAME}/SHA256SUMS.txt"
    entries: dict[str, str] = {}
    for line in reader(checksum_name).decode("utf-8").splitlines():
        if not line:
            continue
        fields = line.split("  ", 1)
        if len(fields) != 2 or len(fields[0]) != 64:
            raise RuntimeError(f"{label} SHA256SUMS.txt 行格式错误：{line}")
        checksum, relative_name = fields[0].lower(), fields[1]
        relative = PurePosixPath(relative_name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise RuntimeError(f"{label} SHA256SUMS.txt 路径不安全：{relative_name}")
        member = (PurePosixPath(NAME) / relative).as_posix()
        if member in entries:
            raise RuntimeError(f"{label} SHA256SUMS.txt 存在重复项：{relative_name}")
        if member not in names:
            raise RuntimeError(f"{label} SHA256SUMS.txt 引用不存在的文件：{relative_name}")
        if digest_bytes(reader(member)) != checksum:
            raise RuntimeError(f"{label} SHA256SUMS.txt 校验失败：{relative_name}")
        entries[member] = checksum
    expected_names = names - {checksum_name}
    if set(entries) != expected_names:
        raise RuntimeError(
            f"{label} SHA256SUMS.txt 覆盖范围错误：缺少={sorted(expected_names-set(entries))}，"
            f"多余={sorted(set(entries)-expected_names)}"
        )


def audit_installer(value: bytes, label: str) -> None:
    text = value.decode("utf-8")
    missing = [snippet for snippet in INSTALLER_REQUIRED_SNIPPETS if snippet not in text]
    if missing:
        raise RuntimeError(f"{label} 安装器缺少现场修复保护：{missing}")
    if "PNC_NAME=" in text or "PNC_ROOT=" in text:
        raise RuntimeError(f"{label} 仍引用旧 PNC 组件，可能覆盖连续导航修复")
    if text.index('sudo bash "${LINKCRAFT_ROOT}/install.sh" --no-restart') \
       > text.index('sudo bash "${FIX_ROOT}/install.sh"'):
       raise RuntimeError(f"{label} 附加组件安装顺序错误：连续导航修复必须最后安装")
    if text.index('sudo bash "${FIX_ROOT}/install.sh"') \
        > text.index("sudo systemctl restart lingxi-nav-console.service"):
        raise RuntimeError(f"{label} 网页控制台必须在连续导航修复之后重启")
    audit_shell_syntax(value, f"{label} install_all.sh")
    audit_work_root_guard(value, label)


def audit_work_root_guard(installer: bytes, label: str) -> None:
    bash = shutil.which("bash")
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            git_bash = Path(git).resolve().parent.parent / "bin" / "bash.exe"
            if git_bash.is_file():
                bash = str(git_bash)
    if not bash:
        raise RuntimeError(f"{label} WORK_ROOT 安全性测试缺少 Bash")
    with tempfile.TemporaryDirectory(prefix="lingxi-work-root-audit-") as temp:
        path = Path(temp) / "install_all.sh"
        path.write_bytes(installer)
        script = r"""
set -eu
die() { echo "$*" >&2; exit 1; }
realpath() {
  [[ "${1:-}" == "-m" && "${2:-}" == "--" ]] || return 2
  case "${3:-}" in
    /home/run/r17) printf '%s\n' /home/run/r17 ;;
    /home/run) printf '%s\n' /home/run ;;
    /home/run/../outside) printf '%s\n' /home/outside ;;
    /home/run/../../tmp) printf '%s\n' /tmp ;;
    /home/run/link) printf '%s\n' /etc ;;
    *) return 2 ;;
  esac
}
source <(sed -n '/^assert_work_root() {/,/^}/p' "$1")
BASE_ROOT=/stale
WORK_ROOT=/home/run/r17
assert_work_root
[[ "${WORK_ROOT}" == /home/run/r17 ]]
[[ "${BASE_ROOT}" == /home/run/r17/base/lingxi-v1.3.1-full-deployment-20261024-r18 ]]
for candidate in /home/run /home/run/../outside /home/run/../../tmp /home/run/link; do
  if ( WORK_ROOT="${candidate}"; assert_work_root ) >/dev/null 2>&1; then
    echo "WORK_ROOT escaped its allowed directory: ${candidate}" >&2
    exit 1
  fi
done
"""
        result = subprocess.run(
            [bash, "-c", script, "audit-work-root", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode:
            raise RuntimeError(
                f"{label} WORK_ROOT 安全性测试失败：\n"
                + (result.stdout or "")
                + (result.stderr or "")
            )


def audit_addon_path_collisions(reader) -> None:
    components = (
        ("LinkCraft", "components/sparkrobot-lingxi-linkcraft-addon-1.0.3-20260902.tar.gz", "tar"),
        ("连续导航修复", f"components/{FIX_NAME}", "tar"),
        ("知识库", "components/sparkrobot-lingxi-kb-1.0.10-20261023.zip", "zip"),
    )
    extracted_paths: list[tuple[str, set[str]]] = []
    for label, relative, kind in components:
        data = reader(f"{NAME}/{relative}")
        paths: set[str] = set()
        if kind == "tar":
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                paths = {
                    PurePosixPath(member.name).as_posix()
                    for member in archive.getmembers()
                    if member.isfile()
                }
        else:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                paths = {
                    PurePosixPath(name).as_posix()
                    for name in archive.namelist()
                    if name and not name.endswith("/")
                }
        extracted_paths.append((label, paths))

    for index, (left_name, left_paths) in enumerate(extracted_paths):
        for right_name, right_paths in extracted_paths[index + 1 :]:
            for left in left_paths:
                for right in right_paths:
                    if (
                        left == right
                        or left.startswith(right + "/")
                        or right.startswith(left + "/")
                    ):
                        raise RuntimeError(
                            f"附加包解压路径冲突：{left_name} 与 {right_name}："
                            f"{left} / {right}"
                        )


def audit_documentation(reader) -> None:
    documents = {
        "README.md": README_REQUIRED_SNIPPETS,
        "docs/灵犀完整部署教程-v1.3.1-complete.md": GUIDE_REQUIRED_SNIPPETS,
        "docs/已有AIMDK部署说明-v1.3.1-r18.md": AIMDK_GUIDE_REQUIRED_SNIPPETS,
    }
    for relative, snippets in documents.items():
        text = reader(f"{NAME}/{relative}").decode("utf-8")
        stable_markers = tuple(snippet for snippet in snippets if snippet.isascii())
        if relative != "README.md":
            stable_markers += (
                "3000",
                "8002",
                "18082",
                "task_type=2",
                "pnc_mode=0",
                "reference_line",
                "target_pose_radius",
            )
        missing = [snippet for snippet in stable_markers if snippet not in text]
        if missing:
            raise RuntimeError(f"部署文档 {relative} 缺少 R18 说明：{missing}")

    guide = reader(f"{NAME}/docs/灵犀完整部署教程-v1.3.1-complete.md").decode("utf-8")
    stale_commands = (
        "/home/run/lingxi-v1.3.1-complete-20261024-r17-deployment",
        "/home/run/v1.3.1-complete-deployment-20261023-r17",
        "1.0.7-20261024-r17",
        "1.0.9-20261024-r17",
    )
    found = [marker for marker in stale_commands if marker in guide]
    if found:
        raise RuntimeError(f"R18 部署教程仍包含旧版可执行路径或组件号：{found}")


def audit_fix_component(reader) -> None:
    name = FIX_NAME
    data = reader(f"{NAME}/components/{name}")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        prefix = f"{name.removesuffix('.tar.gz')}/"
        control = read_tar_member(archive, prefix + "control.sh").decode("utf-8")
        installer = read_tar_member(archive, prefix + "install.sh").decode("utf-8")
        manifest = json.loads(read_tar_member(archive, prefix + "manifest.json"))
        release_info = read_tar_member(archive, prefix + "release-info.txt").decode("utf-8")
        if manifest.get("version") != "1.0.8-20261024-r18" or manifest.get("payload_count") != 11:
            raise RuntimeError("连续导航修复组件版本或 payload 数量不正确")
        if "version=1.0.8-20261024-r18" not in release_info:
            raise RuntimeError("连续导航修复组件 release-info 版本不正确")
        if "repair_runtime_permissions" not in control or "resolve_bridge_run_user" not in control:
            raise RuntimeError("连续导航修复组件缺少运行目录权限保护")
        if "repair_runtime_permissions" not in installer:
            raise RuntimeError("连续导航修复组件安装器缺少权限修复")
        members = {member.name for member in archive.getmembers() if member.isfile()}
        if prefix + "payload/voice_preset_motion.py" not in members:
            raise RuntimeError("连续导航修复组件缺少语音预置动作模块")
        if prefix + "payload/tts_stream_protocol.py" not in members:
            raise RuntimeError("连续导航修复组件缺少 TTS 流式序号协议模块")
        if prefix + "payload/tts_speaker.py" not in members:
            raise RuntimeError("连续导航修复组件缺少原子派发 TTS 适配器")
        if prefix + "payload/models.py" not in members:
            raise RuntimeError("连续导航修复组件缺少 R17 路径安全配置模型")
        if prefix + "payload/path_clearance.py" not in members:
            raise RuntimeError("连续导航修复组件缺少 R17 路径净空规划器")
        if (
            "tts_stream_protocol.py" not in installer
            or "tts_stream_protocol.py" not in control
            or "tts_speaker.py" not in installer
            or "tts_speaker.py" not in control
        ):
            raise RuntimeError("连续导航修复组件未在安装/运维中纳入 TTS 流式序号协议模块")
        point_catalog = read_tar_member(
            archive, prefix + "payload/point_catalog.py"
        ).decode("utf-8")
        bridge = read_tar_member(
            archive, prefix + "payload/lingxi_nav_bridge.py"
        ).decode("utf-8")
        if "def list_ambiguous_voice_names" not in point_catalog:
            raise RuntimeError("连续导航修复组件缺少活动地图歧义别名查询")
        if '"ambiguous_point_names"' not in bridge:
            raise RuntimeError("连续导航修复组件没有向 Java Agent 提供歧义别名")
        stream_protocol = read_tar_member(
            archive, prefix + "payload/tts_stream_protocol.py"
        ).decode("utf-8")
        speaker = read_tar_member(
            archive, prefix + "payload/tts_speaker.py"
        ).decode("utf-8")
        if (
            "class TtsStreamProtocol" not in stream_protocol
            or "def dispatch_if_current(" not in stream_protocol
            or "request_submitter" not in speaker
        ):
            raise RuntimeError("连续导航修复组件的 TTS 序号协议缺少实现")
        models = read_tar_member(archive, prefix + "payload/models.py").decode("utf-8")
        path_clearance = read_tar_member(
            archive, prefix + "payload/path_clearance.py"
        ).decode("utf-8")
        if "class PathSafetyConfig" not in models:
            raise RuntimeError("连续导航修复组件的路径安全模型缺少 PathSafetyConfig")
        if (
            "def plan_reference_line" not in path_clearance
            or "def inflate_obstacles" not in path_clearance
        ):
            raise RuntimeError("连续导航修复组件的路径净空模块缺少规划实现")


def audit_navigation_fix_consistency(reader) -> None:
    nav_name = NAV_NAME
    fix_name = FIX_NAME
    nav_root = nav_name.removesuffix(".tar.gz")
    fix_root = fix_name.removesuffix(".tar.gz")
    nav_data = reader(f"{NAME}/components/{nav_name}")
    fix_data = reader(f"{NAME}/components/{fix_name}")
    with (
        tarfile.open(fileobj=io.BytesIO(nav_data), mode="r:gz") as nav_archive,
        tarfile.open(fileobj=io.BytesIO(fix_data), mode="r:gz") as fix_archive,
    ):
        for relative in (
            "lingxi_nav_bridge.py",
            "navigation_client.py",
            "models.py",
            "path_clearance.py",
        ):
            nav_member = (
                f"{nav_root}/payload/navigation/robot_local_navigation_v3/{relative}"
            )
            fix_member = f"{fix_root}/payload/{relative}"
            nav_bytes = read_tar_member(nav_archive, nav_member)
            fix_bytes = read_tar_member(fix_archive, fix_member)
            if relative == "models.py":
                nav_bytes = nav_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                fix_bytes = fix_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            if nav_bytes != fix_bytes:
                raise RuntimeError(
                    "R17 导航主包与最后覆盖的连续导航修复包不一致："
                    f"{relative}"
                )


def audit_kb_component(reader) -> None:
    name = "sparkrobot-lingxi-kb-1.0.10-20261023.zip"
    with zipfile.ZipFile(io.BytesIO(reader(f"{NAME}/components/{name}"))) as archive:
        root = "sparkrobot-lingxi-kb-1.0.10-20261023/"
        manifest = json.loads(archive.read(f"{root}manifest.json"))
        if manifest.get("version") != "1.0.10-20261023":
            raise RuntimeError("知识库组件版本不正确")
        release_info = archive.read(f"{root}release-info.txt").decode("utf-8")
        if "VERSION=1.0.10-20261023" not in release_info:
            raise RuntimeError("知识库 release-info.txt 版本不正确")
        expected_response_policy = {
            "point_names_cache_ttl_ms": 10000,
            "retriever_timeout_ms": 1200,
            "retriever_min_score": 0.60,
            "retriever_rerank": False,
            "retriever_qa_precheck": False,
            "text_input_greetings_use_deterministic_reply": True,
            "local_or_current_facts_require_grounding": True,
            "unsupported_grounded_facts": "refuse",
            "ordinary_general_questions_use_model_knowledge": True,
            "generic_domain_terms_alone_require_grounding": False,
            "legacy_generated_qa_cache": "disabled_for_answer_selection",
            "text_chat_reuses_intent_reply": True,
            "blank_intent_reply_uses_chat_fallback": True,
            "streaming_replies_default_enabled": False,
            "streaming_replies_scope": "plain_text_chat_only",
            "streaming_replies_require_robot_tts_mode": True,
            "streaming_tts_first_segment_interrupts": True,
            "streaming_tts_continuations_ordered": True,
            "streaming_tts_new_normal_reply_preempts_current_stream": True,
            "streaming_tts_queue_requires_robot_validation": True,
            "streaming_tts_dispatch_revalidates_owner_after_service_wait": True,
            "streaming_tts_stopped_stream_ids_rejected": True,
            "streaming_tts_call_async_cancellation_supported_by_bridge": False,
            "streaming_tts_end_action_stops_robot_playback": False,
            "streaming_tts_preempted_full_reply_fallback": "suppressed",
            "java_tts_decisions_use_owner_tokens": True,
            "streaming_model_failure_before_first_speech": "retry_non_streaming",
            "streaming_model_failure_after_first_speech": "urgent_interruption_prompt",
            "streaming_tts_continuation_failure": "complete_final_reply_if_owner_current",
            "tts_delay_changed": False,
            "known_point_navigation_fast_path": "exact_active_map_voice_name_before_intent_model",
            "raw_audio_navigation_still_requires_transcription_model": True,
            "local_stop_commands_before_intent_model": True,
            "ambiguous_navigation_alias": "clarify_without_navigation_submission",
            "low_asr_confidence": "robot_tts_repeat_prompt_without_navigation_submission",
            "start_resume_commands_require_asr_confidence_gate": True,
            "factual_retrieval_strategy": "parallel_with_intent_parsing",
            "factual_retrieval_worker_threads": 2,
            "factual_retrieval_queue_capacity": 4,
            "factual_retrieval_queue_full_fallback": "serial_after_intent_parsing",
        }
        if manifest.get("voice_response_policy") != expected_response_policy:
            raise RuntimeError("知识库组件语音响应策略清单不正确")
        for item in manifest.get("files", []):
            member = f"{root}{item['path']}"
            if digest_bytes(archive.read(member)) != item["sha256"]:
                raise RuntimeError(f"知识库组件文件 SHA-256 不一致：{item['path']}")
        voice_policy = manifest.get("voice_interaction_policy", {})
        if (
            voice_policy.get("wake_mode") != "enforce"
            or voice_policy.get("metadata_required") is not False
            or voice_policy.get("wake_signal_max_age_ms") != 8000
            or voice_policy.get("session_window_ms") != 8000
            or voice_policy.get("acoustic_dsp_included") is not False
        ):
            raise RuntimeError("知识库组件语音策略清单不正确")
        motion_policy = manifest.get("preset_motion_policy", {})
        expected_motions = {
            "wave", "handshake", "raise_hand", "flying_kiss", "sweetheart", "high_five",
            "salute", "horizontal_arm", "hug", "cheer", "clap", "handx", "chestwave",
            "bow", "bye", "lightwave", "scratch_head", "scratch_back",
        }
        if (
            motion_policy.get("action_count") != 18
            or set(motion_policy.get("actions", [])) != expected_motions
            or motion_policy.get("execution_mode") != "explicit_imperative_only"
            or motion_policy.get("busy_navigation_or_tour") != "reject"
            or motion_policy.get("live_danmu") != "disabled"
            or motion_policy.get("acknowledgement") != "submission_only"
        ):
            raise RuntimeError("知识库组件预置动作策略清单不正确")
        agent_bytes = archive.read(
            f"{root}agent/robot-local-navigation-java-all-kb.jar"
        )
    required_classes = {
        "com/agibot/aiem/nav/AppConfig.class",
        "com/agibot/aiem/nav/LingxiNavAgentApp.class",
        "com/agibot/aiem/nav/LingxiNavAgentApp$StreamingTtsSession.class",
        "com/agibot/aiem/nav/LingxiNavAgentApp$TtsController.class",
        "com/agibot/aiem/nav/LingxiNavAgentApp$TtsDecision.class",
        "com/agibot/aiem/nav/LocalNavigationCommand.class",
        "com/agibot/aiem/nav/VoiceGate.class",
        "com/agibot/aiem/nav/WakeSessionGate.class",
        "com/agibot/aiem/nav/SpeechInputPolicy.class",
        "com/agibot/aiem/nav/Soc2WakeEvidenceProvider.class",
        "com/agibot/aiem/nav/VoiceMetadataDiagnostics.class",
        "com/agibot/aiem/nav/NavigationIntent.class",
        "com/agibot/aiem/nav/PresetMotionCatalog.class",
        "com/agibot/aiem/nav/PresetMotionCatalog$Motion.class",
        "com/agibot/aiem/nav/PresetMotionCatalog$Selection.class",
        "com/agibot/aiem/nav/NavBridgeClient.class",
        "com/agibot/aiem/nav/DoubaoArkClient.class",
        "com/agibot/aiem/nav/DoubaoArkClient$SseChunk.class",
        "com/agibot/aiem/nav/SpeechSegmenter.class",
        "com/agibot/aiem/nav/StreamingJsonReplyExtractor.class",
        "com/agibot/aiem/nav/StreamingJsonReplyExtractor$ParsedString.class",
        "com/agibot/aiem/nav/RetrieverClient.class",
        "com/agibot/aiem/nav/KnowledgeAnswerPolicy.class",
    }
    with zipfile.ZipFile(io.BytesIO(agent_bytes)) as agent:
        missing = required_classes - set(agent.namelist())
        if missing:
            raise RuntimeError(
                f"知识库 Java Agent 缺少交互拒识类：{sorted(missing)}"
            )
        prompt_class = agent.read("com/agibot/aiem/nav/DoubaoArkClient.class")
        if (
            "使用模型已有知识正常回答".encode("utf-8") not in prompt_class
            or "不要因为当前上下文或知识库没有提供常识答案而拒答".encode("utf-8")
            not in prompt_class
        ):
            raise RuntimeError("知识库 Java Agent 未包含普通问题由大模型回答的提示策略")


def audit_base_bundle(reader) -> None:
    base_bytes = reader(f"{NAME}/components/{BASE_BUNDLE}")
    with zipfile.ZipFile(io.BytesIO(base_bytes)) as archive:
        prefix = f"{BASE_ROOT}/"
        script = archive.read(prefix + "scripts/robot/01-replace-aimdk.sh")
        check_linux_text("01-replace-aimdk.sh", script, "基础套件")
        audit_shell_syntax(script, "基础套件 AIMDK 替换脚本")
        if b"set +u\nsource /opt/ros/humble/setup.bash\nset -u" not in script:
            raise RuntimeError("基础套件 AIMDK 脚本缺少 ROS nounset 兼容修复")
        nested_aimdk = archive.read(prefix + f"packages/{AIMDK_NAME}")
        if digest_bytes(nested_aimdk) != AIMDK_SHA256:
            raise RuntimeError("基础套件内 AIMDK v1.1.0 SHA-256 不一致")
        with zipfile.ZipFile(io.BytesIO(nested_aimdk)) as sdk:
            sdk_root = "aimdk-aarch64-faf4c945-artifacts"
            request = sdk.read(
                f"{sdk_root}/src/aimdk_msgs/interface/robot/task_engine/msg/PncTaskRequest.msg"
            )
            response = sdk.read(
                f"{sdk_root}/src/aimdk_msgs/interface/robot/task_engine/msg/PncTaskResponse.msg"
            )
            if b"reference_line" not in request or b"target_pose_radius" not in request:
                raise RuntimeError("基础套件 AIMDK PncTaskRequest 缺少 R18 字段")
            if b"task_status" not in response:
                raise RuntimeError("基础套件 AIMDK PncTaskResponse 缺少状态字段")
        nested_nav = archive.read(prefix + f"packages/{NAV_NAME}")
        nav_checksum = archive.read(prefix + f"packages/{NAV_NAME}.sha256").decode("ascii")
        nav_installer = archive.read(prefix + "scripts/robot/02-install-lingxi-navigation.sh").decode("utf-8")
        nav_verifier = archive.read(prefix + "scripts/robot/03-verify-after-web-start.sh").decode("utf-8")
        if (
            NAV_NAME not in nav_checksum
            or "1.1.0-20261024-r18" not in nav_installer
            or "1.1.0-20261024-r18" not in nav_verifier
            or "1.0.9-20261024-r17" in nav_installer
            or "1.0.9-20261024-r17" in nav_verifier
        ):
            raise RuntimeError("基础套件独立安装脚本未全部指向 R18 导航版本")
    direct_aimdk = reader(f"{NAME}/components/{AIMDK_NAME}")
    direct_nav = reader(f"{NAME}/components/{NAV_NAME}")
    if digest_bytes(nested_aimdk) != digest_bytes(direct_aimdk):
        raise RuntimeError("顶层 AIMDK 与基础套件内 AIMDK 不一致")
    if digest_bytes(nested_nav) != digest_bytes(direct_nav):
        raise RuntimeError("顶层导航主包与基础套件内导航主包不一致")

    nav_root = NAV_NAME.removesuffix(".tar.gz")
    with tarfile.open(fileobj=io.BytesIO(direct_nav), mode="r:gz") as archive:
        prefix = f"{nav_root}/"
        required = {
            prefix + "payload/navigation/robot_local_navigation_v3/path_clearance.py",
            prefix + "payload/navigation/robot_local_navigation_v3/navigation_client.py",
            prefix + "payload/navigation/robot_local_navigation_v3/models.py",
            prefix + "payload/console/dist/public/index.html",
            prefix + "payload/console/dist/public/app.js",
            prefix + "payload/console/dist/robotClient.js",
            prefix + "source/navigation/robot_local_navigation_v3/path_clearance.py",
            prefix + "source/navigation/robot_local_navigation_v3/navigation_client.py",
            prefix + "source/navigation/test/test_path_clearance.py",
            prefix + "source/navigation/test/test_pnc_discovery.py",
            prefix + "source/navigation/test/test_aimdk_110_contract.py",
            prefix + "source/navigation/test/test_map_label_path_safety_settings.py",
            prefix + "templates/unified_nav_config.json",
            prefix + "SHA256SUMS",
        }
        members = {
            member.name: member
            for member in archive.getmembers()
            if member.isfile()
        }
        missing = sorted(required - set(members))
        if missing:
            raise RuntimeError(f"R17 导航包缺少路径净空运行文件、源码或测试：{missing}")

        def nav_text(relative: str) -> str:
            return read_tar_member(archive, prefix + relative).decode("utf-8")

        client = nav_text("payload/navigation/robot_local_navigation_v3/navigation_client.py")
        source_client = nav_text("source/navigation/robot_local_navigation_v3/navigation_client.py")
        path_module = nav_text("payload/navigation/robot_local_navigation_v3/path_clearance.py")
        path_test = nav_text("source/navigation/test/test_path_clearance.py")
        pnc_test = nav_text("source/navigation/test/test_pnc_discovery.py")
        settings_test = nav_text(
            "source/navigation/test/test_map_label_path_safety_settings.py"
        )
        for marker in (
            "point.navigation.task_type == 2 and point.navigation.pnc_mode == 0",
            "if not hasattr(msg, \"reference_line\")",
            "msg.reference_line = reference_line",
            "QoSDurabilityPolicy.VOLATILE",
            "QoSDurabilityPolicy.TRANSIENT_LOCAL",
            "MIN_TARGET_POSE_RADIUS",
            "msg.target_pose_radius = 0.0",
        ):
            if marker not in client:
                raise RuntimeError(f"R18 导航客户端缺少路径净空或 AIMDK 契约：{marker}")
        if client != source_client:
            raise RuntimeError("R18 导航运行代码与交付源码中的 navigation_client.py 不一致")
        models = nav_text("payload/navigation/robot_local_navigation_v3/models.py")
        if "DEFAULT_ARRIVAL_POSITION_TOLERANCE_M = 0.15" not in models:
            raise RuntimeError("R18 PNC 半径下限意外改变了独立到达容差默认值")
        contract_test = nav_text("source/navigation/test/test_aimdk_110_contract.py")
        for marker in (
            "test_pnc_request_uses_best_effort_volatile_qos",
            "test_pnc_response_uses_best_effort_transient_local_qos",
            "test_start_radius_is_clamped_to_sdk_minimum",
            "test_stop_radius_remains_zero",
            "aimdk v1.1.0-gfaf4c94",
        ):
            if marker not in contract_test:
                raise RuntimeError(f"R18 导航包缺少 AIMDK 接口回归测试：{marker}")
        for marker in ("def plan_reference_line", "def inflate_obstacles"):
            if marker not in path_module:
                raise RuntimeError(f"R17 路径规划模块缺少实现：{marker}")
        if "radius_cells = (config.robot_radius_m + config.extra_clearance_m)" not in path_module:
            raise RuntimeError("R17 地图障碍膨胀未使用页面实测半径和额外净空")
        if "test_reference_line_detours_around_inflated_static_obstacle" not in path_test:
            raise RuntimeError("R17 导航包缺少静态障碍绕行测试")
        if (
            "test_configured_radius_and_extra_clearance_move_route_farther_from_obstacle"
            not in path_test
            or "self.assertGreater(increased_clearance_distance, baseline_distance + 1.0)"
            not in path_test
        ):
            raise RuntimeError("R17 导航包缺少半径/净空数值规划测试")
        if "test_start_request_contains_the_planned_reference_line" not in pnc_test:
            raise RuntimeError("R17 导航包缺少 reference_line 请求测试")
        for marker in (
            "test_saves_global_path_safety_and_reports_bridge_application",
            "test_get_settings_reports_persisted_and_runtime_values_separately",
            "test_bridge_application_requires_exact_runtime_readback",
            '"robot_radius_m": 0.253',
            '"extra_clearance_m": 0.18',
        ):
            if marker not in settings_test:
                raise RuntimeError(f"R17 导航包缺少 3000 设置/回读测试：{marker}")
        config = json.loads(nav_text("templates/unified_nav_config.json"))
        safety = config.get("navigation", {}).get("path_safety", {})
        if (
            safety.get("robot_radius_m") is not None
            or safety.get("extra_clearance_m") != 0.1
            or safety.get("occupied_threshold") != 205
        ):
            raise RuntimeError("R17 全局路径净空默认参数不正确")

        console_html = read_tar_member(
            archive, prefix + "payload/console/dist/public/index.html"
        ).decode("utf-8")
        console_app = read_tar_member(
            archive, prefix + "payload/console/dist/public/app.js"
        ).decode("utf-8")
        robot_client = read_tar_member(
            archive, prefix + "payload/console/dist/robotClient.js"
        ).decode("utf-8")
        bridge = nav_text("payload/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py")
        for marker in (
            'id="pointTaskType"',
            'id="robotRadius" form="pathSafetyForm"',
            'id="extraClearance" form="pathSafetyForm"',
            'id="savePathSafetyBtn" form="pathSafetyForm"',
        ):
            if marker not in console_html:
                raise RuntimeError(f"R17 部署包的 3000 页面缺少安全配置字段：{marker}")
        if "R17 将拒绝下发 task_type=2" not in console_app or "R16 将拒绝下发 task_type=2" in console_app:
            raise RuntimeError("R18 部署包的 3000 安全参数提示缺少 R17 路径保护提示")
        if not (
            console_html.index('id="pointTaskType"')
            < console_html.index('id="robotRadius"')
            < console_html.index('id="pointStart"')
        ):
            raise RuntimeError("R17 部署包的 3000 安全配置不在点位 task_type 同页")
        for marker in ("/api/robots/default/navigation/path-safety", "path_safety_applied", "applied_path_safety"):
            if marker not in console_app:
                raise RuntimeError(f"R17 部署包的 3000 页面缺少保存/运行时回读：{marker}")
        for marker in ("pathSafety()", "savePathSafety(pathSafety)", "/api/settings"):
            if marker not in robot_client:
                raise RuntimeError(f"R17 部署包的 3000 后端代理缺少安全设置：{marker}")
        for marker in ('"path_safety": self.navigation_client.defaults.path_safety.__dict__', "applied_path_safety"):
            if marker not in bridge:
                raise RuntimeError(f"R17 导航运行时缺少配置落地回读：{marker}")

        checksum_text = nav_text("SHA256SUMS")
        checksum_entries: dict[str, str] = {}
        for line in checksum_text.splitlines():
            if not line:
                continue
            fields = line.split("  ", 1)
            if len(fields) != 2:
                raise RuntimeError("R17 导航包 SHA256SUMS 行格式错误")
            checksum, relative = fields
            member_name = prefix + relative
            if member_name not in members or digest_bytes(read_tar_member(archive, member_name)) != checksum:
                raise RuntimeError(f"R17 导航包 SHA-256 校验失败：{relative}")
            checksum_entries[member_name] = checksum
        if set(checksum_entries) != set(members) - {prefix + "SHA256SUMS"}:
            raise RuntimeError("R17 导航包 SHA256SUMS 未覆盖全部普通文件")


def audit_sidecar(path: Path) -> None:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not sidecar.is_file():
        raise RuntimeError(f"缺少外层校验文件：{sidecar}")
    value = sidecar.read_bytes()
    check_linux_text(sidecar.name, value, "外层校验文件")
    fields = value.decode("ascii").split()
    if len(fields) != 2 or fields[1] != path.name:
        raise RuntimeError(f"外层校验文件格式错误：{sidecar.name}")
    if fields[0].lower() != digest(path):
        raise RuntimeError(f"外层 SHA-256 不一致：{path.name}")


def audit_archive(path: Path, label: str) -> None:
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            if archive.testzip():
                raise RuntimeError(f"{label} 存在损坏成员")
            names = {name for name in archive.namelist() if not name.endswith("/")}
    else:
        with tarfile.open(path, "r:gz") as archive:
            names = {member.name for member in archive.getmembers() if member.isfile()}
    check_safe_names(names)


def main() -> int:
    tar_path = DIST / f"{NAME}.tar.gz"
    if not tar_path.is_file():
        raise RuntimeError(f"缺少最终 TAR.GZ 归档：{tar_path}")
    audit_archive(tar_path, tar_path.name)
    audit_sidecar(tar_path)
    with tarfile.open(tar_path, "r:gz") as archive:
        tar_names = {
            member.name for member in archive.getmembers() if member.isfile()
        }
        audit_names(tar_names, "TAR")
        audit_internal_checksums(
            tar_names,
            lambda name: read_tar_member(archive, name),
            "TAR",
        )
        for relative in LINUX_TEXT_FILES:
            check_linux_text(relative, read_tar_member(archive, f"{NAME}/{relative}"), "TAR")
        manifest = json.loads(read_tar_member(archive, f"{NAME}/bundle-manifest.json"))
        if manifest.get("version") != VERSION or manifest.get("contains_real_secrets") is not False:
            raise RuntimeError("TAR 版本或秘密标记错误")
        audit_manifest_components(manifest, lambda name: read_tar_member(archive, name))
        audit_installer(read_tar_member(archive, f"{NAME}/install_all.sh"), "TAR")
        audit_shell_syntax(
            read_tar_member(archive, f"{NAME}/control_all.sh"),
            "TAR control_all.sh",
        )
        audit_documentation(lambda name: read_tar_member(archive, name))
        audit_kb_component(lambda name: read_tar_member(archive, name))
        audit_fix_component(lambda name: read_tar_member(archive, name))
        audit_navigation_fix_consistency(lambda name: read_tar_member(archive, name))
        audit_base_bundle(lambda name: read_tar_member(archive, name))
        audit_addon_path_collisions(lambda name: read_tar_member(archive, name))
    print("完整导航部署包 TAR.GZ 审计通过：AIMDK、导航和附加组件完整，现场修复保护、路径安全、Linux 换行、外层校验和无真实密钥标记均正常")
    print(f"TAR SHA-256：{digest(tar_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
