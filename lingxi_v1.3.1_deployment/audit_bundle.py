#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


HERE = Path(__file__).resolve().parent
NAME = "lingxi-v1.3.1-full-deployment-20261024-r18-mapping6"
ARCHIVE = HERE / "dist" / "mapping6" / f"{NAME}.zip"
SIDECAR = ARCHIVE.with_suffix(ARCHIVE.suffix + ".sha256")
PREFIX = f"{NAME}/"
AIMDK = "packages/aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip"
AIMDK_SHA256 = "dc80fe5d2907881ed77beb230680867efac6bd28b458f83d2ff4e9479abe3615"
SDK_ROOT = "aimdk-aarch64-faf4c945-artifacts"
NAV_VERSION = "1.1.0-20261024-r18-mapping6"
NAV_ROOT = f"sparkrobot-lingxi-nav-internal-{NAV_VERSION}"
NAV = f"packages/{NAV_ROOT}.tar.gz"


def stream_digest(handle) -> str:
    value = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        value.update(chunk)
    return value.hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as handle:
        return stream_digest(handle)


def main() -> int:
    if not ARCHIVE.is_file() or not SIDECAR.is_file():
        raise RuntimeError("缺少最终 ZIP 或 SHA-256 文件")
    expected_archive_hash = SIDECAR.read_text(encoding="ascii").split()[0]
    if file_digest(ARCHIVE) != expected_archive_hash:
        raise RuntimeError("最终 ZIP 的 SHA-256 不一致")

    with zipfile.ZipFile(ARCHIVE) as archive:
        infos = archive.infolist()
        for info in infos:
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"ZIP 包含不安全路径：{info.filename}")
        names = {info.filename for info in infos}
        required = {
            PREFIX + "README-先看我.md",
            PREFIX + "VERSION",
            PREFIX + "bundle-manifest.json",
            PREFIX + "SHA256SUMS.txt",
            PREFIX + AIMDK,
            PREFIX + NAV,
            PREFIX + "scripts/Windows-校验并上传全部文件.ps1",
            PREFIX + "scripts/robot/00-install-environment.sh",
            PREFIX + "scripts/robot/01-replace-aimdk.sh",
            PREFIX + "scripts/robot/02-install-lingxi-navigation.sh",
            PREFIX + "scripts/robot/03-verify-after-web-start.sh",
            PREFIX + "scripts/robot/04-check-localization.sh",
            PREFIX + "docs/灵犀-v1.3.1-完整部署教程.md",
            PREFIX + "docs/新机器人验收清单.md",
            PREFIX + "templates/secrets.env.example",
        }
        missing = sorted(required - names)
        if missing:
            raise RuntimeError("ZIP 缺少文件：" + ", ".join(missing))

        aimdk_bytes = archive.read(PREFIX + AIMDK)
        if hashlib.sha256(aimdk_bytes).hexdigest() != AIMDK_SHA256:
            raise RuntimeError("AIMDK v1.1.0 SHA-256 不一致")
        with zipfile.ZipFile(io.BytesIO(aimdk_bytes)) as sdk:
            sdk_names = {item.filename for item in sdk.infolist() if item.filename}
            sdk_roots = {
                PurePosixPath(name).parts[0]
                for name in sdk_names
                if PurePosixPath(name).parts
            }
            if sdk_roots != {SDK_ROOT}:
                raise RuntimeError("AIMDK SDK ZIP 根目录不正确")
            request_member = (
                f"{SDK_ROOT}/src/aimdk_msgs/interface/robot/task_engine/msg/PncTaskRequest.msg"
            )
            if request_member not in sdk_names:
                raise RuntimeError("AIMDK v1.1.0 缺少 PncTaskRequest 接口定义")
            request_definition = sdk.read(request_member)
            if b"reference_line" not in request_definition or b"target_pose_radius" not in request_definition:
                raise RuntimeError("AIMDK v1.1.0 缺少路径或目标半径字段")
            nav_doc = sdk.read(
                f"{SDK_ROOT}/docs/cn/_build/html/zh-cn/dev/Interface/perception/navigation.html"
            ).decode("utf-8", "replace")
            for marker in ("BEST_EFFORT", "VOLATILE", "TRANSIENT_LOCAL", "0.25"):
                if marker not in nav_doc:
                    raise RuntimeError(f"AIMDK v1.1.0 导航接口文档缺少 {marker}")

        manifest = json.loads(archive.read(PREFIX + "bundle-manifest.json"))
        if manifest["bundle_version"] != "1.3.1" or manifest["contains_real_secrets"]:
            raise RuntimeError("套件版本或秘密标记不正确")
        nav_manifest = manifest["components"]["lingxi_navigation"]
        if nav_manifest["version"] != NAV_VERSION:
            raise RuntimeError("导航组件版本不是 R18 mapping6")
        aimdk_manifest = manifest["components"]["aimdk"]
        if (
            aimdk_manifest.get("version") != "aimdk v1.1.0-gfaf4c94"
            or aimdk_manifest.get("file") != AIMDK
            or aimdk_manifest.get("sha256") != AIMDK_SHA256
        ):
            raise RuntimeError("AIMDK v1.1.0 清单信息不正确")
        guide_text = archive.read(
            PREFIX + "docs/灵犀-v1.3.1-完整部署教程.md"
        ).decode("utf-8")
        if "1.0.8-20261023-r16" in guide_text or "20261023-r16" in guide_text:
            raise RuntimeError("完整部署教程仍引用 R16 导航版本")
        if "8002` 和 `18082` 是内部服务端口" not in guide_text:
            raise RuntimeError("完整部署教程没有明确内部端口不是用户配置入口")

        with archive.open(PREFIX + NAV) as nav_handle:
            nav_sha256 = stream_digest(nav_handle)
        if nav_manifest["sha256"] != nav_sha256:
            raise RuntimeError("导航组件版本或 SHA-256 不正确")

        readme = archive.read(PREFIX + "README-先看我.md").decode("utf-8")
        if "地图、点位、任务及全局路径安全参数均只在机器人 `3000` 网页控制台配置" not in readme:
            raise RuntimeError("套件说明没有明确 3000 是唯一用户配置入口")

        checksum_lines = archive.read(PREFIX + "SHA256SUMS.txt").decode("utf-8").splitlines()
        for line in checksum_lines:
            expected, relative = line.split("  ", 1)
            member = PREFIX + relative
            if member not in names:
                raise RuntimeError(f"校验清单成员不存在：{relative}")
            with archive.open(member) as handle:
                if stream_digest(handle) != expected:
                    raise RuntimeError(f"成员 SHA-256 不一致：{relative}")

        ps1 = archive.read(PREFIX + "scripts/Windows-校验并上传全部文件.ps1")
        if not ps1.startswith(b"\xef\xbb\xbf"):
            raise RuntimeError("PowerShell 脚本缺少 UTF-8 BOM")
        powershell = shutil.which("powershell") or shutil.which("powershell.exe")
        if powershell:
            with tempfile.TemporaryDirectory(prefix="lingxi-v131-audit-") as temp:
                script_path = Path(temp) / "upload.ps1"
                script_path.write_bytes(ps1)
                parser_command = (
                    "& { param([string]$Target) "
                    "$errors = $null; "
                    "[System.Management.Automation.Language.Parser]::ParseFile($Target, [ref]$null, [ref]$errors) | Out-Null; "
                    "if ($errors.Count) { $errors | Format-List; exit 1 } "
                    "}"
                )
                subprocess.run(
                    [powershell, "-NoProfile", "-Command", parser_command, str(script_path)],
                    check=True,
                )

        nav_required = {
            f"{NAV_ROOT}/payload/console/dist/public/index.html",
            f"{NAV_ROOT}/payload/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py",
            f"{NAV_ROOT}/payload/navigation/robot_local_navigation_v3/path_clearance.py",
            f"{NAV_ROOT}/payload/navigation/robot_local_navigation_v3/models.py",
            f"{NAV_ROOT}/payload/navigation/robot_local_navigation_v3/navigation_client.py",
            f"{NAV_ROOT}/source/navigation/robot_local_navigation_v3/path_clearance.py",
            f"{NAV_ROOT}/source/navigation/robot_local_navigation_v3/navigation_client.py",
            f"{NAV_ROOT}/source/navigation/test/test_path_clearance.py",
            f"{NAV_ROOT}/source/navigation/test/test_pnc_discovery.py",
            f"{NAV_ROOT}/source/navigation/test/test_aimdk_110_contract.py",
            f"{NAV_ROOT}/payload/java/robot-local-navigation-java-all.jar",
            f"{NAV_ROOT}/source/navigation/setup.py",
            f"{NAV_ROOT}/source/java/pom.xml",
            f"{NAV_ROOT}/source/server/package.json",
            f"{NAV_ROOT}/install.sh",
        }
        nav_names: set[str] = set()
        with archive.open(PREFIX + NAV) as nav_handle:
            with tarfile.open(fileobj=nav_handle, mode="r|gz") as nav_archive:
                for member in nav_archive:
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or ".." in path.parts:
                        raise RuntimeError(f"导航 TAR 包含不安全路径：{member.name}")
                    nav_names.add(member.name)
        nav_missing = sorted(nav_required - nav_names)
        if nav_missing:
            raise RuntimeError("导航组件缺少运行文件或源码：" + ", ".join(nav_missing))

        with archive.open(PREFIX + NAV) as nav_handle:
            with tarfile.open(fileobj=nav_handle, mode="r:gz") as nav_archive:
                html = nav_archive.extractfile(
                    f"{NAV_ROOT}/payload/console/dist/public/index.html"
                )
                app = nav_archive.extractfile(
                    f"{NAV_ROOT}/payload/console/dist/public/app.js"
                )
                if html is None or app is None:
                    raise RuntimeError("導航包缺少 3000 控制台资源")
                html_text = html.read().decode("utf-8")
                app_text = app.read().decode("utf-8")
                if not all(
                    marker in html_text
                    for marker in (
                        'id="pointTaskType"',
                        'id="robotRadius" form="pathSafetyForm"',
                        'id="extraClearance" form="pathSafetyForm"',
                    )
                ):
                    raise RuntimeError("3000 点位页面缺少全局路径安全参数")
                if "R17 将拒绝下发 task_type=2" not in app_text:
                    raise RuntimeError("3000 页面路径安全提示版本错误")
                mapping_controller = nav_archive.extractfile(
                    f"{NAV_ROOT}/payload/console/dist/mappingControl.js"
                )
                if mapping_controller is None:
                    raise RuntimeError("R18 网页建图控制器缺失")
                mapping_text = mapping_controller.read().decode("utf-8")
                for marker in (
                    "GetStoredMapByName",
                    "/integrated_command",
                    "TRANSIENT_LOCAL",
                    "RELIABLE",
                    "get_subscription_count",
                    "mapping-session.json",
                    "awaiting_save",
                ):
                    if marker not in mapping_text:
                        raise RuntimeError(f"R18 网页建图缺少 AIMDK/会话契约：{marker}")
                if "task_type" in mapping_text or "PncTaskRequest" in mapping_text:
                    raise RuntimeError("网页建图控制器包含非预期的机器人移动/PNC 请求")
                nav_client = nav_archive.extractfile(
                    f"{NAV_ROOT}/payload/navigation/robot_local_navigation_v3/navigation_client.py"
                )
                nav_models = nav_archive.extractfile(
                    f"{NAV_ROOT}/payload/navigation/robot_local_navigation_v3/models.py"
                )
                if nav_client is None or nav_models is None:
                    raise RuntimeError("导航包缺少 R18 AIMDK 适配代码")
                nav_client_text = nav_client.read().decode("utf-8")
                nav_models_text = nav_models.read().decode("utf-8")
                for marker in (
                    "QoSDurabilityPolicy.VOLATILE",
                    "QoSDurabilityPolicy.TRANSIENT_LOCAL",
                    "MIN_TARGET_POSE_RADIUS",
                    "msg.target_pose_radius = 0.0",
                ):
                    if marker not in nav_client_text:
                        raise RuntimeError(f"R18 导航客户端缺少 AIMDK 契约：{marker}")
                if "DEFAULT_ARRIVAL_POSITION_TOLERANCE_M = 0.15" not in nav_models_text:
                    raise RuntimeError("AIMDK 半径下限错误影响了导航到达判定默认值")

    print("BUNDLE_AUDIT_OK")
    print(f"archive={ARCHIVE}")
    print(f"sha256={expected_archive_hash}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        OSError,
        RuntimeError,
        KeyError,
        ValueError,
        subprocess.CalledProcessError,
        zipfile.BadZipFile,
        tarfile.TarError,
    ) as exc:
        print(f"BUNDLE_AUDIT_FAILED: {exc}")
        raise SystemExit(1)
