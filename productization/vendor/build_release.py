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
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
PRODUCTIZATION = PROJECT_ROOT / "productization"
PRODUCT_ID = "sparkrobot-lingxi-nav"
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DEV_NODE_MODULES = {".bin", "@esbuild", "@jridgewell", "@rollup", "@types", "@vitest", "typescript", "tsx", "vitest"}
SKIP_DEPENDENCY_DIRS = {
    ".git", ".github", ".deps", "__pycache__", "benchmark", "benchmarks", "coverage",
    "example", "examples", "obj.target", "test", "tests",
}
RUNTIME_DEPENDENCY_SUFFIXES = {".js", ".cjs", ".mjs", ".json", ".node", ".wasm"}
SECRET_PATTERNS = (
    re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"AKLT[A-Za-z0-9_-]{12,}"),
    re.compile(rb"Bearer\s+eyJ[A-Za-z0-9._-]{20,}", re.I),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{20,}"),
)


def run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: object, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    path.chmod(mode)


def copy_file(source: Path, target: Path, mode: int | None = None) -> None:
    if not source.is_file():
        raise RuntimeError(f"发布输入文件不存在：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if mode is not None:
        target.chmod(mode)


def copy_tree(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise RuntimeError(f"发布输入目录不存在：{source}")
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".pytest_cache"),
    )


