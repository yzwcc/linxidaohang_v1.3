#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


REMOTE_PREFIX = "/tmp/sparkrobot-vendor-deploy-"
DEFAULT_INSTALLER = Path(__file__).resolve().parents[1] / "runtime" / "install_product.sh"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_openssl(explicit: str | None) -> str:
    for candidate in (
        explicit,
        shutil.which("openssl"),
        r"C:\Program Files\Git\usr\bin\openssl.exe",
        r"C:\Program Files\Git\mingw64\bin\openssl.exe",
    ):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise RuntimeError("找不到 OpenSSL")


def safe_extract(archive: Path, destination: Path) -> Path:
    top_levels: set[str] = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            posix = PurePosixPath(member.name)
            if posix.is_absolute() or ".." in posix.parts or not posix.parts:
                raise RuntimeError(f"归档包含不安全路径：{member.name}")
            if member.issym() or member.islnk() or member.isdev():
                raise RuntimeError(f"归档包含不允许的链接或设备：{member.name}")
            top_levels.add(posix.parts[0])
        if len(top_levels) != 1:
            raise RuntimeError("发布归档必须只有一个顶层目录")
        try:
            tar.extractall(destination, filter="fully_trusted")
        except TypeError:
            tar.extractall(destination)
    return destination / next(iter(top_levels))


