#!/usr/bin/env python3
"""Transactional r20 tour first-step fix. No robot motion is issued here."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import time
import urllib.request

HERE = Path(__file__).resolve().parent
BRIDGE = "navigation/robot_local_navigation_v3/lingxi_nav_bridge.py"
CLIENT = "navigation/robot_local_navigation_v3/navigation_client.py"
CONTROL = "scripts/navigation_control.sh"
MANIFEST = "payload-manifest.json"
COMPONENTS = ("lingxi_nav_bridge", "map_label_server_v3", "voice_nav_gateway")
ENDPOINTS = ((18082, "/nav/status"), (8002, "/api/config"), (18083, "/nav/status"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("不是普通文件：" + str(path))
    return path.read_bytes()


def safe_name(name):
    path = PurePosixPath(name)
    return bool(name) and not (
        path.is_absolute() or ".." in path.parts or "\\" in name or str(path) != name
    )


def atomic_write(path, data, metadata=None):
    previous = path.stat()
    mode, uid, gid = metadata or (
        stat.S_IMODE(previous.st_mode), previous.st_uid, previous.st_gid,
    )
    fd, temporary = tempfile.mkstemp(prefix=".tour-first-step-", dir=path.parent)
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


def run(command, *, check=True, timeout=240):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError("命令失败：" + " ".join(command) + "\n" + result.stderr[-1800:]
                           + "\n" + result.stdout[-1800:])
    return result


class Installer:
    def __init__(self, root=Path("/"), package=HERE):
        self.root = root.resolve()
        self.package = package.resolve()
        self.base = self.path("/opt/sparkrobot/lingxi-nav")
        self.backups = self.path("/var/lib/sparkrobot/lingxi-tour-first-step-fix/backups")
        self.meta = json.loads(regular(self.package / "release.json"))
        self.release = None

    def path(self, path):
        return self.root / path.lstrip("/")

    def control(self, action):
        return run(["runuser", "-u", "agi", "--",
                    str(self.release / CONTROL), action], check=action != "status")

    def service_state(self):
        result = self.control("status")
        states = {}
        for line in result.stdout.splitlines():
            match = re.fullmatch(r"(lingxi_nav_bridge|map_label_server_v3|voice_nav_gateway)=(running pid=[1-9][0-9]*|stopped)", line.strip())
            if match:
                if match[1] in states:
                    raise RuntimeError("服务状态输出重复")
                states[match[1]] = match[2].startswith("running")
        if set(states) != set(COMPONENTS) or len(set(states.values())) != 1:
            raise RuntimeError("导航服务状态不完整或部分运行，请先用网页统一停止服务后重试")
        active = all(states.values())
        if active != (result.returncode == 0):
            raise RuntimeError("导航服务状态与退出码不一致")
        if not active:
            self.assert_no_processes()
        return active

    def assert_no_processes(self):
        for process in self.path("/proc").iterdir():
            if not process.name.isdigit():
                continue
            try:
                args = (process / "cmdline").read_bytes()
                state = (process / "stat").read_text().rsplit(")", 1)[1].split()[0]
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
            if state != "Z" and any(
                ("robot_local_navigation_v3." + name).encode() in args for name in COMPONENTS
            ):
                raise RuntimeError("发现未停止或未纳管的导航进程：" + process.name)

    def stop(self):
        self.control("stop")
        for _ in range(20):
            try:
                self.assert_no_processes()
                return
            except RuntimeError:
                time.sleep(0.25)
        self.assert_no_processes()

    def read_status(self):
        with urllib.request.urlopen("http://127.0.0.1:18082/nav/status", timeout=3) as response:
            return json.load(response)

    def check_idle(self, active):
        if not active:
            return
        status = self.read_status()
        if status.get("ok") is not True or not isinstance(status.get("task"), dict):
            raise RuntimeError("导航状态响应缺少任务状态")
        if status["task"].get("active") is not False:
            raise RuntimeError("讲解任务仍在运行或暂停；请先在网页停止讲解任务再安装")
        navigation = status.get("navigation", {}).get("status")
        if navigation not in {"idle", "completed", "failed", "timeout", "cancelled", "rejected"}:
            raise RuntimeError("导航仍在运行或状态未知，请先停止导航：" + str(navigation))
        if status.get("auto_return", {}).get("state") != "idle":
            raise RuntimeError("自动返航或到点动作仍在执行，请停止后安装")

    def start(self):
        self.control("start")
        for port, endpoint in ENDPOINTS:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{endpoint}", timeout=4) as response:
                json.load(response)
        if not self.service_state():
            raise RuntimeError("导航服务启动后未稳定运行")

    def check_access(self):
        code = "import pathlib,sys;compile(pathlib.Path(sys.argv[1]).read_bytes(),sys.argv[1],'exec');print('BRIDGE_ACCESS_OK')"
        run(["runuser", "-u", "agi", "--", "python3", "-c", code, str(self.release / BRIDGE)])

    def validate(self):
        current = self.base / "current"
        if not current.is_symlink():
            raise RuntimeError("current 不是软链接")
        release = current.resolve(strict=True)
        if release.parent != self.base / "releases" or release.name not in self.meta["allowed_releases"]:
            raise RuntimeError("本增量只支持 V1.3.8 r20，当前为：" + release.name + "；未覆盖文件")
        self.release = release
        previous = regular(release / BRIDGE)
        if digest(previous) not in {self.meta["baseline_bridge_sha256"], self.meta["patched_bridge_sha256"]}:
            raise RuntimeError("bridge 与 r20 基线或本补丁不一致，拒绝覆盖未知修改")
        for relative, key in ((CLIENT, "client_sha256"), (CONTROL, "controller_sha256")):
            if digest(regular(release / relative)) != self.meta[key]:
                raise RuntimeError("运行依赖与验证版本不一致：" + relative)
        patched = regular(self.package / "payload" / BRIDGE)
        if digest(patched) != self.meta["patched_bridge_sha256"]:
            raise RuntimeError("补丁 bridge 校验失败")
        compile(patched, BRIDGE, "exec")
        manifest = json.loads(regular(release / MANIFEST))
        seen = set()
        for item in manifest["files"]:
            name = item["path"]
            if not safe_name(name) or name in seen:
                raise RuntimeError("运行清单存在不安全或重复路径")
            seen.add(name)
            if name in {BRIDGE, CLIENT}:
                content = regular(release / name)
                if item["sha256"] != digest(content) or item["size"] != len(content):
                    raise RuntimeError("运行文件与清单不一致：" + name)
        if not {BRIDGE, CLIENT}.issubset(seen):
            raise RuntimeError("运行清单缺少 bridge/client")
        self.check_access()
        return manifest

    def current_unchanged(self, expected):
        if (self.base / "current").resolve(strict=True) != self.release:
            raise RuntimeError("安装期间 current 已变化")
        for relative, checksum in expected.items():
            allowed = {checksum} if isinstance(checksum, str) else set(checksum)
            if digest(regular(self.release / relative)) not in allowed:
                raise RuntimeError("安装期间文件已被其他程序修改：" + relative)

    def restore_files(self, backup, transaction):
        self.validate_backup(backup, transaction)
        for relative in (BRIDGE, MANIFEST):
            info = transaction["before"][relative]
            atomic_write(self.release / relative, regular(backup / "files" / relative),
                         (info["mode"], info["uid"], info["gid"]))

    def validate_backup(self, backup, transaction):
        if transaction["release"] != str(self.release):
            raise RuntimeError("备份属于其他 release")
        for relative in (BRIDGE, MANIFEST):
            if digest(regular(backup / "files" / relative)) != transaction["before"][relative]["sha256"]:
                raise RuntimeError("事务备份损坏：" + relative)

    def install(self):
        manifest = self.validate()
        active = self.service_state()
        self.check_idle(active)
        patched = regular(self.package / "payload" / BRIDGE)
        if regular(self.release / BRIDGE) == patched:
            print("此补丁已安装，文件、清单和权限检查通过；未重复重启")
            return
        updated = json.loads(json.dumps(manifest))
        entry = next(entry for entry in updated["files"] if entry["path"] == BRIDGE)
        entry.update(sha256=digest(patched), size=len(patched))
        updated["tour_first_step_fix"] = self.meta["version"]
        replacement = {BRIDGE: patched, MANIFEST: json_bytes(updated)}
        self.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%dT%H%M%S-"), dir=self.backups))
        transaction = {"release": str(self.release), "was_active": active, "before": {}, "after": {}}
        for relative, new_data in replacement.items():
            target = self.release / relative
            old_data = regular(target)
            metadata = target.stat()
            dest = backup / "files" / relative
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            dest.write_bytes(old_data)
            dest.chmod(0o600)
            transaction["before"][relative] = dict(
                sha256=digest(old_data), mode=stat.S_IMODE(metadata.st_mode),
                uid=metadata.st_uid, gid=metadata.st_gid,
            )
            transaction["after"][relative] = digest(new_data)
        (backup / "transaction.json").write_bytes(json_bytes(transaction))
        print("事务备份：" + str(backup), flush=True)
        mutation_started = False
        try:
            self.stop()
            expected = {name: info["sha256"] for name, info in transaction["before"].items()}
            self.current_unchanged(expected)
            mutation_started = True
            for relative, content in replacement.items():
                atomic_write(self.release / relative, content)
            self.check_access()
            if active:
                self.start()
        except Exception as original:
            try:
                if mutation_started:
                    print("安装失败，正在恢复本次事务备份", flush=True)
                    self.stop()
                    self.current_unchanged({
                        name: {info["sha256"], transaction["after"][name]}
                        for name, info in transaction["before"].items()
                    })
                    self.restore_files(backup, transaction)
                else:
                    self.current_unchanged({
                        name: info["sha256"]
                        for name, info in transaction["before"].items()
                    })
                if active:
                    self.start()
            except Exception as recovery:
                raise RuntimeError(
                    "安装失败且自动恢复未完成；请保留事务备份：" + str(backup)
                    + "\n原始错误：" + str(original) + "\n恢复错误：" + str(recovery)
                ) from original
            raise RuntimeError("安装未完成，已恢复安装前文件和运行状态：" + str(original)) from original
        print("V1.3.8 讲解首点修复安装成功：" + self.meta["version"])
        print("回滚：sudo bash ./install.sh --rollback " + str(backup))
        print("导航服务：" + ("已恢复运行" if active else "保持安装前的停止状态"))
        print("未修改 AIMDK、Java、语音配置、地图、点位、讲解任务和密钥")

    def rollback(self, backup):
        self.validate()
        if backup.is_symlink():
            raise RuntimeError("备份路径不能是软链接")
        backup = backup.resolve(strict=True)
        if backup.parent != self.backups.resolve():
            raise RuntimeError("请使用本补丁输出的事务备份目录")
        transaction = json.loads(regular(backup / "transaction.json"))
        self.validate_backup(backup, transaction)
        self.current_unchanged(transaction["after"])
        active = self.service_state()
        self.check_idle(active)
        self.stop()
        self.current_unchanged(transaction["after"])
        self.restore_files(backup, transaction)
        self.check_access()
        if active:
            self.start()
        print("回滚完成，已保持回滚前的运行/停止状态")


def verify_package(package):
    seen = set()
    raw = regular(package / "SHA256SUMS")
    if b"\r" in raw:
        raise RuntimeError("补丁校验清单包含 CRLF，请重新解压原包")
    for line in raw.decode("utf-8").splitlines():
        expected, name = line.split("  ", 1)
        if not safe_name(name) or name in seen or not re.fullmatch("[a-f0-9]{64}", expected):
            raise RuntimeError("补丁校验清单不合法")
        seen.add(name)
        if digest(regular(package / name)) != expected:
            raise RuntimeError("补丁文件校验失败：" + name)
    if not {"install.py", "install.sh", "release.json", "payload/" + BRIDGE}.issubset(seen):
        raise RuntimeError("补丁校验清单缺少必要文件")


def main():
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true")
    action.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("请使用 sudo bash ./install.sh")
    verify_package(HERE)
    import fcntl
    path = Path("/var/lib/sparkrobot/lingxi-tour-first-step-fix/install.lock")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        installer = Installer()
        if args.check:
            installer.validate()
            active = installer.service_state()
            installer.check_idle(active)
            print("版本、依赖、运行清单、agi 读取权限和服务空闲状态检查通过")
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