def resolve_openssl(explicit: str | None) -> str:
    candidates = [
        explicit,
        shutil.which("openssl"),
        r"C:\Program Files\Git\usr\bin\openssl.exe",
        r"C:\Program Files\Git\mingw64\bin\openssl.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise RuntimeError("找不到 OpenSSL，请安装 OpenSSL 或使用 --openssl 指定路径")


def verify_signature(openssl: str, content: Path, signature: Path, public_key: Path) -> None:
    run([openssl, "dgst", "-sha256", "-verify", str(public_key), "-signature", str(signature), str(content)])


def elf_machine(path: Path) -> int | None:
    data = path.read_bytes()[:20]
    if len(data) < 20 or data[:4] != b"\x7fELF":
        return None
    byteorder = "little" if data[5] == 1 else "big"
    return int.from_bytes(data[18:20], byteorder=byteorder)


def validate_native_inputs(
    navigation: Path,
    node_runtime: Path,
    java_runtime: Path,
    *,
    allow_non_arm64: bool,
) -> None:
    package = navigation / "robot_local_navigation_v3"
    binaries = sorted(package.glob("*.so"))
    if not binaries:
        raise RuntimeError("原生导航目录没有 .so 模块")
    leaked = [path for path in package.glob("*.py") if path.name != "__init__.py"]
    if leaked:
        raise RuntimeError(f"原生导航目录包含业务源码：{leaked[0]}")
    java_binary = java_runtime / "bin" / "java"
    java_release = java_runtime / "release"
    if not java_binary.is_file() or not java_release.is_file():
        raise RuntimeError("Java 运行时不完整，必须提供包含 bin/java 和 release 的 ARM64 JRE 17")
    release_text = java_release.read_text(encoding="utf-8", errors="replace")
    version_match = re.search(r'^JAVA_VERSION="([^"]+)"', release_text, re.MULTILINE)
    if not version_match or not version_match.group(1).startswith("17"):
        raise RuntimeError("Java 运行时必须是 JRE 17")
    if not allow_non_arm64:
        invalid = [path for path in [node_runtime, java_binary, *binaries] if elf_machine(path) != 183]
        if invalid:
            raise RuntimeError(f"正式制品包含非 ARM64 ELF 文件：{invalid[0]}")
        incompatible = [path for path in binaries if "cpython-310" not in path.name]
        if incompatible:
            raise RuntimeError(f"导航模块不是 CPython 3.10 ABI：{incompatible[0].name}")


def validate_java_protection(app_jar: Path, allow_unprotected: bool) -> None:
    with zipfile.ZipFile(app_jar) as archive:
        names = set(archive.namelist())
        marker = "META-INF/sparkrobot-protection.json"
        if marker not in names:
            if allow_unprotected:
                return
            raise RuntimeError("Java JAR 没有厂商保护标记；请先运行 protect_java_jar.py")
        payload = json.loads(archive.read(marker).decode("utf-8"))
        if payload.get("format") != "sparkrobot-java-protection-v1" or payload.get("obfuscated") is not True:
            raise RuntimeError("Java JAR 保护标记无效")


def build_console(server_dir: Path, target: Path) -> None:
    npm = shutil.which("npm")
    node = shutil.which("node")
    if not npm or not node:
        raise RuntimeError("厂商构建机缺少 Node.js 或 npm")
    run([npm, "run", "build"], cwd=server_dir)
    esbuild = server_dir / "node_modules" / "esbuild" / "bin" / "esbuild"
    if not esbuild.is_file():
        raise RuntimeError(f"缺少 esbuild：{esbuild}")
    target.mkdir(parents=True, exist_ok=True)
    run([
        node, str(esbuild), str(server_dir / "dist" / "index.js"),
        "--bundle", "--platform=node", "--format=esm", "--target=node20",
        "--packages=external", "--minify", "--legal-comments=none",
        f"--outfile={target / 'app.mjs'}",
    ])
    public = target / "public"
    public.mkdir(parents=True, exist_ok=True)
    run([
        node, str(esbuild), str(server_dir / "public" / "app.js"),
        "--bundle", "--platform=browser", "--format=esm", "--target=es2020",
        "--minify", "--legal-comments=none", f"--outfile={public / 'app.js'}",
    ])
    run([
        node, str(esbuild), str(server_dir / "public" / "styles.css"),
        "--bundle", "--minify", "--legal-comments=none", f"--outfile={public / 'styles.css'}",
    ])
    copy_file(server_dir / "public" / "index.html", public / "index.html")


def copy_runtime_dependencies(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise RuntimeError(f"Node 运行依赖目录不存在：{source}")
    for current, directories, files in os.walk(source):
        current_path = Path(current)
        relative_dir = current_path.relative_to(source)
        if relative_dir.parts and relative_dir.parts[0] in DEV_NODE_MODULES:
            directories[:] = []
            continue
        directories[:] = [
            name for name in directories
            if name.lower() not in SKIP_DEPENDENCY_DIRS and name not in DEV_NODE_MODULES
        ]
        for name in files:
            source_file = current_path / name
            if source_file.is_symlink() or source_file.suffix.lower() not in RUNTIME_DEPENDENCY_SUFFIXES:
                continue
            target_file = target / relative_dir / name
            copy_file(source_file, target_file)


def write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def assemble_system(version_root: Path, public_key: Path) -> list[dict[str, object]]:
    system = version_root / "system"
    library = system / "usr" / "lib" / "sparkrobot-license"
    for name in ("device_identity.py",):
        copy_file(PRODUCTIZATION / "common" / name, library / name)
    for name in ("verify_license.py", "verify_manifest.py", "verify_stable.py"):
        copy_file(PRODUCTIZATION / "bootstrap" / name, library / name, 0o755)
    copy_file(public_key, system / "etc" / "sparkrobot" / "lingxi-nav" / "vendor-public.pem", 0o644)
    copy_file(
        PRODUCTIZATION / "systemd" / "lingxi-nav-console.service",
        system / "etc" / "systemd" / "system" / "lingxi-nav-console.service",
    )
    copy_file(
        PRODUCTIZATION / "systemd" / "lingxi-java-agent-robot.service",
        system / "etc" / "systemd" / "system" / "lingxi-java-agent-robot.service",
    )
    copy_file(
        PRODUCTIZATION / "runtime" / "product_control.sh",
        system / "usr" / "local" / "sbin" / "sparkrobot-lingxi-product",
        0o755,
    )
    write_executable(
        system / "usr" / "local" / "libexec" / "sparkrobot-license-verify",
        """#!/usr/bin/env bash
set -euo pipefail
exec python3 /usr/lib/sparkrobot-license/verify_license.py \\
  --license /etc/sparkrobot/lingxi-nav/license.json \\
  --signature /etc/sparkrobot/lingxi-nav/license.sig \\
  --public-key /etc/sparkrobot/lingxi-nav/vendor-public.pem "$@"
""",
    )
    write_executable(
        system / "usr" / "local" / "libexec" / "sparkrobot-release-verify",
        """#!/usr/bin/env bash
set -euo pipefail
current="$(readlink -f /opt/sparkrobot/lingxi-nav/current)"
[[ -n "${current}" && -d "${current}" ]] || { echo "产品当前版本不存在" >&2; exit 1; }
version="$(basename "${current}")"
meta="/etc/sparkrobot/lingxi-nav/releases/${version}"
python3 "${current}/system/usr/lib/sparkrobot-license/verify_manifest.py" \\
  --root / --manifest "${meta}/manifest.json" --signature "${meta}/manifest.sig" \\
  --public-key /etc/sparkrobot/lingxi-nav/vendor-public.pem --check-modes "$@"
python3 "${current}/system/usr/lib/sparkrobot-license/verify_stable.py" \\
  --root / --list "${current}/stable-files.json" "$@"
""",
    )
    sudoers = system / "etc" / "sudoers.d" / "sparkrobot-lingxi-nav"
    sudoers.parent.mkdir(parents=True, exist_ok=True)
    sudoers.write_text(
        "run ALL=(root) NOPASSWD: /usr/local/libexec/sparkrobot-license-verify *, "
        "/usr/local/libexec/sparkrobot-release-verify *\n"
        "agi ALL=(root) NOPASSWD: /usr/local/libexec/sparkrobot-license-verify *, "
        "/usr/local/libexec/sparkrobot-release-verify *\n"
        "run ALL=(root) NOPASSWD: /usr/bin/systemctl start lingxi-java-agent-robot.service, "
        "/usr/bin/systemctl stop lingxi-java-agent-robot.service, "
        "/usr/bin/systemctl restart lingxi-java-agent-robot.service, "
        "/usr/bin/systemctl is-active lingxi-java-agent-robot.service, "
        "/usr/bin/systemctl show * lingxi-java-agent-robot.service\n"
        "agi ALL=(root) NOPASSWD: /usr/bin/systemctl restart agibot_software.service\n",
        encoding="utf-8",
        newline="\n",
    )
    sudoers.chmod(0o440)
    entries: list[dict[str, object]] = []
    for path in sorted(item for item in system.rglob("*") if item.is_file()):
        entries.append({"path": path.relative_to(system).as_posix(), "sha256": sha256(path), "size": path.stat().st_size})
    return entries


def desired_rootfs_mode(relative: PurePosixPath) -> int:
    value = relative.as_posix()
    name = relative.name
    if value.endswith("/runtime/node"):
        return 0o550
    if "/runtime/java/bin/" in value or value.endswith("/runtime/java/lib/jspawnhelper"):
        return 0o550
    if "/scripts/" in value and name.endswith(".sh"):
        return 0o550
    if "/system/usr/local/libexec/" in value or "/system/usr/local/sbin/" in value:
        return 0o550
    if name.endswith(".so"):
        return 0o550
    if name.endswith(".jar"):
        return 0o440
    if value.endswith("/system/etc/sudoers.d/sparkrobot-lingxi-nav"):
        return 0o440
    return 0o440


def manifest_entries(rootfs: Path) -> list[dict[str, object]]:
    entries = []
    for path in sorted(item for item in rootfs.rglob("*") if item.is_file()):
        relative = PurePosixPath(path.relative_to(rootfs).as_posix())
        entries.append({
            "path": relative.as_posix(),
            "sha256": sha256(path),
            "size": path.stat().st_size,
            "mode": desired_rootfs_mode(relative),
        })
    return entries


def release_tar_filter(top_level: str):
    def apply(info: tarfile.TarInfo) -> tarfile.TarInfo:
        relative = PurePosixPath(info.name).relative_to(top_level)
        if info.isdir():
            info.mode = 0o750 if relative.parts and relative.parts[0] == "rootfs" else 0o755
        elif relative.parts and relative.parts[0] == "rootfs":
            info.mode = desired_rootfs_mode(PurePosixPath(*relative.parts[1:]))
        elif relative.as_posix() == "install_product.sh":
            info.mode = 0o700
        elif relative.parts and relative.parts[0] in {"license", "release-info.json"}:
            info.mode = 0o600
        else:
            info.mode = 0o644
        return info

    return apply


def scan_release(version_root: Path) -> None:
    navigation = version_root / "navigation" / "robot_local_navigation_v3"
    leaked_python = [path for path in navigation.glob("*.py") if path.name != "__init__.py"]
    leaked_typescript = list((version_root / "console").rglob("*.ts"))
    leaked_java = list(version_root.rglob("*.java"))
    if leaked_python or leaked_typescript or leaked_java:
        leaked = (leaked_python + leaked_typescript + leaked_java)[0]
        raise RuntimeError(f"发布包包含禁止的业务源码：{leaked}")
    for path in (item for item in version_root.rglob("*") if item.is_file() and item.stat().st_size <= 32 * 1024 * 1024):
        data = path.read_bytes()
        for pattern in SECRET_PATTERNS:
            if pattern.search(data):
                raise RuntimeError(f"发布包疑似包含密钥或令牌：{path}")
    forbidden_names = {"secrets.env", ".env", "id_rsa", "id_ed25519"}
    for path in version_root.rglob("*"):
        if path.is_file() and path.name.lower() in forbidden_names:
            raise RuntimeError(f"发布包包含秘密文件：{path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建绑定单台机器人的灵犀导航产品发布包")
    parser.add_argument("--version", required=True)
    parser.add_argument("--license-dir", required=True)
    parser.add_argument("--private-key", required=True)
    parser.add_argument("--public-key", required=True)
    parser.add_argument("--native-navigation", required=True)
    parser.add_argument("--java-app-jar", required=True)
    parser.add_argument("--java-sdk-jar", required=True)
    parser.add_argument("--java-runtime", required=True, help="ARM64 JRE 17 根目录")
    parser.add_argument("--node-runtime", required=True)
    parser.add_argument("--node-modules", required=True)
    parser.add_argument("--server-dir", default=str(PROJECT_ROOT / "server"))
    parser.add_argument("--output-dir", default=str(PRODUCTIZATION / "dist"))
    parser.add_argument("--openssl")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-non-arm64", action="store_true", help="仅用于构建流程测试")
    parser.add_argument("--allow-unprotected-java", action="store_true", help="仅用于构建流程测试")
    parser.add_argument("--keep-directory", action="store_true", help="额外保留已解包发布目录")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not VERSION_RE.fullmatch(args.version):
        raise SystemExit("版本号只能包含字母、数字、点、下划线和连字符")
    openssl = resolve_openssl(args.openssl)
    license_dir = Path(args.license_dir).resolve()
    license_path = license_dir / "license.json"
    license_sig = license_dir / "license.sig"
    public_key = Path(args.public_key).resolve()
    private_key = Path(args.private_key).resolve()
    verify_signature(openssl, license_path, license_sig, public_key)
    license_data = json.loads(license_path.read_text(encoding="utf-8"))
    if license_data.get("format") != "sparkrobot-license-v1" or license_data.get("product_id") != PRODUCT_ID:
        raise RuntimeError("许可证格式或产品 ID 不正确")

    native_navigation = Path(args.native_navigation).resolve()
    node_runtime = Path(args.node_runtime).resolve()
    java_runtime = Path(args.java_runtime).resolve()
    java_app_jar = Path(args.java_app_jar).resolve()
    validate_native_inputs(
        native_navigation,
        node_runtime,
        java_runtime,
        allow_non_arm64=args.allow_non_arm64,
    )
    validate_java_protection(java_app_jar, args.allow_unprotected_java)

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    release_name = f"{PRODUCT_ID}-{args.version}-{str(license_data['license_id'])[:8]}"
    final_dir = output_dir / release_name
    if final_dir.exists() and not args.force:
        raise RuntimeError(f"发布目录已存在，拒绝覆盖：{final_dir}")

    staging_parent = Path(output_dir.anchor) if os.name == "nt" and output_dir.anchor else output_dir
    with tempfile.TemporaryDirectory(prefix=".sparkrobot-release-", dir=staging_parent) as temp_name:
        release = Path(temp_name) / release_name
        version_relative = PurePosixPath("opt/sparkrobot/lingxi-nav/releases") / args.version
        version_root = release / "rootfs" / Path(*version_relative.parts)
        version_root.mkdir(parents=True)

        build_console(Path(args.server_dir).resolve(), version_root / "console")
        copy_runtime_dependencies(Path(args.node_modules).resolve(), version_root / "console" / "node_modules")
        copy_file(node_runtime, version_root / "runtime" / "node", 0o755)
        copy_tree(java_runtime, version_root / "runtime" / "java")
        (version_root / "runtime" / "java" / "bin" / "java").chmod(0o755)
        copy_tree(native_navigation, version_root / "navigation")
        copy_file(java_app_jar, version_root / "java" / "robot-local-navigation-java-all.jar", 0o440)
        copy_file(Path(args.java_sdk_jar).resolve(), version_root / "java" / "linksoul-agentsdk-1.4.0.jar", 0o440)

        scripts = version_root / "scripts"
        for source_name, target_name in (
            ("robot_local_navigation_product.sh", "robot_local_navigation_v3.sh"),
            ("lingxi_java_agent_control.sh", "lingxi_java_agent_control.sh"),
            ("start_robot_voice_bridge.sh", "start_robot_voice_bridge.sh"),
            ("restart_robot_voice_stack.sh", "restart_robot_voice_stack.sh"),
        ):
            copy_file(PRODUCTIZATION / "runtime" / source_name, scripts / target_name, 0o755)
        copy_file(
            PRODUCTIZATION / "runtime" / "unified_nav_config.template.json",
            version_root / "templates" / "unified_nav_config.template.json",
        )
        stable_files = assemble_system(version_root, public_key)
        write_json(version_root / "stable-files.json", {"format": "sparkrobot-stable-files-v1", "files": stable_files})
        scan_release(version_root)

        copy_file(license_path, release / "license" / "license.json", 0o600)
        copy_file(license_sig, release / "license" / "license.sig", 0o600)
        copy_file(public_key, release / "vendor-public.pem", 0o644)

        manifest = {
            "format": "sparkrobot-release-manifest-v1",
            "product_id": PRODUCT_ID,
            "version": args.version,
            "license_id": license_data["license_id"],
            "device_fingerprint": license_data["device_fingerprint"],
            "architecture": "aarch64" if not args.allow_non_arm64 else "development",
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "strict_roots": [version_relative.as_posix()],
            "files": manifest_entries(release / "rootfs"),
        }
        write_json(release / "manifest.json", manifest)
        run([openssl, "dgst", "-sha256", "-sign", str(private_key), "-out", str(release / "manifest.sig"), str(release / "manifest.json")])
        verify_signature(openssl, release / "manifest.json", release / "manifest.sig", public_key)
        write_json(release / "release-info.json", {
            "format": "sparkrobot-vendor-release-v1",
            "product_id": PRODUCT_ID,
            "version": args.version,
            "license_id": license_data["license_id"],
            "customer": license_data.get("customer"),
            "device_fingerprint": license_data["device_fingerprint"],
            "manifest_sha256": sha256(release / "manifest.json"),
        }, 0o600)

        archive = output_dir / f"{release_name}.tar.gz"
        if archive.exists():
            if not args.force:
                raise RuntimeError(f"发布归档已存在，拒绝覆盖：{archive}")
            archive.unlink()
        temporary_archive = Path(temp_name) / f"{release_name}.tar.gz"
        with tarfile.open(temporary_archive, "w:gz", format=tarfile.PAX_FORMAT) as tar:
            tar.add(
                release,
                arcname=release_name,
                recursive=True,
                filter=release_tar_filter(release_name),
            )
        shutil.move(str(temporary_archive), str(archive))
        if args.keep_directory:
            if final_dir.exists():
                shutil.rmtree(final_dir)
            shutil.copytree(release, final_dir)

    if args.keep_directory:
        print(f"设备绑定发布目录：{final_dir}")
    print(f"厂商部署归档：{archive}")
    print(f"归档 SHA-256：{sha256(archive)}")
    print("该归档仅供厂商远程实施，不得交给客户。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"发布构建失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
