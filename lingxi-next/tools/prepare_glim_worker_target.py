"""Copy verified controlled dependencies plus new worker code to a fresh private bundle."""
import argparse
import json
import os
from pathlib import Path
import shutil

from glim_target_controlled_mapping import PURPOSE as PARENT_PURPOSE, verify_control
from glim_target_replay import verify_replay_bundle
from glim_target_worker import PURPOSE, RUNTIME_SOURCES
from slam_target_probe import verify_bundle
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]


def prepare(args):
    source = args.controlled_bundle.resolve(strict=True)
    output = args.output.resolve()
    if (output.exists() or not output.is_relative_to((ROOT / ".local").resolve())
            or output.is_relative_to(source)):
        raise ValueError("new_ignored_paced_worker_bundle_required")
    parent = verify_replay_bundle(source, PARENT_PURPOSE)
    verify_control(source)
    verify_bundle(args.core_bundle)
    if sha256(args.core_bundle / "bundle-manifest.json") != parent["coreBundleManifestSha256"]:
        raise ValueError("same_verified_paced_worker_core_required")
    for name, entry in parent["files"].items():
        if name.startswith("tools/") and sha256(ROOT / name) != entry["sha256"]:
            raise ValueError("historical_target_tools_must_remain_unchanged")
    output.mkdir(mode=0o700, parents=True)
    for name in parent["files"]:
        destination = output / name
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
    for name in RUNTIME_SOURCES:
        destination = output / name
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    manifest = {
        **parent, "purpose": PURPOSE, "files": {},
        "parentControlledManifestSha256": sha256(source / "replay-manifest.json"),
        "preparerSourceSha256": sha256(__file__),
        "recordedReceiveTimingPacedAtOneTimes": True, "liveDdsTrial": False,
    }
    for path in sorted(output.rglob("*")):
        if path.is_file():
            path.chmod(0o600)
            manifest["files"][path.relative_to(output).as_posix()] = {
                "sha256": sha256(path), "bytes": path.stat().st_size}
    (output / "replay-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    verify_replay_bundle(output, PURPOSE)
    verify_control(output)
    return {"result": "paced_worker_bundle_prepared_without_native_rebuild",
            "manifestSha256": sha256(output / "replay-manifest.json"), "files": len(manifest["files"]) + 1}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("controlled-bundle", "core-bundle", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        print(json.dumps(prepare(args)))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
