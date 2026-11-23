#!/usr/bin/env python3
"""Read-only ARM64 replay of first-fix GICP and the subsequent tracking gate.

Never initializes ROS, advertises a pose, or changes a map. Uses the recorded
export's fixed base/IMU reference instead of a new same-stamp live TF. The
result is a registration/timing diagnostic, not a live accuracy measurement.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time

REVISION = "90bbc8096c23a5660fc6c5e26dd99ec1ed79da11"


def timing_record(timing, roundtrip_s):
    def ms(value):
        return round(value * 1000, 2)

    match_s = timing["match_s"]
    candidate_s = timing["candidate_wall_s"]
    phases = timing["phases"]
    proposal_s = phases.get("proposals", {}).get("seconds", 0.)
    return dict(roundtripMs=ms(roundtrip_s), matchMs=ms(match_s),
                outsideMatchMs=ms(roundtrip_s - match_s),
                candidateWallMs=ms(candidate_s), proposalMs=ms(proposal_s),
                otherMatchMs=ms(match_s - candidate_s - proposal_s),
                candidates=[dict(gicpMs=ms(item["gicp_s"]), diagnoseMs=ms(item["diagnose_s"]))
                            for item in timing["candidate_timings"]])


def timing_summary(records):
    if not records:
        return None
    fields = ("roundtripMs", "matchMs", "outsideMatchMs", "candidateWallMs", "proposalMs", "otherMatchMs")
    return {field: dict(median=round(statistics.median(record[field] for record in records), 2),
                        maximum=max(record[field] for record in records)) for field in fields}


def result_age_reason(elapsed_s, source_age_ms, limit_s):
    if elapsed_s + source_age_ms / 1000 <= limit_s:
        return None
    return ("worker_elapsed_exceeds_live_age_limit" if source_age_ms == 0
            else "modeled_age_exceeds_live_age_limit")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--map", type=Path, required=True, help="Saved target export directory")
    parser.add_argument("--scan-export", type=Path, required=True, help="Export metadata for the input recording")
    parser.add_argument("--bag", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--skip-frames", type=int, default=15)
    parser.add_argument("--checks", type=int, default=12)
    parser.add_argument("--seed", nargs=3, type=float, metavar=("X_M", "Y_M", "YAW_DEG"),
                        help="Explicit offline pose estimate required for a different recording")
    parser.add_argument("--source-age-ms", type=float, default=0.,
                        help="Synthetic age already accrued at submit; zero reports a worker-only lower bound")
    args = parser.parse_args()
    if not 0 <= args.skip_frames <= 10000 or not 6 <= args.checks <= 30:
        parser.error("require skip-frames in [0, 10000] and checks in [6, 30]")
    if not 0 <= args.source_age_ms < 1500:
        parser.error("source-age-ms must be in [0, 1500)")
    if args.seed and not all(map(math.isfinite, args.seed)):
        parser.error("seed must be finite")
    source = args.source.resolve(strict=True)
    git = ["git", "--no-optional-locks", "-c", f"safe.directory={source}", "-C", str(source)]
    if (subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip() != REVISION
            or subprocess.check_output([*git, "status", "--porcelain"], text=True).strip()):
        parser.error("requires a clean, pinned reference checkout")
    target = json.loads((args.map / "metadata.json").read_text(encoding="utf-8"))
    scan = json.loads((args.scan_export / "metadata.json").read_text(encoding="utf-8"))
    session = scan["source_session_id"]
    bag = args.bag.resolve(strict=True)
    if not bag.parent.name.endswith("__" + session) or bag.name != "rosbag":
        parser.error("recording directory must match the scan export session")
    bag_file = (bag / "metadata.yaml").resolve(strict=True)
    if str(bag_file) not in {entry["path"] for entry in scan["source_files"]}:
        parser.error("bag is not the source listed in the scan export")
    same_bag = target["source_session_id"] == session
    if not same_bag and args.seed is None:
        parser.error("--seed is required for cross-capture replay")

    sys.path.insert(0, str(source))
    import numpy as np
    import yaml
    from robot_nav_tasks.geometry import initial_matrix, navigation_frame, planar_matrix
    from robot_nav_tasks.localization_manager import LocalizationManager
    from robot_nav_tasks.localization_worker import LocalizationWorker
    from robot_nav_tasks.map_export import BagRecording
    from robot_nav_tasks.research.verifier import Verifier, base_attitude_consistent, ground_motion_consistent

    config_bytes = args.config.read_bytes()
    config = yaml.safe_load(config_bytes)
    age_limit = float(config["runtime"]["confirmation_max_age_s"])
    if not 0 < age_limit <= 1.5 or config["verifier"]["min_checks"] < 5 or config["verifier"]["min_span_s"] < 2:
        parser.error("replay requires a 1.5 s or stricter age limit and at least five checks over two seconds")
    if not config["localizer"]["visibility_check"] or config["localizer"]["top_k"] < 4:
        parser.error("replay requires visible correspondences and at least four candidates")
    recording = BagRecording(bag, scan.get("options", {}))
    verifier = Verifier(config["verifier"])
    tracker = LocalizationManager.__new__(LocalizationManager)
    tracker.config = config
    worker = LocalizationWorker(args.map / "localization.ply", config)
    deadline = time.monotonic() + 110

    def receive():
        while time.monotonic() < deadline:
            value = worker.receive(timeout=.2)
            if value is not None:
                return value
        raise TimeoutError("replay worker exceeded its total time budget")

    timings, results = {"first-fix": [], "tracking": []}, Counter()
    accepted = None
    corrections, match_count = 0, 0
    next_stamp, map_from_odom = 0., None
    try:
        ready = receive()
        if ready["kind"] != "ready":
            raise ValueError("replay worker did not report ready")
        print(json.dumps(dict(candidateWorkers=ready["candidate_workers"], mapLoadMs=round(1000 * ready["map_load_s"], 2),
                              threadLimits=[dict(api=part["user_api"], n=part["num_threads"]) for part in ready["threadpools"]])),
              flush=True)
        for frame_index, (stamp_ns, odom, points) in enumerate(recording.frames()):
            stamp = stamp_ns / 1e9
            if frame_index < args.skip_frames or stamp + .004 < next_stamp:
                continue
            base, points, viewpoint, full = navigation_frame({**scan, "odom_imu": odom, "body_points": points})
            if not base_attitude_consistent(full, config["verifier"]) or not ground_motion_consistent(base, config["verifier"]):
                verifier.reset()
                results["recorded_pose_out_of_bounds"] += 1
                continue
            if map_from_odom is None:
                if args.seed is None:
                    initial = initial_matrix(dict(x=.08, y=-.04, yaw=np.deg2rad(3))) @ base
                else:
                    x, y, yaw = args.seed
                    initial = initial_matrix(dict(x=x, y=y, yaw=np.deg2rad(yaw)))
                map_from_odom = initial @ np.linalg.inv(base)
            job = str(frame_index)
            mode = "first-fix" if accepted is None else "tracking"
            began = time.monotonic()
            predicted = map_from_odom @ base
            worker.submit(dict(kind="match", job_id=job, stamp=stamp, points=points, viewpoint=viewpoint,
                               odom_base=base, prior=predicted, tracking=accepted is not None))
            reply = receive()
            elapsed = time.monotonic() - began
            if (reply["kind"], reply["job_id"], reply["stamp"]) != ("result", job, stamp):
                raise ValueError("replay result does not belong to submitted scan")
            result = reply["match"]
            details = timing_record(reply["timing"], elapsed)
            timings[mode].append(details)
            match_count += 1
            age = elapsed + args.source_age_ms / 1000
            reason = result_age_reason(elapsed, args.source_age_ms, age_limit)
            if reason is not None:
                if accepted is None:
                    verifier.reset()
            elif accepted is None:
                check = verifier.check(result, base, stamp)
                reason = check.reason
                if check.accepted:
                    accepted = dict(frame=frame_index, checks=check.checks, spanS=round(check.span, 3),
                                    framesTried=match_count)
                    map_from_odom = planar_matrix(result.best.matrix) @ np.linalg.inv(base)
            else:
                reason = tracker._tracking_reason(result, predicted, verifier)
                if reason == "ok":
                    corrections += 1
                    map_from_odom = planar_matrix(result.best.matrix) @ np.linalg.inv(base)
            results[reason if mode == "first-fix" else "tracking_" + reason] += 1
            print(json.dumps(dict(frame=frame_index, mode=mode, **details, modeledAgeMs=round(age * 1000, 2),
                                  reason=reason, seeds=result.seed_count,
                                  quality=verifier.quality_reason(result) if mode == "first-fix" else None,
                                  inliers=None if not result.best else round(result.best.inlier_fraction, 4),
                                  registrationPoints=None if not result.best else result.best.registration_points,
                                  rmseM=None if not result.best else round(result.best.rmse, 4))), flush=True)
            next_stamp = stamp + max(elapsed, 1 / config["localizer"]["matching_hz"])
            if match_count >= args.checks:
                break
    finally:
        worker.close()
    print(json.dumps(dict(readOnly=True, usesLiveSensors=False, usesSameRecording=same_bag, referenceRevision=REVISION,
                          configSha256=hashlib.sha256(config_bytes).hexdigest(), scanVoxelM=config["localizer"]["scan_voxel_m"],
                          measuredPoseError=False, checksAttempted=match_count, firstFix=accepted,
                          trackingUpdates=corrections, workerExit=worker.process.exitcode,
                          sourceAgeMs=args.source_age_ms, liveAgeLimitS=age_limit,
                          phaseTiming={mode: timing_summary(records) for mode, records in timings.items()},
                          reasons=results)), flush=True)
    return 0 if accepted and corrections and worker.process.exitcode == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
