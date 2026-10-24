#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
VERSION = "1.3.1-complete-20261024-r18-mapping6"
NAME = f"lingxi-v{VERSION}-deployment"
DIST = HERE / "dist" / "mapping6"
COMPONENTS = {
    "lingxi-v1.3.1-full-deployment-20261024-r18-mapping6.zip": ROOT / "lingxi_v1.3.1_deployment/dist/mapping6/lingxi-v1.3.1-full-deployment-20261024-r18-mapping6.zip",
    "sparkrobot-lingxi-kb-1.0.10-20261023.zip": ROOT / "knowledge_base_deployment/dist/sparkrobot-lingxi-kb-1.0.10-20261023.zip",
    "sparkrobot-lingxi-linkcraft-addon-1.0.3-20260902.tar.gz": ROOT / "linkcraft_deployment/dist/sparkrobot-lingxi-linkcraft-addon-1.0.3-20260902.tar.gz",
    "sparkrobot-lingxi-consecutive-nav-fix-1.0.8-20261024-r18.tar.gz": ROOT / "consecutive_nav_fix_deployment/dist/sparkrobot-lingxi-consecutive-nav-fix-1.0.8-20261024-r18.tar.gz",
}
BASE_BUNDLE_NAME = "lingxi-v1.3.1-full-deployment-20261024-r18-mapping6.zip"
BASE_BUNDLE_ROOT = "lingxi-v1.3.1-full-deployment-20261024-r18-mapping6"
EMBEDDED_COMPONENTS = (
    "aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip",
    "sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18-mapping6.tar.gz",
)
AIMDK_SCRIPT_MEMBER = f"{BASE_BUNDLE_ROOT}/scripts/robot/01-replace-aimdk.sh"
AIMDK_NOUNSET_GUARD = b"set +u\nsource /opt/ros/humble/setup.bash\nset -u"
AIMDK_GUIDE_SOURCE = ROOT / "已有AIMDK部署说明-v1.3.1-r18.md"


def write_text(path: Path, value: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=encoding, newline="\n") as handle:
        handle.write(value)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def safe_names(path: Path) -> None:
    opener = zipfile.ZipFile(path) if path.suffix == ".zip" else tarfile.open(path, "r:gz")
    try:
        members = opener.infolist() if isinstance(opener, zipfile.ZipFile) else opener.getmembers()
        for member in members:
            name = member.filename if isinstance(opener, zipfile.ZipFile) else member.name
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"组件包含不安全路径：{name}")
    finally:
        opener.close()


