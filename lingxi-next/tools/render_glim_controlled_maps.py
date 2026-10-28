"""Private, full-point map inspection figures; never export coordinates to public docs."""
import argparse
import json
import os
from pathlib import Path

import numpy as np

from compare_glim_architectures import require_hash
from export_glim_controlled_mapping import load_run
from run_glim_controlled_mapping import ROOT, label
from verify_slam_capture import sha256


def new_private_output(path):
    path = path.resolve()
    if path.exists() or not path.is_relative_to((ROOT / ".local").resolve()):
        raise ValueError("new_ignored_private_preview_directory_required")
    return path


def aligned_for_view(value):
    transform = np.linalg.inv(value["saved"][0])
    points = value["points"] @ transform[:3, :3].T + transform[:3, 3]
    positions = value["saved"][:, :3, 3] @ transform[:3, :3].T + transform[:3, 3]
    return points, positions


def padded_bounds(views, axes=(0, 1)):
    values = np.concatenate([np.concatenate((points[:, axes], poses[:, axes]))
                             for points, poses in views])
    low, high = values.min(axis=0), values.max(axis=0)
    padding = np.maximum((high - low) * .04, .1)
    return low - padding, high + padding


def point_panel(axis, points, poses, dimensions, bounds, color_limits):
    cloud = axis.scatter(points[:, dimensions[0]], points[:, dimensions[1]], c=points[:, 2],
                         s=.35, alpha=.65, cmap="viridis", vmin=color_limits[0], vmax=color_limits[1],
                         linewidths=0, rasterized=True)
    axis.plot(poses[:, dimensions[0]], poses[:, dimensions[1]], color="#c23b22", linewidth=1.1)
    axis.scatter(*poses[0, list(dimensions)], color="#c23b22", s=28, marker="^", zorder=4)
    axis.scatter(*poses[-1, list(dimensions)], color="#111827", s=32, marker="x", zorder=4)
    axis.set_xlim(bounds[0][0], bounds[1][0])
    axis.set_ylim(bounds[0][1], bounds[1][1])
    axis.set_aspect("equal", adjustable="box")
    axis.tick_params(labelsize=10)
    for spine in axis.spines.values():
        spine.set_color("#c8cbd0")
    return cloud


def render_repeatability(loaded, dataset, destination):
    import matplotlib.pyplot as plt

    names = [f"{dataset}-{hypothesis}-repeat{repeat}"
             for hypothesis in ("start", "end") for repeat in (1, 2, 3)]
    views = [aligned_for_view(loaded[name]) for name in names]
    bounds = padded_bounds(views)
    color_limits = (min(points[:, 2].min() for points, _ in views),
                    max(points[:, 2].max() for points, _ in views))
    figure, axes = plt.subplots(2, 3, figsize=(15, 10), dpi=130)
    for axis, name, view in zip(axes.ravel(), names, views):
        cloud = point_panel(axis, *view, (0, 1), bounds, color_limits)
        report = loaded[name]["report"]["mapping"]
        axis.set_title(f"{name.replace(dataset + '-', '')}\n"
                       f"{report['points']:,} map points | {report['submaps']} submaps", fontsize=12)
    figure.suptitle(f"{dataset.capitalize()} recording | full saved point clouds, not occupancy grids\n"
                    "First-valid-LiDAR rigid frame per run; no scale fit or height crop", fontsize=14, y=.975)
    figure.supxlabel("X (raw coordinate units, not calibrated metres)", fontsize=12, y=.035)
    figure.supylabel("Y (raw coordinate units)", fontsize=12, x=.025)
    figure.text(.5, .01, "Red: saved LiDAR trajectory | triangle: first valid pose | cross: last valid pose",
                ha="center", fontsize=11)
    figure.subplots_adjust(left=.07, right=.88, bottom=.11, top=.86, hspace=.32, wspace=.23)
    color_axis = figure.add_axes((.91, .2, .014, .6))
    figure.colorbar(cloud, cax=color_axis, label="Z (raw coordinate units)")
    figure.savefig(destination, metadata={"Description": "Private diagnostic, not geometry or navigation acceptance"})
    plt.close(figure)


