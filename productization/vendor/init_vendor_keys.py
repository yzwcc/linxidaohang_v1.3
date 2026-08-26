#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def resolve_openssl(explicit: str | None) -> str:
    for candidate in (explicit, shutil.which("openssl"), r"C:\Program Files\Git\usr\bin\openssl.exe"):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise SystemExit("找不到 OpenSSL，请使用 --openssl 指定路径")


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化厂商离线签名密钥")
    parser.add_argument("--output-dir", default="productization/vendor-private")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--unencrypted", action="store_true", help="仅用于隔离测试环境")
    parser.add_argument("--openssl")
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    openssl = resolve_openssl(args.openssl)
    private_key = output / "license-signing-private.pem"
    public_key = output / "license-signing-public.pem"
    output.mkdir(parents=True, exist_ok=True)
    if private_key.exists() and not args.force:
        raise SystemExit(f"私钥已存在，拒绝覆盖：{private_key}")
    command = [openssl, "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072"]
    if not args.unencrypted:
        command.append("-aes-256-cbc")
    command.extend(["-out", str(private_key)])
    run(command)
    run([openssl, "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)])
    try:
        os.chmod(private_key, 0o600)
        os.chmod(public_key, 0o644)
    except OSError:
        pass
    print(f"厂商私钥：{private_key}")
    print(f"产品公钥：{public_key}")
    print("私钥只能离线保存在厂商侧，不得复制到机器人或交付包。")
    if args.unencrypted:
        print("警告：当前为不加密测试私钥，不得用于正式客户设备。")
    else:
        print("正式私钥已使用 AES-256 加密；签发和构建时由厂商人员交互输入口令。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
