"""Read-only selection of a target-tested private ARM64 terrain trial.

The operator pins a trusted local runner receipt, not a navigation permission.
No target executable or bundled Python test is imported or run here.
"""
from pathlib import Path, PurePosixPath

from localization_control import direct, strict_json
import terrain_target_probe as probe

ROOT = Path(__file__).resolve().parents[2]
CURRENT_SOURCES = (
    "robot-navigation/lingxi_navigation/__init__.py",
    "robot-navigation/lingxi_navigation/navfn.py",
    "robot-navigation/lingxi_navigation/ground.py",
    "robot-navigation/lingxi_navigation/occupancy.py",
    "robot-navigation/native-ground/ground_bridge.cpp",
    "robot-navigation/native-octomap/octomap_bridge.cpp",
    "robot-navigation/patchworkpp.lock.json",
    "robot-navigation/octomap.lock.json",
    "tools/terrain_target_probe.py",
)


def pinned(path, expected, maximum):
    path = Path(path)
    if (not probe.digest(expected) or not 0 < direct(path).st_size <= maximum
            or probe.sha256(path) != expected):
        raise ValueError("pinned_bounded_terrain_target_input_required")
    return path


def source_hashes():
    result = {}
    for name in CURRENT_SOURCES:
        direct(ROOT / name)
        result[name] = probe.sha256(ROOT / name)
    return result


def verify_selection(selection, ground, octomap):
    if (type(selection) is not dict
            or set(selection) != {"root", "manifestSha256", "report", "reportSha256"}):
        raise ValueError("explicit_terrain_target_selection_required")
    root = Path(selection["root"])
    direct(root, directory=True)
    identity = selection["manifestSha256"]
    manifest_path = pinned(root / probe.MANIFEST, identity, 131072)
    manifest = probe.verify_bundle(root)
    sources = source_hashes()
    if any(manifest["sourceSha256"].get(name) != identity for name, identity in sources.items()):
        raise ValueError("current_production_terrain_target_sources_required")
    report_path = pinned(selection["report"], selection["reportSha256"], 131072)
    if root in report_path.parents:
        raise ValueError("external_terrain_target_runner_receipt_required")
    report = strict_json(report_path.read_bytes())
    fields = {"result", "trialManifestSha256", "privateRemoteDirectory", "probeExitCode",
              "privateOutputSha256", "targetProbe"}
    if (type(report) is not dict or set(report) != fields or report["result"] != probe.RESULT
            or report["trialManifestSha256"] != identity
            or type(report["probeExitCode"]) is not int or report["probeExitCode"] != 0
            or not probe.digest(report["privateOutputSha256"])
            or type(report["privateRemoteDirectory"]) is not str
            or not PurePosixPath(report["privateRemoteDirectory"]).is_absolute()
            or PurePosixPath("/root") in PurePosixPath(report["privateRemoteDirectory"]).parents
            or ".." in PurePosixPath(report["privateRemoteDirectory"]).parts):
        raise ValueError("successful_terrain_target_runner_receipt_required")
    probe.validate_result(report["targetProbe"], manifest, identity)
    for selected, name in zip((ground, octomap), probe.LIBRARIES):
        member = "native/" + name
        if (Path(selected["library"]) != root / member
                or selected["sha256"] != manifest["files"][member]["sha256"]):
            raise ValueError("same_target_tested_terrain_libraries_required")
    dependencies = {name: manifest["files"]["native/lib/" + name]["sha256"] for name in probe.DEPENDENCIES}
    if octomap["dependencies"] != dependencies:
        raise ValueError("same_target_tested_terrain_dependencies_required")
    if (probe.sha256(manifest_path) != identity or probe.verify_bundle(root) != manifest
            or probe.sha256(report_path) != selection["reportSha256"] or source_hashes() != sources):
        raise ValueError("unchanged_terrain_target_selection_required")
