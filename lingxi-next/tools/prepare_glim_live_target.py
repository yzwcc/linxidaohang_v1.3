"""Prepare a fresh private live diagnostic from the verified paced worker bundle."""
import argparse
import json
import os
from pathlib import Path
import shutil

from glim_live_session import PURPOSE, RUNTIME_SOURCES
from glim_target_replay import verify_replay_bundle
from glim_target_worker import PURPOSE as PARENT_PURPOSE
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]


def prepare(args):
    source, output = args.worker_bundle.resolve(strict=True), args.output.resolve()
    if (output.exists() or not output.is_relative_to((ROOT / ".local").resolve())
            or output.is_relative_to(source)):
        raise ValueError("new_ignored_live_bundle_required")
    parent = verify_replay_bundle(source, PARENT_PURPOSE)
    for name, entry in parent["files"].items():
        if name.startswith(("tools/", "robot-slam/")) and sha256(ROOT / name) != entry["sha256"]:
            raise ValueError("historical_worker_sources_must_remain_unchanged")
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
        "parentWorkerManifestSha256": sha256(source / "replay-manifest.json"),
        "preparerSourceSha256": sha256(__file__),
        "recordedReceiveTimingPacedAtOneTimes": False, "liveDdsTrial": True,
    }
    for path in sorted(output.rglob("*")):
        if path.is_file():
            path.chmod(0o600)
            manifest["files"][path.relative_to(output).as_posix()] = {
                "sha256": sha256(path), "bytes": path.stat().st_size}
    (output / "replay-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    verify_replay_bundle(output, PURPOSE)
    return {"result": "live_bundle_prepared_without_native_rebuild",
            "manifestSha256": sha256(output / "replay-manifest.json"), "files": len(manifest["files"]) + 1}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("worker-bundle", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        print(json.dumps(prepare(args)))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
