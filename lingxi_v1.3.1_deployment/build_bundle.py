#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
VERSION = "1.3.1"
RELEASE_TAG = "20261024-r18"
RELEASE_DATE = "2026-10-24"
BUNDLE_NAME = f"lingxi-v{VERSION}-full-deployment-{RELEASE_TAG}"

AIMDK_SOURCE = (
    Path.home()
    / "Downloads"
    / "aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip"
)
AIMDK_NAME = AIMDK_SOURCE.name
AIMDK_SHA256 = "dc80fe5d2907881ed77beb230680867efac6bd28b458f83d2ff4e9479abe3615"

NAV_NAME = "sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18.tar.gz"
NAV_SOURCE = ROOT / "internal_deployment" / "dist" / NAV_NAME

FULL_GUIDE_SOURCE = ROOT / "新机器人完整部署命令手册-AIMDK与灵犀导航-2026-09-01.md"
ACCEPTANCE_SOURCE = ROOT / "internal_deployment" / "docs" / "新机器人验收清单.md"
OUTPUT_DIR = HERE / "dist"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def require_file(path: Path, expected_sha256: str | None = None) -> None:
    if not path.is_file():
        raise RuntimeError(f"缺少构建输入：{path}")
    if expected_sha256 and digest(path) != expected_sha256:
        raise RuntimeError(f"文件校验失败：{path}")


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def build_stage(stage: Path) -> None:
    require_file(AIMDK_SOURCE, AIMDK_SHA256)
    require_file(NAV_SOURCE)
    require_file(FULL_GUIDE_SOURCE)
    require_file(ACCEPTANCE_SOURCE)
    nav_sha256 = digest(NAV_SOURCE)

    copy_file(HERE / "README-先看我.md", stage / "README-先看我.md")
    copy_file(HERE / "docs" / "组件版本与验收边界.md", stage / "docs" / "组件版本与验收边界.md")
    copy_file(ACCEPTANCE_SOURCE, stage / "docs" / "新机器人验收清单.md")
    copy_file(HERE / "templates" / "secrets.env.example", stage / "templates" / "secrets.env.example")
    shutil.copytree(HERE / "scripts", stage / "scripts")
    for shell_script in stage.rglob("*.sh"):
        shell_script.write_bytes(
            shell_script.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        )
    powershell_script = stage / "scripts" / "Windows-校验并上传全部文件.ps1"
    powershell_script.write_text(
        powershell_script.read_text(encoding="utf-8"),
        encoding="utf-8-sig",
        newline="\r\n",
    )

    guide_header = f"""# 灵犀 v{VERSION} 完整部署教程

套件版本：`v{VERSION}`  
套件发布日期：`2026-10-24`<br>
内含 AIMDK：`aimdk v1.1.0-gfaf4c94`
内含导航运行组件：`1.1.0-20261024-r18`

本文档是 v1.3.1 离线套件的完整教程。先按根目录《README-先看我》执行最短流程；需要逐步部署或排障时，按以下完整手册操作。

---

"""
    full_guide = FULL_GUIDE_SOURCE.read_text(encoding="utf-8")
    section_start = full_guide.index("## 6. 上传并更换 AIMDK")
    section_end = full_guide.index("## 7. 上传灵犀导航安装包", section_start)
    r18_aimdk_steps = """## 6. 上传并更换 AIMDK

完整包的 `prepare` 阶段会校验并复制 AIMDK v1.1.0 ZIP 与替换脚本。先按完整包 README 执行 `prepare`。如果设备当前 AIMDK 不是 v1.1.0，或契约检查未通过，登录 `agi` 并执行：

```bash
cd /home/agi
bash ./01-replace-aimdk.sh
```

脚本会核对 SHA-256 `dc80fe5d2907881ed77beb230680867efac6bd28b458f83d2ff4e9479abe3615`，安全解压并保留 ZIP 内的相对符号链接，在替换前完成编译、ROS 导入、PNC 消息字段和 `aimdk_msgs` 验证。编译/预检失败不会动旧目录。切换时旧 `/home/agi/aimdk` 会备份到 `/home/agi/aimdk-backups/`；干净 Shell 复验失败时会隔离新目录并恢复旧目录。

部署包附带的接口依据：

- PNC 请求 `/aima/te/pnc_task_request`：`BEST_EFFORT + VOLATILE`。
- PNC 响应 `/aima/pnc/pnc_task_response`：`BEST_EFFORT + TRANSIENT_LOCAL`。
- `PncTaskRequest` 保留 `reference_line`；START 的 `target_pose_radius` 最小有效值为 `0.25 m`，STOP 保持 `0.0`。

若当前 `/home/agi/aimdk/version` 已为 `aimdk v1.1.0-gfaf4c94`，且消息契约检查通过，可按“已有 AIMDK”说明跳过替换；旧版本即使可以导入，也不能跳过 R18 契约门槛。

"""
    full_guide = full_guide[:section_start] + r18_aimdk_steps + full_guide[section_end:]
    for old, new in (
        ("v1.3.1-complete-20261024-r17", "v1.3.1-complete-20261024-r18"),
        ("v1.3.1-complete-deployment-20261023-r17", "v1.3.1-complete-deployment-20261024-r18"),
        ("lingxi-v1.3.1-complete-20261024-r17", "lingxi-v1.3.1-complete-20261024-r18"),
        ("lingxi-v1.3.1-full-deployment-20261024-r17", "lingxi-v1.3.1-full-deployment-20261024-r18"),
        ("sparkrobot-lingxi-consecutive-nav-fix-1.0.7-20261024-r17", "sparkrobot-lingxi-consecutive-nav-fix-1.0.8-20261024-r18"),
        ("1.0.7-20261024-r17", "1.0.8-20261024-r18"),
        ("aimdk-aarch64-1bde262f-artifacts.7z", AIMDK_NAME),
        ("aimdk v0.0.0-g1bde262", "aimdk v1.1.0-gfaf4c94"),
        ("f13147a175bc3fb621fb118698aae0353c097adda38bba640f9a39c01433cc98", AIMDK_SHA256),
        ("1.0.8-20261023-r16", "1.1.0-20261024-r18"),
        ("1.0.9-20261024-r17", "1.1.0-20261024-r18"),
        ("20261023-r16", "20261024-r18"),
        ("20261024-r17", "20261024-r18"),
        ("1bde262f", "faf4c945"),
        ("1bde262", "faf4c94"),
        ("7z x", "unzip -q"),
        ("p7zip-full", "unzip"),
        ("command -v 7z", "command -v unzip"),
        ("7z", "unzip"),
        ("pnc_mode=0", "pnc_mode=0"),
    ):
        full_guide = full_guide.replace(old, new)
    full_guide = full_guide.replace(
        "| `8002` | 地图发现、预览、点位和任务配置 |",
        "| `8002` | 地图数据内部 API；用户不要直接访问此端口配置 |",
    )
    full_guide = (
        "所有地图、点位、任务和全局路径安全参数都只在机器人 `3000` 网页控制台配置。"
        "`8002` 和 `18082` 是内部服务端口，不是给用户操作的配置页面。\n\n"
        + full_guide
    )
    full_guide += (
        "\n\n地图、点位、任务及全局路径安全参数均只在机器人 `3000` 网页控制台配置。"
        "`8002` 和 `18082` 是内部服务端口，不是用户配置页面。\n"
        "\nAIMDK v1.1.0 导航接口：PNC 请求 QoS 为 BEST_EFFORT + VOLATILE，响应 QoS 为 "
        "BEST_EFFORT + TRANSIENT_LOCAL；START 的 target_pose_radius 不小于 0.25 m，"
        "STOP 请求保持 0。`reference_line` 字段保留。替换脚本会先完整编译并验证新 SDK，"
        "再保留旧 `/home/agi/aimdk` 备份并切换；健康检查失败会尝试恢复旧目录。\n"
        "\nR18 AIMDK 的 START 最小半径只约束 PNC 下发字段，不改变独立的导航到达 `position_tolerance_m`（默认仍为 0.15 m）。\n"
    )
    write_text(stage / "docs" / "灵犀-v1.3.1-完整部署教程.md", guide_header + full_guide)

    packages = stage / "packages"
    copy_file(AIMDK_SOURCE, packages / AIMDK_NAME)
    copy_file(NAV_SOURCE, packages / NAV_NAME)
    write_text(packages / f"{AIMDK_NAME}.sha256", f"{AIMDK_SHA256}  {AIMDK_NAME}\n")
    write_text(packages / f"{NAV_NAME}.sha256", f"{nav_sha256}  {NAV_NAME}\n")
    write_text(
        packages / "README.md",
        "# 安装组件\n\n"
        f"- `{AIMDK_NAME}`：AIMDK v1.1.0 ARM64 SDK 原始构建包。\n"
        f"- `{NAV_NAME}`：导航、地图、语音、网页和完整源码。\n\n"
        "不要重命名组件文件；自动上传和安装脚本会校验固定文件名与 SHA-256。\n",
    )

    manifest = {
        "format": "sparkrobot-lingxi-full-deployment-v1",
        "bundle_name": BUNDLE_NAME,
        "bundle_version": VERSION,
        "release_date": RELEASE_DATE,
        "architecture": "aarch64",
        "target": "Agibot X2 Ultra / Ubuntu 22.04 / ROS 2 Humble",
        "contains_real_secrets": False,
        "components": {
            "aimdk": {
                "version": "aimdk v1.1.0-gfaf4c94",
                "file": f"packages/{AIMDK_NAME}",
                "sha256": AIMDK_SHA256,
            },
            "lingxi_navigation": {
                "version": "1.1.0-20261024-r18",
                "file": f"packages/{NAV_NAME}",
                "sha256": nav_sha256,
                "includes_source": True,
            },
        },
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    write_text(stage / "bundle-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    write_text(stage / "VERSION", f"LINGXI_BUNDLE_VERSION={VERSION}\nRELEASE_DATE={RELEASE_DATE}\n")

    checksums = []
    for path in sorted(item for item in stage.rglob("*") if item.is_file()):
        if path.name == "SHA256SUMS.txt":
            continue
        checksums.append(f"{digest(path)}  {path.relative_to(stage).as_posix()}")
    write_text(stage / "SHA256SUMS.txt", "\n".join(checksums) + "\n")


def audit_stage(stage: Path) -> None:
    required = (
        "README-先看我.md",
        "VERSION",
        "bundle-manifest.json",
        "SHA256SUMS.txt",
        f"packages/{AIMDK_NAME}",
        f"packages/{NAV_NAME}",
        "scripts/Windows-校验并上传全部文件.ps1",
        "scripts/robot/00-install-environment.sh",
        "scripts/robot/01-replace-aimdk.sh",
        "scripts/robot/02-install-lingxi-navigation.sh",
        "scripts/robot/03-verify-after-web-start.sh",
        "scripts/robot/04-check-localization.sh",
        "templates/secrets.env.example",
        "docs/灵犀-v1.3.1-完整部署教程.md",
        "docs/新机器人验收清单.md",
    )
    for relative in required:
        if not (stage / relative).is_file():
            raise RuntimeError(f"套件缺少：{relative}")

    if digest(stage / "packages" / AIMDK_NAME) != AIMDK_SHA256:
        raise RuntimeError("套件内 AIMDK 校验失败")
    if digest(stage / "packages" / NAV_NAME) != digest(NAV_SOURCE):
        raise RuntimeError("套件内导航包校验失败")

    forbidden = ("sk-", "AKLT", "BEGIN OPENSSH PRIVATE KEY", "BEGIN RSA PRIVATE KEY")
    for path in stage.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".7z", ".gz", ".zip", ".jar"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(marker in text for marker in forbidden):
            raise RuntimeError(f"套件疑似包含真实秘密：{path.relative_to(stage)}")


def make_zip(stage: Path, output: Path) -> None:
    output.unlink(missing_ok=True)
    fixed_time = (2026, 9, 1, 0, 0, 0)
    with zipfile.ZipFile(output, "w", allowZip64=True) as archive:
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            relative = PurePosixPath(BUNDLE_NAME) / PurePosixPath(path.relative_to(stage).as_posix())
            info = zipfile.ZipInfo(relative.as_posix(), date_time=fixed_time)
            executable = path.suffix.lower() in {".sh", ".ps1"}
            info.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
            compressed_asset = path.suffix.lower() in {".7z", ".gz", ".zip", ".jar"}
            info.compress_type = zipfile.ZIP_STORED if compressed_asset else zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes(), compresslevel=None if compressed_asset else 6)


def verify_zip(output: Path) -> None:
    with zipfile.ZipFile(output) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP 成员损坏：{bad}")
        names = set(archive.namelist())
        prefix = f"{BUNDLE_NAME}/"
        for relative in ("README-先看我.md", "SHA256SUMS.txt", f"packages/{AIMDK_NAME}", f"packages/{NAV_NAME}"):
            if prefix + relative not in names:
                raise RuntimeError(f"ZIP 缺少：{relative}")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / f"{BUNDLE_NAME}.zip"
    with tempfile.TemporaryDirectory(prefix=".lingxi-v131-", dir=OUTPUT_DIR) as temp:
        stage = Path(temp) / BUNDLE_NAME
        stage.mkdir()
        build_stage(stage)
        audit_stage(stage)
        make_zip(stage, output)
    verify_zip(output)
    archive_sha256 = digest(output)
    write_text(output.with_suffix(output.suffix + ".sha256"), f"{archive_sha256}  {output.name}\n")
    print(f"部署包：{output}")
    print(f"大小：{output.stat().st_size} bytes")
    print(f"SHA-256：{archive_sha256}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"构建失败：{exc}", file=os.sys.stderr)
        raise SystemExit(1)
