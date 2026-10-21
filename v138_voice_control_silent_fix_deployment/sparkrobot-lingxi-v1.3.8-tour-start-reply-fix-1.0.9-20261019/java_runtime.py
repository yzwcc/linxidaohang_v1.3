#!/usr/bin/env python3
"""Java-only, transactional V1.3.8 navigation and tour routing hotfix."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile

HERE = Path(__file__).resolve().parent
JAR = "java/robot-local-navigation-java-all.jar"
MANIFEST = "payload-manifest.json"
UNIT = "lingxi-java-agent-robot.service"
MAIN = "com/agibot/aiem/nav/LingxiNavAgentApp.class"
MAIN_CLASS = "com.agibot.aiem.nav.LingxiNavAgentApp"
SDK = "java/linksoul-agentsdk-1.4.0.jar"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("不是普通文件：" + str(path))
    return path.read_bytes()


def atomic_write(path, data, metadata=None):
    reference = path.stat()
    mode, uid, gid = metadata or (stat.S_IMODE(reference.st_mode), reference.st_uid, reference.st_gid)
    fd, temporary = tempfile.mkstemp(prefix=".control-reply-", dir=path.parent)
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


def run(command, check=True):
    result = subprocess.run(command, capture_output=True, text=True, timeout=45)
    if check and result.returncode:
        raise RuntimeError("命令失败：" + " ".join(command) + "\n" + result.stderr[-2000:])
    return result.stdout.strip()


class Installer:
    def __init__(self, root=Path("/"), package=HERE):
        self.root = root.resolve()
        self.package = package.resolve()
        self.base = self.path("/opt/sparkrobot/lingxi-nav")
        self.backups = self.path("/var/lib/sparkrobot/lingxi-control-reply-fix/backups")
        self.release = None
        self.meta = json.loads(regular(self.package / "release.json"))

    def path(self, value):
        return self.root / value.lstrip("/")

    def state(self):
        state = run(["systemctl", "show", UNIT, "-p", "ActiveState", "--value"])
        if state not in {"active", "activating", "reloading", "inactive", "failed"}:
            raise RuntimeError("Java 服务状态无法安全处理：" + state)
        return state in {"active", "activating", "reloading"}

    def stop(self):
        run(["systemctl", "stop", UNIT])
        if self.state():
            raise RuntimeError("Java 服务停止失败")

    def start(self):
        run(["systemctl", "reset-failed", UNIT], check=False)
        run(["systemctl", "start", UNIT])
        last_pid = None
        for _ in range(6):
            if not self.state():
                raise RuntimeError("Java Agent 启动失败")
            pid = run(["systemctl", "show", UNIT, "-p", "MainPID", "--value"])
            if not pid.isdigit() or int(pid) <= 0 or (last_pid and last_pid != pid):
                raise RuntimeError("Java Agent 启动后反复退出")
            last_pid = pid
            time.sleep(0.5)
        self.check_runtime(live=True)

    def check_runtime(self, live=False):
        def has_path(command, relative):
            aliases = (str(self.base / "current" / relative), str(self.release / relative))
            return any(re.search(r"(?<![A-Za-z0-9_./-])" + re.escape(path) + r"(?=$|[\s:;}])",
                                 command) for path in aliases)
        command = run(["systemctl", "show", UNIT, "-p", "ExecStart", "--value"])
        if (not has_path(command, JAR) or not has_path(command, SDK)
                or MAIN_CLASS not in command or "/overrides/" in command):
            raise RuntimeError("Java 服务启动命令未使用当前 JAR/SDK；请先处理旧 systemd 覆盖配置，未自动删除现场配置")
        if not live:
            return
        pid = run(["systemctl", "show", UNIT, "-p", "MainPID", "--value"])
        if not pid.isdigit() or int(pid) <= 1:
            raise RuntimeError("Java 进程 PID 无效，无法核验实际加载路径")
        args = regular(self.path("/proc") / pid / "cmdline").decode("utf-8").rstrip("\0").split("\0")
        indexes = [i for i, value in enumerate(args) if value in {"-cp", "-classpath", "--class-path"}]
        if len(indexes) != 1 or indexes[0] + 2 >= len(args):
            raise RuntimeError("Java 进程未使用受支持的 classpath 启动方式")
        index = indexes[0]
        entries = args[index + 1].split(":")
        expected = [(self.release / relative).resolve() for relative in (JAR, SDK)]
        if ([Path(entry).resolve() for entry in entries] != expected or args[index + 2] != MAIN_CLASS):
            raise RuntimeError("Java 实际进程未加载当前 JAR/SDK，可能仍在运行旧覆盖包")

    def check_access(self):
        user = run(["systemctl", "show", UNIT, "-p", "User", "--value"]) or "root"
        # Open as the actual service account; root readability proves nothing.
        code = (
            "import pathlib,sys,zipfile;"
            "p=pathlib.Path(sys.argv[1]);"
            "z=zipfile.ZipFile(p/'java/robot-local-navigation-java-all.jar');"
            "z.read('com/agibot/aiem/nav/LingxiNavAgentApp.class');"
            "s=zipfile.ZipFile(p/'java/linksoul-agentsdk-1.4.0.jar');"
            "assert s.namelist();"
            "open(sys.argv[2],'rb').read(1);"
            "print('JAVA_RUNTIME_ACCESS_OK')"
        )
        run(["runuser", "-u", user, "--", "python3", "-c", code, str(self.release),
             str(self.path("/etc/sparkrobot/lingxi-nav/lingxi-nav.properties"))])

    def validate(self):
        current = self.base / "current"
        if not current.is_symlink():
            raise RuntimeError("current 不是软链接")
        release = current.resolve(strict=True)
        if release.parent != self.base / "releases" or release.name not in self.meta["allowed_releases"]:
            raise RuntimeError("当前版本不是支持的 V1.3.8 r11-r16；已停止，未覆盖文件")
        self.release = release
        app = regular(release / JAR)
        supported = {self.meta["baseline_jar_sha256"], self.meta["patched_jar_sha256"]}
        supported.update(self.meta.get("superseded_jar_sha256", []))
        if digest(app) not in supported:
            raise RuntimeError("Java JAR 与验证基线不一致，拒绝覆盖未知现场修改")
        if digest(regular(release / "java/linksoul-agentsdk-1.4.0.jar")) != self.meta["sdk_sha256"]:
            raise RuntimeError("AgentSDK 与验证版本不一致")
        with zipfile.ZipFile(self.package / "payload" / JAR) as archive:
            archive.read(MAIN)
            if archive.testzip() is not None:
                raise RuntimeError("补丁 JAR CRC 校验失败")
        if digest(regular(self.package / "payload" / JAR)) != self.meta["patched_jar_sha256"]:
            raise RuntimeError("补丁 JAR 校验失败")
        manifest = json.loads(regular(release / MANIFEST))
        seen = set()
        for item in manifest["files"]:
            name = item["path"]
            path = PurePosixPath(name)
            if (name in seen or path.is_absolute() or ".." in path.parts or "\\" in name
                    or str(path) != name or not name):
                raise RuntimeError("运行清单包含重复或不安全路径")
            seen.add(name)
        entries = [e for e in manifest["files"] if e["path"] == JAR]
        if len(entries) != 1 or entries[0]["sha256"] != digest(app) or entries[0]["size"] != len(app):
            raise RuntimeError("运行 JAR 与运行清单不一致，已停止")
        self.check_access()
        self.check_runtime()
        return manifest

    def restore_files(self, backup, transaction):
        if Path(transaction["release"]) != self.release:
            raise RuntimeError("备份属于另一个 release，拒绝恢复")
        for relative in (JAR, MANIFEST):
            data = regular(backup / "files" / relative)
            if digest(data) != transaction["before"][relative]["sha256"]:
                raise RuntimeError("备份已损坏：" + relative)
        for relative in (JAR, MANIFEST):
            info = transaction["before"][relative]
            atomic_write(self.release / relative, regular(backup / "files" / relative),
                         (info["mode"], info["uid"], info["gid"]))

    def install(self):
        manifest = self.validate()
        new_jar = regular(self.package / "payload" / JAR)
        if regular(self.release / JAR) == new_jar:
            self.check_runtime(live=self.state())
            print("此补丁已经安装，运行清单和服务用户访问检查通过；未重复重启")
            return
        was_active = self.state()
        new_manifest = json.loads(json.dumps(manifest))
        entry = next(e for e in new_manifest["files"] if e["path"] == JAR)
        entry.update(sha256=digest(new_jar), size=len(new_jar))
        new_manifest["control_reply_fix"] = self.meta["version"]
        replacement = {JAR: new_jar, MANIFEST: json_bytes(new_manifest)}
        self.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%dT%H%M%S-"), dir=self.backups))
        transaction = {"release": str(self.release), "was_active": was_active, "before": {}, "after": {}}
        for relative, data in replacement.items():
            target = self.release / relative
            previous = regular(target)
            ref = target.stat()
            dest = backup / "files" / relative
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            dest.write_bytes(previous)
            dest.chmod(0o600)
            transaction["before"][relative] = dict(sha256=digest(previous),
                mode=stat.S_IMODE(ref.st_mode), uid=ref.st_uid, gid=ref.st_gid)
            transaction["after"][relative] = digest(data)
        (backup / "transaction.json").write_bytes(json_bytes(transaction))
        print("事务备份：" + str(backup), flush=True)
        stopped = False
        try:
            self.stop()
            stopped = True
            if (self.base / "current").resolve(strict=True) != self.release:
                raise RuntimeError("安装期间 current 已变化")
            for relative, data in replacement.items():
                atomic_write(self.release / relative, data)
            self.check_access()
            self.check_runtime()
            if was_active:
                self.start()
        except Exception:
            if stopped:
                print("安装失败，恢复本次事务备份（仅执行一次）", flush=True)
                self.stop()
                self.restore_files(backup, transaction)
                if was_active:
                    self.start()
            raise
        print("V1.3.8 导航和讲解语音路由修复安装成功：" + self.meta["version"])
        print("回滚：sudo bash ./install.sh --rollback " + str(backup))
        print("Java Agent：" + ("已恢复运行" if was_active else "保持安装前的停止状态，请用网页启动"))
        print("AIMDK、地图任务、密钥、知识库、回调配置及其他服务未修改")

    def rollback(self, backup):
        self.validate()
        backup = backup.resolve(strict=True)
        if backup.parent != self.backups.resolve() or backup.is_symlink():
            raise RuntimeError("请使用安装器输出的本补丁备份目录")
        transaction = json.loads(regular(backup / "transaction.json"))
        if Path(transaction["release"]) != self.release:
            raise RuntimeError("备份属于另一个 release，拒绝恢复")
        for relative in (JAR, MANIFEST):
            if digest(regular(self.release / relative)) != transaction["after"][relative]:
                raise RuntimeError("安装后文件已有其他变更，拒绝覆盖：" + relative)
        # Validate every backup before stopping a healthy service.
        for relative in (JAR, MANIFEST):
            if digest(regular(backup / "files" / relative)) != transaction["before"][relative]["sha256"]:
                raise RuntimeError("备份已损坏：" + relative)
        was_active = self.state()
        self.stop()
        self.restore_files(backup, transaction)
        self.check_access()
        if was_active:
            self.start()
        print("回滚完成；Java 服务保持回滚前的运行/停止状态")


def verify_package(package):
    seen = set()
    raw = regular(package / "SHA256SUMS")
    if b"\r" in raw:
        raise RuntimeError("校验清单不是 LF 格式，请重新解压原包")
    for line in raw.decode("utf-8").splitlines():
        expected, name = line.split("  ", 1)
        path = PurePosixPath(name)
        if name in seen or path.is_absolute() or ".." in path.parts or "\\" in name or str(path) != name:
            raise RuntimeError("补丁校验清单路径不安全")
        seen.add(name)
        if digest(regular(package / name)) != expected:
            raise RuntimeError("补丁文件校验失败：" + name)
    for name in ("install.py", "install.sh", "release.json", "payload/" + JAR):
        if name not in seen:
            raise RuntimeError("补丁校验清单缺少：" + name)


def main():
    parser = argparse.ArgumentParser()
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--check", action="store_true")
    choice.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("请使用 sudo bash ./install.sh")
    verify_package(HERE)
    import fcntl
    lock_path = Path("/var/lib/sparkrobot/lingxi-control-reply-fix/install.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        installer = Installer()
        if args.check:
            installer.validate()
            installer.check_runtime(live=installer.state())
            print("版本、JAR、AgentSDK、运行清单、服务用户权限和实际启动路径检查通过")
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
