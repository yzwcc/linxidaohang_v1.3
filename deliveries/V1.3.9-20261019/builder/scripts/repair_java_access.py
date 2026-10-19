#!/usr/bin/env python3
"""Repair access to owned Java inputs without changing their contents."""
import grp
import os
from pathlib import Path
import stat


def repair(base: Path, config_dir: Path, gid: int) -> None:
    link = base / "current"
    if not link.is_symlink():
        raise RuntimeError("current must be a release symlink")
    release = link.resolve(strict=True)
    if release.parent != base / "releases":
        raise RuntimeError("current is outside the controlled releases directory")
    directories = (base.parent, base, base / "releases", release, release / "java",
                   config_dir.parent, config_dir)
    files = (release / "java/robot-local-navigation-java-all.jar",
             release / "java/linksoul-agentsdk-1.4.0.jar",
             config_dir / "lingxi-nav.properties", config_dir / "runtime.env")
    # Validate the entire scope before changing any permissions.  Never recurse
    # into backups or follow a file symlink into another product or system path.
    for path in (*directories, *files):
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise RuntimeError(f"Unexpected symlink: {path}")
        if path in directories and not path.is_dir():
            raise RuntimeError(f"Missing runtime directory: {path}")
        if path in files and not path.is_file():
            raise RuntimeError(f"Missing runtime file: {path}")
    for path in directories:
        os.chown(path, -1, gid)
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) | 0o050)
    for path in files:
        os.chown(path, -1, gid)
        os.chmod(path, 0o640)


if __name__ == "__main__":
    group = os.environ.get("SPARKROBOT_RUNTIME_GROUP", "sparkrobot-nav")
    repair(Path("/opt/sparkrobot/lingxi-nav"), Path("/etc/sparkrobot/lingxi-nav"),
           grp.getgrnam(group).gr_gid)
    print("JAVA_RUNTIME_PERMISSIONS_REPAIRED")
