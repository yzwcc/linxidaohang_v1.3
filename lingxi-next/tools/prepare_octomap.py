"""Freeze the OctoMap adapter sources; the pinned system library is not redistributed."""
import argparse
import json
import os
from pathlib import Path
import shutil

from prepare_navfn import ROOT, digest

SOURCES = (
    "robot-navigation/octomap.lock.json",
    "robot-navigation/THIRD_PARTY_NOTICES.md",
    "robot-navigation/native-octomap/CMakeLists.txt",
    "robot-navigation/native-octomap/octomap_bridge.cpp",
    "robot-navigation/lingxi_navigation/__init__.py",
    "robot-navigation/lingxi_navigation/occupancy.py",
    "robot-navigation/lingxi_navigation/navfn.py",
    "tools/tests/test_octomap.py", "tools/check_octomap_native.py",
    "tools/prepare_octomap.py", "tools/run_octomap_target.py",
)


def prepare(output):
    output = Path(output).absolute()
    if (output.exists() or output.is_symlink() or output.parent.resolve(strict=True) != output.parent
            or output == ROOT or output in ROOT.parents
            or ROOT in output.parents and not output.is_relative_to(ROOT / ".local")):
        raise ValueError("new_separate_octomap_source_output_required")
    before = {name: digest(ROOT / name) for name in SOURCES}
    output.mkdir(mode=0o700)
    for name in SOURCES:
        source, target = ROOT / name, output / name
        if source.is_symlink() or not source.is_file():
            raise ValueError("direct_octomap_sources_required")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source, target, follow_symlinks=False)
        target.chmod(0o600)
    files = {name: {"sha256": digest(output / name), "bytes": (output / name).stat().st_size} for name in SOURCES}
    if any(digest(ROOT / name) != value or files[name]["sha256"] != value for name, value in before.items()):
        raise ValueError("octomap_sources_changed_during_preparation")
    lock = json.loads((ROOT / SOURCES[0]).read_bytes())
    manifest = {"schemaVersion": 1, "purpose": "octomap_sources_not_navigation_acceptance",
                "octomapVersion": lock["version"], "files": files,
                "motionCommandsSent": False, "navigationReady": False}
    with (output / "octomap-source-manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return {"sourceManifestSha256": digest(output / "octomap-source-manifest.json"), "sourceFiles": len(files)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        print(json.dumps(prepare(args.output)))
    finally:
        os.umask(previous)
