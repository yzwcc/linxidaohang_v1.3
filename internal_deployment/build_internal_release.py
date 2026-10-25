#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "internal_deployment"
PRODUCT_ID = "sparkrobot-lingxi-nav-internal"
DEFAULT_VERSION = "1.1.0-20261025-r18-live-mapfit5"
FIELD_VERIFIED_JAVA_SHA256 = "d3858827d71aa54dc4b7cc794a0d8446cf0168d65e4cca0a2e686b6f7012dd04"
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DEV_NODE_MODULES = {
    ".bin", "@esbuild", "@jridgewell", "@rollup", "@types", "@vitest",
    "typescript", "tsx", "vite", "vite-node", "vitest",
}
SKIP_DIRS = {
    ".git", ".github", ".deps", ".vite", "__pycache__", "benchmark",
    "benchmarks", "coverage", "example", "examples", "obj.target",
}
RUNTIME_SUFFIXES = {".js", ".cjs", ".mjs", ".json", ".node", ".wasm"}
SOURCE_EXCLUDES = {".git", "node_modules", "target", "dist", "__pycache__", "pids", "maps"}
SECRET_PATTERNS = (
    re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"AKLT[A-Za-z0-9_-]{12,}"),
    re.compile(rb"Bearer\s+eyJ[A-Za-z0-9._-]{20,}", re.I),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{20,}"),
)


def run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def copy_file(source: Path, target: Path, mode: int | None = None) -> None:
    if not source.is_file():
        raise RuntimeError(f"缺少构建输入：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if target.suffix.lower() == ".sh":
        target.write_bytes(target.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    if mode is not None:
        target.chmod(mode)


def copy_tree(source: Path, target: Path, excludes: set[str] | None = None) -> None:
    if not source.is_dir():
        raise RuntimeError(f"缺少构建目录：{source}")
    excludes = excludes or set()
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*sorted(excludes)) if excludes else None,
    )
    for script in target.rglob("*.sh"):
        script.write_bytes(script.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))


