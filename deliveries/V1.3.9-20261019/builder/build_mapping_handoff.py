"""Build a source-and-runtime handoff bundle for the X2 mapping workbench."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import tarfile
import zipfile

HERE = Path(__file__).resolve().parent
DELIVERY = HERE.parent
SERVER = DELIVERY / "source/server"
NAME = "lingxi-x2-mapping-workbench-0.1.4-20261024"
STAGE = DELIVERY / ".build" / NAME
ARCHIVE_FILES = (
    "package.json", "package-lock.json", "tsconfig.json", "MAPPING_HANDOFF.md",
    "X2建图工作台接口与实机验收.md", "src", "public", "test", "scripts", "dist",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    if not (SERVER / "dist/index.js").is_file():
        raise SystemExit("Missing server build. Run npm run build in source/server first.")
    if STAGE.exists():
        raise SystemExit(f"Preserving existing stage; choose a new build name: {STAGE}")
    STAGE.mkdir(parents=True)
    for name in ARCHIVE_FILES:
        source = SERVER / name if name not in {"X2建图工作台接口与实机验收.md"} else SERVER / name
        if not source.exists():
            raise SystemExit(f"Required handoff input is missing: {source}")
        target = STAGE / "server" / name
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("node_modules", "__pycache__", "*.pyc", "artifacts"))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    (STAGE / "README.md").write_text(
        "# Lingxi X2 Mapping Workbench Handoff\n\n"
        "This bundle contains the integrated robot-hosted console source/build, automated tests, "
        "and the robot-side interface handoff checklist. The web app does not drive the base; "
        "the external remote is used for motion. Collection/save remain disabled until the target X2 adapter passes acceptance. "
        "See `server/MAPPING_HANDOFF.md`. No credentials or robot maps are included.\n",
        encoding="utf-8", newline="\n",
    )
    checksums = []
    for path in sorted(p for p in STAGE.rglob("*") if p.is_file()):
        checksums.append(f"{sha256(path)}  {path.relative_to(STAGE).as_posix()}\n")
    (STAGE / "SHA256SUMS").write_text("".join(checksums), encoding="utf-8", newline="\n")

    outputs = (DELIVERY / f"{NAME}.zip", DELIVERY / f"{NAME}.tar.gz")
    if any(path.exists() for path in outputs):
        raise SystemExit("Preserving existing archive(s); choose a new build name.")
    with zipfile.ZipFile(outputs[0], "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(p for p in STAGE.rglob("*") if p.is_file()):
            archive.write(path, f"{NAME}/{path.relative_to(STAGE).as_posix()}")
    with tarfile.open(outputs[1], "w:gz", format=tarfile.PAX_FORMAT) as archive:
        archive.add(STAGE, arcname=NAME)
    for path in outputs:
        path.with_name(path.name + ".sha256").write_text(
            f"{sha256(path)}  {path.name}\n", encoding="ascii", newline="\n"
        )
        print(f"{path.name} {sha256(path)} {path.stat().st_size}")


if __name__ == "__main__":
    main()
