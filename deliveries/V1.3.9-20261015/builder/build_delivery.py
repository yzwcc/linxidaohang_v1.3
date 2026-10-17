from __future__ import annotations
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tarfile
import zipfile

HERE = Path(__file__).resolve().parent
DELIVERY = HERE.parent
MAIN = HERE.parents[2]
V139 = MAIN.with_name(MAIN.name + "-v1.3.9")
VERSION = "1.3.9-complete-20261015-r3"
NAME = f"lingxi-v{VERSION}-deployment"
NAV_VERSION = "1.0.21-20261015"
NAV_NAME = f"sparkrobot-lingxi-nav-internal-{NAV_VERSION}"
OLD_VERSION = "1.3.9-complete-20260923-r2"
JAVA_SHA = "c495fbe6f20c57656aa5d031b3a7e51e24cbeabbb22dfa48ac2254d97ffc241c"

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()

def text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.replace("\r\n", "\n"), encoding="utf-8", newline="\n")

def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)

def configs(target):
    for name in ("release_config.py", "backup_state.py", "verify_release.py"):
        copy(HERE / name, target / name)
    pnc = MAIN / "narrow_corridor_policy_deployment"
    for name in ("apply_policy.py", "policy.json", "control.sh"):
        copy(pnc / name, target / "pnc_policy" / name)

def inner():
    builder = load(V139 / "internal_deployment/build_internal_release.py", "internal_builder")
    stage = DELIVERY / ".build" / NAV_NAME
    if stage.exists() and "--resume" not in sys.argv:
        raise RuntimeError(f"构建目录已存在，先检查后改用新构建版本：{stage}")
    stage.mkdir(parents=True, exist_ok=True)
    jar = V139 / "internal_deployment/build/robot-local-navigation-java-all.jar"
    assert sha(jar) == JAVA_SHA
    builder.SOURCE_EXCLUDES.update({
        "unified_nav_config.json", "relocalization_result.json", "qa-store.json", ".env", ".env.local",
    })
    builder.build_payload(stage, jar)
    builder.copy_sources(stage)
    copy(V139 / "productization/build/linksoul-agentsdk-1.4.0.jar",
         stage / "source/java/linksoul-agentsdk-1.4.0-SNAPSHOT.jar")
    builder.copy_release_files(stage, NAV_VERSION)
    configs(stage / "scripts")
    profile = json.loads((stage / "templates/unified_nav_config.json").read_text(encoding="utf-8"))
    profile["navigation"]["dense_crowd"] = {
        "enabled": True, "target_pose_radius": 0.45, "position_tolerance_m": 0.45,
        "yaw_tolerance_rad": 0.5235987755982988, "stable_duration_sec": 1.0,
        "require_low_speed": True, "low_speed_threshold_mps": 0.08,
        "max_forward_speed": 0.30, "timeout_sec": 150.0,
    }
    profile["navigation"]["task_failure_policy"] = {
        "continue_after_failure": True, "max_retries_per_step": 1,
        "retry_delay_sec": 1.0, "announce_skip": False,
    }
    builder.write_json(stage / "templates/unified_nav_config.json", profile)
    copy(stage / "templates/unified_nav_config.json", stage / "source/navigation/unified_nav_config.json")
    # A source build must not retain the older Java/configuration baseline docs.
    for p in (stage / "docs").glob("*"):
        if p.is_file():
            p.unlink()
    for p in (HERE / "docs").glob("*.md"):
        copy(p, stage / "docs" / p.name)
    copy(HERE / "docs/V1.3.9-完整部署说明.md", stage / "docs/内部部署与命令手册.md")
    builder.build_manifests(stage)
    builder.audit(stage)
    builder.create_archives(stage, DELIVERY / "components", NAV_NAME, False)
    return stage

