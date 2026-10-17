from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
VERSION = "1.0.0-20260929"
NAME = f"sparkrobot-lingxi-v1.3.9-blocked-reminder-{VERSION}"
DIST = HERE / "dist"
FILES = (
    "install.sh",
    "control.sh",
    "README.md",
    "payload/robot_local_navigation_v3/blocked_navigation.py",
    "payload/robot_local_navigation_v3/navigation_events.py",
    "payload/robot_local_navigation_v3/navigation_client.py",
    "payload/robot_local_navigation_v3/lingxi_nav_bridge.py",
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    missing = [name for name in FILES if not (HERE / name).is_file()]
    if missing:
        raise SystemExit("文件缺失：" + ", ".join(missing))
    DIST.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="blocked-reminder-build-", dir=str(DIST)) as temp:
        stage = Path(temp) / NAME
        stage.mkdir(parents=True)
        for name in FILES:
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(HERE / name, target)
        os.chmod(stage / "install.sh", 0o755)
        os.chmod(stage / "control.sh", 0o755)
        manifest_files = []
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                manifest_files.append({"path": path.relative_to(stage).as_posix(), "sha256": digest(path)})
        with (stage / "manifest.json").open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"format": "sparkrobot-lingxi-blocked-reminder-v1", "version": VERSION, "files": manifest_files}, indent=2) + "\n")
        archive = DIST / f"{NAME}.tar.gz"
        zip_archive = DIST / f"{NAME}.zip"
        archive.unlink(missing_ok=True)
        zip_archive.unlink(missing_ok=True)
        shutil.make_archive(str(DIST / NAME), "gztar", root_dir=stage.parent, base_dir=NAME)
        with zipfile.ZipFile(zip_archive, "w", zipfile.ZIP_DEFLATED) as output:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    info = zipfile.ZipInfo((PurePosixPath(NAME) / path.relative_to(stage).as_posix()).as_posix())
                    info.external_attr = (stat.S_IFREG | (0o755 if path.name in {"install.sh", "control.sh"} else 0o644)) << 16
                    output.writestr(info, path.read_bytes())
    for path in (archive, zip_archive):
        with path.with_suffix(path.suffix + ".sha256").open("w", encoding="ascii", newline="\n") as handle:
            handle.write(f"{digest(path)}  {path.name}\n")
    print(f"已生成：{archive}")
    print(f"SHA-256：{digest(archive)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
