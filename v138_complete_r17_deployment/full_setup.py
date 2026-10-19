#!/usr/bin/env python3
"""Apply the archived native mapping transaction after full runtime installation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

import install as patch

EXPECTED_RELEASE = "1.0.19-20261019-r17"
HERE = Path(__file__).resolve().parent


def status_file(value):
    target = patch.STATE / "full-r17-status.json"
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".full-r17-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(patch.encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(installer, local, remote, *, check=False):
    installer.validate()
    if installer.release.name != EXPECTED_RELEASE:
        raise RuntimeError("完整包原生技能处理仅适用于 " + EXPECTED_RELEASE)
    before = installer.snapshot(require_idle=True)
    installer.check_runtime(live=before["java"])
    # Runtime is merged in the full component. Never silently repair a different build.
    for name, expected in installer.meta["payload_sha256"].items():
        if patch.digest(patch.regular(installer.release / name)) != expected:
            raise RuntimeError("完整包运行文件不符合已合并版本：" + name)
    plans = (local.plan(), remote.call("plan"))
    matches = sum(plan["matches"] for plan in plans)
    changes = sum(plan["changes"] for plan in plans)
    remote.service()
    installer.voice_state()
    print("原生讲解映射：SOC1=" + str(plans[0]["matches"])
          + "，SOC2=" + str(plans[1]["matches"])
          + "；待禁用=" + str(changes), flush=True)
    result = dict(release=EXPECTED_RELEASE, matches=matches, changes=changes,
                  field_audio_verified=False)
    if not matches:
        result["status"] = "not_applicable"
        print("提示：两块控制板均没有历史讲解映射，此项不适用；"
              "未修改原生配置，不能据此认定底层错误声音已消除。", flush=True)
    elif not changes:
        result["status"] = "already_disabled"
        print("目标映射已禁用，运行文件符合累计 1.0.8；未重复重启。", flush=True)
    elif check:
        raise RuntimeError("原生讲解映射仍启用；执行 bash ./install_all.sh native-tour-quiet")
    else:
        transaction = time.strftime("%Y%m%dT%H%M%S-") + uuid.uuid4().hex[:12]
        record = patch.STATE / "transactions" / (transaction + ".json")
        patch.install_transaction(installer, local, remote, *plans, record, transaction)
        result.update(status="installed", transaction=str(record))
        print("原生讲解映射已处理。事务记录：" + str(record), flush=True)
        print("原生配置回滚：sudo python3 " + str(HERE / "install.py")
              + " --rollback " + str(record), flush=True)
    if not check:
        status_file(result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("请通过完整包 install_all.sh 或 sudo python3 执行")
    patch.base.verify_package(HERE)
    import fcntl
    lock = Path("/var/lib/sparkrobot/lingxi-control-reply-fix/install.lock")
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch.RemoteNative(package=HERE) as remote:
            configure(patch.Installer(package=HERE), patch.NativeConfig(), remote, check=args.check)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("原生讲解配置阶段失败：" + str(error), file=sys.stderr)
        raise SystemExit(1)
