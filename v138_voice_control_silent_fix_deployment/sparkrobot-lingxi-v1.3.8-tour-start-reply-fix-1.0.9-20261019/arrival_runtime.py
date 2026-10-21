#!/usr/bin/env python3
"""Transactional arrival/TTS patch; reuses the shipped Java runtime validator."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from urllib.request import urlopen

import java_runtime as base

HERE = Path(__file__).resolve().parent
JAR, MANIFEST = base.JAR, base.MANIFEST
NAV_CONTROL = "scripts/navigation_control.sh"
NAV_USER = "agi"
COMPONENTS = ("lingxi_nav_bridge", "map_label_server_v3", "voice_nav_gateway")
digest, regular, json_bytes = base.digest, base.regular, base.json_bytes


def write_atomic(path, data, metadata):
    mode, uid, gid = metadata
    fd, temporary = tempfile.mkstemp(prefix=".arrival-tts-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, uid, gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Installer(base.Installer):
    def __init__(self, root=Path("/"), package=HERE):
        super().__init__(root, package)
        self.backups = self.path("/var/lib/sparkrobot/lingxi-arrival-tts-fix/backups")

    def nav_command(self, operation):
        result = subprocess.run(["runuser", "-u", NAV_USER, "--", "bash",
                                 str(self.release / NAV_CONTROL), operation],
                                capture_output=True, text=True, timeout=200)
        if operation != "status" and result.returncode:
            raise RuntimeError("导航服务操作失败：" + operation + "\n" + (result.stdout + result.stderr)[-3000:])
        return result.stdout

    def nav_state(self):
        result = self.nav_command("status")
        states = {}
        for component in COMPONENTS:
            matches = re.findall(r"^" + component + r"=(running|stopped)(?: pid=\d+)?$", result, re.M)
            if len(matches) != 1:
                raise RuntimeError("无法确认导航服务状态：" + component)
            states[component] = matches[0] == "running"
        if len(set(states.values())) != 1:
            raise RuntimeError("导航服务处于部分启动状态，请先通过网页统一停止或启动后重试")
        return all(states.values())

    def idle_check(self):
        with urlopen("http://127.0.0.1:18082/nav/status", timeout=3) as response:
            status = json.load(response)
        navigation, task = status.get("navigation", {}), status.get("task", {})
        if (navigation.get("status") != "idle" or task.get("active") is not False
                or status.get("auto_return", {}).get("state") != "idle"):
            raise RuntimeError("机器人仍有导航、讲解或自动返航任务；请先在网页结束任务并确认机器人停稳")

    def check_nav_access(self):
        paths = [str(self.release / relative) for relative in self.meta["python_baselines"]
                 if (self.release / relative).exists()]
        code = "import sys; [open(p,'rb').read() for p in sys.argv[1:]]; print('NAV_RUNTIME_ACCESS_OK')"
        base.run(["runuser", "-u", NAV_USER, "--", "python3", "-c", code, *paths])

    def safe_target(self, relative):
        target = self.release / relative
        part = self.release
        for name in Path(relative).parts:
            part = part / name
            if part.is_symlink():
                raise RuntimeError("补丁目标含软链接，拒绝覆盖：" + relative)
        if not target.parent.is_dir():
            raise RuntimeError("补丁目标目录不存在：" + relative)
        return target

    def validate(self):
        manifest = super().validate()
        payload = self.meta["payload_sha256"]
        if set(payload) != {JAR, *self.meta["python_baselines"]}:
            raise RuntimeError("补丁载荷集合与版本元数据不一致")
        for relative, expected in payload.items():
            data = regular(self.package / "payload" / relative)
            if digest(data) != expected:
                raise RuntimeError("补丁载荷校验失败：" + relative)
            if relative.endswith(".py"):
                compile(data, relative, "exec")
        entries = {entry["path"]: entry for entry in manifest["files"]}
        for relative, baseline in self.meta["python_baselines"].items():
            target = self.safe_target(relative)
            if target.exists():
                data = regular(target)
                if digest(data) not in {baseline, payload[relative]}:
                    raise RuntimeError("Python 文件不是已验证版本：" + relative)
                entry = entries.get(relative, {})
                if entry.get("sha256") != digest(data) or entry.get("size") != len(data):
                    raise RuntimeError("Python 运行清单不一致：" + relative)
            elif baseline is not None or relative in entries:
                raise RuntimeError("运行文件缺失或清单异常：" + relative)
        if digest(regular(self.release / NAV_CONTROL)) != self.meta["navigation_control_sha256"]:
            raise RuntimeError("导航控制脚本不是已验证版本，拒绝自动重启")
        self.check_nav_access()
        return manifest

    def snapshot(self, require_idle=False):
        state = {"java": self.state(), "navigation": self.nav_state()}
        if state["navigation"] and require_idle:
            self.idle_check()
        return state

    def stop_services(self):
        self.stop()
        self.nav_command("stop")
        if self.nav_state():
            raise RuntimeError("导航服务未完全停止")

    def restore_services(self, state):
        if state["navigation"]:
            self.nav_command("start")
            if not self.nav_state():
                raise RuntimeError("导航服务启动后未就绪")
        if state["java"]:
            self.start()

    def verify_backup(self, backup, transaction):
        expected_paths = {MANIFEST, *self.meta["payload_sha256"]}
        if (Path(transaction["release"]) != self.release
                or set(transaction["before"]) != expected_paths
                or set(transaction["after"]) != expected_paths):
            raise RuntimeError("备份版本或文件集合不匹配")
        for relative, info in transaction["before"].items():
            if info is not None and digest(regular(backup / "files" / relative)) != info["sha256"]:
                raise RuntimeError("事务备份已损坏：" + relative)

    def restore_files(self, backup, transaction):
        self.verify_backup(backup, transaction)
        for relative, info in transaction["before"].items():
            target = self.safe_target(relative)
            if info is None:
                if target.exists():
                    target.unlink()
            else:
                write_atomic(target, regular(backup / "files" / relative),
                             (info["mode"], info["uid"], info["gid"]))

    def install(self):
        manifest = self.validate()
        before_state = self.snapshot(require_idle=True)
        replacement = {relative: regular(self.package / "payload" / relative)
                       for relative in self.meta["payload_sha256"]}
        if all((self.release / relative).exists()
               and regular(self.release / relative) == data for relative, data in replacement.items()):
            self.check_runtime(live=before_state["java"])
            print("该累计补丁已经安装，文件、权限和服务检查通过；未重复重启")
            return
        new_manifest = json.loads(json.dumps(manifest))
        entries = {entry["path"]: entry for entry in new_manifest["files"]}
        for relative, data in replacement.items():
            entries[relative] = dict(path=relative, sha256=digest(data), size=len(data))
        new_manifest["files"] = [entries[key] for key in sorted(entries)]
        new_manifest["control_reply_fix"] = self.meta["version"]
        new_manifest["arrival_tts_fix"] = self.meta["version"]
        replacement[MANIFEST] = json_bytes(new_manifest)
        self.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%dT%H%M%S-"), dir=self.backups))
        transaction = {"release": str(self.release), "state": before_state, "before": {}, "after": {}}
        reference = (self.release / "navigation/robot_local_navigation_v3/navigation_client.py").stat()
        default_metadata = (stat.S_IMODE(reference.st_mode), reference.st_uid, reference.st_gid)
        for relative, data in replacement.items():
            target = self.safe_target(relative)
            info = None
            if target.exists():
                previous, ref = regular(target), target.stat()
                info = dict(sha256=digest(previous), mode=stat.S_IMODE(ref.st_mode), uid=ref.st_uid, gid=ref.st_gid)
                saved = backup / "files" / relative
                saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                saved.write_bytes(previous)
                saved.chmod(0o600)
            transaction["before"][relative] = info
            transaction["after"][relative] = digest(data)
        (backup / "transaction.json").write_bytes(json_bytes(transaction))
        print("事务备份：" + str(backup), flush=True)
        changed = False
        try:
            self.stop_services()
            if (self.base / "current").resolve(strict=True) != self.release:
                raise RuntimeError("安装期间 current 已切换，拒绝继续写入")
            # Recheck every byte after shutdown, before the first write.
            for relative, info in transaction["before"].items():
                target = self.safe_target(relative)
                if (info is None and target.exists()) or (
                    info is not None and digest(regular(target)) != info["sha256"]
                ):
                    raise RuntimeError("安装期间文件发生变化：" + relative)
            for relative, data in replacement.items():
                info = transaction["before"][relative]
                metadata = (info["mode"], info["uid"], info["gid"]) if info else default_metadata
                changed = True
                write_atomic(self.release / relative, data, metadata)
            self.check_access()
            self.check_nav_access()
            self.restore_services(before_state)
        except Exception:
            print("安装失败，恢复事务备份及安装前服务状态（仅执行一次）", flush=True)
            self.stop_services()
            if changed:
                self.restore_files(backup, transaction)
            self.restore_services(before_state)
            raise
        print("V1.3.8 到达判定与 TTS 确认累计修复安装成功：" + self.meta["version"])
        print("回滚：sudo bash ./install.sh --rollback " + str(backup))
        print("服务保持安装前的运行/停止状态；AIMDK、地图任务、密钥、回调配置和机器人语音栈未修改")

    def rollback(self, backup):
        self.validate()
        if backup.is_symlink():
            raise RuntimeError("备份目录不能是软链接")
        backup = backup.resolve(strict=True)
        if backup.parent != self.backups.resolve():
            raise RuntimeError("请使用本补丁输出的事务备份目录")
        transaction = json.loads(regular(backup / "transaction.json"))
        self.verify_backup(backup, transaction)
        for relative, expected in transaction["after"].items():
            if digest(regular(self.safe_target(relative))) != expected:
                raise RuntimeError("安装后文件已有其他变更，拒绝覆盖：" + relative)
        state = self.snapshot(require_idle=True)
        self.stop_services()
        self.restore_files(backup, transaction)
        self.check_access()
        self.check_nav_access()
        self.restore_services(state)
        print("回滚完成，恢复原文件及元数据，服务保持回滚前状态")


def main():
    parser = argparse.ArgumentParser()
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--check", action="store_true")
    choice.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("请使用 sudo bash ./install.sh")
    base.verify_package(HERE)
    import fcntl
    # Share the prior Java patch lock to prevent competing cumulative installers.
    lock = Path("/var/lib/sparkrobot/lingxi-control-reply-fix/install.lock")
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        installer = Installer()
        if args.check:
            installer.validate()
            state = installer.snapshot(require_idle=True)
            installer.check_runtime(live=state["java"])
            print("版本、载荷、运行清单、权限、启动路径及空闲状态检查通过；未停止或启动服务")
        elif args.rollback:
            installer.rollback(args.rollback)
        else:
            installer.install()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("失败：" + str(error), file=__import__("sys").stderr)
        raise SystemExit(1)
