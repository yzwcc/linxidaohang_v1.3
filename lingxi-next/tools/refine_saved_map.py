"""Offline CPU submap refinement, never an in-place edit or a navigation acceptance gate."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

import json5
import numpy as np

from build_slam_cpu_trial import PINS
from export_glim_mapping_evidence import check_reload_log
from glim_trial_config import verify_source
from run_glim_mapping_trial import SOURCE_FILES, compare_points, core_hashes
from run_glim_offline_trial import library_environment
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools/glim-refine"
BOUNDARIES = ("robotContacted", "liveSensorsSubscribed", "motionCommandsSent", "sourceMapModified",
              "physicalScaleValidated", "geometricAccuracyValidated", "loopClosureValidated",
              "navigationValidated", "deploymentApproved")
PYTHON_SOURCES = tuple(sorted(set(SOURCE_FILES) | {"refine_saved_map.py", "export_glim_mapping_evidence.py"}))


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False) + "\n")


def private_output(path, source=None):
    path = Path(path).absolute()
    if path.exists() or path.is_symlink() or path.resolve() != path:
        raise ValueError("new_direct_output_required")
    if source is not None:
        source = Path(source).resolve(strict=True)
        if path.is_relative_to(source) or source.is_relative_to(path):
            raise ValueError("source_map_and_output_must_be_separate")
    allowed = (ROOT / ".local").resolve()
    cache = (Path.home() / ".cache/lingxi-next").resolve()
    if not (path.is_relative_to(allowed) or path.is_relative_to(cache)):
        raise ValueError("ignored_private_workspace_output_required")
    return path


def inventory(directory):
    directory = Path(directory).absolute()
    if not directory.is_dir() or directory.is_symlink() or directory.resolve() != directory:
        raise ValueError("direct_map_directory_required")
    files, total = [], 0
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("regular_map_tree_required")
        if path.is_file():
            size = path.stat().st_size
            total += size
            if size > 64_000_000 or total > 512_000_000 or len(files) >= 4096:
                raise ValueError("bounded_saved_map_required")
            files.append((path.relative_to(directory).as_posix(), size, sha256(path)))
    required = {"graph.bin", "values.bin", "graph.txt", "odom_lidar.txt", "traj_lidar.txt",
                "odom_imu.txt", "traj_imu.txt"}
    if not required.issubset({name for name, _, _ in files}):
        raise ValueError("complete_saved_map_required")
    lines = (directory / "graph.txt").read_text().splitlines()
    metadata = {}
    for index, key in enumerate(("num_submaps:", "num_all_frames:", "num_matching_cost_factors:")):
        fields = lines[index].split() if index < len(lines) else []
        if len(fields) != 2 or fields[0] != key or not fields[1].isdigit():
            raise ValueError("explicit_saved_graph_metadata_required")
        metadata[key[:-1]] = int(fields[1])
    count, frames, matching = (metadata[key] for key in ("num_submaps", "num_all_frames", "num_matching_cost_factors"))
    if not 0 < count <= 512 or not 0 < frames <= 1_000_000 or len(lines) != matching + 3:
        raise ValueError("bounded_nonempty_complete_graph_required")
    pairs = set()
    for line in lines[3:]:
        fields = line.split()
        if (len(fields) != 4 or fields[:2] not in (["matching_cost", "vgicp"], ["matching_cost", "gicp"])
                or not all(value.isdigit() for value in fields[2:])):
            raise ValueError("reviewed_cpu_matching_cost_required")
        pair = tuple(map(int, fields[2:]))
        if not 0 <= pair[0] < pair[1] < count or pair in pairs:
            raise ValueError("unique_valid_graph_pairs_required")
        pairs.add(pair)
    children = {path.name for path in directory.iterdir() if path.is_dir()}
    expected = {f"{i:06d}" for i in range(count)}
    if children - {"config"} != expected:
        raise ValueError("complete_numbered_submaps_required")
    for name in required - {"graph.bin", "values.bin", "graph.txt"}:
        trajectory = np.loadtxt(directory / name, ndmin=2)
        if (trajectory.shape != (frames, 8) or not np.isfinite(trajectory).all()
                or np.any(np.diff(trajectory[:, 0]) <= 0)):
            raise ValueError("complete_finite_ordered_trajectories_required")
        if np.max(np.abs(np.linalg.norm(trajectory[:, 4:8], axis=1) - 1)) > 2e-5:
            raise ValueError("rounded_unit_trajectory_quaternions_required")
    return {**metadata, "nonAdjacentMatchingPairs": sum(b > a + 1 for a, b in pairs),
            "fileCount": len(files), "totalBytes": total,
            "artifactInventorySha256": hashlib.sha256(json.dumps(files, separators=(",", ":")).encode()).hexdigest()}


def implementation_inputs(work):
    core = json.loads((work / "build-report.json").read_text())
    if core.get("result") != "core_libraries_built_not_target_acceptance" or core.get("sourceCommits") != PINS:
        raise ValueError("pinned_cpu_core_build_required")
    return {"sources": {name: sha256(SOURCE / name) for name in ("CMakeLists.txt", "refine.cpp")},
            "runnerSources": {name: sha256(ROOT / "tools" / name) for name in PYTHON_SOURCES},
            "coreBuildReportSha256": sha256(work / "build-report.json"),
            "coreLibrariesSha256": core_hashes(work)}


def build(work, output):
    work = Path(work).resolve(strict=True)
    output = private_output(output)
    inputs = implementation_inputs(work)
    output.mkdir(mode=0o700)
    for name in ("CMakeLists.txt", "refine.cpp"):
        shutil.copyfile(SOURCE / name, output / name)
    for label, command in (("configure", ["cmake", "-S", str(output), "-B", str(output / "build")]),
                           ("build", ["cmake", "--build", str(output / "build"), "--parallel", "1"])):
        with (output / (label + ".private.log")).open("x") as log:
            result = subprocess.run(command, env=library_environment(work), stdout=log, stderr=log, timeout=300)
        if result.returncode:
            raise RuntimeError("refinement_build_failed_see_private_log")
    if (implementation_inputs(work) != inputs or
            any(sha256(output / name) != digest for name, digest in inputs["sources"].items())):
        raise ValueError("build_inputs_must_remain_unchanged")
    report = {"result": "offline_refinement_tool_built_not_geometry_acceptance", "sourceCommits": PINS,
              **inputs,
              "executableSha256": sha256(output / "build/lingxi_glim_refine")}
    save(output / "build-report.json", report)
    return report


def verified_executable(adapter, work):
    adapter, work = Path(adapter).resolve(strict=True), Path(work).resolve(strict=True)
    value = json.loads((adapter / "build-report.json").read_text())
    binary = adapter / "build/lingxi_glim_refine"
    inputs = implementation_inputs(work)
    if (value.get("result") != "offline_refinement_tool_built_not_geometry_acceptance"
            or value.get("sourceCommits") != PINS
            or any(value.get(name) != expected for name, expected in inputs.items())
            or value.get("executableSha256") != sha256(binary)):
        raise ValueError("source_bound_refinement_executable_required")
    return binary


def configuration_hashes(directory):
    directory = Path(directory).absolute()
    if directory.is_symlink() or directory.resolve(strict=True) != directory:
        raise ValueError("direct_configuration_directory_required")
    entries = sorted(directory.iterdir())
    if not 1 <= len(entries) <= 32 or any(path.is_symlink() or not path.is_file()
                                         or path.stat().st_size > 2_000_000 for path in entries):
        raise ValueError("bounded_direct_configuration_required")
    return {path.name: sha256(path) for path in entries}


def source_receipt(path, source, points_sha, config_hashes):
    if path is None:
        return {"identityBound": False, "classification": "unbound_offline_saved_map"}
    path = Path(path).absolute()
    if (path.is_symlink() or path.resolve(strict=True) != path or not path.is_file()
            or not 0 < path.stat().st_size <= 2_000_000):
        raise ValueError("bounded_direct_source_receipt_required")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("saved_source_receipt_required")
    counts, provenance = value.get("counts"), value.get("sensorProvenance")
    expected_counts = {"finalizedFrames": source["num_all_frames"], "savedFrames": source["num_all_frames"],
                       "submaps": source["num_submaps"],
                       "points": counts.get("points") if isinstance(counts, dict) else None}
    if (type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 2
            or not isinstance(value.get("session"), str) or not re.fullmatch(r"[0-9a-f]{32}", value["session"])
            or value.get("inputMode") not in ("live", "recorded")
            or not isinstance(counts, dict) or counts != expected_counts
            or any(type(number) is not int or number <= 0 for number in counts.values())
            or value.get("savedPointsSha256") != points_sha or value.get("freshProcessReloadPassed") is not True
            or not isinstance(value.get("mapInventory"), dict)
            or any(value["mapInventory"].get(key) != source[key] for key in
                   ("num_submaps", "num_all_frames", "num_matching_cost_factors", "fileCount",
                    "totalBytes", "artifactInventorySha256"))
            or not isinstance(provenance, dict) or provenance.get("schemaVersion") != 1
            or provenance.get("configurationSha256") != config_hashes):
        raise ValueError("receipt_map_points_configuration_identity_mismatch")
    return {"identityBound": True, "mapId": "slam-" + value["session"],
            "receiptSha256": sha256(path), "inputMode": value["inputMode"],
            "pointCount": counts["points"]}


def prepare_config(upstream, original, output):
    revision = verify_source(upstream)
    original = Path(original).absolute()
    hashes = configuration_hashes(original)
    output.mkdir(mode=0o700)
    for name in hashes:
        shutil.copyfile(original / name, output / name)
    global_config = json5.loads((original / "config.json").read_text())
    global_config["global"]["config_global_mapping"] = "config_global_mapping_cpu.json"
    (output / "config.json").unlink()
    save(output / "config.json", global_config)
    cpu = Path(upstream) / "config/config_global_mapping_cpu.json"
    shutil.copyfile(cpu, output / cpu.name)
    return {"upstreamCommit": revision, "upstreamCpuConfigurationSha256": sha256(cpu),
            "originalConfigurationSha256": hashes, "effectiveConfigurationSha256": configuration_hashes(output),
            "effectiveOverrides": {"enable_gpu": False, "enable_imu": False,
                                   "refinementOptimization": True, "reloadOptimization": False,
                                   "registration_error_factor_type": "VGICP", "randomsampling_rate": 1.0}}


def render_comparison(first, last, z_range, output):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    lower, upper = np.quantile(first[:, :2], [0.01, 0.99], axis=0)
    padding = np.maximum((upper - lower) * 0.05, 0.1)
    lower, upper = lower - padding, upper + padding
    fig, axes = plt.subplots(2, 2, figsize=(14, 10), dpi=140)
    retained = []
    for row, band in enumerate((None, z_range)):
        for column, (label, cloud) in enumerate((("Source", first), ("Offline refined", last))):
            shown = cloud if band is None else cloud[(cloud[:, 2] >= band[0]) & (cloud[:, 2] <= band[1])]
            retained.append(int(len(shown)))
            axis = axes[row, column]
            axis.scatter(shown[:, 0], shown[:, 1], s=0.3, color="#155e75", alpha=0.55, linewidths=0)
            axis.set(xlim=(lower[0], upper[0]), ylim=(lower[1], upper[1]), aspect="equal",
                     xlabel="X (raw units)", ylabel="Y (raw units)",
                     title=f"{label} | {'All Z' if band is None else f'Z [{band[0]}, {band[1]}] raw'} | {len(shown):,} points")
            axis.grid(alpha=0.15)
    fig.suptitle("Same XY limits and scale | Source 1%-99% viewport | Accuracy not validated", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    path = output / "comparison.private.png"
    fig.savefig(path)
    plt.close(fig)
    return {"displayOnly": True, "matplotlibVersion": matplotlib.__version__, "sha256": sha256(path),
            "sourceViewportQuantiles": [0.01, 0.99], "xyLowerRaw": lower.tolist(), "xyUpperRaw": upper.tolist(),
            "zBandRaw": list(z_range), "panelRetainedPointCounts": retained,
            "geometricAccuracyValidated": False}


def run(args):
    if isinstance(args.overlap, bool) or not math.isfinite(args.overlap) or not 0 < args.overlap <= 1:
        raise ValueError("explicit_finite_overlap_fraction_required")
    render = getattr(args, "render_comparison", False)
    z_range = getattr(args, "z_range", None)
    if (render and (z_range is None or len(z_range) != 2 or
                    not all(math.isfinite(x) for x in z_range) or z_range[0] >= z_range[1])) or (not render and z_range):
        raise ValueError("explicit_valid_display_z_range_required_with_render")
    original = Path(args.map).resolve(strict=True)
    output = private_output(args.output, original)
    before = inventory(original)
    points_sha = sha256(args.points)
    size = Path(args.points).stat().st_size
    if size <= 0 or size % 24 or size > 256_000_000:
        raise ValueError("bounded_complete_source_xyz_points_required")
    original_config = configuration_hashes(args.config)
    receipt = source_receipt(getattr(args, "receipt", None), before, points_sha, original_config)
    if receipt["identityBound"] and receipt["pointCount"] != size // 24:
        raise ValueError("receipt_point_count_mismatch")
    binary = verified_executable(args.adapter, args.work)
    inputs = implementation_inputs(args.work)
    build_sha = sha256(args.adapter / "build-report.json")
    output.mkdir(mode=0o700)
    configuration = prepare_config(args.upstream, args.config, output / "config")
    reports = {}
    stages = (("source-reload", "reload", original, output / "unused-source-map"),
              ("refine", "refine", original, output / "map"),
              ("refined-reload", "reload", output / "map", output / "unused-refined-map"))
    for name, mode, source_map, destination in stages:
        command = [str(binary), mode, str(output / "config"), str(source_map), str(destination),
                   str(output / (name + ".private.f64")), str(output / (name + ".native.json")), str(args.overlap)]
        with (output / (name + ".private.log")).open("x") as log:
            child = subprocess.run(command, env=library_environment(args.work), stdout=log, stderr=log, timeout=240)
        if child.returncode:
            raise RuntimeError("native_refinement_failed_see_private_log")
        check_reload_log(output / (name + ".private.log"))
        report = json.loads((output / (name + ".native.json")).read_text())
        if (report.get("refined") is not (mode == "refine") or report.get("points") != size // 24
                or report.get("loadedSubmaps") != before["num_submaps"]
                or type(report.get("pid")) is not int or report["pid"] <= 0
                or type(report.get("peakRssKiB")) is not int or report["peakRssKiB"] <= 0
                or type(report.get("wallSeconds")) not in (int, float)
                or not math.isfinite(report["wallSeconds"]) or report["wallSeconds"] < 0):
            raise ValueError("complete_native_map_accounting_required")
        report["exportPointsSha256"] = sha256(output / (name + ".private.f64"))
        report["privateLogSha256"] = sha256(output / (name + ".private.log"))
        reports[name] = report
        if name == "source-reload":
            source_comparison = compare_points(args.points, output / (name + ".private.f64"), size // 24)
    if len({report["pid"] for report in reports.values()}) != 3:
        raise ValueError("three_fresh_native_processes_required")
    refined = inventory(output / "map")
    if any(refined[key] != before[key] for key in ("num_submaps", "num_all_frames")):
        raise ValueError("all_source_submaps_and_frames_must_remain")
    round_trip = compare_points(output / "refine.private.f64", output / "refined-reload.private.f64", size // 24)
    first = np.fromfile(output / "source-reload.private.f64", dtype="<f8").reshape(-1, 3)
    last = np.fromfile(output / "refine.private.f64", dtype="<f8").reshape(-1, 3)
    displacement = np.linalg.norm(last - first, axis=1)
    if inventory(original) != before or sha256(args.points) != points_sha:
        raise ValueError("source_map_and_point_copy_must_remain_unchanged")
    if (implementation_inputs(args.work) != inputs or sha256(args.adapter / "build-report.json") != build_sha
            or configuration_hashes(args.config) != original_config
            or configuration_hashes(output / "config") != configuration["effectiveConfigurationSha256"]
            or source_receipt(getattr(args, "receipt", None), before, points_sha, original_config) != receipt
            or verify_source(args.upstream) != configuration["upstreamCommit"]
            or sha256(args.upstream / "config/config_global_mapping_cpu.json") !=
            configuration["upstreamCpuConfigurationSha256"]):
        raise ValueError("all_refinement_inputs_must_remain_unchanged")
    verified_executable(args.adapter, args.work)
    result = {"result": "offline_submap_refinement_and_fresh_reload_checked_not_accuracy_acceptance",
              "sourceMap": before, "refinedMap": refined, "sourceReceipt": receipt, "sourcePointsSha256": points_sha,
              "refinedPointsSha256": sha256(output / "refine.private.f64"), "pointCount": size // 24,
              "explicitOverlapThreshold": args.overlap, "configuration": configuration,
              "nativeStages": reports, "sourceRoundTrip": source_comparison, "refinedRoundTrip": round_trip,
              "pointDisplacementRawP50P95Max": np.percentile(displacement, [50, 95, 100]).tolist(),
              "runnerSha256": sha256(__file__), "buildReportSha256": build_sha, "implementationInputs": inputs,
              "sourceInputsUnchanged": True,
              "boundaries": dict.fromkeys(BOUNDARIES, False)}
    if render:
        result["displayOnlyComparison"] = render_comparison(first, last, z_range, output)
    save(output / "report.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build")
    runner = commands.add_parser("run")
    for command in (builder, runner):
        command.add_argument("--work", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    for name in ("adapter", "upstream", "config", "map", "points"):
        runner.add_argument("--" + name, type=Path, required=True)
    runner.add_argument("--overlap", type=float, required=True)
    runner.add_argument("--receipt", type=Path)
    runner.add_argument("--render-comparison", action="store_true")
    runner.add_argument("--z-range", nargs=2, type=float)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64" or os.geteuid() == 0:
        parser.error("ordinary_user_offline_linux_cpu_environment_required")
    previous = os.umask(0o077)
    try:
        report = build(args.work, args.output) if args.command == "build" else run(args)
        print(json.dumps({"result": report["result"], "reportSha256": sha256(args.output / ("build-report.json"
                         if args.command == "build" else "report.json"))}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
