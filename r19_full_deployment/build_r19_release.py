#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from shutil import copyfileobj
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
R18_REF = "b12ce51"
R18_SOURCE_PATHS = (
    "server",
    "robot_local_navigation_v3/robot_local_navigation_v3-new-auto-return-interaction-end",
    "deploy/robot/robot_lingxi_bridge.py",
)
RUNTIME = HERE / "runtime"
VERSION = "1.1.1-20261027-r19-full"
PACKAGE_NAME = f"sparkrobot-lingxi-r19-full-{VERSION}"
AIMDK_PREFIX = "aimdk-aarch64-faf4c945-artifacts/src/aimdk_msgs/prebuilt_aarch64/"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def copy_tree(source: Path, target: Path, excludes: set[str] | None = None) -> None:
    if not source.is_dir():
        raise RuntimeError(f"missing build directory: {source}")
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*sorted(excludes)) if excludes else None,
    )


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"expected one {label} anchor, found {count}")
    return source.replace(old, new, 1)


def remove_between(source: str, start: str, end: str, label: str) -> str:
    if source.count(start) != 1 or source.count(end) != 1:
        raise RuntimeError(f"could not uniquely locate {label} block")
    left = source.index(start)
    right = source.index(end, left)
    return source[:left] + source[right:]


def git_snapshot(target: Path) -> None:
    command = ["git", "-C", str(ROOT), "archive", "--format=tar", R18_REF, *R18_SOURCE_PATHS]
    result = subprocess.run(command, check=True, stdout=subprocess.PIPE)
    import io

    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or not (member.isfile() or member.isdir()):
                raise RuntimeError(f"unsafe R18 source archive member: {member.name}")
            destination = target.joinpath(*name.parts)
            if not destination.resolve().is_relative_to(target.resolve()):
                raise RuntimeError(f"R18 source archive escaped the build directory: {member.name}")
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"could not read R18 source archive member: {member.name}")
            with source, destination.open("wb") as output:
                copyfileobj(source, output)


def extract_aimdk_messages(archive_path: Path, target: Path) -> dict[str, object]:
    if not archive_path.is_file():
        raise RuntimeError(f"missing user AIMDK artifacts ZIP: {archive_path}")
    prefix = PurePosixPath(AIMDK_PREFIX)
    count = 0
    total_size = 0
    with zipfile.ZipFile(archive_path) as archive:
        if len(archive.infolist()) > 10000:
            raise RuntimeError("AIMDK ZIP contains too many entries")
        for info in archive.infolist():
            name = PurePosixPath(info.filename)
            if not name.is_relative_to(prefix) or info.is_dir():
                continue
            relative = name.relative_to(prefix)
            mode = info.external_attr >> 16
            if relative.is_absolute() or ".." in relative.parts or "\\" in info.filename or stat.S_ISLNK(mode):
                raise RuntimeError(f"unsafe AIMDK ZIP member: {info.filename}")
            total_size += info.file_size
            if info.file_size > 100 * 1024 * 1024 or total_size > 700 * 1024 * 1024:
                raise RuntimeError("AIMDK ZIP expands beyond the supported overlay size")
            destination = target.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(info))
            count += 1
    package_xml = target / "share/aimdk_msgs/package.xml"
    msg_init = target / "local/lib/python3.10/dist-packages/aimdk_msgs/msg/__init__.py"
    srv_init = target / "local/lib/python3.10/dist-packages/aimdk_msgs/srv/__init__.py"
    if count < 100 or not all(path.is_file() for path in (package_xml, msg_init, srv_init)):
        raise RuntimeError("AIMDK ZIP does not contain a complete prebuilt aimdk_msgs overlay")
    required_messages = {"PncTaskRequest"}
    required_services = {"ExecuteActionResource", "GetStoredMapByName", "PlayAudioFile", "PlayTts", "SetMcAction", "SetMcPresetMotion"}
    for path, required in ((msg_init, required_messages), (srv_init, required_services)):
        text = path.read_text(encoding="utf-8")
        exported = set(re.findall(r"\bimport\s+([A-Za-z][A-Za-z0-9_]*)", text))
        missing = required - exported
        if missing:
            raise RuntimeError(f"AIMDK overlay is missing interfaces: {', '.join(sorted(missing))}")
    if not (target / "share/ament_index/resource_index/packages/aimdk_msgs").is_file():
        raise RuntimeError("AIMDK overlay is missing the ament package index marker")
    setup = target / "setup.bash"
    setup.write_text(
        """#!/usr/bin/env bash
set -e
R19_AIMDK_PREFIX="${R19_AIMDK_PREFIX:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
ROS_SETUP="${ROBOT_LOCAL_NAV_ROS_SETUP:-/opt/ros/humble/setup.bash}"
[[ -r "${ROS_SETUP}" ]] || { echo "ROS 2 setup is missing: ${ROS_SETUP}" >&2; return 1 2>/dev/null || exit 1; }
set +u
source "${ROS_SETUP}"
[[ ! -r /agibot/software/common/local_setup.bash ]] || source /agibot/software/common/local_setup.bash
set -u
export AMENT_PREFIX_PATH="${R19_AIMDK_PREFIX}${AMENT_PREFIX_PATH:+:${AMENT_PREFIX_PATH}}"
export CMAKE_PREFIX_PATH="${R19_AIMDK_PREFIX}${CMAKE_PREFIX_PATH:+:${CMAKE_PREFIX_PATH}}"
export PYTHONPATH="${R19_AIMDK_PREFIX}/local/lib/python3.10/dist-packages${PYTHONPATH:+:${PYTHONPATH}}"
export LD_LIBRARY_PATH="${R19_AIMDK_PREFIX}/lib:${R19_AIMDK_PREFIX}/lib/aarch64-linux-gnu${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
""",
        encoding="utf-8",
        newline="\n",
    )
    setup.chmod(0o755)
    return {
        "archive_sha256": digest(archive_path),
        "archive_size": archive_path.stat().st_size,
        "artifact_version": "aimdk v1.1.0-gfaf4c94",
        "files": count,
    }


