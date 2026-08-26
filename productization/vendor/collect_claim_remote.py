#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import shlex
from pathlib import Path

from deploy_robot import connect, load_paramiko, run_remote, upload


PRODUCTIZATION = Path(__file__).resolve().parents[1]
REMOTE_PREFIX = "/tmp/sparkrobot-device-claim-"


def main() -> int:
    parser = argparse.ArgumentParser(description="厂商专用：通过 SSH 采集新机器人的设备绑定申请")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", default="agi")
    parser.add_argument("--ssh-key")
    parser.add_argument("--password-env", default="SPARKROBOT_SSH_PASSWORD")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    output = Path(args.output).resolve()
    if output.exists():
        raise RuntimeError(f"设备申请文件已存在，拒绝覆盖：{output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    password = os.environ.get(args.password_env) or (None if args.ssh_key else getpass.getpass("机器人 SSH 密码："))
    remote = REMOTE_PREFIX + secrets.token_hex(8)
    paramiko = load_paramiko()
    client = connect(paramiko, args.host, args.port, args.user, password, args.ssh_key)
    try:
        run_remote(client, f"mkdir -m 700 {shlex.quote(remote)} {shlex.quote(remote + '/bootstrap')} {shlex.quote(remote + '/common')}")
        upload(client, PRODUCTIZATION / "bootstrap" / "collect_device_claim.py", remote + "/bootstrap/collect_device_claim.py", 0o500)
        upload(client, PRODUCTIZATION / "common" / "device_identity.py", remote + "/common/device_identity.py", 0o400)
        run_remote(
            client,
            f"python3 {shlex.quote(remote + '/bootstrap/collect_device_claim.py')} "
            f"--output {shlex.quote(remote + '/device-claim.json')}",
            timeout=30,
        )
        sftp = client.open_sftp()
        try:
            sftp.get(remote + "/device-claim.json", str(output))
        finally:
            sftp.close()
        claim = json.loads(output.read_text(encoding="utf-8"))
        if claim.get("format") != "sparkrobot-device-claim-v1":
            raise RuntimeError("机器人返回的设备申请格式无效")
        if claim.get("product_id") != "sparkrobot-lingxi-nav":
            raise RuntimeError("机器人返回的产品 ID 无效")
        if claim.get("architecture") not in {"aarch64", "arm64"}:
            raise RuntimeError("目标机器人不是 ARM64")
        print(f"设备申请已保存：{output}")
        print(f"设备指纹：{claim['device_fingerprint']}")
    except Exception:
        output.unlink(missing_ok=True)
        raise
    finally:
        try:
            cleanup = f"case {shlex.quote(remote)} in {REMOTE_PREFIX}*) rm -rf -- {shlex.quote(remote)} ;; *) exit 2 ;; esac"
            run_remote(client, cleanup, timeout=30)
        finally:
            client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"设备申请采集失败：{exc}", file=os.sys.stderr)
        raise SystemExit(1)
