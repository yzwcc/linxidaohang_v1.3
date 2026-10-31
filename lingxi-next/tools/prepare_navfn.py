"""Prepare pinned, unmodified NavFn sources and the independent planning adapter."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "robot-navigation/navfn.lock.json",
    "robot-navigation/native-navfn/CMakeLists.txt",
    "robot-navigation/native-navfn/navfn_bridge.cpp",
    "robot-navigation/lingxi_navigation/__init__.py",
    "robot-navigation/lingxi_navigation/navfn.py",
    "tools/tests/test_navfn.py", "tools/check_navfn_native.py", "tools/prepare_navfn.py",
    "tools/run_navfn_target.py",
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(upstream, output):
    upstream = Path(upstream).resolve(strict=True)
    output = Path(output).absolute()
    if (output.exists() or output.is_symlink() or output.parent.resolve(strict=True) != output.parent
            or output == upstream or upstream in output.parents or output in upstream.parents
            or output == ROOT or output in ROOT.parents
            or ROOT in output.parents and not output.is_relative_to(ROOT / ".local")):
        raise ValueError("new_separate_navfn_source_output_required")
    lock = json.loads((ROOT / SOURCES[0]).read_bytes())
    selected = {name: upstream / Path(name).name for name in lock["files"]}
    for name, source in selected.items():
        if source.is_symlink() or not source.is_file() or digest(source) != lock["files"][name]:
            raise ValueError("unchanged_hash_pinned_upstream_navfn_sources_required")
    before = {name: digest(ROOT / name) for name in SOURCES}
    output.mkdir(mode=0o700)
    for name, source in {**{name: ROOT / name for name in SOURCES},
                         **{"upstream/" + name: path for name, path in selected.items()}}.items():
        target = output / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source, target, follow_symlinks=False)
        target.chmod(0o600)
    files = {path.relative_to(output).as_posix(): {"sha256": digest(path), "bytes": path.stat().st_size}
             for path in sorted(output.rglob("*")) if path.is_file()}
    if any(digest(ROOT / name) != sha for name, sha in before.items()):
        raise ValueError("navfn_sources_changed_during_preparation")
    manifest = {"schemaVersion": 1, "purpose": "navfn_sources_not_navigation_acceptance",
                "upstreamCommit": lock["commit"], "files": files,
                "motionCommandsSent": False, "navigationReady": False}
    with (output / "navfn-source-manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return {"sourceManifestSha256": digest(output / "navfn-source-manifest.json"), "sourceFiles": len(files)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        print(json.dumps(prepare(args.upstream, args.output)))
    finally:
        os.umask(previous)