def strip_navigation_path_safety(navigation: Path) -> None:
    package = navigation / "robot_local_navigation_v3"
    models = package / "models.py"
    source = models.read_text(encoding="utf-8")
    source = remove_python_class(source, "PathSafetyConfig")
    source = replace_once(source, "    path_safety: PathSafetyConfig = field(default_factory=PathSafetyConfig)\n", "", "navigation path_safety field")
    source = replace_once(source, "            path_safety=PathSafetyConfig.from_dict(payload.get(\"path_safety\")),\n", "", "navigation path_safety loader")
    models.write_text(source, encoding="utf-8", newline="\n")

    client = package / "navigation_client.py"
    source = client.read_text(encoding="utf-8")
    for line in (
        "from pathlib import Path\n",
        "from geometry_msgs.msg import Pose2D, PoseStamped\n",
        "from robot_local_navigation_v3.path_clearance import PathClearanceError, plan_reference_line\n",
    ):
        source = replace_once(source, line, "" if "Pose2D" not in line else "from geometry_msgs.msg import PoseStamped\n", line.strip())
    source = replace_once(source, "        map_root: str | None = None,\n", "", "navigation map_root argument")
    source = remove_between(source, "        self.map_root = map_root or os.environ.get(", "        self.republish_period_sec = max(", "navigation map_root state")
    source = source.replace("        self._planning_goal = False\n", "", 1)
    source = replace_python_method(source, "NavigationClient", "start", "\n".join([
        "    def start(",
        "        self,",
        "        point: NavPoint,",
        "        listener: NavigationEventListener | None = None,",
        "        *,",
        "        timeout_sec: float | None = None,",
        "        allow_initial_arrival_hysteresis: bool = False,",
        "    ) -> NavigationSessionHandle:",
        "        with self._lock:",
        "            if self._active_session is not None and self._active_session.is_active:",
        "                session = self._active_session",
        "                reason = f\"navigation is busy with task_id={session.task_id}\"",
        "                emit_safely(listener, NavigationEvent(",
        "                    type=NavigationEventType.REJECTED, task_id=None, goal_id=point.id,",
        "                    point_name=point.name, status=\"busy\", reason=reason,",
        "                ), logger=self.node.get_logger())",
        "                return NavigationSessionHandle(",
        "                    task_id=None, goal_id=point.id, point_name=point.name,",
        "                    accepted=False, status=\"busy\", reason=reason,",
        "                )",
        "            session = NavigationSession(",
        "                node=self.node, publisher=self.publisher, defaults=self.defaults,",
        "                point=point, listener=listener, mode=self.mode,",
        "                republish_period_sec=self.republish_period_sec,",
        "                progress_period_sec=self.progress_period_sec,",
        "                timeout_sec=timeout_sec or self.defaults.timeout_sec,",
        "                allow_initial_arrival_hysteresis=allow_initial_arrival_hysteresis,",
        "                on_finished=self._clear_session,",
        "            )",
        "            self._active_session = session",
        "        return session.start()",
        "",
    ]) + "\n")
    source = remove_python_method(source, "NavigationClient", "plan_safe_reference_line")
    source = remove_python_method(source, "NavigationClient", "_verify_planning_pose_still_valid")
    source = remove_python_method(source, "NavigationClient", "_requires_global_path_safety")
    source = replace_once(source, "        safe_reference_line: list[tuple[float, float]],\n", "", "route session parameter")
    source = replace_once(source, "        self.safe_reference_line = list(safe_reference_line)\n", "", "route session field")
    source = remove_between(
        source,
        "        reference_line = []\n        if NavigationClient._requires_global_path_safety(self.point):\n",
        '        self.node.get_logger().info(\n            "Navigation task started:',
        "PNC custom reference line",
    )
    source = replace_once(source, '            f"safe_reference_points={len(reference_line)}"\n', '            f"target_pose_radius={msg.target_pose_radius:.3f}"\n', "custom route log")
    if any(term in source for term in ("PathClearance", "safe_reference_line", "path_safety", "reference_line")):
        raise RuntimeError("navigation client still contains custom path-clearance logic")
    client.write_text(source, encoding="utf-8", newline="\n")
    (package / "path_clearance.py").unlink(missing_ok=True)

    bridge = package / "lingxi_nav_bridge.py"
    source = bridge.read_text(encoding="utf-8")
    source = replace_once(source, "from robot_local_navigation_v3.models import PathSafetyConfig\n", "", "bridge path safety import")
    source = replace_once(source, '            "path_safety": self.navigation_client.defaults.path_safety.__dict__,\n', "", "bridge path safety status")
    source = replace_python_method(source, "LingxiNavBridge", "set_nav_mode", "\n".join([
        "    def set_nav_mode(self, payload: dict[str, Any]) -> dict[str, Any]:",
        "        \"\"\"Update navigation mode without changing firmware safety behavior.\"\"\"",
        "        mode = str(payload.get(\"nav_mode\", \"\")).strip()",
        "        if mode not in {\"execute\", \"dry-run\"}:",
        "            return {\"ok\": False, \"message\": \"nav_mode must be execute or dry-run\"}",
        "        self.config.app.nav_mode = mode",
        "        self.config_store.save(self.config)",
        "        self.navigation_client.mode = mode",
        "        return {\"ok\": True, \"nav_mode\": mode}",
        "",
    ]) + "\n")
    bridge.write_text(source, encoding="utf-8", newline="\n")

    label = package / "map_label_server_v3.py"
    source = label.read_text(encoding="utf-8")
    source = remove_between(source, "    <h2>全局路径安全</h2>\n", '    <label for="navMode">', "legacy path safety UI")
    source = replace_once(source, 'const robotRadiusInput = document.getElementById("robotRadius");\n', "", "robot radius control")
    source = replace_once(source, 'const extraClearanceInput = document.getElementById("extraClearance");\n', "", "extra clearance control")
    source = replace_once(source, 'const occupiedThresholdInput = document.getElementById("occupiedThreshold");\n', "", "occupied threshold control")
    source = replace_python_method(source, "V3LabelApp", "load_settings", "") if False else source
    source = remove_between(source, '    const safety = payload.path_safety || {};\n', '  }\n}\n\nasync function saveSettings()', "legacy settings load")
    source = remove_between(source, '      path_safety: {\n', '      }\n    })', "legacy settings save")
    source = replace_once(source, '      }\n    })', '    })', "legacy settings body close")
    source = replace_once(source, '`导航模式和全局路径安全参数已保存：${payload.nav_mode}`', '`导航模式已保存：${payload.nav_mode}`', "settings status")
    source = replace_python_method(source, "V3LabelApp", "get_settings", "\n".join([
        "    def get_settings(self) -> dict[str, object]:",
        "        config = self.config_store.load()",
        "        bridge_status = self.bridge_manager.status()",
        "        bridge = bridge_status.get(\"bridge\")",
        "        bridge = bridge if isinstance(bridge, dict) else {}",
        "        return {**config.app.__dict__, \"bridge_running\": bridge.get(\"ok\") is True}",
        "",
    ]) + "\n")
    source = replace_python_method(source, "V3LabelApp", "save_settings", "\n".join([
        "    def save_settings(self, payload: dict[str, object]) -> dict[str, object]:",
        "        config = self.config_store.load()",
        "        merged = config.app.__dict__.copy()",
        "        merged.update(payload)",
        "        config.app = AppSettings.from_dict(merged)",
        "        self.config_store.save(config)",
        "        bridge_update = self.bridge_manager.set_nav_mode(config.app.nav_mode)",
        "        return {",
        "            **config.app.__dict__,",
        "            \"settings_saved\": True,",
        "            \"settings_applied\": bool(bridge_update.get(\"applied\")),",
        "            \"bridge_running\": bool(bridge_update.get(\"running\")),",
        "            \"settings_apply_error\": bridge_update.get(\"error\", \"\"),",
        "        }",
        "",
    ]) + "\n")
    source = replace_python_method(source, "BridgeServiceManager", "set_nav_mode", "\n".join([
        "    def set_nav_mode(self, mode: str) -> dict[str, object]:",
        "        payload: dict[str, object] = {\"nav_mode\": mode}",
        "        remote_status = self._get_remote_status()",
        "        if remote_status.get(\"ok\") is not True:",
        "            return {\"running\": False, \"applied\": False, \"nav_mode\": mode, \"error\": \"导航桥接未在线\"}",
        "        try:",
        "            result = self._post_json(\"/nav/mode\", payload)",
        "        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:",
        "            return {\"running\": False, \"applied\": False, \"nav_mode\": mode, \"error\": str(exc)}",
        "        if result.get(\"ok\") is not True:",
        "            return {\"running\": True, \"applied\": False, \"nav_mode\": mode, \"error\": str(result.get(\"message\", \"导航桥接拒绝应用模式\"))}",
        "        runtime_status = self._get_remote_status()",
        "        applied = runtime_status.get(\"ok\") is True and runtime_status.get(\"nav_mode\") == mode",
        "        return {\"running\": True, \"applied\": applied, \"nav_mode\": mode, \"error\": \"\" if applied else \"桥接未回读到网页保存的导航模式\"}",
        "",
    ]) + "\n")
    source = remove_python_function(source, "path_safety_matches")
    if source.count("import math\n"):
        source = source.replace("import math\n", "")
    if any(token in source for token in ("path_safety", "pathSafety", "robotRadius", "extraClearance", "occupiedThreshold")):
        raise RuntimeError("legacy map-label page still contains custom path safety controls")
    label.write_text(source, encoding="utf-8", newline="\n")


def remove_python_function(source: str, name: str) -> str:
    import ast

    tree = ast.parse(source)
    matches = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    if len(matches) != 1:
        raise RuntimeError(f"expected one top-level function {name}")
    node = matches[0]
    lines = source.splitlines(keepends=True)
    return "".join(lines[: node.lineno - 1] + lines[node.end_lineno :])


def remove_python_class(source: str, name: str) -> str:
    import ast

    tree = ast.parse(source)
    matches = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name]
    if len(matches) != 1:
        raise RuntimeError(f"expected one top-level class {name}")
    node = matches[0]
    lines = source.splitlines(keepends=True)
    return "".join(lines[: node.lineno - 1] + lines[node.end_lineno :])