def render_orthographic(loaded, destination):
    import matplotlib.pyplot as plt

    names = ["dynamic-start-repeat1", "dynamic-end-repeat1"]
    views = [aligned_for_view(loaded[name]) for name in names]
    color_limits = (min(points[:, 2].min() for points, _ in views),
                    max(points[:, 2].max() for points, _ in views))
    figure, axes = plt.subplots(2, 3, figsize=(15, 10), dpi=130)
    for column, dimensions in enumerate(((0, 1), (0, 2), (1, 2))):
        bounds = padded_bounds(views, dimensions)
        projection = "".join("XYZ"[value] for value in dimensions)
        for row, (name, view) in enumerate(zip(names, views)):
            axis = axes[row, column]
            cloud = point_panel(axis, *view, dimensions, bounds, color_limits)
            axis.set_title(f"{name.replace('dynamic-', '')} | {projection}", fontsize=12)
            axis.set_xlabel("XYZ"[dimensions[0]] + " (raw units)", fontsize=11)
            axis.set_ylabel("XYZ"[dimensions[1]] + " (raw units)", fontsize=11)
    figure.suptitle("Dynamic map | all saved points, three orthographic projections\n"
                    "First-valid-LiDAR rigid frame; no scale fit, range or height crop", fontsize=14, y=.975)
    figure.text(.5, .015, "Diagnostic timing hypotheses; neither is selected as calibrated or production-ready",
                ha="center", fontsize=11)
    figure.subplots_adjust(left=.075, right=.88, bottom=.1, top=.87, hspace=.34, wspace=.35)
    color_axis = figure.add_axes((.92, .2, .014, .6))
    figure.colorbar(cloud, cax=color_axis, label="Z (raw coordinate units)")
    figure.savefig(destination, metadata={"Description": "Private diagnostic, not geometry or navigation acceptance"})
    plt.close(figure)


def run(args):
    output = new_private_output(args.output)
    evidence = json.loads(args.evidence.read_text())
    require_hash(ROOT / "tools/run_glim_controlled_mapping.py", evidence["runnerSourceSha256"])
    require_hash(ROOT / "tools/export_glim_controlled_mapping.py", evidence["exporterSourceSha256"])
    require_hash(args.results / "collection-report.json", evidence["collectionReportSha256"])
    loaded = {}
    for dataset in ("static", "dynamic"):
        for hypothesis in ("header_is_scan_start", "header_is_scan_end"):
            for repeat in (1, 2, 3):
                name = label(dataset, hypothesis, repeat)
                loaded[name] = load_run(
                    args.results / name, (dataset, hypothesis, repeat),
                    {"exitCode": 0, "reportSha256": evidence["runs"][name]["controlledReportSha256"]},
                    evidence["baselineConfigurationManifestSha256"])
    output.mkdir(mode=0o700, parents=True)
    os.environ["MPLCONFIGDIR"] = str(output / "matplotlib-cache")
    import matplotlib
    matplotlib.use("Agg")
    files = ("static-repeatability.png", "dynamic-repeatability.png", "dynamic-orthographic.png")
    for dataset, filename in zip(("static", "dynamic"), files):
        render_repeatability(loaded, dataset, output / filename)
    render_orthographic(loaded, output / files[-1])
    from PIL import Image
    for filename in files:
        with Image.open(output / filename) as image:
            pixels = np.asarray(image.convert("RGB"))
            if image.size != (1950, 1300) or np.std(pixels.astype(float)) < 5:
                raise ValueError("nonblank_expected_size_private_preview_required")
    report = {
        "purpose": "private_full_point_controlled_map_inspection_not_accuracy_acceptance",
        "rendererSourceSha256": sha256(__file__), "evidenceSha256": sha256(args.evidence),
        "figuresSha256": {name: sha256(output / name) for name in files},
        "allSavedMapPointsPlottedWithoutAdditionalDownsampling": True,
        "firstSavedLidarPoseRigidAlignmentForViewingOnly": True,
        "rawCoordinateScaleNotCalibrated": True, "navigationValidated": False,
    }
    with (output / "preview-report.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"result": "three_private_full_point_map_figures_saved", "files": list(files)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("results", "evidence", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