def verify_release(release: Path, openssl: str) -> dict[str, object]:
    manifest_path = release / "manifest.json"
    signature = release / "manifest.sig"
    public_key = release / "vendor-public.pem"
    result = subprocess.run(
        [openssl, "dgst", "-sha256", "-verify", str(public_key), "-signature", str(signature), str(manifest_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or "Verified OK" not in result.stdout:
        raise RuntimeError("厂商部署归档签名无效")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "sparkrobot-release-manifest-v1":
        raise RuntimeError("发布清单格式不受支持")
    declared: set[str] = set()
    rootfs = release / "rootfs"
    for entry in manifest.get("files", []):
        relative = str(entry.get("path") or "")
        posix = PurePosixPath(relative)
        if not relative or posix.is_absolute() or ".." in posix.parts or relative in declared:
            raise RuntimeError(f"发布清单路径无效：{relative}")
        declared.add(relative)
        target = rootfs.joinpath(*posix.parts)
        if not target.is_file() or target.is_symlink():
            raise RuntimeError(f"发布文件缺失：{relative}")
        if target.stat().st_size != int(entry.get("size", -1)) or sha256(target) != entry.get("sha256"):
            raise RuntimeError(f"发布文件校验失败：{relative}")
    for strict_root in manifest.get("strict_roots", []):
        posix = PurePosixPath(str(strict_root))
        protected = rootfs.joinpath(*posix.parts)
        for target in protected.rglob("*"):
            if target.is_symlink():
                raise RuntimeError(f"保护目录包含符号链接：{target}")
            if target.is_file() and target.relative_to(rootfs).as_posix() not in declared:
                raise RuntimeError(f"保护目录包含未签名文件：{target}")
    license_data = json.loads((release / "license" / "license.json").read_text(encoding="utf-8"))
    license_result = subprocess.run(
        [openssl, "dgst", "-sha256", "-verify", str(public_key), "-signature", str(release / "license" / "license.sig"), str(release / "license" / "license.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    if license_result.returncode != 0 or "Verified OK" not in license_result.stdout:
        raise RuntimeError("设备许可证签名无效")
    if manifest.get("license_id") != license_data.get("license_id") or manifest.get("device_fingerprint") != license_data.get("device_fingerprint"):
        raise RuntimeError("发布清单与设备许可证不匹配")
    return manifest


def load_paramiko():
    try:
        import paramiko  # type: ignore
    except ImportError as exc:
        raise RuntimeError("厂商电脑缺少 paramiko，请先在独立虚拟环境安装 paramiko") from exc
    return paramiko


def connect(paramiko, host: str, port: int, user: str, password: str | None, key: str | None, *, sock=None):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=user,
        password=password,
        key_filename=key,
        sock=sock,
        timeout=10,
        banner_timeout=15,
        auth_timeout=15,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def run_remote(client, command: str, *, sudo_password: str | None = None, timeout: int = 180) -> str:
    remote_command = command
    if sudo_password is not None:
        remote_command = "sudo -S -p '' -- bash -lc " + shlex.quote(command)
    stdin, stdout, stderr = client.exec_command(remote_command, timeout=timeout)
    if sudo_password is not None:
        stdin.write(sudo_password + "\n")
        stdin.flush()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    combined = (out + err).strip()
    if code != 0:
        raise RuntimeError(f"远程命令失败（退出码 {code}）：{combined or '无输出'}")
    return combined


def upload(client, source: Path, target: str, mode: int = 0o600) -> None:
    sftp = client.open_sftp()
    try:
        sftp.put(str(source), target)
        sftp.chmod(target, mode)
    finally:
        sftp.close()


def provision_soc2(paramiko, main_client, *, host: str, user: str, password: str, private_key: Path) -> None:
    public_key = Path(str(private_key) + ".pub")
    transport = main_client.get_transport()
    if transport is None:
        raise RuntimeError("主机器人 SSH 连接不可用")
    channel = transport.open_channel("direct-tcpip", (host, 22), ("127.0.0.1", 0), timeout=10)
    soc2 = connect(paramiko, host, 22, user, password, None, sock=channel)
    try:
        public = public_key.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+ sparkrobot-soc2", public):
            raise RuntimeError("SOC2 公钥格式异常")
        restricted = f'from="10.0.1.0/24",restrict {public}'
        command = (
            "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; "
            f"grep -Fq {shlex.quote(public)} ~/.ssh/authorized_keys || printf '%s\\n' {shlex.quote(restricted)} >> ~/.ssh/authorized_keys; "
            "chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys; "
            "sudo -n /usr/bin/systemctl is-active agibot_software.service >/dev/null"
        )
        run_remote(soc2, command, timeout=30)
    finally:
        soc2.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="厂商专用：向单台机器人部署设备绑定灵犀导航产品")
    parser.add_argument("--archive", required=True)
    parser.add_argument("--installer", default=str(DEFAULT_INSTALLER), help="厂商侧安装器，不包含在设备归档中")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", default="run")
    parser.add_argument("--ssh-key")
    parser.add_argument("--password-env", default="SPARKROBOT_SSH_PASSWORD")
    parser.add_argument("--sudo-password-env", default="SPARKROBOT_SUDO_PASSWORD")
    parser.add_argument("--secrets")
    parser.add_argument("--soc2-host", default="10.0.1.42")
    parser.add_argument("--soc2-user", default="run")
    parser.add_argument("--soc2-password-env", default="SPARKROBOT_SOC2_PASSWORD")
    parser.add_argument("--skip-soc2", action="store_true", help="仅升级且已有 SOC2 专用密钥时使用")
    parser.add_argument("--openssl")
    parser.add_argument("--reinstall", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    archive = Path(args.archive).resolve()
    installer_file = Path(args.installer).resolve()
    if not archive.is_file():
        raise RuntimeError("发布归档不存在")
    if not installer_file.is_file():
        raise RuntimeError("厂商安装器不存在")
    secrets_file = Path(args.secrets).resolve() if args.secrets else None
    if not args.verify_only and (not args.host or secrets_file is None or not secrets_file.is_file()):
        raise RuntimeError("实际部署必须提供 --host 和有效的 --secrets 文件")

    remote_dir = REMOTE_PREFIX + secrets.token_hex(8)
    main_client = None
    temp_parent = Path(archive.anchor) if os.name == "nt" and archive.anchor else None
    with tempfile.TemporaryDirectory(prefix=".sparkrobot-vendor-deploy-", dir=temp_parent) as temp_name:
        temp = Path(temp_name)
        release = safe_extract(archive, temp / "release")
        manifest = verify_release(release, resolve_openssl(args.openssl))
        print(f"厂商侧验签通过：version={manifest.get('version')} license={manifest.get('license_id')}")
        if args.verify_only:
            print("发布归档离线验证通过")
            return 0

        assert args.host and secrets_file is not None
        paramiko = load_paramiko()
        password = os.environ.get(args.password_env) or (None if args.ssh_key else getpass.getpass("主机器人 SSH 密码："))
        sudo_password = os.environ.get(args.sudo_password_env) or getpass.getpass("主机器人 sudo 密码：")
        soc2_password = None
        if not args.skip_soc2:
            soc2_password = os.environ.get(args.soc2_password_env) or getpass.getpass("SOC2 SSH 密码：")

        soc2_private = temp / "sparkrobot-soc2"
        if not args.skip_soc2:
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "sparkrobot-soc2", "-f", str(soc2_private)], check=True)

        main_client = connect(paramiko, args.host, args.port, args.user, password, args.ssh_key)
        try:
            identity = run_remote(main_client, "printf 'host='; hostname; printf '\\narch='; uname -m; printf '\\nuser='; id -un")
            print("目标机器人：" + identity.replace("\n", "，"))
            run_remote(main_client, f"mkdir -m 700 {shlex.quote(remote_dir)}")
            remote_archive = f"{remote_dir}/release.tar.gz"
            remote_secrets = f"{remote_dir}/secrets.env"
            remote_installer = f"{remote_dir}/install_product.sh"
            upload(main_client, archive, remote_archive)
            upload(main_client, secrets_file, remote_secrets)
            upload(main_client, installer_file, remote_installer, 0o700)
            remote_soc2_key = ""
            if not args.skip_soc2:
                assert soc2_password is not None
                provision_soc2(paramiko, main_client, host=args.soc2_host, user=args.soc2_user, password=soc2_password, private_key=soc2_private)
                remote_soc2_key = f"{remote_dir}/soc2-ed25519"
                upload(main_client, soc2_private, remote_soc2_key)

            top = release.name
            prepare = "; ".join([
                "set -euo pipefail",
                f"mkdir -p {shlex.quote(remote_dir + '/unpacked')}",
                f"tar -xzf {shlex.quote(remote_archive)} -C {shlex.quote(remote_dir + '/unpacked')}",
            ])
            install = [
                prepare + "; " + f"bash {shlex.quote(remote_installer)}",
                f"--release-dir {shlex.quote(remote_dir + '/unpacked/' + top)}",
                f"--secrets {shlex.quote(remote_secrets)}",
            ]
            if remote_soc2_key:
                install.append(f"--soc2-private-key {shlex.quote(remote_soc2_key)}")
            if args.reinstall:
                install.append("--reinstall")
            output = run_remote(main_client, " ".join(install), sudo_password=sudo_password, timeout=600)
            print(output)
            status = run_remote(main_client, "/usr/local/sbin/sparkrobot-lingxi-product status", timeout=60)
            print(status)
        finally:
            if main_client is not None:
                try:
                    cleanup = f"case {shlex.quote(remote_dir)} in {REMOTE_PREFIX}*) rm -rf -- {shlex.quote(remote_dir)} ;; *) exit 2 ;; esac"
                    run_remote(main_client, cleanup, sudo_password=sudo_password, timeout=30)
                except Exception as cleanup_error:
                    print(f"警告：远程暂存清理失败：{cleanup_error}", file=sys.stderr)
                main_client.close()
    print(f"厂商部署完成：http://{args.host}:3000")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, tarfile.TarError, json.JSONDecodeError) as exc:
        print(f"厂商部署失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