def replace_python_method(source: str, class_name: str, name: str, replacement: str) -> str:
    import ast

    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name]
    if len(classes) != 1:
        raise RuntimeError(f"expected one class {class_name}")
    matches = [node for node in classes[0].body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    if len(matches) != 1:
        raise RuntimeError(f"expected one method {class_name}.{name}")
    node = matches[0]
    lines = source.splitlines(keepends=True)
    normalized = replacement if replacement.endswith("\n") else replacement + "\n"
    return "".join(lines[: node.lineno - 1] + [normalized] + lines[node.end_lineno :])


def remove_python_method(source: str, class_name: str, name: str) -> str:
    import ast

    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name]
    if len(classes) != 1:
        raise RuntimeError(f"expected one class {class_name}")
    matches = [node for node in classes[0].body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    if len(matches) != 1:
        raise RuntimeError(f"expected one method {class_name}.{name}")
    node = matches[0]
    lines = source.splitlines(keepends=True)
    return "".join(lines[: node.lineno - 1] + lines[node.end_lineno :])


def strip_typescript_path_safety(server: Path) -> None:
    app = server / "src/app.ts"
    source = app.read_text(encoding="utf-8")
    source = remove_between(source, "const pathSafetySchema = z.object({\n", "function sendActionResult(", "path safety schema")
    source = remove_between(source, '  app.get("/api/robots/default/navigation/path-safety", async () => {\n', '  app.get("/api/robots/default/relocalization/status",', "path safety routes")
    app.write_text(source, encoding="utf-8", newline="\n")

    client = server / "src/robotClient.ts"
    source = client.read_text(encoding="utf-8")
    source = remove_between(source, "    pathSafety() {\n", "    relocalizationStatus()", "robot client path safety methods")
    client.write_text(source, encoding="utf-8", newline="\n")

    app_test = server / "test/app.test.ts"
    source = app_test.read_text(encoding="utf-8")
    source = remove_between(
        source,
        "    pathSafety: async () => ({\n",
        "    relocalizationStatus: async () => ({ running: false, result: {} }),\n",
        "legacy path-safety test fixture",
    )
    source = remove_between(
        source,
        '  it("reads and saves global path safety without mixing it into point data", async () => {\n',
        '  it("reports rejected tour start without changing successful lifecycle responses", async () => {',
        "legacy global path-safety tests",
    )
    source = replace_once(
        source,
        '  it("reports rejected tour start without changing successful lifecycle responses", async () => {',
        '  it("removes custom path-safety API while preserving navigation", async () => {\n'
        '    const app = buildApp(config, robot());\n'
        '    const removed = await app.inject({ method: "GET", url: "/api/robots/default/navigation/path-safety" });\n'
        '    expect(removed.statusCode).toBe(404);\n'
        '    const navigation = await app.inject({ method: "POST", url: "/api/robots/default/nav/start", payload: { target_name: "起点" } });\n'
        '    expect(navigation.statusCode).toBe(200);\n'
        '    expect(navigation.json()).toMatchObject({ ok: true, target_name: "起点" });\n'
        '  });\n\n'
        '  it("reports rejected tour start without changing successful lifecycle responses", async () => {',
        "removed path-safety API regression test",
    )
    app_test.write_text(source, encoding="utf-8", newline="\n")

    ui_test = server / "test/uiAssets.test.ts"
    source = ui_test.read_text(encoding="utf-8")
    source = source.replace("/console/app.js?v=20261026-r18-mapfit16", "/console/app.js?v=20261027-r19-full")
    marker = '  it("places global path safety inputs beside point task_type without making point editing require a radius", () => {\n'
    if source.count(marker) != 1 or not source.rstrip().endswith("});"):
        raise RuntimeError("could not uniquely locate legacy path safety UI test")
    source = source[:source.index(marker)].rstrip() + "\n});\n"
    ui_test.write_text(source, encoding="utf-8", newline="\n")

    client_test = server / "test/robotClient.test.ts"
    source = client_test.read_text(encoding="utf-8")
    marker = 'describe("global path safety settings", () => {\n'
    if source.count(marker) != 1:
        raise RuntimeError("could not uniquely locate legacy path safety robot-client test")
    source = source[:source.index(marker)].rstrip() + "\n"
    client_test.write_text(source, encoding="utf-8", newline="\n")

    html = server / "public/index.html"
    source = html.read_text(encoding="utf-8")
    source = replace_once(source, '        <form id="pathSafetyForm"></form>\n', "", "path safety form")
    source = remove_between(source, "          <div class=\"path-safety-settings wide\">\n", "          <label class=\"checkbox\">", "path safety controls")
    source = source.replace("styles.css?v=20261026-r18-mapfit13", "styles.css?v=20261027-r19-full")
    source = source.replace("app.js?v=20261026-r18-mapfit16", "app.js?v=20261027-r19-full")
    html.write_text(source, encoding="utf-8", newline="\n")

    script = server / "public/app.js"
    source = script.read_text(encoding="utf-8")
    source = replace_once(source, "  pathSafety: { robot_radius_m: null, extra_clearance_m: 0.1 },\n", "", "path safety UI state")
    source = replace_once(source, "  pathSafetyLoaded: false,\n", "", "path safety loaded state")
    source = remove_between(source, "function renderPathSafetyStatus(", "function applyStartup(", "path safety UI actions")
    source = remove_between(source, '  $("pathSafetyForm").onsubmit = (event) => {\n', '  $("newPointBtn").onclick =', "path safety event handler")
    source = replace_once(
        source,
        "    try {\n      await loadPathSafety();\n    } catch (error) {\n      renderPathSafetyStatus(`读取机器人全局安全配置失败：${error.message}`, \"error\");\n    }\n",
        "",
        "path safety initial request",
    )
    if any(term in source for term in ("pathSafety", "path-safety", "robotRadius", "extraClearance", "occupiedThreshold")):
        raise RuntimeError("R19 browser script still contains custom robot geometry/clearance controls")
    script.write_text(source, encoding="utf-8", newline="\n")

    css = server / "public/styles.css"
    source = css.read_text(encoding="utf-8")
    start = source.index(".path-safety-settings {")
    end = source.index(".checkbox {", start)
    source = source[:start] + source[end:]
    source = replace_once(source, "  .path-safety-grid { grid-template-columns: 1fr; }\n", "", "path safety responsive styles")
    css.write_text(source, encoding="utf-8", newline="\n")


def configure_mobile_mapping_ui(server: Path) -> None:
    html = server / "public/index.html"
    source = html.read_text(encoding="utf-8")
    source = source.replace("styles.css?v=20261026-r18-mapfit13", "styles.css?v=20261027-r19-full")
    source = source.replace("app.js?v=20261026-r18-mapfit16", "app.js?v=20261027-r19-full")
    section_start = '      <section id="mappingPanel" class="panel mapping-panel view-section" data-view-section="mapping" hidden>\n'
    section_end = '      <section id="serviceCheckPanel"'
    if source.count(section_start) != 1 or source.count(section_end) != 1:
        raise RuntimeError("could not uniquely locate the R18 mapping workspace")
    start = source.index(section_start)
    end = source.index(section_end, start)
    mapping_section = """      <section id="mappingPanel" class="panel mapping-panel view-section" data-view-section="mapping" hidden>
        <div class="panel-head mapping-page-head">
          <div>
            <h2>实时建图</h2>
            <p class="hint">先检查传感器与 SLAM，再开始采集；地图会在此实时更新。</p>
          </div>
          <span id="mappingBadge" class="badge bad">尚未检查</span>
        </div>
        <div class="mapping-safety notice">本网页只读取传感器并调用 AIMDK 建图/保存接口，不发送底盘速度或导航运动命令；请使用外置遥控器移动机器人。</div>
        <div id="mappingRotatePrompt" class="mapping-rotate-prompt" role="status" aria-live="polite" hidden>
          <strong>请将手机横向旋转</strong>
          <span>如果浏览器没有自动切换方向，手动横屏后即可继续查看实时地图。</span>
        </div>
        <div class="mapping-workspace">
          <section class="mapping-live" aria-label="实时扫描地图">
            <div class="mapping-preview-head">
              <strong>实时扫描地图</strong>
              <div class="mapping-view-actions">
                <span id="mappingPreviewState" class="badge">等待开始</span>
                <button id="mappingFullscreenBtn" class="secondary" type="button" aria-label="进入横屏全屏建图视图" aria-pressed="false">横屏全屏</button>
              </div>
            </div>
            <div class="mapping-map-frame">
              <canvas id="mappingPreviewCanvas" role="img" aria-label="机器人实时雷达地图和移动轨迹" hidden></canvas>
              <div id="mappingPreviewEmpty">开始建图并由外置遥控器移动机器人后，实时扫描点和轨迹会显示在这里。</div>
            </div>
            <p id="mappingPreviewMeta" class="hint">扫描数据来自机器人雷达与 SLAM 坐标变换。</p>
          </section>
          <aside class="mapping-side-rail" aria-label="建图操作与状态">
            <section class="mapping-controls" aria-label="地图采集">
              <button id="refreshMappingBtn" class="secondary mapping-check-btn" type="button">检查传感器与建图接口</button>
              <p id="mappingQuickDiagnosis" class="mapping-quick-diagnosis" role="status" aria-live="polite">正在读取传感器与 SLAM 状态。</p>
              <label>地图名称<input id="mappingName" maxlength="48" placeholder="后端接口验证后启用" disabled></label>
              <div class="mapping-actions">
                <button id="startMappingBtn" class="primary-large" type="button" disabled>采集接口待验证</button>
                <button id="saveMappingBtn" class="warning" type="button" disabled>停止采集并保存地图</button>
              </div>
              <div id="mappingResult" class="mapping-result" aria-live="polite"></div>
            </section>
            <details class="mapping-diagnostics">
              <summary>传感器与 SLAM 状态</summary>
              <div class="mapping-diagnostic-content">
                <div class="mapping-preview-head"><strong>接口状态</strong></div>
                <div class="status-grid compact">
                  <div><span>底层服务</span><strong id="mappingSoftware">-</strong></div>
                  <div><span>雷达点云</span><strong id="mappingLidar">-</strong></div>
                  <div><span>雷达 IMU</span><strong id="mappingImu">-</strong></div>
                  <div><span>SLAM</span><strong id="mappingSlam">-</strong></div>
                  <div><span>定位里程计</span><strong id="mappingOdom">-</strong></div>
                  <div><span>建图后端</span><strong id="mappingBackend">-</strong></div>
                </div>
                <pre id="mappingDiagnosis" class="log neutral">正在等待状态检查</pre>
                <div class="mapping-graph">
                  <div><strong>地图相关 ROS 话题</strong><pre id="mappingTopics" class="log neutral">尚未检查</pre></div>
                  <div><strong>地图相关 ROS 服务</strong><pre id="mappingServices" class="log neutral">尚未检查</pre></div>
                </div>
                <div class="mapping-prerequisites"><strong>建图依赖预检</strong><ul id="mappingPrerequisites"><li>尚未检查</li></ul></div>
              </div>
            </details>
            <details class="mapping-saved-details">
              <summary>最近保存的地图</summary>
              <div class="mapping-saved-content">
                <div class="mapping-preview-head"><strong>地图列表</strong><button id="refreshMappingMapsBtn" class="secondary" type="button">刷新</button></div>
                <ul id="mappingSavedMaps" class="mapping-saved-maps"><li>尚未读取地图</li></ul>
              </div>
            </details>
          </aside>
        </div>
      </section>
"""
    source = source[:start] + mapping_section + source[end:]
    source = replace_once(
        source,
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
        "mobile safe-area viewport",
    )
    html.write_text(source, encoding="utf-8", newline="\n")

    script = server / "public/app.js"
    source = script.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "let mappingPreviewInFlight = false;\n",
        "let mappingPreviewInFlight = false;\nlet mappingFocusActive = false;\n",
        "mapping focus state",
    )
    source = replace_once(
        source,
        "function switchWorkspace(view, { scroll = false } = {}) {\n",
        "function switchWorkspace(view, { scroll = false } = {}) {\n  if (view !== \"mapping\" && mappingFocusActive) exitMappingFocus();\n",
        "mapping focus navigation exit",
    )
    source = replace_once(
        source,
        '    setText("mappingDiagnosis", error.message || "暂时无法读取机器人传感器状态");\n',
        '    setText("mappingDiagnosis", error.message || "暂时无法读取机器人传感器状态");\n'
        '    setText("mappingQuickDiagnosis", error.message || "暂时无法读取机器人传感器状态");\n',
        "mapping visible check error",
    )
    source = replace_once(
        source,
        '  setText("mappingDiagnosis", health.diagnosis || "未读取到建图诊断");\n',
        '  setText("mappingDiagnosis", health.diagnosis || "未读取到建图诊断");\n'
        '  setText("mappingQuickDiagnosis", health.diagnosis || "未读取到建图诊断");\n',
        "mapping visible health result",
    )
    source = replace_once(
        source,
        "  mappingPreviewTimer = setInterval(() => void refreshMappingPreview(), 2500);\n",
        "  mappingPreviewTimer = setInterval(() => void refreshMappingPreview(), 500);\n",
        "live map refresh cadence",
    )
    source = replace_once(
        source,
        'window.addEventListener("resize", () => { if (state.mappingPreview) drawMappingPreview(); });\n',
        """function updateMappingOrientationPrompt() {
  const prompt = $("mappingRotatePrompt");
  if (!prompt) return;
  const portraitMobile = window.matchMedia("(max-width: 980px) and (orientation: portrait)").matches;
  prompt.hidden = !(mappingFocusActive && portraitMobile);
}

function enterMappingFocus() {
  const panel = $("mappingPanel");
  if (!panel) return;
  mappingFocusActive = true;
  document.body.classList.add("mapping-focus");
  $("mappingFullscreenBtn").textContent = "退出全屏";
  $("mappingFullscreenBtn").setAttribute("aria-label", "退出横屏全屏建图视图");
  $("mappingFullscreenBtn").setAttribute("aria-pressed", "true");
  const touchDevice = window.matchMedia("(pointer: coarse)").matches;
  if (typeof panel.requestFullscreen === "function") {
    const request = panel.requestFullscreen({ navigationUI: "hide" });
    if (touchDevice && screen.orientation?.lock) {
      void request.then(() => screen.orientation.lock("landscape")).catch(() => {});
    } else void request.catch(() => {});
  } else if (touchDevice && screen.orientation?.lock) {
    void screen.orientation.lock("landscape").catch(() => {});
  }
  updateMappingOrientationPrompt();
  if (state.mappingPreview) requestAnimationFrame(drawMappingPreview);
}

function exitMappingFocus() {
  const panel = $("mappingPanel");
  mappingFocusActive = false;
  document.body.classList.remove("mapping-focus");
  $("mappingFullscreenBtn").textContent = "横屏全屏";
  $("mappingFullscreenBtn").setAttribute("aria-label", "进入横屏全屏建图视图");
  $("mappingFullscreenBtn").setAttribute("aria-pressed", "false");
  $("mappingRotatePrompt").hidden = true;
  try { screen.orientation?.unlock?.(); } catch {}
  if (panel && document.fullscreenElement === panel) void document.exitFullscreen().catch(() => {});
  if (state.mappingPreview) requestAnimationFrame(drawMappingPreview);
}

document.addEventListener("fullscreenchange", () => {
  if (mappingFocusActive && document.fullscreenElement !== $("mappingPanel")) exitMappingFocus();
});
window.addEventListener("orientationchange", updateMappingOrientationPrompt);
window.addEventListener("resize", () => {
  updateMappingOrientationPrompt();
  if (state.mappingPreview) drawMappingPreview();
});
""",
        "mapping focus and orientation controls",
    )
    source = replace_once(
        source,
        '  $("startMappingBtn").onclick = () => runAction("建图采集已开始；请通过外置遥控器移动机器人", startMapping);\n',
        '  $("startMappingBtn").onclick = () => {\n'
        '    if (window.matchMedia("(pointer: coarse)").matches) enterMappingFocus();\n'
        '    return runAction("建图采集已开始；请通过外置遥控器移动机器人", startMapping);\n'
        '  };\n'
        '  $("mappingFullscreenBtn").onclick = () => mappingFocusActive ? exitMappingFocus() : enterMappingFocus();\n',
        "mobile mapping start and fullscreen actions",
    )
    script.write_text(source, encoding="utf-8", newline="\n")

    css = server / "public/styles.css"
    source = css.read_text(encoding="utf-8")
    source += """

.mapping-workspace { align-items: stretch; display: grid; gap: 12px; grid-template-columns: minmax(0, 1fr) minmax(260px, 320px); }
.mapping-live { display: flex; flex-direction: column; margin: 0; min-height: 0; min-width: 0; }
.mapping-live .mapping-preview-head { flex: 0 0 auto; min-height: 40px; }
.mapping-view-actions { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
.mapping-map-frame { aspect-ratio: auto; flex: 1 1 auto; min-height: min(62vh, 620px); }
.mapping-side-rail { align-content: start; display: grid; gap: 12px; min-width: 0; }
.mapping-controls { gap: 10px; }
.mapping-check-btn { min-height: 44px; }
.mapping-quick-diagnosis { color: #536574; font-size: 12px; line-height: 1.45; margin: 0; overflow-wrap: anywhere; }
.mapping-actions { display: grid; gap: 8px; }
.mapping-actions button { min-height: 48px; }
.mapping-diagnostics, .mapping-saved-details { border-top: 1px solid #dce3e7; min-width: 0; padding-top: 10px; }
.mapping-diagnostics > summary, .mapping-saved-details > summary { color: #354653; cursor: pointer; font-size: 14px; font-weight: 700; min-height: 38px; padding: 8px 0; }
.mapping-diagnostic-content, .mapping-saved-content { padding-top: 8px; }
.mapping-rotate-prompt { align-items: center; background: #fff5df; border: 1px solid #e6c981; border-radius: 6px; color: #5e491b; display: flex; flex-wrap: wrap; gap: 6px 12px; margin-bottom: 10px; padding: 10px 12px; }
.mapping-rotate-prompt[hidden] { display: none; }
.mapping-panel:fullscreen { background: #fff; box-sizing: border-box; height: 100vh; height: 100dvh; max-height: none; overflow: auto; padding: max(10px, env(safe-area-inset-top)) max(10px, env(safe-area-inset-right)) max(10px, env(safe-area-inset-bottom)) max(10px, env(safe-area-inset-left)); width: 100vw; }
body.mapping-focus { overflow: hidden; }
body.mapping-focus .topbar, body.mapping-focus .customer-shell { display: none !important; }
body.mapping-focus .layout { display: block; height: 100vh; height: 100dvh; margin: 0; max-width: none; overflow: auto; padding: 0; }
body.mapping-focus #mappingPanel { background: #fff; box-sizing: border-box; display: flex; flex-direction: column; height: 100vh; height: 100dvh; inset: 0; margin: 0; max-height: none; overflow: auto; padding: max(10px, env(safe-area-inset-top)) max(10px, env(safe-area-inset-right)) max(10px, env(safe-area-inset-bottom)) max(10px, env(safe-area-inset-left)); position: fixed; width: 100vw; z-index: 1000; }
body.mapping-focus .mapping-page-head, body.mapping-focus .mapping-safety { display: none; }
body.mapping-focus .mapping-workspace { flex: 1 1 auto; min-height: 0; }
body.mapping-focus .mapping-live { min-height: 0; }
body.mapping-focus .mapping-map-frame { min-height: 0; }
body.mapping-focus .mapping-side-rail { max-height: 100%; overflow-y: auto; overscroll-behavior: contain; }

@media (max-width: 980px) {
  .mapping-workspace { grid-template-columns: minmax(0, 1fr); }
  .mapping-live { min-height: 48svh; }
  .mapping-map-frame { min-height: 44svh; }
  .mapping-side-rail { grid-template-columns: minmax(0, 1fr); }
}

@media (max-width: 980px) and (orientation: landscape) {
  .mapping-workspace { align-items: stretch; grid-template-columns: minmax(0, 1fr) minmax(220px, 30vw); }
  .mapping-live { height: clamp(210px, calc(100dvh - 180px), 560px); min-height: 0; }
  .mapping-map-frame { aspect-ratio: auto; min-height: 0; }
  .mapping-side-rail { grid-template-columns: minmax(0, 1fr); max-height: calc(100dvh - 150px); overflow-y: auto; overscroll-behavior: contain; }
  body.mapping-focus #mappingPanel { overflow: hidden; }
  body.mapping-focus .mapping-page-head, body.mapping-focus .mapping-safety { display: none; }
  body.mapping-focus .mapping-workspace { height: calc(100dvh - 24px); }
  body.mapping-focus .mapping-live { height: 100%; }
  body.mapping-focus .mapping-side-rail { max-height: 100%; }
}

@media (max-width: 640px) and (orientation: portrait) {
  .mapping-live { min-height: 42svh; }
  .mapping-map-frame { min-height: 38svh; }
  .mapping-view-actions { gap: 6px; }
  .mapping-view-actions button { min-height: 42px; }
}
"""
    css.write_text(source, encoding="utf-8", newline="\n")

    ui_test = server / "test/uiAssets.test.ts"
    source = ui_test.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "    expect(appScript).toContain('setInterval(() => void refreshMappingPreview(), 2500)');\n",
        "    expect(appScript).toContain('setInterval(() => void refreshMappingPreview(), 500)');\n",
        "live map refresh assertion",
    )
    trimmed = source.rstrip()
    if not trimmed.endswith("});"):
        raise RuntimeError("could not locate UI test suite ending for mobile mapping assertions")
    source = trimmed[:-3] + """

  it("prioritizes live mapping and supports mobile landscape focus", () => {
    expect(html).toContain('id="mappingFullscreenBtn"');
    expect(html).toContain('id="mappingRotatePrompt"');
    expect(html).toContain('id="refreshMappingBtn" class="secondary mapping-check-btn"');
    expect(html).toContain('id="mappingQuickDiagnosis"');
    expect(html).toContain('aria-label="机器人实时雷达地图和移动轨迹"');
    expect(html.indexOf('class="mapping-live"')).toBeLessThan(html.indexOf('class="mapping-side-rail"'));
    expect(appScript).toContain('screen.orientation.lock("landscape")');
    expect(appScript).toContain("panel.requestFullscreen");
    expect(appScript).toContain('if (window.matchMedia("(pointer: coarse)").matches) enterMappingFocus();');
    expect(appScript).toContain("setInterval(() => void refreshMappingPreview(), 500)");
    expect(styles).toContain("@media (max-width: 980px) and (orientation: landscape)");
    expect(styles).toContain("body.mapping-focus #mappingPanel");
    expect(styles).toContain("100dvh");
  });
""" + "\n});\n"
    ui_test.write_text(source, encoding="utf-8", newline="\n")