def outer():
    builder = load(V139 / "integrated_deployment/build_package.py", "outer_builder")
    stage = DELIVERY / NAME
    if stage.exists():
        raise RuntimeError(f"输出已存在：{stage}")
    stage.mkdir()
    components = stage / "components"
    components.mkdir()
    builder.copy_aimdk(components / builder.AIMDK_NAME)
    copy(DELIVERY / "components" / f"{NAV_NAME}.tar.gz", components / f"{NAV_NAME}.tar.gz")
    kb = V139 / "knowledge_base_deployment/dist" / builder.KB_NAME
    builder.validate_kb_component(kb)
    copy(kb, components / kb.name)
    builder.NAV_VERSION = NAV_VERSION
    builder.copy_robot_scripts(stage)
    # Fix privileged path resolution and preserve the previous AIMDK.
    check = stage / "scripts/03-verify-after-web-start.sh"
    s = check.read_text(encoding="utf-8").replace(
        "CURRENT=\"$(readlink -f /opt/sparkrobot/lingxi-nav/current)\"",
        "CURRENT=\"$(sudo readlink -f /opt/sparkrobot/lingxi-nav/current)\"")
    text(check, s)
    aimdk = stage / "scripts/01-replace-aimdk.sh"
    s = aimdk.read_text(encoding="utf-8").replace(
        'echo "新 AIMDK 已通过验证，开始无备份替换旧目录..."',
        'echo "新 AIMDK 已通过验证，备份并替换旧目录..."')
    s = s.replace('rm -rf -- "${OLD_ROOT}"\nmv -- "${NEW_ROOT}" "${OLD_ROOT}"',
        'if [[ -e "${OLD_ROOT}" ]]; then\n'
        '  AIMDK_BACKUP="/home/agi/aimdk-before-v139-$(date -u +%Y%m%dT%H%M%SZ).$$"\n'
        '  mv -- "${OLD_ROOT}" "${AIMDK_BACKUP}"\n'
        '  echo "原 AIMDK 备份：${AIMDK_BACKUP}"\nfi\n'
        'mv -- "${NEW_ROOT}" "${OLD_ROOT}"')
    text(aimdk, s)
    configs(stage / "scripts")
    copy(HERE / "install_all.sh", stage / "install_all.sh")
    control = (V139 / "integrated_deployment/control_all.sh").read_text(encoding="utf-8")
    control = control.replace(OLD_VERSION, VERSION).replace("1.0.20-20260923", NAV_VERSION)
    control = control.replace(
        '  echo "完整套件健康检查通过；未发送导航、重定位、动作、舞蹈或语音指令。"',
        '  python3 "${CURRENT}/scripts/verify_release.py"\n'
        '  echo "完整套件健康检查通过；未发送导航、重定位、动作、舞蹈或语音指令。"')
    control = control.replace(
        '  rollback)\n',
        '  backup) require_root; python3 "${CURRENT}/scripts/backup_state.py" backup ;;\n'
        '  backups) require_root; python3 "${CURRENT}/scripts/backup_state.py" list ;;\n'
        '  restore)\n'
        '    require_root\n'
        '    helper="$(mktemp /tmp/lingxi-restore.XXXXXX.py)"\n'
        '    cp "${CURRENT}/scripts/backup_state.py" "${helper}"\n'
        '    stop_all\n'
        '    python3 "${helper}" restore "${2:?需要完整备份目录}"\n'
        '    rm -f -- "${helper}"\n'
        '    systemctl daemon-reload\n'
        '    echo "配置、状态及版本链接已恢复。请按说明重启 PNC、网页和旧版业务服务。"\n'
        '    ;;\n'
        '  rollback)\n')
    control = control.replace(
        'logs [类型]|rollback [版本]',
        'logs [类型]|backup|backups|restore 备份目录|rollback [版本]')
    text(stage / "control_all.sh", control)
    for p in (HERE / "docs").glob("*.md"):
        copy(p, stage / "docs" / p.name)
    text(stage / "README.md", (
        f"# 灵犀 V1.3.9 完整整合安装包\n\n交付版本：`{VERSION}`；导航主包：`{NAV_VERSION}`。\n\n"
        "请先阅读 `docs/V1.3.9-完整部署说明.md` 和 `docs/V1.3.9-增量合并核对表.md`。\n\n"
        "本包可在恢复厂商系统的机器人上安装，全部已发现的 V1.3.9 增量功能已合入运行包或统一安装步骤，无需逐个补包。\n"
        "保留现有 Java/AgentSDK 语音与智能体架构，包含 AIMDK、ARM64 Node/JRE、网页、导航、Java、SDK、知识库服务、配置迁移和回退工具。\n"
        "环境准备会使用 apt 网络源；不是 Ubuntu/ROS/机器人固件镜像。灵创资源、地图和客户凭据需要现场准备。\n"
    ))
    for n in ("all-builds-inventory.json", "build-history-inventory.json"):
        copy(DELIVERY / n, stage / "audit" / n)
    text(stage / "VERSION", f"LINGXI_COMPLETE_VERSION={VERSION}\n")
    manifest = {
        "format": "sparkrobot-lingxi-complete-deployment-v3", "version": VERSION,
        "navigation_version": NAV_VERSION, "architecture": "aarch64",
        "voice_architecture": "existing-java-agentsdk-custom-agent",
        "contains_real_secrets": False, "baseline": OLD_VERSION,
        "components": {p.name: sha(p) for p in components.iterdir()},
        "java_sha256": JAVA_SHA,
        "merged_increments": [
            "tour-loop-persistence-1.0.0-20260927", "tour-arrival-fix-1.0.1-20260927",
            "dense-crowd-navigation-1.0.1-20260929", "tour-task-save-fix-1.0.2-20260929",
            "blocked-reminder-1.0.0-20260929", "narrow-corridor-policy-1.0.0-20260929",
            "action-failure-duplicate-skill-1.0.0-20260922",
        ],
        "configuration_effects": ["dense_crowd_enabled", "retry_once_then_skip",
                                  "legacy_skill_disabled_when_present", "a2_balanced_narrow",
                                  "managed_wake_dropins_backed_up_and_removed"],
        "validation": "见 audit/验证记录.md；未执行目标机器人安装或实机运动/语音验收",
    }
    text(stage / "bundle-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return stage

def refresh():
    """Refresh owned deployment helpers without re-copying third-party runtimes."""
    builder = load(V139 / "internal_deployment/build_internal_release.py", "internal_builder")
    inner_stage = DELIVERY / ".build" / NAV_NAME
    stage = DELIVERY / NAME
    if not (inner_stage / "release-info.json").is_file() or not (stage / "bundle-manifest.json").is_file():
        raise RuntimeError("需要先完成首次构建")
    configs(inner_stage / "scripts")
    copy(V139 / "productization/build/linksoul-agentsdk-1.4.0.jar",
         inner_stage / "source/java/linksoul-agentsdk-1.4.0-SNAPSHOT.jar")
    for p in (HERE / "docs").glob("*.md"):
        copy(p, inner_stage / "docs" / p.name)
        copy(p, stage / "docs" / p.name)
    copy(HERE / "docs/V1.3.9-完整部署说明.md", inner_stage / "docs/内部部署与命令手册.md")
    builder.build_manifests(inner_stage)
    builder.audit(inner_stage)
    builder.create_archives(inner_stage, DELIVERY / "components", NAV_NAME, True)
    copy(DELIVERY / "components" / f"{NAV_NAME}.tar.gz", stage / "components" / f"{NAV_NAME}.tar.gz")
    configs(stage / "scripts")
    env_script = stage / "scripts/00-install-environment.sh"
    content = env_script.read_text(encoding="utf-8")
    if "  curl openssh-client unzip python3 \\" not in content:
        content = content.replace("  p7zip-full \\", "  curl openssh-client unzip python3 \\\n  p7zip-full \\")
    text(env_script, content)
    control = stage / "control_all.sh"
    text(control, control.read_text(encoding="utf-8").replace(
        "logs [类型]|rollback [版本]", "logs [类型]|backup|backups|restore 备份目录|rollback [版本]"))
    manifest = json.loads((stage / "bundle-manifest.json").read_text(encoding="utf-8"))
    manifest["components"] = {p.name: sha(p) for p in (stage / "components").iterdir()}
    text(stage / "bundle-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print("Refreshed:", stage)

def finalize(stage):
    lines = [f"{sha(p)}  {p.relative_to(stage).as_posix()}" for p in sorted(stage.rglob("*"))
             if p.is_file() and p.name != "SHA256SUMS.txt"]
    text(stage / "SHA256SUMS.txt", "\n".join(lines) + "\n")
    tar_path, zip_path = DELIVERY / f"{NAME}.tar.gz", DELIVERY / f"{NAME}.zip"
    def normalize(info):
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        info.mode = 0o755 if info.isdir() or info.name.endswith(".sh") else 0o644
        return info
    with tarfile.open(tar_path, "w:gz", format=tarfile.PAX_FORMAT, compresslevel=6) as f:
        f.add(stage, arcname=NAME, filter=normalize)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as f:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                info = zipfile.ZipInfo(NAME + "/" + p.relative_to(stage).as_posix())
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | (0o755 if p.suffix == ".sh" else 0o644)) << 16
                f.writestr(info, p.read_bytes())
    for p in (tar_path, zip_path):
        text(p.with_suffix(p.suffix + ".sha256"), f"{sha(p)}  {p.name}\n")
        print(p, sha(p), p.stat().st_size)

if __name__ == "__main__":
    if "--refresh" in sys.argv:
        refresh()
    elif "--finalize" in sys.argv:
        finalize(DELIVERY / NAME)
    else:
        inner()
        stage = outer()
        print("Stage ready:", stage)
        print("Run audit_delivery.py before finalizing archives.")
