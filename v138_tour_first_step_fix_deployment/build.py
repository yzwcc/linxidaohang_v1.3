"""Build the first-step fix from the verified, shipped V1.3.8 r20 baseline."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
NAME = "sparkrobot-lingxi-v1.3.8-tour-first-step-fix-1.0.0-20261022"
RELEASE = "1.0.20-20261021-r20"
BRIDGE = "navigation/robot_local_navigation_v3/lingxi_nav_bridge.py"
BASELINE = HERE.parent / "v138_complete_r20_deployment/dist/lingxi-v1.3.8-complete-20261021-r20-deployment.tar.gz"
BASELINE_SHA = "f3514257f3bdb0c5ca8424189cce9de5adfab552c7635ed9e7d697180ddf12db"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def normalized(path):
    return path.read_bytes().replace(b"\r\n", b"\n")


def prepare_reference():
    if digest(BASELINE.read_bytes()) != BASELINE_SHA:
        raise RuntimeError("r20 archive differs from verified baseline")
    with tarfile.open(BASELINE, "r:gz") as outer:
        prefix = "lingxi-v1.3.8-complete-20261021-r20-deployment/"
        component = outer.extractfile(prefix + f"components/sparkrobot-lingxi-nav-internal-{RELEASE}.tar.gz").read()
        for member in outer.getmembers():
            if member.isfile() and member.name.startswith(prefix + "tests/navigation/"):
                relative = member.name[len(prefix + "tests/navigation/"):]
                target = HERE / "reference/navigation" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(outer.extractfile(member).read())
    with tarfile.open(fileobj=io.BytesIO(component), mode="r:gz") as inner:
        prefix = f"sparkrobot-lingxi-nav-internal-{RELEASE}/"
        manifest = json.loads(inner.extractfile(prefix + "payload-manifest.json").read())
        for member in inner.getmembers():
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            relative = member.name[len(prefix):]
            if relative.startswith("payload/navigation/"):
                relative = relative[len("payload/"):]
            if relative in {BRIDGE, "scripts/navigation_control.sh", "payload-manifest.json"}:
                target = HERE / "reference/runtime" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(inner.extractfile(member).read())
    actual = (HERE / "reference/runtime" / BRIDGE).read_bytes()
    expected = next(entry for entry in manifest["files"] if entry["path"] == BRIDGE)
    if digest(actual) != expected["sha256"] or len(actual) != expected["size"]:
        raise RuntimeError("r20 runtime bridge does not match its manifest")
    return manifest


def build(manifest):
    patched = normalized(HERE / "source" / BRIDGE)
    compile(patched, BRIDGE, "exec")
    entries = {entry["path"]: entry for entry in manifest["files"]}
    meta = {
        "version": "1.0.0-20261022",
        "name": NAME,
        "allowed_releases": [RELEASE],
        "baseline_archive_sha256": BASELINE_SHA,
        "baseline_bridge_sha256": entries[BRIDGE]["sha256"],
        "patched_bridge_sha256": digest(patched),
        "client_sha256": entries["navigation/robot_local_navigation_v3/navigation_client.py"]["sha256"],
        "controller_sha256": digest((HERE / "reference/runtime/scripts/navigation_control.sh").read_bytes()),
    }
    files = {name: normalized(HERE / name) for name in (
        "install.py", "install.sh", "README.md", "docs/部署说明.md",
    )}
    files["payload/" + BRIDGE] = patched
    files["release.json"] = (json.dumps(meta, indent=2) + "\n").encode()
    files["SHA256SUMS"] = "".join(
        f"{digest(data)}  {name}\n" for name, data in sorted(files.items())
    ).encode()
    write_archive(files, NAME, "部署说明.md")


def write_archive(files, name, guide_name):
    output = HERE / "dist"
    output.mkdir(exist_ok=True)
    archive = output / (name + ".tar.gz")
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as tar:
                for relative, data in sorted(files.items()):
                    info = tarfile.TarInfo(name + "/" + relative)
                    info.size = len(data)
                    info.mode = 0o755 if relative.endswith(".sh") else 0o644
                    info.mtime = 0
                    tar.addfile(info, io.BytesIO(data))
    archive.with_name(archive.name + ".sha256").write_bytes(
        f"{digest(archive.read_bytes())}  {archive.name}\n".encode()
    )
    (output / guide_name).write_bytes(files["docs/部署说明.md"])
    print(archive)
    print("SHA256=" + digest(archive.read_bytes()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    manifest = prepare_reference()
    if not args.prepare_only:
        build(manifest)