def configure_incremental_mapping_preview(navigation: Path, server: Path) -> None:
    controller = navigation / "robot_local_navigation_v3/mapping_control.py"
    source = controller.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "        self._preview_points: dict[tuple[int, int], tuple[float, float]] = {}\n",
        "        self._preview_points: dict[tuple[int, int], tuple[float, float]] = {}\n"
        "        self._preview_point_revisions: dict[tuple[int, int], int] = {}\n"
        "        self._preview_revision = 0\n"
        "        self._preview_full_reset_revision = 0\n",
        "incremental map preview state",
    )
    source = replace_python_method(source, "MappingController", "live_preview", "\n".join([
        "    def live_preview(self, since_revision: int | None = None) -> dict[str, Any]:",
        "        with self._lock:",
        "            if not self._active_map_name:",
        "                return {\"ok\": False, \"revision\": self._preview_revision, \"message\": \"建图会话未运行\"}",
        "            if not self._preview_points or not self._robot_pose:",
        "                return {\"ok\": False, \"revision\": self._preview_revision, \"message\": \"等待 SLAM 里程计与雷达坐标变换，收到后将显示累计地图\"}",
        "            revision = self._preview_revision",
        "            full_snapshot = (",
        "                since_revision is None",
        "                or since_revision < self._preview_full_reset_revision",
        "                or since_revision > revision",
        "            )",
        "            if full_snapshot:",
        "                points = list(self._preview_points.values())",
        "            else:",
        "                points = [",
        "                    self._preview_points[key]",
        "                    for key, point_revision in self._preview_point_revisions.items()",
        "                    if point_revision > since_revision",
        "                ]",
        "            return {",
        "                \"ok\": True,",
        "                \"topic\": \"/aima/hal/sensor/lidar_chest_front/lidar_pointcloud\",",
        "                \"source_frame\": \"lidar_chest_front\",",
        "                \"frame_id\": self._robot_pose.get(\"frame_id\", \"map\"),",
        "                \"point_count\": len(self._preview_points),",
        "                \"points\": [[x, y] for x, y in points],",
        "                \"full_snapshot\": full_snapshot,",
        "                \"revision\": revision,",
        "                \"robot\": {key: value for key, value in self._robot_pose.items() if key != \"frame_id\"},",
        "                \"robot_path\": list(self._robot_path) if full_snapshot else [],",
        "                \"updated_at\": self._last_cloud_stamp,",
        "            }",
        "",
    ]) + "\n")
    source = replace_once(
        source,
        "                self._preview_points.update(frame_points)\n",
        "                self._preview_points.update(frame_points)\n"
        "                if frame_points:\n"
        "                    self._preview_revision += 1\n"
        "                    for key in frame_points:\n"
        "                        self._preview_point_revisions[key] = self._preview_revision\n"
        "                    while len(self._preview_points) > MAX_PREVIEW_POINTS:\n"
        "                        evicted_key = next(iter(self._preview_points))\n"
        "                        self._preview_points.pop(evicted_key)\n"
        "                        self._preview_point_revisions.pop(evicted_key, None)\n"
        "                        self._preview_full_reset_revision = self._preview_revision\n",
        "incremental scan batch capture",
    )
    source = replace_once(
        source,
        "            self._preview_points.clear()\n"
        "            self._robot_path.clear()\n"
        "            self._robot_pose.clear()\n",
        "            self._preview_points.clear()\n"
        "            self._preview_point_revisions.clear()\n"
        "            self._preview_revision = 0\n"
        "            self._preview_full_reset_revision = 0\n"
        "            self._robot_path.clear()\n"
        "            self._robot_pose.clear()\n",
        "incremental scan session reset",
    )
    controller.write_text(source, encoding="utf-8", newline="\n")

    mapping_test = navigation / "test/test_mapping_control.py"
    if mapping_test.is_file():
        source = mapping_test.read_text(encoding="utf-8")
        anchor = '    assert preview["frame_id"] == "map"\n'
        source = replace_once(
            source,
            anchor,
            anchor
            +
            '    assert preview["full_snapshot"] is True\n'
            '    controller._preview_point_revisions[(12, 24)] = 2\n'
            '    controller._preview_revision = 2\n'
            '    incremental = controller.live_preview(since_revision=1)\n'
            '    assert incremental["full_snapshot"] is False\n'
            '    assert incremental["revision"] == 2\n'
            '    assert incremental["points"] == [[1.0, 2.0]]\n'
            '    assert incremental["robot_path"] == []\n'
            '    controller._preview_full_reset_revision = 2\n'
            '    stale = controller.live_preview(since_revision=1)\n'
            '    assert stale["full_snapshot"] is True\n',
            "incremental mapping snapshot regression test",
        )
        mapping_test.write_text(source, encoding="utf-8", newline="\n")

    bridge = navigation / "robot_local_navigation_v3/lingxi_nav_bridge.py"
    source = bridge.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "from typing import Any\n",
        "from typing import Any\nfrom urllib.parse import parse_qs, urlsplit\n",
        "mapping preview query parsing import",
    )
    source = replace_once(
        source,
        "    def mapping_preview(self) -> dict[str, Any]:\n"
        "        return self.mapping_controller.live_preview()\n",
        "    def mapping_preview(self, since_revision: int | None = None) -> dict[str, Any]:\n"
        "        return self.mapping_controller.live_preview(since_revision=since_revision)\n",
        "mapping preview revision bridge",
    )
    source = replace_once(
        source,
        '        if self.path == "/mapping/preview":\n'
        "            self.write_json(200, self.server.bridge.mapping_preview())\n"
        "            return\n",
        '        if self.path.split("?", 1)[0] == "/mapping/preview":\n'
        "            query = parse_qs(urlsplit(self.path).query)\n"
        '            raw_revision = query.get("since", [""])[0]\n'
        "            try:\n"
        "                since_revision = int(raw_revision) if raw_revision else None\n"
        "            except ValueError:\n"
        "                since_revision = None\n"
        "            self.write_json(200, self.server.bridge.mapping_preview(since_revision))\n"
        "            return\n",
        "mapping preview revision HTTP route",
    )
    bridge.write_text(source, encoding="utf-8", newline="\n")

    client = server / "src/robotClient.ts"
    source = client.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "    mappingPreview() {\n"
        "      return requestJson(`${bridgeBase}/mapping/preview`, { timeoutMs: 5000 });\n"
        "    },\n",
        "    mappingPreview(sinceRevision?: number) {\n"
        "      const query = Number.isSafeInteger(sinceRevision) && Number(sinceRevision) >= 0 ? `?since=${sinceRevision}` : \"\";\n"
        "      return requestJson(`${bridgeBase}/mapping/preview${query}`, { timeoutMs: 5000 });\n"
        "    },\n",
        "incremental mapping preview client",
    )
    client.write_text(source, encoding="utf-8", newline="\n")

    app = server / "src/app.ts"
    source = app.read_text(encoding="utf-8")
    source = replace_once(
        source,
        '  app.get("/api/robots/default/mapping/preview", async () => getRobot().mappingPreview());\n',
        '  app.get("/api/robots/default/mapping/preview", async (request) => {\n'
        '    const query = request.query as { since?: string };\n'
        '    const since = query.since === undefined ? undefined : Number(query.since);\n'
        '    return getRobot().mappingPreview(Number.isSafeInteger(since) && Number(since) >= 0 ? since : undefined);\n'
        '  });\n',
        "incremental mapping preview API query",
    )
    app.write_text(source, encoding="utf-8", newline="\n")

    client_test = server / "test/robotClient.test.ts"
    source = client_test.read_text(encoding="utf-8")
    source = replace_once(
        source,
        '    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ ok: true, points: [] })));\n',
        '    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ ok: true, points: [] })));\n',
        "incremental mapping preview fresh response test fixture",
    )
    source = replace_once(
        source,
        '    await expect(client.mappingPreview()).resolves.toMatchObject({ ok: true, points: [] });\n'
        '    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:18082/mapping/preview", expect.objectContaining({ signal: expect.any(AbortSignal) }));\n',
        '    await expect(client.mappingPreview()).resolves.toMatchObject({ ok: true, points: [] });\n'
        '    await expect(client.mappingPreview(7)).resolves.toMatchObject({ ok: true, points: [] });\n'
        '    expect(fetchMock).toHaveBeenNthCalledWith(1, "http://127.0.0.1:18082/mapping/preview", expect.objectContaining({ signal: expect.any(AbortSignal) }));\n'
        '    expect(fetchMock).toHaveBeenNthCalledWith(2, "http://127.0.0.1:18082/mapping/preview?since=7", expect.objectContaining({ signal: expect.any(AbortSignal) }));\n',
        "incremental mapping preview client regression test",
    )
    client_test.write_text(source, encoding="utf-8", newline="\n")

    script = server / "public/app.js"
    source = script.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "let mappingSessionKey = \"\";\n",
        "let mappingSessionKey = \"\";\nlet mappingPreviewRevision = null;\n",
        "browser mapping preview cursor",
    )
    source = replace_once(
        source,
        "    if (sessionKey !== mappingSessionKey) { mappingPointCells.clear(); mappingRobotPath = []; mappingSessionKey = sessionKey; }\n",
        "    if (sessionKey !== mappingSessionKey) { mappingPointCells.clear(); mappingRobotPath = []; mappingPreviewRevision = null; mappingSessionKey = sessionKey; }\n"
        "    if (payload.full_snapshot) {\n"
        "      mappingPointCells.clear();\n"
        "      mappingRobotPath = [];\n"
        "    }\n",
        "browser map snapshot and delta handling",
    )
    source = replace_once(
        source,
        "    for (const pose of payload.robot_path || []) {\n"
        "      const previous = mappingRobotPath.at(-1);\n"
        "      if (!previous || Math.hypot(Number(pose.x) - previous.x, Number(pose.y) - previous.y) >= 0.1) mappingRobotPath.push({ x: Number(pose.x), y: Number(pose.y) });\n"
        "    }\n",
        "    if (payload.full_snapshot) {\n"
        "      for (const pose of payload.robot_path || []) {\n"
        "        const previous = mappingRobotPath.at(-1);\n"
        "        if (!previous || Math.hypot(Number(pose.x) - previous.x, Number(pose.y) - previous.y) >= 0.1) mappingRobotPath.push({ x: Number(pose.x), y: Number(pose.y) });\n"
        "      }\n"
        "    }\n"
        "    const robotPose = payload.robot;\n"
        "    if (robotPose && Number.isFinite(Number(robotPose.x)) && Number.isFinite(Number(robotPose.y))) {\n"
        "      const previous = mappingRobotPath.at(-1);\n"
        "      if (!previous || Math.hypot(Number(robotPose.x) - previous.x, Number(robotPose.y) - previous.y) >= 0.1) mappingRobotPath.push({ x: Number(robotPose.x), y: Number(robotPose.y) });\n"
        "    }\n"
        "    if (Number.isSafeInteger(payload.revision) && payload.revision >= 0) mappingPreviewRevision = payload.revision;\n",
        "browser incremental map trajectory handling",
    )
    source = replace_once(
        source,
        '  try { renderMappingPreview(await api("/api/robots/default/mapping/preview")); }\n',
        '  try {\n'
        '    const cursor = Number.isSafeInteger(mappingPreviewRevision) ? `?since=${mappingPreviewRevision}` : "";\n'
        '    renderMappingPreview(await api(`/api/robots/default/mapping/preview${cursor}`));\n'
        '  }\n',
        "browser incremental preview request",
    )
    source = replace_once(
        source,
        "  state.mappingPreview = null;\n"
        "  mappingPointCells.clear(); mappingRobotPath = []; mappingSessionKey = name;\n",
        "  state.mappingPreview = null;\n"
        "  mappingPointCells.clear(); mappingRobotPath = []; mappingPreviewRevision = null; mappingSessionKey = name;\n",
        "browser mapping start cursor reset",
    )
    script.write_text(source, encoding="utf-8", newline="\n")

    ui_test = server / "test/uiAssets.test.ts"
    source = ui_test.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "    expect(appScript).toContain('/api/robots/default/mapping/preview');\n",
        "    expect(appScript).toContain('/api/robots/default/mapping/preview');\n"
        '    expect(appScript).toContain("?since=${mappingPreviewRevision}");\n'
        '    expect(appScript).toContain("payload.full_snapshot");\n',
        "incremental map preview UI regression checks",
    )
    ui_test.write_text(source, encoding="utf-8", newline="\n")


