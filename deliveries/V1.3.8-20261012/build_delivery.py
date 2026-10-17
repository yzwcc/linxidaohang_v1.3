"""Repackage the fixed V1.3.8 installer with unchanged runtime components."""

from __future__ import annotations

import hashlib
import io
import json
import re
import runpy
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERSION = "1.3.8-complete-20261012-r10"
NAME = f"lingxi-v{VERSION}-deployment"
STAGE = HERE / NAME
NAV_VERSION = "1.0.19-20260921"
COMPONENT_HASHES = {
    "aimdk-aarch64-1bde262f-artifacts.7z":
        "f13147a175bc3fb621fb118698aae0353c097adda38bba640f9a39c01433cc98",
    f"sparkrobot-lingxi-nav-internal-{NAV_VERSION}.tar.gz":
        "30ed70324de3c65f82ba3d6caa2ca0114492819fe4eea758fe1208293ebb25bf",
    "sparkrobot-lingxi-kb-1.0.2-20260902.zip":
        "b8f73d925cc914b0440bc6478424f7759f9472cc9c61d8ce06050c37ddf9cf30",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def main() -> None:
    # Reuse the original release's deep audits for the unchanged components.
    worktree = HERE.parents[1].with_name(HERE.parents[1].name + "-v1.3.8")
    audit = runpy.run_path(str(worktree / "integrated_deployment/audit_package.py"))
    for name, expected in COMPONENT_HASHES.items():
        data = (STAGE / "components" / name).read_bytes()
        assert sha256(data) == expected, name
        if name.startswith("sparkrobot-lingxi-nav"):
            audit["audit_nav_component"](data, NAME)
        elif name.endswith(".zip"):
            audit["audit_kb_component"](data, NAME)

    for relative in ("install_all.sh", "control_all.sh"):
        text = (STAGE / relative).read_text(encoding="utf-8")
        assert f'VERSION="{VERSION}"' in text, relative
        assert f'NAV_VERSION="{NAV_VERSION}"' in text, relative
        assert "1.0.18-20260921" not in text, relative
        assert "20260921-r8" not in text, relative
        write(STAGE / relative, text)
    installer = (STAGE / "install_all.sh").read_text(encoding="utf-8")
    assert 'sudo test -d "${EXPECTED_RELEASE}"' in installer
    assert 'sudo readlink -f /opt/sparkrobot/lingxi-nav/current' in installer
    assert not re.search(r"\$\((?:readlink|test) ", installer)
    assert "install-addons" not in installer

    write(STAGE / "VERSION", f"LINGXI_COMPLETE_VERSION={VERSION}\n")
    write(
        STAGE / "README.md",
        "# 灵犀 V1.3.8 完整部署包\n\n"
        f"交付版：`{VERSION}`；导航运行组件：`{NAV_VERSION}`。\n\n"
        "本次修正外层安装器的组件版本引用和权限判断，运行组件保持原版。\n\n"
        "完整安装和已有机器更新见 `docs/部署说明-V1.3.8-r10.md`。\n"
        "包含 AIMDK、导航、地图、网页、语音 Agent、知识库和灵创接口。\n"
        "本包通过本地完整性及脚本检查，实机功能需按说明验收。\n",
    )
    document_name = "部署说明-V1.3.8-r10.md"
    write(
        STAGE / "docs" / document_name,
        (HERE / document_name).read_text(encoding="utf-8"),
    )
    metadata = json.loads((STAGE / "bundle-manifest.json").read_text(encoding="utf-8"))
    metadata["version"] = VERSION
    metadata["created_at"] = "2026-10-12"
    metadata["runtime_baseline"] = "1.3.8-complete-20260921-r9"
    metadata["installer_fixes"] = [
        "consistent_navigation_component_version",
        "privileged_current_symlink_checks",
        "privileged_existing_release_detection",
    ]
    write(STAGE / "bundle-manifest.json", json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    relatives = [
        "README.md", "VERSION", "bundle-manifest.json",
        "install_all.sh", "control_all.sh",
        f"docs/{document_name}",
        *(f"components/{name}" for name in COMPONENT_HASHES),
        *(path.relative_to(STAGE).as_posix() for path in sorted((STAGE / "scripts").glob("*.sh"))),
    ]
    for relative in relatives:
        data = (STAGE / relative).read_bytes()
        if relative.endswith((".sh", ".md", ".json")) or relative == "VERSION":
            assert b"\r" not in data, relative
            data.decode("utf-8")
    write(
        STAGE / "SHA256SUMS.txt",
        "".join(f"{sha256((STAGE / item).read_bytes())}  {item}\n" for item in sorted(relatives)),
    )
    relatives.append("SHA256SUMS.txt")
    archive_path = HERE / f"{NAME}.tar.gz"
    with tarfile.open(archive_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for relative in sorted(relatives):
            data = (STAGE / relative).read_bytes()
            info = tarfile.TarInfo(f"{NAME}/{relative}")
            info.size = len(data)
            info.mode = 0o755 if relative.endswith(".sh") else 0o644
            archive.addfile(info, io.BytesIO(data))

    archive_hash = sha256(archive_path.read_bytes())
    write(HERE / (archive_path.name + ".sha256"), f"{archive_hash}  {archive_path.name}\n")
    with tarfile.open(archive_path, "r:gz") as archive:
        actual = {member.name for member in archive.getmembers()}
        assert actual == {f"{NAME}/{item}" for item in relatives}
        checksums = archive.extractfile(f"{NAME}/SHA256SUMS.txt").read().decode("utf-8")
        for line in checksums.splitlines():
            expected, relative = line.split("  ", 1)
            data = archive.extractfile(f"{NAME}/{relative}").read()
            assert sha256(data) == expected, relative
    print(f"VERIFIED_FILES={len(relatives)-1}")
    print("RUNTIME_COMPONENTS_UNCHANGED=True")
    print(f"TAR={archive_path}")
    print(f"SHA256={archive_hash}")


if __name__ == "__main__":
    main()
