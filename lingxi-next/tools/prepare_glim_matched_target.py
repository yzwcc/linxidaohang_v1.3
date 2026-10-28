"""Copy frozen live dependencies plus new matched-input tools to a private bundle."""
import argparse
import json
import os
from pathlib import Path
import shutil

from glim_live_session import PURPOSE as PARENT_PURPOSE
from glim_matched_session import PURPOSE, SOURCES
from glim_target_replay import verify_replay_bundle
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]


def prepare(args):
    source, output = args.live_bundle.resolve(strict=True), args.output.resolve()
    if output.exists() or not output.is_relative_to((ROOT / ".local").resolve()) or output.is_relative_to(source):
        raise ValueError("new_private_matched_input_bundle_required")
    parent = verify_replay_bundle(source, PARENT_PURPOSE)
    for name, entry in parent["files"].items():
        if name.startswith(("tools/", "robot-slam/")) and sha256(ROOT / name) != entry["sha256"]:
            raise ValueError("historical_live_sources_must_remain_unchanged")
    output.mkdir(mode=0o700, parents=True)
    for name in (*parent["files"], *SOURCES):
        destination = output / name
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name if name in SOURCES else source / name, destination)
        destination.chmod(0o600)
    manifest = {
        **parent, "purpose": PURPOSE, "files": {},
        "parentLiveManifestSha256": sha256(source / "replay-manifest.json"),
        "preparerSourceSha256": sha256(__file__), "sameCallbackInputDiagnostic": True,
    }
    for path in sorted(output.rglob("*")):
        if path.is_file():
            manifest["files"][path.relative_to(output).as_posix()] = {
                "sha256": sha256(path), "bytes": path.stat().st_size}
    (output / "replay-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    verify_replay_bundle(output, PURPOSE)
    return {"result": "matched_input_bundle_prepared_without_native_changes",
            "manifestSha256": sha256(output / "replay-manifest.json")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("live-bundle", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    previous = os.umask(0o077)
    try:
        print(json.dumps(prepare(parser.parse_args())))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