def configure_mapping_aimdk_setup(server: Path) -> None:
    mapping = (server / "src/mappingControl.ts").read_text(encoding="utf-8")
    if "config.robot.bridgePort" not in mapping or "/mapping/status" not in mapping:
        raise RuntimeError("R18 mapping sensor diagnostics are not routed to the robot bridge")


def configure_runtime_aimdk_setup(runtime: Path) -> None:
    for relative in ("scripts/navigation_control.sh", "scripts/robot_voice_bridge_control.sh"):
        path = runtime / relative
        source = path.read_text(encoding="utf-8")
        if 'AIMDK_SETUP="${ROBOT_LOCAL_NAV_AIMDK_SETUP:-${ROBOT_AIMDK_SETUP:-}}"' not in source:
            raise RuntimeError(f"{relative} does not accept the configured R19 AIMDK setup")
        if '[[ -n "${AIMDK_SETUP}" && -r "${AIMDK_SETUP}" ]]' not in source:
            raise RuntimeError(f"{relative} does not validate the configured R19 AIMDK setup")
        if '"${AIMDK_SETUP%/setup.bash}"' not in source:
            raise RuntimeError(f"{relative} does not use the selected AIMDK library prefix")
        if "/home/agi/aimdk/install" in source:
            raise RuntimeError(f"{relative} still contains the legacy AIMDK path")
        path.write_text(source, encoding="utf-8", newline="\n")

    control = runtime / "control.sh"
    source = control.read_text(encoding="utf-8")
    source = replace_once(
        source,
        'STATE_DIR="$(root_path /var/lib/sparkrobot/lingxi-nav)"\n',
        'STATE_DIR="$(root_path /var/lib/sparkrobot/lingxi-nav)"\n'
        'CONFIG_DIR="$(root_path /etc/sparkrobot/lingxi-nav)"\n'
        'if [[ -r "${CONFIG_DIR}/console.env" ]]; then set -a; source "${CONFIG_DIR}/console.env"; set +a; fi\n'
        'ROBOT_LOCAL_NAV_AIMDK_SETUP="${ROBOT_LOCAL_NAV_AIMDK_SETUP:-${ROBOT_AIMDK_SETUP:-}}"\n',
        "control AIMDK environment loading",
    )
    source = source.replace(
        'runuser -u agi -- "${CURRENT}/scripts/navigation_control.sh" restart',
        'runuser -u agi -- env ROBOT_LOCAL_NAV_AIMDK_SETUP="${ROBOT_LOCAL_NAV_AIMDK_SETUP}" "${CURRENT}/scripts/navigation_control.sh" restart',
    )
    source = source.replace(
        'runuser -u agi -- "${CURRENT}/scripts/navigation_control.sh" stop',
        'runuser -u agi -- env ROBOT_LOCAL_NAV_AIMDK_SETUP="${ROBOT_LOCAL_NAV_AIMDK_SETUP}" "${CURRENT}/scripts/navigation_control.sh" stop',
    )
    source = source.replace(
        'runuser -u agi -- "${CURRENT}/scripts/navigation_control.sh" status',
        'runuser -u agi -- env ROBOT_LOCAL_NAV_AIMDK_SETUP="${ROBOT_LOCAL_NAV_AIMDK_SETUP}" "${CURRENT}/scripts/navigation_control.sh" status',
    )
    source = source.replace(
        '"${CURRENT}/scripts/robot_voice_bridge_control.sh" stop',
        'ROBOT_LOCAL_NAV_AIMDK_SETUP="${ROBOT_LOCAL_NAV_AIMDK_SETUP}" "${CURRENT}/scripts/robot_voice_bridge_control.sh" stop',
    )
    control.write_text(source, encoding="utf-8", newline="\n")