def validate_component(name: str, path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"缺少组件：{path}")
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if sidecar.is_file():
        expected = sidecar.read_text(encoding="ascii").split()[0].lower()
        actual = digest(path)
        if expected != actual:
            raise RuntimeError(f"组件 SHA-256 不一致：{name}")
    safe_names(path)
    if name.startswith("lingxi-v"):
        with zipfile.ZipFile(path) as archive:
            required = f"{BASE_BUNDLE_ROOT}/packages/{EMBEDDED_COMPONENTS[1]}"
            if required not in archive.namelist():
                raise RuntimeError("基础导航包缺少导航 TAR")
            aimdk_script = archive.read(AIMDK_SCRIPT_MEMBER).replace(b"\r\n", b"\n")
            if AIMDK_NOUNSET_GUARD not in aimdk_script:
                raise RuntimeError("基础导航包的 AIMDK 替换脚本缺少 ROS nounset 兼容修复")
    if name.startswith("sparkrobot-lingxi-kb"):
        with zipfile.ZipFile(path) as archive:
            root = "sparkrobot-lingxi-kb-1.0.10-20261023/"
            agent_member = f"{root}agent/robot-local-navigation-java-all-kb.jar"
            if agent_member not in archive.namelist():
                raise RuntimeError("知识库包缺少 Java Agent JAR")
            manifest = json.loads(archive.read(f"{root}manifest.json"))
            if manifest.get("version") != "1.0.10-20261023":
                raise RuntimeError("知识库包版本清单不正确")
            agent_bytes = archive.read(agent_member)
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
                if not required_classes.issubset(agent.namelist()):
                    raise RuntimeError("知识库 Java Agent 缺少交互拒识策略类")


    if name.startswith("sparkrobot-lingxi-consecutive-nav-fix"):
        with tarfile.open(path, "r:gz") as archive:
            prefix = "sparkrobot-lingxi-consecutive-nav-fix-1.0.8-20261024-r18/"
            required = {
                f"{prefix}install.sh",
                f"{prefix}control.sh",
                f"{prefix}payload/lingxi_nav_bridge.py",
                f"{prefix}payload/motion_option_policy.py",
                f"{prefix}payload/sdk_motion_controller.py",
                f"{prefix}payload/navigation_client.py",
                f"{prefix}payload/pnc_response.py",
                f"{prefix}payload/voice_preset_motion.py",
                f"{prefix}payload/point_catalog.py",
                f"{prefix}payload/tts_stream_protocol.py",
                f"{prefix}payload/tts_speaker.py",
                f"{prefix}payload/models.py",
                f"{prefix}payload/path_clearance.py",
            }
            names = {member.name for member in archive.getmembers() if member.isfile()}
            if not required.issubset(names):
                raise RuntimeError("连续导航修复包内容不完整")
            control = archive.extractfile(f"{prefix}control.sh")
            installer = archive.extractfile(f"{prefix}install.sh")
            control_text = control.read().decode("utf-8") if control else ""
            installer_text = installer.read().decode("utf-8") if installer else ""
            for marker in ("repair_runtime_permissions", "RUNTIME_GROUP", "resolve_bridge_run_user"):
                if marker not in control_text:
                    raise RuntimeError(f"连续导航修复包缺少权限保护：{marker}")
            if "repair_runtime_permissions" not in installer_text:
                raise RuntimeError("连续导航修复包安装器缺少权限修复")
            client_member = archive.extractfile(f"{prefix}payload/navigation_client.py")
            path_member = archive.extractfile(f"{prefix}payload/path_clearance.py")
            if client_member is None or path_member is None:
                raise RuntimeError("R17 连续导航修复包缺少路径净空模块")
            client_text = client_member.read().decode("utf-8")
            path_text = path_member.read().decode("utf-8")
            if (
                "msg.reference_line = reference_line" not in client_text
                or "def plan_reference_line" not in path_text
            ):
                raise RuntimeError("连续导航修复包没有保留 R17 净空实现")