def copy_runtime_dependencies(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise RuntimeError(f"缺少 Node.js 依赖：{source}")
    for current, directories, files in os.walk(source):
        current_path = Path(current)
        relative = current_path.relative_to(source)
        if relative.parts and relative.parts[0] in DEV_NODE_MODULES:
            directories[:] = []
            continue
        directories[:] = [
            name for name in directories
            if name not in DEV_NODE_MODULES and name.lower() not in SKIP_DIRS
        ]
        for name in files:
            item = current_path / name
            if item.is_symlink() or item.suffix.lower() not in RUNTIME_SUFFIXES:
                continue
            copy_file(item, target / relative / name)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def package_mode(relative: PurePosixPath) -> int:
    value = relative.as_posix()
    if value.endswith(".sh") or value in {"install.sh", "configure.sh", "control.sh", "quick_install.sh"}:
        return 0o755
    if "/runtime/node" in value or "/runtime/java/bin/" in value or value.endswith("/lib/jspawnhelper"):
        return 0o755
    return 0o644


def build_payload(stage: Path, java_jar: Path) -> None:
    server = ROOT / "server"
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise RuntimeError("构建机缺少 npm")
    run([npm, "run", "build"], cwd=server)

    payload = stage / "payload"
    copy_tree(server / "dist", payload / "console" / "dist")
    copy_file(server / "package.json", payload / "console" / "package.json")
    copy_file(server / "package-lock.json", payload / "console" / "package-lock.json")
    copy_runtime_dependencies(server / "node_modules", payload / "console" / "node_modules")
    copy_file(ROOT / "productization" / "build" / "node-arm64", payload / "runtime" / "node", 0o755)
    copy_tree(ROOT / "productization" / "build" / "jre17-arm64", payload / "runtime" / "java")

    nav_source = ROOT / "robot_local_navigation_v3" / "robot_local_navigation_v3-new-auto-return-interaction-end"
    navigation = payload / "navigation"
    for name in ("robot_local_navigation_v3", "resource", "python_bridge"):
        copy_tree(nav_source / name, navigation / name, {"__pycache__"})
    for name in ("setup.py", "package.xml", "ros_dds_configuration.xml"):
        copy_file(nav_source / name, navigation / name)
    copy_file(
        ROOT / "deploy" / "robot" / "robot_lingxi_bridge.py",
        navigation / "robot_local_navigation_v3" / "robot_lingxi_bridge.py",
    )

    copy_file(java_jar, payload / "java" / "robot-local-navigation-java-all.jar")
    copy_file(
        ROOT / "productization" / "build" / "linksoul-agentsdk-1.4.0.jar",
        payload / "java" / "linksoul-agentsdk-1.4.0.jar",
    )


def copy_sources(stage: Path) -> None:
    source = stage / "source"
    nav = ROOT / "robot_local_navigation_v3" / "robot_local_navigation_v3-new-auto-return-interaction-end"
    java = ROOT / "robot_local_nav_java" / "robot_local_nav_java-new-java-interaction-end-hook"
    server = ROOT / "server"
    copy_tree(nav, source / "navigation", SOURCE_EXCLUDES)
    copy_tree(java / "src", source / "java" / "src")
    copy_file(java / "pom.xml", source / "java" / "pom.xml")
    for name in ("src", "public", "scripts"):
        copy_tree(server / name, source / "server" / name, {"__pycache__"})
    for name in ("package.json", "package-lock.json", "tsconfig.json"):
        copy_file(server / name, source / "server" / name)


def copy_release_files(stage: Path, version: str) -> None:
    for name in ("install.sh", "configure.sh", "control.sh", "quick_install.sh"):
        copy_file(HERE / "runtime" / name, stage / name, 0o755)
    copy_tree(HERE / "runtime" / "scripts", stage / "scripts")
    copy_tree(HERE / "runtime" / "systemd", stage / "systemd")
    copy_tree(HERE / "runtime" / "templates", stage / "templates")
    copy_tree(HERE / "docs", stage / "docs")
    for path in stage.rglob("*.sh"):
        path.chmod(0o755)
    write_json(stage / "release-info.json", {
        "format": "sparkrobot-internal-plaintext-release-v1",
        "product_id": PRODUCT_ID,
        "version": version,
        "architecture": "aarch64",
        "encrypted": False,
        "device_bound": False,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    })


def build_manifests(stage: Path) -> None:
    payload_files = []
    for path in sorted(item for item in (stage / "payload").rglob("*") if item.is_file()):
        payload_files.append({
            "path": path.relative_to(stage / "payload").as_posix(),
            "sha256": digest(path),
            "size": path.stat().st_size,
        })
    write_json(stage / "payload-manifest.json", {"format": "sparkrobot-internal-payload-v1", "files": payload_files})

    lines = []
    for path in sorted(item for item in stage.rglob("*") if item.is_file() and item.name != "SHA256SUMS"):
        lines.append(f"{digest(path)}  {path.relative_to(stage).as_posix()}")
    (stage / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def audit(stage: Path) -> None:
    required = (
        "install.sh", "configure.sh", "control.sh", "quick_install.sh", "payload-manifest.json",
        "payload/runtime/node", "payload/runtime/java/bin/java",
        "payload/console/dist/index.js",
        "payload/console/dist/public/index.html",
        "payload/console/dist/public/app.js",
        "payload/console/dist/mappingControl.js",
        "payload/console/dist/robotClient.js",
        "payload/console/dist/public/index.html",
        "payload/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py",
        "payload/navigation/robot_local_navigation_v3/arrival_hysteresis.py",
        "payload/navigation/robot_local_navigation_v3/path_clearance.py",
        "payload/navigation/robot_local_navigation_v3/models.py",
        "payload/navigation/robot_local_navigation_v3/navigation_client.py",
        "source/navigation/robot_local_navigation_v3/path_clearance.py",
        "source/navigation/robot_local_navigation_v3/navigation_client.py",
        "source/navigation/test/test_path_clearance.py",
        "source/navigation/test/test_pnc_discovery.py",
        "source/navigation/test/test_aimdk_110_contract.py",
        "source/navigation/test/test_map_label_path_safety_settings.py",
        "source/navigation/test/test_r18_voice_bridge_sdk_optional.py",
        "payload/navigation/robot_local_navigation_v3/robot_lingxi_bridge.py",
        "payload/java/robot-local-navigation-java-all.jar",
        "payload/java/linksoul-agentsdk-1.4.0.jar",
        "scripts/robot_local_navigation_v3.sh",
        "scripts/navigation_control.sh",
        "scripts/restart_robot_voice_stack.sh",
        "scripts/java_agent_control.sh",
        "scripts/lingxi_java_agent_control.sh",
        "docs/内部部署与命令手册.md",
    )
    for relative in required:
        if not (stage / relative).is_file():
            raise RuntimeError(f"发布包缺少：{relative}")
    if (stage / "payload" / "java" / "robot-local-navigation-java-all.jar").read_bytes()[:4] != b"PK\x03\x04":
        raise RuntimeError("Java JAR 格式无效")
    path_clearance = (
        stage / "payload" / "navigation" / "robot_local_navigation_v3" / "path_clearance.py"
    ).read_text(encoding="utf-8")
    models = (
        stage / "payload" / "navigation" / "robot_local_navigation_v3" / "models.py"
    ).read_text(encoding="utf-8")
    navigation_client = (
        stage / "payload" / "navigation" / "robot_local_navigation_v3" / "navigation_client.py"
    ).read_text(encoding="utf-8")
    path_clearance_tests = (
        stage / "source" / "navigation" / "test" / "test_path_clearance.py"
    ).read_text(encoding="utf-8")
    pnc_discovery_tests = (
        stage / "source" / "navigation" / "test" / "test_pnc_discovery.py"
    ).read_text(encoding="utf-8")
    aimdk_contract_tests = (
        stage / "source" / "navigation" / "test" / "test_aimdk_110_contract.py"
    ).read_text(encoding="utf-8")
    path_safety_settings_tests = (
        stage / "source" / "navigation" / "test" / "test_map_label_path_safety_settings.py"
    ).read_text(encoding="utf-8")
    console_html = (
        stage / "payload" / "console" / "dist" / "public" / "index.html"
    ).read_text(encoding="utf-8")
    console_app = (
        stage / "payload" / "console" / "dist" / "public" / "app.js"
    ).read_text(encoding="utf-8")
    mapping_controller = (
        stage / "payload" / "console" / "dist" / "mappingControl.js"
    ).read_text(encoding="utf-8")
    if any(marker not in mapping_controller for marker in (
        "GetStoredMapByName",
        "/integrated_command",
        "TRANSIENT_LOCAL",
        "RELIABLE",
        "get_subscription_count",
        "mapping-session.json",
        "awaiting_save",
    )):
        raise RuntimeError("R18 控制台建图接口缺少 AIMDK v1.1.0 合约")
    if "data-view=\"mapping\"" not in console_html or "外置遥控器" not in console_html:
        raise RuntimeError("R18 控制台缺少建图工作区或外置遥控安全提示")
    mapping_controller_source = (
        stage / "source" / "server" / "src" / "mappingControl.ts"
    ).read_text(encoding="utf-8")
    if '"can_start"' not in mapping_controller and 'can_start: canStart' not in mapping_controller_source:
        raise RuntimeError("R18 建图控制器缺少启动条件与实时里程计输出分离逻辑")
    robot_client = (
        stage / "payload" / "console" / "dist" / "robotClient.js"
    ).read_text(encoding="utf-8")
    bridge = (
        stage / "payload" / "navigation" / "robot_local_navigation_v3" / "lingxi_nav_bridge.py"
    ).read_text(encoding="utf-8")
    voice_bridge = (
        stage / "payload" / "navigation" / "robot_local_navigation_v3" / "robot_lingxi_bridge.py"
    ).read_text(encoding="utf-8")
    voice_bridge_control = (stage / "scripts" / "robot_voice_bridge_control.sh").read_text(encoding="utf-8")
    for marker in (
        "try:\n    from aimdk_msgs.srv import PlayMediaFile",
        "except ImportError:\n    PlayMediaFile = None",
        "if PlayMediaFile is not None",
        '"media_service_unsupported_by_sdk"',
    ):
        if marker not in voice_bridge:
            raise RuntimeError(f"R18 语音桥接未对可选 PlayMediaFile SDK 接口安全降级：{marker}")
    if "if robot_lingxi_bridge.PlayMediaFile is None" not in voice_bridge_control:
        raise RuntimeError("语音 bridge 预检必须识别缺少 PlayMediaFile 但允许其余功能启动")
    voice_bridge_test = (
        stage / "source" / "navigation" / "test" / "test_r18_voice_bridge_sdk_optional.py"
    ).read_text(encoding="utf-8")
    if "test_sdk_without_play_media_file_imports_bridge_capability_as_unavailable" not in voice_bridge_test:
        raise RuntimeError("R18 缺少 AIMDK 不含 PlayMediaFile 时的启动兼容性回归")
    for marker in ("class PathClearanceError", "def plan_reference_line", "inflate_obstacles"):
        if marker not in path_clearance:
            raise RuntimeError(f"安全路径模块缺少实现：{marker}")
    if "class PathSafetyConfig" not in models:
        raise RuntimeError("导航模型缺少安全路径配置：PathSafetyConfig")
    for marker in (
        "reference_line",
        "path_safety",
        "point.navigation.task_type == 2 and point.navigation.pnc_mode == 0",
        "if not hasattr(msg, \"reference_line\")",
        "msg.reference_line = reference_line",
    ):
        if marker not in navigation_client:
            raise RuntimeError(f"导航客户端缺少安全参考线实现：{marker}")
    if "test_reference_line_detours_around_inflated_static_obstacle" not in path_clearance_tests:
        raise RuntimeError("导航源码缺少静态障碍净空绕行测试")
    if "test_start_request_contains_the_planned_reference_line" not in pnc_discovery_tests:
        raise RuntimeError("导航源码缺少 reference_line 请求测试")
    for marker in (
        "test_pnc_request_uses_best_effort_volatile_qos",
        "test_pnc_response_uses_best_effort_transient_local_qos",
        "test_start_radius_is_clamped_to_sdk_minimum",
        "test_stop_radius_remains_zero",
        "aimdk v1.1.0-gfaf4c94",
    ):
        if marker not in aimdk_contract_tests:
            raise RuntimeError(f"R18 AIMDK 契約測試缺少：{marker}")
    if "radius_cells = (config.robot_radius_m + config.extra_clearance_m)" not in path_clearance:
        raise RuntimeError("R17 地图障碍膨胀未使用页面实测半径和额外净空")
    request_qos = """cmd_qos = QoSProfile(
            depth=10,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            durability=QoSDurabilityPolicy.VOLATILE,
        )"""
    response_qos = """response_qos = QoSProfile(
                depth=10,
                reliability=QoSReliabilityPolicy.BEST_EFFORT,
                durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            )"""
    if request_qos not in navigation_client:
        raise RuntimeError("R18 PNC 请求 QoS 与 AIMDK 1.1.0 契约不一致")
    if response_qos not in navigation_client:
        raise RuntimeError("R18 PNC 响应 QoS 与 AIMDK 1.1.0 契约不一致")
    if "MIN_TARGET_POSE_RADIUS" not in models or "max(\n            MIN_TARGET_POSE_RADIUS" not in navigation_client:
        raise RuntimeError("R18 START 请求未落实 AIMDK 最小目标半径")
    if "msg.target_pose_radius = 0.0" not in navigation_client:
        raise RuntimeError("R18 STOP 请求必须保留 target_pose_radius=0")
    for marker in (
        "test_configured_radius_and_extra_clearance_move_route_farther_from_obstacle",
        "self.assertGreater(increased_clearance_distance, baseline_distance + 1.0)",
    ):
        if marker not in path_clearance_tests:
            raise RuntimeError(f"R17 半径/净空数值规划测试缺少：{marker}")
    for marker in (
        "test_saves_global_path_safety_and_reports_bridge_application",
        "test_get_settings_reports_persisted_and_runtime_values_separately",
        "test_bridge_application_requires_exact_runtime_readback",
        '"robot_radius_m": 0.253',
        '"extra_clearance_m": 0.18',
    ):
        if marker not in path_safety_settings_tests:
            raise RuntimeError(f"R17 3000 设置落盘/运行回读测试缺少：{marker}")
    for marker in (
        'id="pointTaskType"',
        'id="robotRadius" form="pathSafetyForm"',
        'id="extraClearance" form="pathSafetyForm"',
        'id="savePathSafetyBtn" form="pathSafetyForm"',
    ):
        if marker not in console_html:
            raise RuntimeError(f"3000 点位配置页缺少全局安全参数字段：{marker}")
    if not (
        console_html.index('id="pointTaskType"')
        < console_html.index('id="robotRadius"')
        < console_html.index('id="pointStart"')
    ):
        raise RuntimeError("3000 全局安全参数未紧邻点位 task_type 配置")
    for marker in ("/api/robots/default/navigation/path-safety", "path_safety_applied", "applied_path_safety"):
        if marker not in console_app:
            raise RuntimeError(f"3000 页面缺少安全参数保存/回读闭环：{marker}")
    for marker in ("pathSafety()", "savePathSafety(pathSafety)", "/api/settings"):
        if marker not in robot_client:
            raise RuntimeError(f"3000 后端缺少路径安全设置代理：{marker}")
    for marker in ('"path_safety": self.navigation_client.defaults.path_safety.__dict__', "applied_path_safety"):
        if marker not in bridge:
            raise RuntimeError(f"8002 map service 无法返回导航运行时安全配置：{marker}")
    for path in stage.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".jar", ".zip", ".gz", ".png", ".jpg", ".so", ".node"}:
            continue
        data = path.read_bytes()
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(data):
                raise RuntimeError(f"发布包疑似包含秘密：{path.relative_to(stage)}")


def create_archives(stage: Path, output: Path, name: str, force: bool) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    tar_path = output / f"{name}.tar.gz"
    if tar_path.exists() and not force:
        raise RuntimeError(f"输出已存在：{tar_path}")
    tar_path.unlink(missing_ok=True)

    def normalize_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
        relative_text = PurePosixPath(info.name).relative_to(name).as_posix()
        relative = PurePosixPath(relative_text) if relative_text != "." else PurePosixPath()
        info.uid = 0
        info.gid = 0
        info.uname = "root"
        info.gname = "root"
        if info.isdir():
            info.mode = 0o755
        elif info.isfile():
            info.mode = package_mode(relative)
        return info

    with tarfile.open(tar_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        archive.add(stage, arcname=name, filter=normalize_tar_info)
    tar_path.with_suffix(tar_path.suffix + ".sha256").write_text(
        f"{digest(tar_path)}  {tar_path.name}\n", encoding="ascii", newline="\n"
    )
    return tar_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建灵犀导航内部明文部署包")
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--java-jar", default=str(HERE / "build" / "robot-local-navigation-java-all.jar"))
    parser.add_argument("--expected-java-sha256", default=FIELD_VERIFIED_JAVA_SHA256)
    parser.add_argument("--output-dir", default=str(HERE / "dist"))
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not VERSION_RE.fullmatch(args.version):
        raise RuntimeError("版本号不安全")
    java_jar = Path(args.java_jar).resolve()
    if not java_jar.is_file():
        raise RuntimeError(f"缺少现场验收 Java JAR：{java_jar}")
    if args.expected_java_sha256 and digest(java_jar) != args.expected_java_sha256.lower():
        raise RuntimeError("Java JAR 与现场验收版本的 SHA-256 不一致")
    output = Path(args.output_dir).resolve()
    name = f"{PRODUCT_ID}-{args.version}"
    temp_parent = Path(output.anchor) if os.name == "nt" and output.anchor else output
    temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".lingxi-internal-", dir=temp_parent) as temp_name:
        stage = Path(temp_name) / name
        stage.mkdir()
        build_payload(stage, java_jar)
        copy_sources(stage)
        copy_release_files(stage, args.version)
        build_manifests(stage)
        audit(stage)
        tar_path = create_archives(stage, output, name, args.force)
    print(f"Linux 部署归档：{tar_path}")
    print(f"TAR SHA-256：{digest(tar_path)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"内部部署包构建失败：{exc}", file=os.sys.stderr)
        raise SystemExit(1)
