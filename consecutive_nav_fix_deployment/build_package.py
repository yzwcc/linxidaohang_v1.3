#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import inspect
import json
import os
import runpy
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
NAV_ROOT = (
    ROOT
    / "robot_local_navigation_v3"
    / "robot_local_navigation_v3-new-auto-return-interaction-end"
)
SOURCE_ROOT = NAV_ROOT / "robot_local_navigation_v3"
TEST_ROOT = NAV_ROOT / "test"
VERSION = "1.0.1-20260907"
NAME = f"sparkrobot-lingxi-consecutive-nav-fix-{VERSION}"
DIST = HERE / "dist"
PAYLOAD_FILES = (
    "lingxi_nav_bridge.py",
    "motion_option_policy.py",
    "sdk_motion_controller.py",
    "navigation_client.py",
    "pnc_response.py",
)
SOURCE_FILES = (
    "install.sh",
    "control.sh",
    "README.md",
    "docs/连续导航卡住修复包部署说明.md",
)
EXECUTABLES = {"install.sh", "control.sh"}


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def normalized_text(path: Path) -> bytes:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return text.encode("utf-8")


def validate_sources() -> None:
    missing = [name for name in PAYLOAD_FILES if not (SOURCE_ROOT / name).is_file()]
    missing += [name for name in SOURCE_FILES if not (HERE / name).is_file()]
    if missing:
        raise RuntimeError("构建源文件缺失：" + ", ".join(missing))

    bridge = (SOURCE_ROOT / "lingxi_nav_bridge.py").read_text(encoding="utf-8")
    policy = (SOURCE_ROOT / "motion_option_policy.py").read_text(encoding="utf-8")
    client = (SOURCE_ROOT / "navigation_client.py").read_text(encoding="utf-8")
    required_markers = {
        "lingxi_nav_bridge.py": (
            "_finish_arrival_motion_after_wait",
            "Tour task motion release wait started",
            "Arrival empty motion ignored",
        ),
        "motion_option_policy.py": (
            "CONTINUOUS_MOTION_IDS = frozenset({6001})",
            "completion_action_after_wait",
        ),
        "navigation_client.py": (
            "PNC_RESPONSE_MAX_RETRIES = 15",
            "get_publishers_info_by_topic",
            "claim_bounded_retry",
        ),
    }
    content = {
        "lingxi_nav_bridge.py": bridge,
        "motion_option_policy.py": policy,
        "navigation_client.py": client,
    }
    for name, markers in required_markers.items():
        for marker in markers:
            if marker not in content[name]:
                raise RuntimeError(f"{name} 缺少修复标记：{marker}")
    forbidden = (
        '_stop_arrival_motion("task_arrival_done")',
        '_stop_arrival_motion("arrival_action_done")',
        '_stop_arrival_motion("before_next_task_step")',
    )
    for marker in forbidden:
        if marker in bridge:
            raise RuntimeError(f"lingxi_nav_bridge.py 仍含危险的普通到点空动作：{marker}")

    control = (HERE / "control.sh").read_text(encoding="utf-8")
    installer = (HERE / "install.sh").read_text(encoding="utf-8")
    for marker in (
        'RUNTIME_GROUP="${SPARKROBOT_RUNTIME_GROUP:-sparkrobot-nav}"',
        "resolve_bridge_run_user",
        "repair_runtime_permissions",
        '拒绝以 root 运行导航 bridge',
    ):
        if marker not in control:
            raise RuntimeError(f"control.sh 缺少共享运行目录修复标记：{marker}")
    if "repair_runtime_permissions" not in installer:
        raise RuntimeError("install.sh 未在安装时修复共享运行目录")

    with tempfile.TemporaryDirectory(prefix="lingxi-consecutive-compile-") as temp:
        for index, name in enumerate(PAYLOAD_FILES):
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import py_compile,sys; py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)",
                    str(SOURCE_ROOT / name),
                    str(Path(temp) / f"{index}.pyc"),
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode:
                raise RuntimeError(f"无法编译 {name}:\n{result.stderr}")


