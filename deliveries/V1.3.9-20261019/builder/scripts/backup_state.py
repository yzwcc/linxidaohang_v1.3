#!/usr/bin/env python3
"""Local, secret-bearing installation snapshots. Never included as release data."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tarfile
import uuid

PATHS = (
    "etc/sparkrobot/lingxi-nav", "etc/sparkrobot/lingxi-kb",
    "var/lib/sparkrobot/lingxi-nav", "var/lib/sparkrobot/lingxi-kb",
    "var/lib/sparkrobot/lingxi-complete",
    "etc/systemd/system/lingxi-nav-console.service",
    "etc/systemd/system/lingxi-java-agent-robot.service",
    "etc/systemd/system/lingxi-java-agent-robot.service.d",
    "etc/systemd/system/sparkrobot-lingxi-kb.service",
    "etc/sudoers.d/sparkrobot-lingxi-internal",
    "usr/local/sbin/sparkrobot-lingxi-internal",
    "usr/local/sbin/sparkrobot-lingxi-complete",
    "usr/local/sbin/sparkrobot-lingxi-kb", "usr/local/bin/kb-search",
    "agibot/software/nav/config/vectorflux_common/a2/app2.yaml",
    "agibot/software/nav/config/vectorflux_common/a2/mppi_controller.yaml",
    "agibot/software/agent/bin/cfg/skills.json",
)
LINKS = ("opt/sparkrobot/lingxi-nav/current", "opt/sparkrobot/lingxi-kb/current")
BACKUPS = "var/backups/sparkrobot/lingxi-v139"

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()

def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]

def backup(root):
    output = root / BACKUPS / stamp()
    output.mkdir(parents=True, mode=0o700)
    output.chmod(0o700)
    metadata = {"format": "lingxi-full-install-backup-v1", "paths": list(PATHS), "links": {}}
    for relative in LINKS:
        path = root / relative
        if path.exists() and not path.is_symlink():
            raise ValueError(f"current 不是软链接：{relative}")
        metadata["links"][relative] = os.readlink(path) if path.is_symlink() else None
    archive = output / "state.tar.gz"
    with tarfile.open(archive, "w:gz", dereference=False) as f:
        for relative in PATHS:
            path = root / relative
            if path.exists() or path.is_symlink():
                f.add(path, arcname=relative)
    archive.chmod(0o600)
    metadata["sha256"] = digest(archive)
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (output / "metadata.json").chmod(0o600)
    (root / BACKUPS / "last-backup").write_text(str(output) + "\n", encoding="utf-8")
    return output

def validate(root, directory):
    root = root.resolve()
    directory = Path(directory).resolve()
    base = (root / BACKUPS).resolve()
    if directory.parent != base:
        raise ValueError("仅接受完整安装器生成的备份目录")
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("format") != "lingxi-full-install-backup-v1" or metadata.get("paths") != list(PATHS):
        raise ValueError("备份格式/清单不匹配")
    if digest(directory / "state.tar.gz") != metadata["sha256"]:
        raise ValueError("备份归档校验失败")
    if set(metadata.get("links", {})) != set(LINKS):
        raise ValueError("备份版本链接清单不完整")
    for relative in (*PATHS, *LINKS):
        path = root / relative
        for parent in path.parents:
            if parent == root:
                break
            if parent.is_symlink():
                raise ValueError(f"恢复路径的父目录是软链接：{parent}")
    for relative in LINKS:
        current = root / relative
        if current.exists() and not current.is_symlink():
            raise ValueError(f"current 不是软链接：{relative}")
        target = metadata["links"][relative]
        if target is not None:
            if not isinstance(target, str) or not target:
                raise ValueError("备份版本链接无效")
            resolved = (root / target.lstrip("/") if target.startswith("/") else current.parent / target).resolve()
            if not resolved.is_relative_to(root) or not resolved.is_dir():
                raise ValueError(f"旧发布目录缺失或越界：{target}")
    with tarfile.open(directory / "state.tar.gz", "r:gz") as f:
        members = f.getmembers()
        names = set()
        symlinks = {m.name.rstrip("/") for m in members if m.issym()}
        for member in members:
            p = PurePosixPath(member.name)
            if p.is_absolute() or ".." in p.parts or not any(
                member.name == n or member.name.startswith(n + "/") for n in PATHS
            ):
                raise ValueError("备份包含非预期路径")
            if p.as_posix().rstrip("/") != member.name.rstrip("/") or member.name in names:
                raise ValueError("备份包含重复或非规范路径")
            names.add(member.name)
            if not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError("备份包含不支持的特殊文件")
            if any(parent.as_posix() in symlinks for parent in p.parents):
                raise ValueError("备份文件位于软链接目录之下")
    return metadata

def restore(root, directory):
    root = root.resolve()
    directory = Path(directory).resolve()
    metadata = validate(root, directory)
    # Preserve the current state as a second snapshot before restoration.
    saved = backup(root)
    recovery = root / BACKUPS / ("before-restore-" + stamp())
    recovery.mkdir(mode=0o700)
    for relative in PATHS:
        path = root / relative
        if path.exists() or path.is_symlink():
            target = recovery / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            path.rename(target)
    with tarfile.open(directory / "state.tar.gz", "r:gz") as f:
        # The archive was created locally and paths checked above. Preserve
        # root ownership, private modes, and original configuration symlinks.
        options = {"filter": "fully_trusted"} if hasattr(tarfile, "data_filter") else {}
        f.extractall(root, **options)
    for relative in LINKS:
        path = root / relative
        path.unlink(missing_ok=True)
        target = metadata["links"][relative]
        if target:
            path.symlink_to(target, target_is_directory=True)
    return {"restored": str(directory), "before_restore_snapshot": str(saved)}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("backup", "check", "restore", "list"))
    parser.add_argument("directory", nargs="?")
    parser.add_argument("--root", type=Path, default=Path("/"))
    args = parser.parse_args()
    if args.mode == "backup":
        print(backup(args.root))
    elif args.mode in {"check", "restore"}:
        if not args.directory:
            parser.error("restore 需要备份目录")
        result = ({"backup_valid": True} if args.mode == "check" and validate(args.root, args.directory)
                  else restore(args.root, args.directory))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("\n".join(str(p) for p in sorted((args.root / BACKUPS).glob("*"))
                        if (p / "metadata.json").is_file()))

if __name__ == "__main__":
    main()