def configure_config_and_ssh(server: Path) -> None:
    config = (server / "src/config.ts").read_text(encoding="utf-8")
    if "aimdkSetup: string" not in config or "process.env.ROBOT_AIMDK_SETUP" not in config:
        raise RuntimeError("R18 config does not expose ROBOT_AIMDK_SETUP")
    ssh = (server / "src/sshControl.ts").read_text(encoding="utf-8")
    if "commandWithAimdk" not in ssh or "config.robot.aimdkSetup" not in ssh or "client.exec(commandWithAimdk" not in ssh:
        raise RuntimeError("R18 SSH command transport does not propagate the configured AIMDK setup")
    for relative in ("src/systemControl.ts", "src/localizationControl.ts"):
        text = (server / relative).read_text(encoding="utf-8")
        if "config.robot.aimdkSetup" not in text or "/home/agi/aimdk/install/setup.bash" in text:
            raise RuntimeError(f"{relative} is not using the configured AIMDK setup path")


def strip_path_safety_from_template(template: Path) -> None:
    data = json.loads(template.read_text(encoding="utf-8"))
    data.get("navigation", {}).pop("path_safety", None)
    template.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def copy_runtime_dependencies(source: Path, target: Path) -> None:
    skip = {".bin", "@esbuild", "@jridgewell", "@rollup", "@types", "@vitest", "typescript", "tsx", "vite", "vite-node", "vitest"}
    suffixes = {".js", ".cjs", ".mjs", ".json", ".node", ".wasm"}
    for current, directories, files in os.walk(source):
        current_path = Path(current)
        relative = current_path.relative_to(source)
        directories[:] = [name for name in directories if name not in skip and name.lower() not in {"test", "tests", "__tests__", "coverage", "benchmark", "benchmarks", "example", "examples"}]
        for name in files:
            item = current_path / name
            if not item.is_symlink() and item.suffix.lower() in suffixes:
                destination = target / relative / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, destination)


