from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
NAME = "sparkrobot-lingxi-v1.3.8-motion-stand-fix-1.0.0-20261017"
STAGE = HERE / NAME
ARCHIVE = HERE / f"{NAME}.tar.gz"
FILES = [
    "README.md",
    "VERSION",
    "install.sh",
    "control.sh",
    "docs/部署说明.md",
    "payload/sdk_motion_controller.py",
    "payload/disable_legacy_skills.py",
    "tests/test_package.py",
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if not STAGE.is_dir():
        raise SystemExit(f"missing stage: {STAGE}")
    (STAGE / "VERSION").write_text(
        "LINGXI_V138_MOTION_STAND_FIX=1.0.0-20261017\n",
        encoding="utf-8",
        newline="\n",
    )
    for relative in FILES:
        path = STAGE / relative
        if not path.is_file():
            raise SystemExit(f"missing file: {relative}")
        data = path.read_bytes()
        if b"\r" in data:
            path.write_bytes(data.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    manifest = "".join(f"{digest(STAGE / item)}  {item}\n" for item in sorted(FILES))
    (STAGE / "SHA256SUMS.txt").write_text(manifest, encoding="utf-8", newline="\n")
    archive_files = [*FILES, "SHA256SUMS.txt"]
    with tarfile.open(ARCHIVE, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for relative in sorted(archive_files):
            data = (STAGE / relative).read_bytes()
            info = tarfile.TarInfo(f"{NAME}/{relative}")
            info.size = len(data)
            info.mode = 0o755 if relative.endswith((".sh", ".py")) else 0o644
            archive.addfile(info, io.BytesIO(data))
    archive_hash = digest(ARCHIVE)
    (HERE / f"{ARCHIVE.name}.sha256").write_text(
        f"{archive_hash}  {ARCHIVE.name}\n", encoding="utf-8", newline="\n"
    )
    with tarfile.open(ARCHIVE, "r:gz") as archive:
        names = {member.name for member in archive.getmembers()}
        expected = {f"{NAME}/{item}" for item in archive_files}
        assert names == expected, sorted(names ^ expected)
        for line in manifest.splitlines():
            expected_hash, relative = line.split("  ", 1)
            data = archive.extractfile(f"{NAME}/{relative}").read()
            assert hashlib.sha256(data).hexdigest() == expected_hash, relative
    print(f"FILES={len(archive_files)}")
    print(f"ARCHIVE={ARCHIVE}")
    print(f"SHA256={archive_hash}")


if __name__ == "__main__":
    main()
