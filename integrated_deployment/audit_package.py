#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


HERE = Path(__file__).resolve().parent
VERSION = "1.3.1-complete-20261023-r11"
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
    "components/lingxi-v1.3.1-full-deployment-20260901.zip",
    "components/aimdk-aarch64-1bde262f-artifacts.7z",
    "components/sparkrobot-lingxi-nav-internal-1.0.7-20260901.tar.gz",
    "components/sparkrobot-lingxi-kb-1.0.6-20261023.zip",
    "components/sparkrobot-lingxi-linkcraft-addon-1.0.3-20260902.tar.gz",
    "components/sparkrobot-lingxi-consecutive-nav-fix-1.0.2-20261022.tar.gz",
}
LINUX_TEXT_FILES = {
    "SHA256SUMS.txt",
    "install_all.sh",
    "control_all.sh",
    "README.md",
    "docs/灵犀完整部署教程-v1.3.1-complete.md",
    "docs/客户日常使用手册-v1.3.1.md",
}
BASE_BUNDLE = "lingxi-v1.3.1-full-deployment-20260901.zip"
BASE_ROOT = "lingxi-v1.3.1-full-deployment-20260901"
AIMDK_NAME = "aimdk-aarch64-1bde262f-artifacts.7z"
NAV_NAME = "sparkrobot-lingxi-nav-internal-1.0.7-20260901.tar.gz"
INSTALLER_REQUIRED_SNIPPETS = (
    "verify_aimdk_runtime",
    "sudo test -f /etc/sparkrobot/lingxi-nav/lingxi-nav.properties",
    '[[ "${staging}" == "${WORK_ROOT}/addons" ]]',
    'sudo rm -rf -- "${staging}"',
    'sudo bash "${LINKCRAFT_ROOT}/install.sh" --no-restart',
    'sudo bash "${FIX_ROOT}/install.sh"',
    'KB_NAME="sparkrobot-lingxi-kb-1.0.6-20261023.zip"',
    'KB_ROOT="${staging}/sparkrobot-lingxi-kb-1.0.6-20261023"',
    "repair-permissions",
    "sudo systemctl restart lingxi-nav-console.service",
)
README_REQUIRED_SNIPPETS = (
    "v1.3.1-complete-20261023-r11",
    "sparkrobot-lingxi-kb-1.0.6-20261023",
    "已知点位导航快速路径",
    "原始音频仍需调用当前配置的转写/意图模型",
)
GUIDE_REQUIRED_SNIPPETS = (
    "v1.3.1-complete-20261023-r11",
    "sparkrobot-lingxi-kb-1.0.6-20261023",
    "### 10.5 已知点位导航快速路径",
    "r11 没有实现端侧离线 ASR",
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


def audit_documentation(reader) -> None:
    documents = {
        "README.md": README_REQUIRED_SNIPPETS,
        "docs/灵犀完整部署教程-v1.3.1-complete.md": GUIDE_REQUIRED_SNIPPETS,
    }
    for relative, snippets in documents.items():
        text = reader(f"{NAME}/{relative}").decode("utf-8")
        missing = [snippet for snippet in snippets if snippet not in text]
        if missing:
            raise RuntimeError(f"部署文档 {relative} 缺少 r11 说明：{missing}")


def audit_fix_component(reader) -> None:
    name = "sparkrobot-lingxi-consecutive-nav-fix-1.0.2-20261022.tar.gz"
    data = reader(f"{NAME}/components/{name}")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        prefix = f"sparkrobot-lingxi-consecutive-nav-fix-1.0.2-20261022/"
        control = read_tar_member(archive, prefix + "control.sh").decode("utf-8")
        installer = read_tar_member(archive, prefix + "install.sh").decode("utf-8")
        if "repair_runtime_permissions" not in control or "resolve_bridge_run_user" not in control:
            raise RuntimeError("连续导航修复组件缺少运行目录权限保护")
        if "repair_runtime_permissions" not in installer:
            raise RuntimeError("连续导航修复组件安装器缺少权限修复")
        members = {member.name for member in archive.getmembers() if member.isfile()}
        if prefix + "payload/voice_preset_motion.py" not in members:
            raise RuntimeError("连续导航修复组件缺少语音预置动作模块")


def audit_kb_component(reader) -> None:
    name = "sparkrobot-lingxi-kb-1.0.6-20261023.zip"
    with zipfile.ZipFile(io.BytesIO(reader(f"{NAME}/components/{name}"))) as archive:
        root = "sparkrobot-lingxi-kb-1.0.6-20261023/"
        manifest = json.loads(archive.read(f"{root}manifest.json"))
        if manifest.get("version") != "1.0.6-20261023":
            raise RuntimeError("知识库组件版本不正确")
        release_info = archive.read(f"{root}release-info.txt").decode("utf-8")
        if "VERSION=1.0.6-20261023" not in release_info:
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
            "legacy_generated_qa_cache": "disabled_for_answer_selection",
            "text_chat_reuses_intent_reply": True,
            "blank_intent_reply_uses_chat_fallback": True,
            "streaming_model_output": False,
            "tts_delay_changed": False,
            "known_point_navigation_fast_path": "exact_active_map_voice_name_before_intent_model",
            "raw_audio_navigation_still_requires_transcription_model": True,
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
        "com/agibot/aiem/nav/RetrieverClient.class",
        "com/agibot/aiem/nav/KnowledgeAnswerPolicy.class",
    }
    with zipfile.ZipFile(io.BytesIO(agent_bytes)) as agent:
        missing = required_classes - set(agent.namelist())
        if missing:
            raise RuntimeError(
                f"知识库 Java Agent 缺少交互拒识类：{sorted(missing)}"
            )


