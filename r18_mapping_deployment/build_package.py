from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
NAV_SOURCE = ROOT / "robot_local_navigation_v3" / "robot_local_navigation_v3-new-auto-return-interaction-end"
NODE_RUNTIME = ROOT / "productization" / "build" / "node-arm64"
VERSION = "1.1.0-20261026-r18-mapfit16"
PACKAGE_PREFIX = "sparkrobot-lingxi-r18-mapping"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_arm64_elf(path: Path) -> None:
    header = path.read_bytes()[:20]
    if len(header) < 20 or header[:4] != b"\x7fELF" or header[4:6] != b"\x02\x01":
        raise RuntimeError("Node 运行时不是 ELF64 little-endian Linux 可执行文件")
    if int.from_bytes(header[18:20], "little") != 183:
        raise RuntimeError("Node 运行时 ELF machine 不是 AArch64")


def copy_tree(source: Path, target: Path, excludes: set[str] | None = None) -> None:
    if not source.is_dir():
        raise RuntimeError(f"缺少构建目录：{source}")
    ignore = shutil.ignore_patterns(*sorted(excludes)) if excludes else None
    shutil.copytree(source, target, ignore=ignore)


def copy_file(source: Path, target: Path, mode: int | None = None) -> None:
    if not source.is_file():
        raise RuntimeError(f"缺少构建文件：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix == ".sh":
        target.write_bytes(source.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
        shutil.copystat(source, target)
    else:
        shutil.copy2(source, target)
    if mode is not None:
        target.chmod(mode)


def build(stage: Path, server: Path) -> None:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise RuntimeError("构建机缺少 npm")
    verify_arm64_elf(NODE_RUNTIME)
    subprocess.run([npm, "run", "build"], cwd=server, check=True)
    payload = stage / "payload"
    console = payload / "server"
    copy_tree(server / "dist", console / "dist")
    copy_tree(server / "public", console / "public")
    copy_tree(server / "node_modules", console / "node_modules", {
        ".bin", "@esbuild", "@jridgewell", "@rollup", "@types", "@vitest",
        "typescript", "tsx", "vite", "vite-node", "vitest", "test", "tests",
        "__tests__", "coverage", "benchmark", "benchmarks", "example", "examples",
        "build", "obj", "*.tlog", "*.obj", "*.lastbuildstate", "cpu-features",
    })
    copy_file(server / "package.json", console / "package.json")
    copy_file(server / "package-lock.json", console / "package-lock.json")
    copy_file(NODE_RUNTIME, payload / "runtime" / "bin" / "node", 0o755)

    navigation = payload / "navigation"
    for name in ("robot_local_navigation_v3", "python_bridge", "resource"):
        copy_tree(NAV_SOURCE / name, navigation / name, {"__pycache__", ".pytest_cache", "test", "tests"})
    for name in ("setup.py", "package.xml", "ros_dds_configuration.xml"):
        copy_file(NAV_SOURCE / name, navigation / name)
    (navigation / "scripts").mkdir(parents=True, exist_ok=True)
    for name in ("robot_local_navigation_v3.sh", "start_lingxi_nav_bridge.sh", "start_voice_nav_gateway.sh"):
        copy_file(NAV_SOURCE / "scripts" / name, navigation / "scripts" / name, 0o755)

    for required in (
        console / "dist" / "index.js",
        console / "dist" / "mappingControl.js",
        console / "public" / "app.js",
        console / "node_modules" / "fastify" / "package.json",
        payload / "runtime" / "bin" / "node",
        navigation / "robot_local_navigation_v3" / "mapping_control.py",
        navigation / "scripts" / "robot_local_navigation_v3.sh",
    ):
        if not required.is_file():
            raise RuntimeError(f"制品缺少关键文件：{required.relative_to(stage)}")

    files = []
    for path in sorted(item for item in payload.rglob("*") if item.is_file()):
        files.append({
            "path": path.relative_to(payload).as_posix(),
            "sha256": sha256(path),
            "size": path.stat().st_size,
        })
    (stage / "payload-manifest.json").write_text(
        json.dumps({"format": "sparkrobot-r18-payload-v1", "files": files}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    scripts = [HERE / "install.sh", HERE / "README.md"]
    for source in scripts:
        copy_file(source, stage / source.name, 0o755 if source.suffix == ".sh" else None)
    (stage / "server.env.template").write_text(
        "\n".join((
            "PORT=3000",
            "HOST=0.0.0.0",
            "DEPLOYMENT_MODE=robot",
            "ROBOT_CONNECTION_MODE=direct",
            "ROBOT_HOST=127.0.0.1",
            "ROBOT_BRIDGE_PORT=18082",
            "ROBOT_WEB_PORT=8002",
            "ROBOT_MAP_ROOT=/agibot/data/var/MapManagerModule/",
            "ROBOT_SSH_USER=agi",
            "ROBOT_SYSTEM_SSH_USER=run",
            "ROBOT_NAV_HOME=/home/agi/robot_local_navigation_v3",
            "ROBOT_AIMDK_SETUP=",
            "JAVA_AGENT_EXECUTION_MODE=robot",
            "ROBOT_JAVA_AGENT_HOME=/home/run/lingxi_java_agent",
            "ROBOT_JAVA_AGENT_BRIDGE_PORT=18083",
            "JAVA_AGENT_AUTO_START=false",
            "DEV_AUTH_BYPASS=true",
            "TOKEN_SECRET=replace-during-install",
            "",
        )),
        encoding="utf-8",
        newline="\n",
    )
    verifier = '''#!/usr/bin/env python3
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
manifest = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
expected = {item["path"]: item for item in manifest.get("files", [])}
actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
if actual != set(expected):
    raise SystemExit("payload 文件集合与 manifest 不一致")
for relative, item in expected.items():
    data = (root / relative).read_bytes()
    if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise SystemExit(f"payload 校验失败：{relative}")
print(f"payload OK: {len(expected)} files")
'''
    (stage / "verify_payload.py").write_text(verifier, encoding="utf-8", newline="\n")
    sums = []
    for path in sorted(item for item in stage.rglob("*") if item.is_file()):
        if path.name != "SHA256SUMS":
            sums.append(f"{sha256(path)}  {path.relative_to(stage).as_posix()}")
    (stage / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="ascii", newline="\n")


def create_archive(stage: Path, output: Path, force: bool) -> Path:
    archive_path = output / f"{PACKAGE_PREFIX}-{VERSION}.tar.gz"
    if archive_path.exists() and not force:
        raise RuntimeError(f"输出已存在，拒绝覆盖：{archive_path}")
    temporary = archive_path.with_suffix(archive_path.suffix + ".tmp")
    with tarfile.open(temporary, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        def normalize(info: tarfile.TarInfo) -> tarfile.TarInfo:
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            if info.isdir():
                info.mode = 0o755
            elif info.name.endswith(".sh") or info.name.endswith("/runtime/bin/node"):
                info.mode = 0o755
            elif info.isfile():
                info.mode = 0o644
            return info
        archive.add(stage, arcname=PACKAGE_PREFIX, filter=normalize)
    temporary.replace(archive_path)
    sidecar = archive_path.with_suffix(archive_path.suffix + ".sha256")
    sidecar.write_text(f"{sha256(archive_path)}  {archive_path.name}\n", encoding="ascii", newline="\n")
    return archive_path


def audit_archive(archive_path: Path, stage: Path) -> None:
    with tarfile.open(archive_path, "r:gz") as archive:
        root = PACKAGE_PREFIX
        members = {member.name: member for member in archive.getmembers()}
        expected_members = {root} | {f"{root}/{path.relative_to(stage).as_posix()}" for path in stage.rglob("*")}
        if set(members) != expected_members:
            raise RuntimeError("归档文件集合与 staging 目录不一致")
        sums_lines = (stage / "SHA256SUMS").read_text(encoding="ascii").splitlines()
        for line in sums_lines:
            expected, relative = line.split("  ", 1)
            member_name = f"{root}/{relative}"
            if member_name not in members:
                raise RuntimeError(f"SHA256SUMS 引用的文件缺失：{relative}")
            if hashlib.sha256((stage / relative).read_bytes()).hexdigest() != expected:
                raise RuntimeError(f"SHA256SUMS 校验失败：{relative}")
        if f"{root}/payload-manifest.json" not in members:
            raise RuntimeError("归档缺少 payload-manifest.json")
        manifest = json.loads((stage / "payload-manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format") != "sparkrobot-r18-payload-v1":
            raise RuntimeError("payload manifest 格式不正确")
        for entry in manifest.get("files", []):
            member_name = f"{root}/payload/{entry['path']}"
            if member_name not in members:
                raise RuntimeError(f"归档缺少 payload 文件：{entry['path']}")
            data = (stage / "payload" / entry["path"]).read_bytes()
            if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise RuntimeError(f"payload 校验失败：{entry['path']}")
        for path in stage.rglob("*"):
            if not path.is_file() or not path.name.endswith((".py", ".ts", ".js", ".sh", ".md", ".json")):
                continue
            data = path.read_bytes()
            forbidden = (
                b"ROBOT_LOCAL_NAV_AIMDK_SETUP",
                b"/home/agi/aimdk",
            )
            if any(value in data for value in forbidden):
                raise RuntimeError(f"归档包含旧版 AIMDK 默认路径：{path.relative_to(stage)}")
        for relative in (
            "install.sh",
        "payload/runtime/bin/node",
            "payload/navigation/scripts/robot_local_navigation_v3.sh",
            "payload/navigation/scripts/start_lingxi_nav_bridge.sh",
            "payload/navigation/scripts/start_voice_nav_gateway.sh",
        ):
            member = members.get(f"{root}/{relative}")
            if member is None:
                raise RuntimeError(f"归档缺少脚本：{relative}")
            data = archive.extractfile(member).read()
            if relative.endswith(".sh") and b"\r" in data:
                raise RuntimeError(f"Shell 脚本包含 CR 行尾：{relative}")
            if not member.mode & 0o111:
                raise RuntimeError(f"可执行文件缺少执行权限：{relative}")
            if relative.endswith("/runtime/bin/node"):
                node_header = data[:20]
                if len(node_header) < 20 or node_header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(node_header[18:20], "little") != 183:
                    raise RuntimeError("归档中的 Node 不是 AArch64 ELF64 可执行文件")
        env_member = f"{root}/server.env.template"
        if env_member not in members:
            raise RuntimeError("归档缺少安全环境模板")
        env_data = (stage / "server.env.template").read_bytes()
        if re.search(rb"(?m)^(?:ROBOT_SSH_PASSWORD|ROBOT_SYSTEM_SSH_PASSWORD)=\S+", env_data):
            raise RuntimeError("默认环境模板包含非空 SSH 凭据")
        unsafe_names = [
            name for name in members
            if Path(name).name == ".env" or Path(name).suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
        ]
        if unsafe_names:
            raise RuntimeError(f"归档包含本机环境文件或私钥：{unsafe_names[0]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Lingxi R18 AIMDK v1.1.0 mapping update package")
    parser.add_argument("--server", default=str(ROOT / "server"))
    parser.add_argument("--output", default=str(HERE / "dist"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".r18-package-", dir=output) as temp:
        stage = Path(temp) / PACKAGE_PREFIX
        stage.mkdir()
        build(stage, Path(args.server).resolve())
        archive = create_archive(stage, output, args.force)
        try:
            audit_archive(archive, stage)
        except Exception:
            archive.unlink(missing_ok=True)
            archive.with_suffix(archive.suffix + ".sha256").unlink(missing_ok=True)
            raise
    print(f"Package: {archive}")
    print(f"SHA-256: {sha256(archive)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"R18 包构建失败：{exc}")