def run_navigation_tests() -> int:
    old_path = list(sys.path)
    sys.path.insert(0, str(NAV_ROOT))
    total = 0
    try:
        for path in sorted(TEST_ROOT.glob("test_*.py")):
            namespace = runpy.run_path(str(path))
            for name, function in sorted(namespace.items()):
                if (
                    name.startswith("test_")
                    and callable(function)
                    and not inspect.signature(function).parameters
                ):
                    function()
                    total += 1
            test_cases = [
                value
                for value in namespace.values()
                if inspect.isclass(value)
                and issubclass(value, unittest.TestCase)
                and value is not unittest.TestCase
            ]
            for test_case in test_cases:
                suite = unittest.defaultTestLoader.loadTestsFromTestCase(test_case)
                result = unittest.TestResult()
                suite.run(result)
                if result.failures or result.errors:
                    details = "\n".join(
                        f"{case}: {message}"
                        for case, message in (*result.failures, *result.errors)
                    )
                    raise AssertionError(f"{path.name} unittest 失败：\n{details}")
                total += result.testsRun
    finally:
        sys.path[:] = old_path
    return total


def write_file(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, mode)


def stage_files(stage: Path) -> None:
    for name in PAYLOAD_FILES:
        write_file(stage / "payload" / name, normalized_text(SOURCE_ROOT / name))
    for name in SOURCE_FILES:
        mode = 0o755 if name in EXECUTABLES else 0o644
        write_file(stage / name, normalized_text(HERE / name), mode)
    release_info = (
        f"version={VERSION}\n"
        "package=sparkrobot-lingxi-consecutive-nav-fix\n"
        "compatibility=lingxi-v1.3.1-complete-20260906 and later compatible navigation layouts\n"
        f"built_at={datetime.now(timezone.utc).isoformat()}\n"
        "scope=continuous-navigation-motion-release-bounded-pnc-discovery-and-runtime-permissions\n"
    )
    write_file(stage / "release-info.txt", release_info.encode("utf-8"))


def write_manifest(stage: Path) -> None:
    files = []
    for path in sorted(item for item in stage.rglob("*") if item.is_file()):
        files.append(
            {
                "path": path.relative_to(stage).as_posix(),
                "sha256": digest(path),
                "size": path.stat().st_size,
                "mode": "0755" if path.name in EXECUTABLES else "0644",
            }
        )
    manifest = {
        "format": "sparkrobot-lingxi-consecutive-nav-fix-v1",
        "version": VERSION,
        "payload_count": len(PAYLOAD_FILES),
        "files": files,
    }
    write_file(
        stage / "manifest.json",
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def write_zip(stage: Path, output: Path) -> None:
    output.unlink(missing_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            relative = PurePosixPath(NAME) / PurePosixPath(path.relative_to(stage).as_posix())
            info = zipfile.ZipInfo(relative.as_posix())
            info.date_time = (2026, 9, 7, 0, 0, 0)
            info.create_system = 3
            mode = 0o755 if path.name in EXECUTABLES else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())


def write_tar(stage: Path, output: Path) -> None:
    output.unlink(missing_ok=True)
    with tarfile.open(output, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            relative = (PurePosixPath(NAME) / PurePosixPath(path.relative_to(stage).as_posix())).as_posix()
            info = archive.gettarinfo(str(path), arcname=relative)
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 1788739200
            info.mode = 0o755 if path.name in EXECUTABLES else 0o644
            with path.open("rb") as handle:
                archive.addfile(info, handle)


def write_checksum(path: Path) -> Path:
    output = path.with_suffix(path.suffix + ".sha256")
    with output.open("w", encoding="ascii", newline="\n") as handle:
        handle.write(f"{digest(path)}  {path.name}\n")
    return output


def main() -> int:
    validate_sources()
    test_count = run_navigation_tests()
    DIST.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lingxi-consecutive-stage-", dir=DIST) as temp:
        stage = Path(temp) / NAME
        stage.mkdir()
        stage_files(stage)
        write_manifest(stage)
        zip_path = DIST / f"{NAME}.zip"
        tar_path = DIST / f"{NAME}.tar.gz"
        write_zip(stage, zip_path)
        write_tar(stage, tar_path)
    write_checksum(zip_path)
    write_checksum(tar_path)
    print(f"导航离线测试通过：{test_count} 项")
    print(f"已生成：{tar_path}")
    print(f"TAR SHA-256：{digest(tar_path)}")
    print(f"已生成：{zip_path}")
    print(f"ZIP SHA-256：{digest(zip_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