def copy_stage(stage: Path) -> dict[str, str]:
    components = stage / "components"
    components.mkdir(parents=True)
    hashes = {}
    for name, source in COMPONENTS.items():
        validate_component(name, source)
        target = components / name
        shutil.copy2(source, target)
        hashes[name] = digest(target)
    base_bundle = components / BASE_BUNDLE_NAME
    with zipfile.ZipFile(base_bundle) as archive:
        for name in EMBEDDED_COMPONENTS:
            member = f"{BASE_BUNDLE_ROOT}/packages/{name}"
            checksum_member = f"{member}.sha256"
            expected = archive.read(checksum_member).decode("ascii").split()[0].lower()
            target = components / name
            with archive.open(member) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            actual = digest(target)
            if actual != expected:
                raise RuntimeError(f"基础套件内嵌组件 SHA-256 不一致：{name}")
            if name.endswith(".tar.gz"):
                safe_names(target)
                checksum_path = target.with_suffix(target.suffix + ".sha256")
                write_text(checksum_path, f"{expected}  {name}\n", encoding="ascii")
            hashes[name] = actual
            if name.endswith(".tar.gz"):
                hashes[checksum_path.name] = digest(checksum_path)
        aimdk_bytes = archive.read(
            f"{BASE_BUNDLE_ROOT}/packages/aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip"
        )
        if hashlib.sha256(aimdk_bytes).hexdigest() != "dc80fe5d2907881ed77beb230680867efac6bd28b458f83d2ff4e9479abe3615":
            raise RuntimeError("基础套件中的 AIMDK v1.1.0 SHA-256 不正确")
        with zipfile.ZipFile(io.BytesIO(aimdk_bytes)) as aimdk:
            request = aimdk.read(
                "aimdk-aarch64-faf4c945-artifacts/src/aimdk_msgs/interface/robot/task_engine/msg/PncTaskRequest.msg"
            )
            if b"reference_line" not in request or b"target_pose_radius" not in request:
                raise RuntimeError("AIMDK v1.1.0 缺少 R18 所需 PNC 字段")
    for name in ("README.md", "install_all.sh", "control_all.sh"):
        source = HERE / name
        text = source.read_text(encoding="utf-8")
        for old, new in (
            ("1.3.1-complete-20261024-r18", VERSION),
            ("v1.3.1-complete-deployment-20261024-r18", "v1.3.1-complete-deployment-20261024-r18-mapping6"),
            ("lingxi-v1.3.1-complete-20261024-r18-deployment", NAME),
            ("lingxi-v1.3.1-full-deployment-20261024-r18", BASE_BUNDLE_ROOT),
            ("sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18", "sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18-mapping6"),
        ):
            if new not in text:
                text = text.replace(old, new)
        write_text(stage / name, text)
    os.chmod(stage / "install_all.sh", 0o755)
    os.chmod(stage / "control_all.sh", 0o755)
    replacements = (
        ("1.3.1-complete-20261024-r18", VERSION),
        ("v1.3.1-complete-deployment-20261024-r18", "v1.3.1-complete-deployment-20261024-r18-mapping6"),
        ("lingxi-v1.3.1-complete-20261024-r18-deployment", NAME),
        ("lingxi-v1.3.1-full-deployment-20261024-r18", BASE_BUNDLE_ROOT),
        ("sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18", "sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18-mapping6"),
    )
    for source in sorted((HERE / "docs").rglob("*")):
        if source.is_dir():
            continue
        relative = source.relative_to(HERE / "docs")
        text = source.read_text(encoding="utf-8")
        for old, new in replacements:
            if new not in text:
                text = text.replace(old, new)
        write_text(stage / "docs" / relative, text)
    if not AIMDK_GUIDE_SOURCE.is_file():
        raise RuntimeError(f"缺少已有 AIMDK 部署说明：{AIMDK_GUIDE_SOURCE}")
    write_text(
        stage / "docs" / "已有AIMDK部署说明-v1.3.1-r18.md",
        f"套件版本：v{VERSION}\n\n" + AIMDK_GUIDE_SOURCE.read_text(encoding="utf-8")
        .replace("v1.3.1-complete-20261024-r18", VERSION)
        .replace(
            "lingxi-v1.3.1-complete-20261024-r18-deployment",
            NAME,
        )
        .replace(
            "integrated_deployment\\dist\\lingxi-v1.3.1-complete-20261024-r18-deployment.tar.gz",
            "integrated_deployment\\dist\\mapping6\\lingxi-v1.3.1-complete-20261024-r18-mapping6-deployment.tar.gz",
        )
        .replace(
            "sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18",
            "sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18-mapping6",
        ),
    )
    write_text(stage / "VERSION", f"LINGXI_COMPLETE_VERSION={VERSION}\n")
    return hashes


def write_metadata(stage: Path, hashes: dict[str, str]) -> None:
    manifest = {
        "format": "sparkrobot-lingxi-complete-deployment-v1",
        "version": VERSION,
        "architecture": "aarch64",
        "contains_real_secrets": False,
        "installation_order": [
            "prepare_environment",
            "replace_aimdk",
            "install_navigation",
            "install_linkcraft",
            "install_knowledge_base",
            "install_consecutive_navigation_fix_last",
        ],
        "components": hashes,
    }
    write_text(
        stage / "bundle-manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    lines = []
    for path in sorted(item for item in stage.rglob("*") if item.is_file()):
        if path.name != "SHA256SUMS.txt":
            lines.append(f"{digest(path)}  {path.relative_to(stage).as_posix()}")
    write_text(stage / "SHA256SUMS.txt", "\n".join(lines) + "\n")


def make_tar(stage: Path) -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    output = DIST / f"{NAME}.tar.gz"
    if output.exists():
        raise RuntimeError(f"输出已存在，不覆盖：{output}")
    with tarfile.open(output, "w:gz", compresslevel=6) as archive:
        archive.add(stage, arcname=NAME, recursive=True)
    return output


def checksum(path: Path) -> None:
    write_text(
        path.with_suffix(path.suffix + ".sha256"),
        f"{digest(path)}  {path.name}\n",
        encoding="ascii",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix=".lingxi-complete-", dir=HERE) as temp:
        stage = Path(temp) / NAME
        stage.mkdir()
        hashes = copy_stage(stage)
        write_metadata(stage, hashes)
        tar_path = make_tar(stage)
    checksum(tar_path)
    print(f"TAR：{tar_path}")
    print(f"TAR SHA-256：{digest(tar_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
