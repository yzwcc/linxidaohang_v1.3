"""Isolate the pinned exporter's randomized ground fit from the live ROS process."""

from itertools import combinations
import json
import math
import os
from pathlib import Path
import subprocess
import sys


SEEDS = (0, 1, 2)
MAX_NORMAL_ANGLE_DEG = 1.0
MAX_ANCHORED_HEIGHT_DELTA_M = 0.03
WORKER_TIMEOUT_S = 120


def agree(left, right):
    """Compare fitted normals and floor height at the same initial base position."""
    a = left["measured_ground_plane"][:3]
    b = right["measured_ground_plane"][:3]
    dot = sum(x * y for x, y in zip(a, b))
    return (dot >= math.cos(math.radians(MAX_NORMAL_ANGLE_DEG))
            and abs(left["base_height_m"] - right["base_height_m"]) <= MAX_ANCHORED_HEIGHT_DELTA_M)


def consensus(estimate, seed, pending, args, kwargs):
    trials = []
    for value in SEEDS:
        seed(value)
        trials.append(estimate(*args, **kwargs))
    valid = [trial for trial in trials if trial["state"] == "ready"]
    if not valid:
        return trials[0]
    # No fallback to a single pass. Also reject an ambiguous chain where the
    # middle candidate agrees with each end but the ends disagree with each other.
    if len(valid) == 3 and all(agree(a, b) for a, b in combinations(valid, 2)):
        accepted = valid
    else:
        pairs = [(a, b) for a, b in combinations(valid, 2) if agree(a, b)]
        if len(pairs) != 1:
            return pending("本次录制未找到一致的地面拟合，请保留原始录制", valid[0]["evidence"])
        accepted = pairs[0]
    # Preserve the original complete reference, not an averaged transform that
    # has never passed the per-plane geometry and coverage gates.
    selected = max(accepted, key=lambda item: (
        item["quality"]["occupied_area_m2"], item["quality"]["support_points"],
        -item["quality"]["residual_p95_m"],
    ))
    return {**selected, "quality": {**selected["quality"],
            "lingxi_ground_consensus": {"algorithm": "seeded_ransac_consensus_v1",
                                        "trials": len(trials), "agreed": len(accepted)}}}


def read_saved_export_reference(source, session):
    """Recompute from the immutable saved session; do not register a map or write a cache."""
    env = {**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "RCUTILS_LOGGING_USE_STDOUT": "0"}
    try:
        result = subprocess.run(
            [sys.executable, "-s", "-B", str(Path(__file__).resolve()), str(source)],
            input=json.dumps(session, allow_nan=False), stdout=subprocess.PIPE,
            text=True, encoding="utf-8", timeout=WORKER_TIMEOUT_S, env=env, check=True,
        )
        data = json.loads(result.stdout)
        if set(data) != {"reference", "context"} or data["reference"]["state"] not in {"ready", "pending"}:
            raise ValueError("unexpected_ground_result")
        return data
    except (subprocess.SubprocessError, ValueError, TypeError, KeyError, OSError) as error:
        raise RuntimeError("reference_ground_worker_failed") from error


def main():
    # The service already checked that this checkout is root-owned and at the
    # pinned, unmodified revision. Reuse its original input checks and all of its
    # original per-plane gravity, ankle, coverage and residual thresholds.
    sys.path.insert(0, sys.argv[1])
    from robot_nav_tasks import display_reference

    base_estimate = display_reference.estimate_reference

    def stable_estimate(*args, **kwargs):
        return consensus(base_estimate, display_reference.o3d.utility.random.seed,
                         display_reference._pending, args, kwargs)

    display_reference.estimate_reference = stable_estimate
    result = display_reference.analyze_saved_reference(json.load(sys.stdin))
    print(json.dumps({key: result[key] for key in ("reference", "context")}, allow_nan=False))


if __name__ == "__main__":
    main()
