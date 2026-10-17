#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
VERSION = "1.3.1-complete-20260907-r7"
NAME = f"lingxi-v{VERSION}-deployment"
DIST = HERE / "dist"
COMPONENTS = {
    "lingxi-v1.3.1-full-deployment-20260901.zip": ROOT / "lingxi_v1.3.1_deployment/dist/lingxi-v1.3.1-full-deployment-20260901.zip",
    "sparkrobot-lingxi-kb-1.0.2-20260902.zip": ROOT / "knowledge_base_deployment/dist/sparkrobot-lingxi-kb-1.0.2-20260902.zip",
    "sparkrobot-lingxi-linkcraft-addon-1.0.3-20260902.tar.gz": ROOT / "linkcraft_deployment/dist/sparkrobot-lingxi-linkcraft-addon-1.0.3-20260902.tar.gz",
    "sparkrobot-lingxi-consecutive-nav-fix-1.0.1-20260907.tar.gz": ROOT / "consecutive_nav_fix_deployment/dist/sparkrobot-lingxi-consecutive-nav-fix-1.0.1-20260907.tar.gz",
}
BASE_BUNDLE_NAME = "lingxi-v1.3.1-full-deployment-20260901.zip"
BASE_BUNDLE_ROOT = "lingxi-v1.3.1-full-deployment-20260901"
EMBEDDED_COMPONENTS = (
    "aimdk-aarch64-1bde262f-artifacts.7z",
    "sparkrobot-lingxi-nav-internal-1.0.7-20260901.tar.gz",
)
AIMDK_SCRIPT_MEMBER = f"{BASE_BUNDLE_ROOT}/scripts/robot/01-replace-aimdk.sh"
AIMDK_NOUNSET_GUARD = b"set +u\nsource /opt/ros/humble/setup.bash\nset -u"


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
            required = "lingxi-v1.3.1-full-deployment-20260901/packages/sparkrobot-lingxi-nav-internal-1.0.7-20260901.tar.gz"
            if required not in archive.namelist():
                raise RuntimeError("基础导航包缺少导航 TAR")
            aimdk_script = archive.read(AIMDK_SCRIPT_MEMBER)
            if AIMDK_NOUNSET_GUARD not in aimdk_script:
                raise RuntimeError("基础导航包的 AIMDK 替换脚本缺少 ROS nounset 兼容修复")
    if name.startswith("sparkrobot-lingxi-kb"):
        with zipfile.ZipFile(path) as archive:
            if not any(item.endswith("/agent/robot-local-navigation-java-all-kb.jar") for item in archive.namelist()):
                raise RuntimeError("知识库包缺少 Java Agent JAR")


    if name.startswith("sparkrobot-lingxi-consecutive-nav-fix"):
        with tarfile.open(path, "r:gz") as archive:
            prefix = "sparkrobot-lingxi-consecutive-nav-fix-1.0.1-20260907/"
            required = {
                f"{prefix}install.sh",
                f"{prefix}control.sh",
                f"{prefix}payload/lingxi_nav_bridge.py",
                f"{prefix}payload/motion_option_policy.py",
                f"{prefix}payload/sdk_motion_controller.py",
                f"{prefix}payload/navigation_client.py",
                f"{prefix}payload/pnc_response.py",
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
            hashes[name] = actual
    for name in ("README.md", "install_all.sh", "control_all.sh"):
        source = HERE / name
        write_text(stage / name, source.read_text(encoding="utf-8"))
    os.chmod(stage / "install_all.sh", 0o755)
    os.chmod(stage / "control_all.sh", 0o755)
    for source in sorted((HERE / "docs").rglob("*")):
        if source.is_dir():
            continue
        relative = source.relative_to(HERE / "docs")
        write_text(stage / "docs" / relative, source.read_text(encoding="utf-8"))
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
    output.unlink(missing_ok=True)
    with tarfile.open(output, "w:gz", compresslevel=6) as archive:
        archive.add(stage, arcname=NAME, recursive=True)
    return output


def make_zip(stage: Path) -> Path:
    output = DIST / f"{NAME}.zip"
    output.unlink(missing_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            relative = PurePosixPath(NAME) / PurePosixPath(path.relative_to(stage).as_posix())
            info = zipfile.ZipInfo(relative.as_posix())
            executable = path.name in {"install_all.sh", "control_all.sh"}
            info.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
            archive.writestr(info, path.read_bytes())
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
        zip_path = make_zip(stage)
    checksum(tar_path)
    checksum(zip_path)
    print(f"TAR：{tar_path}")
    print(f"ZIP：{zip_path}")
    print(f"TAR SHA-256：{digest(tar_path)}")
    print(f"ZIP SHA-256：{digest(zip_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