def build_server(r18: Path, work: Path) -> Path:
    server = work / "server"
    copy_tree(r18 / "server", server, {"node_modules", "dist", "coverage"})
    strip_typescript_path_safety(server)
    configure_mobile_mapping_ui(server)
    configure_incremental_mapping_preview(r18 / R18_SOURCE_PATHS[1], server)
    copy_tree(ROOT / "server/node_modules", server / "node_modules")
    configure_config_and_ssh(server)
    configure_mapping_aimdk_setup(server)
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise RuntimeError("npm is required to build the console")
    subprocess.run([npm, "run", "test"], cwd=server, check=True)
    subprocess.run([npm, "run", "build"], cwd=server, check=True)
    navigation_tests = r18 / R18_SOURCE_PATHS[1]
    subprocess.run(
        [shutil.which("python") or shutil.which("python3") or os.sys.executable, "-m", "pytest", "-q", "test/test_mapping_control.py"],
        cwd=navigation_tests,
        env={**os.environ, "PYTHONPATH": str(navigation_tests) + os.pathsep + os.environ.get("PYTHONPATH", "")},
        check=True,
    )
    return server


def build_payload(stage: Path, aimdk_archive: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="r19-source-") as temporary:
        temp = Path(temporary)
        r18 = temp / "r18"
        git_snapshot(r18)
        server = build_server(r18, temp / "build")

        payload = stage / "payload"
        payload.mkdir(parents=True, exist_ok=True)
        console = payload / "console"
        copy_tree(server / "dist", console / "dist")
        shutil.copy2(server / "package.json", console / "package.json")
        shutil.copy2(server / "package-lock.json", console / "package-lock.json")
        copy_runtime_dependencies(server / "node_modules", console / "node_modules")

        node = ROOT / "productization/build/node-arm64"
        java = ROOT / "productization/build/jre17-arm64"
        if not node.is_file() or not (java / "bin/java").is_file():
            raise RuntimeError("missing bundled AArch64 Node or Java runtime")
        (payload / "runtime").mkdir(parents=True, exist_ok=True)
        shutil.copy2(node, payload / "runtime/node")
        (payload / "runtime/node").chmod(0o755)
        copy_tree(java, payload / "runtime/java")

        nav_source = r18 / R18_SOURCE_PATHS[1]
        navigation = payload / "navigation"
        copy_tree(nav_source, navigation, {"__pycache__", ".pytest_cache", "test", "tests"})
        strip_navigation_path_safety(navigation)
        bridge = r18 / "deploy/robot/robot_lingxi_bridge.py"
        shutil.copy2(bridge, navigation / "robot_local_navigation_v3/robot_lingxi_bridge.py")

        jar = ROOT / "internal_deployment/build/robot-local-navigation-java-all.jar"
        sdk = ROOT / "productization/build/linksoul-agentsdk-1.4.0.jar"
        if not jar.is_file() or not sdk.is_file():
            raise RuntimeError("missing V1.3.1 Java agent build inputs")
        (payload / "java").mkdir(parents=True, exist_ok=True)
        shutil.copy2(jar, payload / "java/robot-local-navigation-java-all.jar")
        shutil.copy2(sdk, payload / "java/linksoul-agentsdk-1.4.0.jar")

        aimdk_info = extract_aimdk_messages(aimdk_archive, payload / "aimdk-v1.1.0")
        (payload / "aimdk-v1.1.0/SOURCE.json").write_text(
            json.dumps(aimdk_info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        (stage / "AIMDK-COMPONENT.json").write_text(
            json.dumps(aimdk_info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    for name in ("install.sh", "configure.sh", "control.sh", "quick_install.sh"):
        shutil.copy2(RUNTIME / name, stage / name)
        (stage / name).chmod(0o755)
    for name in ("scripts", "systemd", "templates"):
        copy_tree(RUNTIME / name, stage / name, {"__pycache__", ".pytest_cache"})
    copy_tree(HERE / "docs", stage / "docs", {"__pycache__", ".pytest_cache"})
    strip_path_safety_from_template(stage / "templates/unified_nav_config.json")
    configure_runtime_aimdk_setup(stage)
    shutil.copy2(HERE / "R19-DEPLOYMENT-GUIDE.md", stage / "R19-DEPLOYMENT-GUIDE.md")
    shutil.copy2(HERE / "README.md", stage / "README.md")

    for path in stage.rglob("*.sh"):
        path.chmod(0o755)
    files = [
        {"path": path.relative_to(payload).as_posix(), "sha256": digest(path), "size": path.stat().st_size}
        for path in sorted(item for item in payload.rglob("*") if item.is_file())
    ]
    (stage / "payload-manifest.json").write_text(
        json.dumps({"format": "sparkrobot-r19-full-payload-v1", "files": files}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    info = {
        "format": "sparkrobot-r19-full-release-v1",
        "product_id": "sparkrobot-lingxi-r19-full",
        "version": VERSION,
        "architecture": "aarch64",
        "navigation_baseline": "Lingxi X2 V1.3.1 R18",
        "r18_source_commit": R18_REF,
        "aimdk": "aimdk v1.1.0-gfaf4c94 aimdk_msgs prebuilt_aarch64",
        "mapping": True,
        "mapping_preview_poll_ms": 500,
        "mobile_mapping_landscape": True,
        "mapping_fullscreen_api": True,
        "navigation": True,
        "custom_robot_geometry": False,
        "custom_path_clearance": False,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    (stage / "release-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    required = (
        "payload/console/dist/index.js", "payload/console/dist/public/index.html",
        "payload/runtime/node", "payload/runtime/java/bin/java",
        "payload/console/dist/mappingControl.js",
        "payload/navigation/robot_local_navigation_v3/navigation_client.py",
        "payload/aimdk-v1.1.0/setup.bash", "scripts/verify_payload.py",
        "install.sh", "systemd/lingxi-nav-console.service",
        "docs/新机器人验收清单.md", "docs/内部部署与命令手册.md",
    )
    for relative in required:
        if not (stage / relative).is_file():
            raise RuntimeError(f"release is missing required file: {relative}")
    for relative in (
        "payload/console/dist/app.js", "payload/console/dist/index.js",
        "payload/navigation/robot_local_navigation_v3/navigation_client.py",
        "payload/navigation/robot_local_navigation_v3/models.py",
        "payload/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py",
        "payload/console/dist/mappingControl.js",
        "templates/unified_nav_config.json",
    ):
        path = stage / relative
        if path.suffix == ".json":
            text = path.read_text(encoding="utf-8")
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
        if any(term.lower() in text.lower() for term in (
            "path_safety", "path-safety", "path_clearance", "pathclearance", "robot_radius_m",
            "extra_clearance_m", "robotradius", "extraclearance", "occupiedthreshold", "safe_reference_line",
        )):
            raise RuntimeError(f"custom robot geometry/path safety remains in {relative}")
    mapping_client = (stage / "payload/console/dist/robotClient.js").read_text(encoding="utf-8")
    mapping_bridge = (stage / "payload/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py").read_text(encoding="utf-8")
    mapping_core = (stage / "payload/navigation/robot_local_navigation_v3/mapping_control.py").read_text(encoding="utf-8")
    if "/mapping/start" not in mapping_client or "/mapping/stop" not in mapping_client or "GetStoredMapByName" not in mapping_core:
        raise RuntimeError("R18 live mapping and map-save controller failed the payload audit")
    mapping_html = (stage / "payload/console/dist/public/index.html").read_text(encoding="utf-8")
    mapping_js = (stage / "payload/console/dist/public/app.js").read_text(encoding="utf-8")
    mapping_css = (stage / "payload/console/dist/public/styles.css").read_text(encoding="utf-8")
    if (
        mapping_html.index('class="mapping-live"') > mapping_html.index('class="mapping-side-rail"')
        or 'id="mappingFullscreenBtn"' not in mapping_html
        or 'id="mappingRotatePrompt"' not in mapping_html
        or 'id="refreshMappingBtn" class="secondary mapping-check-btn"' not in mapping_html
        or 'id="mappingQuickDiagnosis"' not in mapping_html
        or 'screen.orientation.lock("landscape")' not in mapping_js
        or 'setInterval(() => void refreshMappingPreview(), 500)' not in mapping_js
        or "@media (max-width: 980px) and (orientation: landscape)" not in mapping_css
    ):
        raise RuntimeError("mobile landscape live-map workspace failed the payload audit")
    if (
        "full_snapshot" not in mapping_core
        or "_preview_point_revisions" not in mapping_core
        or 'query.get("since"' not in mapping_bridge
        or "?since=${sinceRevision}" not in mapping_client
        or "mappingPreview(sinceRevision" not in mapping_client
    ):
        raise RuntimeError("incremental live-map preview transport failed the payload audit")
    node_header = (payload / "runtime/node").read_bytes()[:20]
    if node_header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(node_header[18:20], "little") != 183:
        raise RuntimeError("bundled Node runtime is not an AArch64 ELF64 executable")

    sums = []
    for path in sorted(item for item in stage.rglob("*") if item.is_file() and item.name != "SHA256SUMS"):
        sums.append(f"{digest(path)}  {path.relative_to(stage).as_posix()}")
    (stage / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the full Lingxi X2 V1.3.1 R19 deployment bundle")
    parser.add_argument("--aimdk-archive", required=True, help="user-provided AIMDK v1.1.0 artifacts ZIP")
    parser.add_argument("--output", default=str(HERE / "dist"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"{PACKAGE_NAME}.tar.gz"
    if destination.exists() and not args.force:
        raise SystemExit(f"output exists; pass --force to replace it: {destination}")
    with tempfile.TemporaryDirectory(prefix="r19-full-") as temporary:
        stage = Path(temporary) / PACKAGE_NAME
        stage.mkdir()
        build_payload(stage, Path(args.aimdk_archive).resolve())
        with tarfile.open(destination, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            def normalize(info: tarfile.TarInfo) -> tarfile.TarInfo:
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                if info.isdir():
                    info.mode = 0o755
                elif info.isfile():
                    info.mode = 0o755 if info.name.endswith(".sh") or "/runtime/node" in info.name or "/runtime/java/bin/" in info.name else 0o644
                return info
            archive.add(stage, arcname=PACKAGE_NAME, filter=normalize)
    checksum = digest(destination)
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        f"{checksum}  {destination.name}\n", encoding="ascii", newline="\n")
    print(f"TAR: {destination}\nSIZE: {destination.stat().st_size}\nSHA256: {checksum}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError, zipfile.BadZipFile, tarfile.TarError) as error:
        raise SystemExit(f"R19 build failed: {error}")
