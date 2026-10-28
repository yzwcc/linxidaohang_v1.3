"""Materialize the verified cross-built libraries in a synthetic, private trial bundle."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil

from build_slam_arm64_trial import cross_environment, inspect_elf
from build_slam_cpu_trial import PINS
from slam_target_probe import sha256


ROOT = Path(__file__).resolve().parents[1]


def prepare(cross, native, output):
    cross, native, output = cross.resolve(strict=True), native.resolve(strict=True), output.resolve()
    if platform.system() != "Linux" or platform.machine() != "x86_64" or os.geteuid() == 0:
        raise ValueError("ordinary_user_linux_build_host_required")
    if output.exists():
        raise ValueError("existing_target_bundle_is_preserved")
    if any(output == path or path in output.parents for path in (cross, native)):
        raise ValueError("bundle_must_be_outside_build_inputs")
    report_path = cross / "cross-build-report.json"
    report = json.loads(report_path.read_text())
    if (report.get("result") != "arm64_cross_built_and_linked_not_executed"
            or report.get("sourceCommits") != PINS or report.get("targetExecutionValidated") is not False):
        raise ValueError("verified_cross_build_required")
    for entry in report["artifacts"]:
        source = (cross / ("link-smoke" if entry["file"] == "lingxi_glim_time_smoke" else "prefix/lib")
                  / entry["file"])
        if sha256(source) != entry["sha256"]:
            raise ValueError("cross_build_artifact_changed")
    configuration = cross / "synthetic-config"
    trial = json.loads((configuration / "trial-manifest.json").read_text())
    if trial.get("inputClassification") != "synthetic_fixture":
        raise ValueError("synthetic_config_required")
    env = cross_environment(cross, native)
    readelf = cross / "toolchain/usr/bin/aarch64-linux-gnu-readelf"
    sources = [(ROOT / "tools/slam_target_probe.py", "slam_target_probe.py", False)]
    sources.append((cross / "link-smoke/lingxi_glim_time_smoke", "bin/lingxi_glim_time_smoke", True))
    for library in sorted((cross / "prefix/lib").glob("*.so*")):
        if not library.resolve().is_relative_to((cross / "prefix/lib").resolve()):
            raise ValueError("external_library_not_allowed")
        inspect_elf(library, readelf, env)
        sources.append((library, "lib/" + library.name, False))
    for path in sorted(configuration.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise ValueError("regular_synthetic_config_files_required")
        if path.name != "trial-manifest.json" and sha256(path) != trial["files"].get(path.name):
            raise ValueError("synthetic_config_changed")
        sources.append((path, "config/" + path.name, False))
    output.mkdir(parents=True, mode=0o700)
    manifest = {
        "schemaVersion": 1, "purpose": "synthetic_arm64_loading_trial",
        "inputClassification": "synthetic_fixture", "sourceCommits": PINS,
        "crossBuildReportSha256": sha256(report_path), "files": {},
        "navigationValidated": False, "deploymentApproved": False,
    }
    for source, name, executable in sources:
        destination = output / name
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o700 if executable else 0o600)
        manifest["files"][name] = {
            "sha256": sha256(destination), "bytes": destination.stat().st_size,
            "executable": executable,
        }
    (output / "bundle-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return {"result": "synthetic_target_bundle_prepared", "files": len(sources),
            "bytes": sum(item["bytes"] for item in manifest["files"].values()),
            "manifestSha256": sha256(output / "bundle-manifest.json")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cross-work", "native-work", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    old_umask = os.umask(0o077)
    try:
        print(json.dumps(prepare(args.cross_work, args.native_work, args.output), indent=2))
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
