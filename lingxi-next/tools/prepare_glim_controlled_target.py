"""Stage unchanged ARM64 native libraries with a separately verified one-thread control."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from build_glim_target_replay import validate_python_sources
from glim_target_controlled_mapping import PURPOSE, verify_control
from glim_target_mapping import PURPOSE as PARENT_PURPOSE
from glim_target_replay import verify_replay_bundle
from run_glim_controlled_mapping import make_control
from verify_slam_capture import sha256
from slam_target_probe import verify_bundle


ROOT = Path(__file__).resolve().parents[1]


def prepare(args):
    source = args.mapping_bundle.resolve(strict=True)
    output = args.output.resolve()
    if (output.exists() or not output.is_relative_to((ROOT / ".local").resolve())
            or output.is_relative_to(source)):
        raise ValueError("new_ignored_controlled_bundle_required")
    original = verify_replay_bundle(source, PARENT_PURPOSE)
    verify_bundle(args.core_bundle)
    if sha256(args.core_bundle / "bundle-manifest.json") != original["coreBundleManifestSha256"]:
        raise ValueError("same_verified_arm64_core_bundle_required")
    for name in original["files"]:
        if name.startswith("tools/") and sha256(source / name) != sha256(ROOT / name):
            raise ValueError("unchanged_current_target_dependencies_required")
    output.mkdir(mode=0o700, parents=True)
    for name in original["files"]:
        destination = output / ("baseline-config/" + name[7:] if name.startswith("config/") else name)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
    control = make_control(output / "baseline-config", output / "config")
    (output / "control-manifest.json").write_text(json.dumps(control, indent=2) + "\n")
    runner = "glim_target_controlled_mapping.py"
    shutil.copyfile(ROOT / "tools" / runner, output / "tools" / runner)
    names = tuple(path.name for path in (output / "tools").iterdir())
    validate_python_sources(ROOT / "tools", names)
    subprocess.run([
        sys.executable, "-I", "-B", "-c",
        "import sys; sys.path.insert(0, sys.argv[1]); import glim_target_controlled_mapping",
        str(output / "tools"),
    ], check=True, cwd=output)
    verify_control(output)
    manifest = {
        **original, "purpose": PURPOSE, "files": {},
        "parentMappingManifestSha256": sha256(source / "replay-manifest.json"),
        "controlManifestSha256": sha256(output / "control-manifest.json"),
        "preparerSourceSha256": sha256(__file__),
    }
    for path in sorted(output.rglob("*")):
        if path.is_file():
            path.chmod(0o600)
            manifest["files"][path.relative_to(output).as_posix()] = {
                "sha256": sha256(path), "bytes": path.stat().st_size}
    (output / "replay-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    verify_replay_bundle(output, PURPOSE)
    return {"result": "new_arm64_controlled_bundle_prepared_without_native_rebuild",
            "files": len(manifest["files"]) + 1, "manifestSha256": sha256(output / "replay-manifest.json")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("mapping-bundle", "core-bundle", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    old_umask = os.umask(0o077)
    try:
        print(json.dumps(prepare(args)))
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
