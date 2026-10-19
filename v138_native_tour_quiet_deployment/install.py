#!/usr/bin/env python3
"""Coordinate the unchanged 1.0.8 runtime and the firmware mapping transaction."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid

import arrival_runtime as runtime
from arrival_runtime import *
from native_tour_quiet import NativeConfig, encoded

HERE = Path(__file__).resolve().parent
STATE = Path("/var/lib/sparkrobot/lingxi-native-tour-quiet")
VOICE_SCRIPT = "scripts/robot_voice_bridge_control.sh"


def run_command(args, **kwargs):
    return subprocess.run(args, check=True, timeout=180, **kwargs)


def ssh_settings(path):
    wanted = {"SOC2_HOST", "SOC2_USER", "SOC2_SSH_KEY", "SOC2_KNOWN_HOSTS_FILE"}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip().removeprefix("export ")
        key, separator, raw = value.partition("=")
        if separator and key.strip() in wanted:
            fields = shlex.split(raw, comments=True)
            if len(fields) != 1 or "$" in fields[0] or "`" in fields[0]:
                raise RuntimeError("SOC2 SSH 配置必须是字面值：" + key.strip())
            values[key.strip()] = fields[0]
    host, user = values.get("SOC2_HOST", ""), values.get("SOC2_USER", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]*", host):
        raise RuntimeError("runtime.env 缺少明确的 SOC2_HOST")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", user):
        raise RuntimeError("runtime.env 缺少明确的 SOC2_USER")
    key = values.get("SOC2_SSH_KEY", "/etc/sparkrobot/lingxi-nav/soc2-ed25519")
    known = values.get("SOC2_KNOWN_HOSTS_FILE", "/var/lib/sparkrobot/lingxi-nav/ssh/soc2-known-hosts")
    for item in (key, known):
        if not Path(item).is_absolute() or not Path(item).is_file():
            raise RuntimeError("SOC2 SSH 文件缺失：" + item)
    options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
               "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3",
               "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + known,
               "-i", key]
    return options, user + "@" + host


class RemoteNative:
    def __init__(self, package=HERE, settings=Path("/etc/sparkrobot/lingxi-nav/runtime.env")):
        self.options, self.destination = ssh_settings(settings)
        self.package = Path(package)
        self.directory = None
        self.serial = 0

    def ssh(self, command, *, tty=False, **kwargs):
        return run_command(["ssh", "-tt" if tty else "-T", *self.options,
                            self.destination, command], **kwargs)

    def output(self, command):
        return self.ssh(command, capture_output=True, text=True).stdout.strip()

    def __enter__(self):
        try:
            directory = self.output("mktemp -d /tmp/lingxi-tour-quiet.XXXXXXXXXX")
            if not re.fullmatch(r"/tmp/lingxi-tour-quiet\.[A-Za-z0-9]{10}", directory):
                raise RuntimeError("SOC2 未返回有效的临时目录")
            self.directory = directory
            script = directory + "/native.py"
            upload = ("import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
                      "p.write_bytes(sys.stdin.buffer.read()); p.chmod(0o600)")
            self.ssh("python3 -c " + shlex.quote(upload) + " " + shlex.quote(script),
                     input=(self.package / "native_tour_quiet.py").read_bytes(),
                     capture_output=True)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def call(self, mode, transaction=None, expected=None):
        self.serial += 1
        report = self.directory + "/report-" + str(self.serial) + ".json"
        args = ["python3", self.directory + "/native.py", mode, "--report", report]
        if transaction:
            args += ["--transaction", transaction]
        if expected:
            args += ["--expected", expected]
        command = "LC_ALL=C sudo -n " + shlex.join(args)
        result = subprocess.run(["ssh", "-T", *self.options, self.destination, command],
                                capture_output=True, text=True, timeout=180)
        if result.returncode:
            # Retry only sudo authentication failure, never a helper failure.
            if not re.search(r"sudo:.*(?:password is required|a terminal is required|not allowed to execute|not in the sudoers)",
                             result.stderr, re.I):
                raise RuntimeError("SOC2 原生技能操作失败：" + (result.stderr or result.stdout)[-1600:])
            if not sys.stdin.isatty():
                raise RuntimeError("SOC2 需要 sudo 密码；请在交互终端执行安装命令")
            print("SOC2 原生技能配置需要权限，请输入 " + self.destination + " 的 sudo 密码。", flush=True)
            self.ssh("sudo " + shlex.join(args), tty=True)
        read = "import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))"
        return json.loads(self.output("python3 -c " + shlex.quote(read) + " " + shlex.quote(report)))

    def service(self):
        state = self.output("systemctl show agibot_software.service --property=ActiveState --value")
        if state not in {"active", "inactive", "failed"}:
            raise RuntimeError("SOC2 原生服务状态不稳定：" + state)
        return state

    def reload(self, state):
        if state != "active":
            print("SOC2 原生语音服务原本未启动；配置将在下一次启动时加载。", flush=True)
            return
        print("重新加载 SOC2 原生语音技能配置并检查进程。", flush=True)
        result = self.call("reload")
        if result.get("reloaded") is not True or result.get("active") is not True:
            raise RuntimeError("SOC2 原生语音服务重启后未就绪")

    def __exit__(self, *ignored):
        if self.directory:
            cleanup = ("import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
                       "files=list(p.iterdir()); "
                       "assert all(x.name == 'native.py' or "
                       "(x.name.startswith('report-') and x.suffix == '.json') for x in files); "
                       "[x.unlink() for x in files]; p.rmdir()")
            try:
                self.ssh("python3 -c " + shlex.quote(cleanup) + " " + shlex.quote(self.directory),
                         capture_output=True, text=True)
            except Exception:
                print("提示：SOC2 临时辅助目录保留：" + self.directory, file=sys.stderr)


class Installer(runtime.Installer):
    def __init__(self, root=Path("/"), package=HERE):
        super().__init__(root, package)
        self.backups = self.path(str(STATE / "runtime-backups"))

    def validate(self):
        base.Installer.validate(self)
        for relative, allowed in self.meta.get("python_accepted_sha256", {}).items():
            target = self.safe_target(relative)
            actual = digest(regular(target)) if target.exists() else None
            if actual not in allowed and actual != self.meta["payload_sha256"][relative]:
                raise RuntimeError("Python 文件不是已验证版本：" + relative)
            self.meta["python_baselines"][relative] = actual
        return super().validate()

    def voice_state(self):
        script = self.release / VOICE_SCRIPT
        manifest = json.loads(regular(self.release / MANIFEST))
        expected = next((item for item in manifest["files"] if item["path"] == VOICE_SCRIPT), None)
        if expected is None or digest(regular(script)) != expected["sha256"]:
            raise RuntimeError("语音 bridge 控制脚本清单不一致")
        result = subprocess.run(["bash", str(script), "status"], capture_output=True,
                                text=True, timeout=30)
        states = re.findall(r"^robot_voice_bridge=(running|stopped)(?: pid=\d+)?$", result.stdout, re.M)
        if len(states) != 1:
            raise RuntimeError("无法确认机器人语音 bridge 状态：" + result.stdout[-500:])
        return states[0] == "running"

    def restart_voice(self, active):
        if active:
            run_command(["bash", str(self.release / VOICE_SCRIPT), "restart"])
            if not self.voice_state():
                raise RuntimeError("机器人语音 bridge 重启后未就绪")

    def runtime_install(self):
        output = io.StringIO()
        before = set(self.backups.glob("*/transaction.json"))
        with contextlib.redirect_stdout(output):
            super().install()
        new = set(self.backups.glob("*/transaction.json")) - before
        if len(new) > 1:
            raise RuntimeError("运行文件备份数量异常")
        return next(iter(new)).parent if new else None

    def runtime_rollback(self, backup):
        if backup:
            with contextlib.redirect_stdout(io.StringIO()):
                super().rollback(Path(backup))


def load_transaction(path):
    if path.is_symlink() or path.parent.resolve() != (STATE / "transactions").resolve():
        raise RuntimeError("请使用本增量包输出的事务记录文件")
    transaction = json.loads(regular(path))
    if transaction.get("version") != "1.0.10-20261019":
        raise RuntimeError("回滚事务版本不匹配")
    return transaction


def install_transaction(installer, local, remote, plan_local, plan_remote, record_path, transaction):
    record = dict(version="1.0.10-20261019", transaction=transaction, state="prepared",
                  runtime_backup=None, local_applied=False, remote_started=False, remote_applied=False,
                  service=remote.service(), before=installer.snapshot(require_idle=True),
                  voice=installer.voice_state(), native_changes=plan_local["changes"] + plan_remote["changes"])
    record_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    record_path.write_bytes(encoded(record))
    record_path.chmod(0o600)

    def save():
        record_path.write_bytes(encoded(record))

    try:
        installer.stop_services()
        local.apply(transaction, plan_local["fingerprint"])
        record["local_applied"] = True
        save()
        record["remote_started"] = True
        save()
        remote.call("apply", transaction, plan_remote["fingerprint"])
        record["remote_applied"] = True
        save()
        backup = installer.runtime_install()
        record["runtime_backup"] = str(backup) if backup else None
        save()
        local_after, remote_after = local.plan(), remote.call("verify")
        if local_after["changes"] or remote_after["changes"]:
            raise RuntimeError("原生讲解重复映射未完全禁用")
        if record["native_changes"]:
            remote.reload(record["service"])
            installer.restart_voice(record["voice"])
        installer.restore_services(record["before"])
        installer.check_runtime(live=record["before"]["java"])
        record["state"] = "installed"
        save()
    except BaseException:
        record["state"] = "recovering"
        errors = []
        journal_error = None
        try:
            save()
        except OSError as failure:
            journal_error = str(failure)

        def recover(label, operation):
            try:
                operation()
            except BaseException as recovery:
                errors.append(label + ": " + str(recovery))

        recover("stop_runtime", installer.stop_services)
        recover("runtime_files", lambda: installer.runtime_rollback(record["runtime_backup"]))
        if record["local_applied"]:
            recover("soc1_config", lambda: local.rollback(transaction))
        # A lost apply acknowledgement can hide a successful remote write.
        # Attempt local recovery even if SOC2 is no longer reachable.
        if record["remote_started"]:
            recover("soc2_config", lambda: remote.call(
                "rollback" if record["remote_applied"] else "recover", transaction))
            if record["native_changes"]:
                recover("soc2_service", lambda: remote.reload(record["service"]))
                recover("voice_bridge", lambda: installer.restart_voice(record["voice"]))
        recover("runtime_services", lambda: installer.restore_services(record["before"]))
        record["state"] = "recovery_required" if errors else "restored_after_error"
        record["recovery_errors"] = errors
        try:
            save()
        except OSError as failure:
            journal_error = str(failure)
        if journal_error:
            print("提示：事务记录写入失败，已继续执行恢复：" + journal_error, file=sys.stderr)
        if errors:
            raise RuntimeError("安装未完成且自动恢复未完成，请保留事务记录：" + str(record_path)
                               + "；" + errors[0]) from None
        raise
    return record


def rollback_transaction(installer, local, remote, record_path):
    record = load_transaction(record_path)
    transaction = record["transaction"]
    installer.validate()
    before = installer.snapshot(require_idle=True)
    voice = installer.voice_state()
    native_state = remote.service()
    local.rollback(transaction, check=True)
    remote.call("check-rollback", transaction)
    runtime_restored = False
    if record["runtime_backup"]:
        backup = Path(record["runtime_backup"])
        if backup.is_symlink() or backup.parent.resolve() != installer.backups.resolve():
            raise RuntimeError("运行文件备份不属于本补丁")
        saved = json.loads(regular(backup / "transaction.json"))
        installer.verify_backup(backup, saved)
        current = {relative: digest(regular(installer.safe_target(relative)))
                   if installer.safe_target(relative).exists() else None
                   for relative in saved["after"]}
        old = {relative: info["sha256"] if info else None
               for relative, info in saved["before"].items()}
        runtime_restored = current == old
        if not runtime_restored and current != saved["after"]:
            raise RuntimeError("运行文件已有其他变更，拒绝覆盖")
    try:
        installer.stop_services()
    except BaseException:
        installer.restore_services(before)
        raise
    errors = []

    def restore(label, operation):
        try:
            operation()
        except BaseException as error:
            errors.append(label + ": " + str(error))

    if not runtime_restored:
        restore("runtime_files", lambda: installer.runtime_rollback(record["runtime_backup"]))
    restore("soc1_config", lambda: local.rollback(transaction))
    restore("soc2_config", lambda: remote.call("rollback", transaction))
    if record["native_changes"]:
        restore("soc2_service", lambda: remote.reload(native_state))
        restore("voice_bridge", lambda: installer.restart_voice(voice))
    restore("runtime_services", lambda: installer.restore_services(before))
    record["state"] = "rollback_incomplete" if errors else "rolled_back"
    record["rollback_errors"] = errors
    try:
        record_path.write_bytes(encoded(record))
    except OSError as error:
        print("提示：回滚事务记录写入失败：" + str(error), file=sys.stderr)
    if errors:
        raise RuntimeError("回滚未全部完成，本机恢复已继续执行；请保留事务记录：" + str(record_path)
                           + "；" + errors[0])


def main():
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--check", action="store_true")
    selection.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("请使用 sudo bash ./install.sh")
    base.verify_package(HERE)
    import fcntl
    lock = Path("/var/lib/sparkrobot/lingxi-control-reply-fix/install.lock")
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        installer = Installer()
        installer.validate()
        local = NativeConfig()
        with RemoteNative() as remote:
            if args.rollback:
                rollback_transaction(installer, local, remote, args.rollback)
                print("运行文件和两块控制板上的原生讲解映射已回滚。")
                return
            before = installer.snapshot(require_idle=True)
            installer.check_runtime(live=before["java"])
            local_plan, remote_plan = local.plan(), remote.call("plan")
            matches = local_plan["matches"] + remote_plan["matches"]
            print("原生讲解映射：SOC1=" + str(local_plan["matches"])
                  + "，SOC2=" + str(remote_plan["matches"])
                  + "；待禁用=" + str(local_plan["changes"] + remote_plan["changes"]), flush=True)
            if not matches:
                raise RuntimeError("未找到历史原生讲解映射；未修改运行文件或停止服务。本机需进一步核对原生技能配置")
            remote.service()
            installer.voice_state()
            if args.check:
                print("版本、运行文件、服务、空闲状态和两块控制板技能配置检查通过；未修改配置、未重启服务。")
                return
            if (not local_plan["changes"] and not remote_plan["changes"]
                    and all((installer.release / name).exists()
                            and digest(regular(installer.release / name)) == expected
                            for name, expected in installer.meta["payload_sha256"].items())):
                print("原生映射已经禁用且运行文件等同于 1.0.8；未重复写入或重启。")
                return
            transaction = time.strftime("%Y%m%dT%H%M%S-") + uuid.uuid4().hex[:12]
            record_path = STATE / "transactions" / (transaction + ".json")
            install_transaction(installer, local, remote, local_plan, remote_plan, record_path, transaction)
            print("V1.3.8 原生讲解重复映射修复已安装：1.0.10-20261019")
            print("Java、导航和 TTS 运行文件与已交付 1.0.8 逐字节一致。")
            print("事务记录：" + str(record_path))
            print("回滚：sudo bash ./install.sh --rollback " + str(record_path))
            print("请验收：导航、开始讲解、到点讲解和普通问答；离线验证不替代真实声音验收。")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("失败：" + str(error), file=sys.stderr)
        raise SystemExit(1)