def audit_base_bundle(reader) -> None:
    base_bytes = reader(f"{NAME}/components/{BASE_BUNDLE}")
    with zipfile.ZipFile(io.BytesIO(base_bytes)) as archive:
        prefix = f"{BASE_ROOT}/"
        script = archive.read(prefix + "scripts/robot/01-replace-aimdk.sh")
        check_linux_text("01-replace-aimdk.sh", script, "基础套件")
        if b"set +u\nsource /opt/ros/humble/setup.bash\nset -u" not in script:
            raise RuntimeError("基础套件 AIMDK 脚本缺少 ROS nounset 兼容修复")
        nested_aimdk = archive.read(prefix + f"packages/{AIMDK_NAME}")
        nested_nav = archive.read(prefix + f"packages/{NAV_NAME}")
    direct_aimdk = reader(f"{NAME}/components/{AIMDK_NAME}")
    direct_nav = reader(f"{NAME}/components/{NAV_NAME}")
    if digest_bytes(nested_aimdk) != digest_bytes(direct_aimdk):
        raise RuntimeError("顶层 AIMDK 与基础套件内 AIMDK 不一致")
    if digest_bytes(nested_nav) != digest_bytes(direct_nav):
        raise RuntimeError("顶层导航主包与基础套件内导航主包不一致")


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
    zip_path = DIST / f"{NAME}.zip"
    for path in (tar_path, zip_path):
        if not path.is_file():
            raise RuntimeError(f"缺少最终归档：{path}")
        audit_archive(path, path.name)
        audit_sidecar(path)
    with zipfile.ZipFile(zip_path) as archive:
        audit_names({name for name in archive.namelist() if not name.endswith("/")}, "ZIP")
        for relative in LINUX_TEXT_FILES:
            check_linux_text(relative, archive.read(f"{NAME}/{relative}"), "ZIP")
        manifest = json.loads(archive.read(f"{NAME}/bundle-manifest.json"))
        if manifest.get("version") != VERSION or manifest.get("contains_real_secrets") is not False:
            raise RuntimeError("版本或秘密标记错误")
        expected_order = [
            "prepare_environment",
            "replace_aimdk",
            "install_navigation",
            "install_linkcraft",
            "install_knowledge_base",
            "install_consecutive_navigation_fix_last",
        ]
        if manifest.get("installation_order") != expected_order:
            raise RuntimeError("安装顺序元数据错误")
        audit_manifest_components(manifest, archive.read)
        audit_installer(archive.read(f"{NAME}/install_all.sh"), "ZIP")
        audit_documentation(archive.read)
        audit_kb_component(archive.read)
        audit_fix_component(archive.read)
        audit_base_bundle(archive.read)
    with tarfile.open(tar_path, "r:gz") as archive:
        audit_names({member.name for member in archive.getmembers() if member.isfile()}, "TAR")
        for relative in LINUX_TEXT_FILES:
            check_linux_text(relative, read_tar_member(archive, f"{NAME}/{relative}"), "TAR")
        manifest = json.loads(read_tar_member(archive, f"{NAME}/bundle-manifest.json"))
        if manifest.get("version") != VERSION or manifest.get("contains_real_secrets") is not False:
            raise RuntimeError("TAR 版本或秘密标记错误")
        audit_manifest_components(manifest, lambda name: read_tar_member(archive, name))
        audit_installer(read_tar_member(archive, f"{NAME}/install_all.sh"), "TAR")
        audit_documentation(lambda name: read_tar_member(archive, name))
        audit_kb_component(lambda name: read_tar_member(archive, name))
    print("完整导航部署包审计通过：AIMDK、导航和附加组件完整，现场修复保护、路径安全、Linux 换行、外层校验和无真实密钥标记均正常")
    print(f"TAR SHA-256：{digest(tar_path)}")
    print(f"ZIP SHA-256：{digest(zip_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
