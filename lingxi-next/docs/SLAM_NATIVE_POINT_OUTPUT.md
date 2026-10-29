# Isolated Native Corrected Points

Status on 2026-10-29: a new, separately built callback extension and Python
decorator have completed both an x86 logical-clock check and an x86 one-times
health-supervised recorded-input check. This is not a deployed mapping backend,
a global map, navigation acceptance, live throughput acceptance or an ARM64
point-extension acceptance.

## Data Contract

- The frozen `tools/glim-replay/offline.cpp`, its compiled library, native
  core, sensor configuration, `SensorStream` and `SensorWorker` are unchanged.
- `robot-slam/native-point-output` links the same pinned CPU GLIM core. Its
  callback synchronously copies points at `on_update_new_frame`.
- GLIM commit `1a197c999e4865f5cab7c9cd25474128413ebbca` initializes frame 0
  from transformed raw points before its first smoother update. That frame is
  explicitly excluded from the corrected/deskewed output.
- Subsequent pinned CPU frames contain deskewed points in IMU coordinates.
  Each copied point is transformed using `T_world_imu`. The separate output
  pose is `T_world_lidar`, not a robot-base pose or fixed waist transform.
- Output coordinates are labeled `lingxi_glim_odom`. They are odometry
  estimates, not a saved or loop-closure-corrected global map.
- At most 8,000 points are deterministically selected by source index per
  callback. One native packet can be pending. Overwriting it latches failure.
  The diagnostic consumer retains only first/latest samples, not a cloud
  backlog. This is not a live data channel or accumulated UI map.
- Copying occurs before the callback returns; no GLIM frame or shallow clone
  crosses a thread boundary. Native callback/API state is mutex-protected;
  error snapshots are thread-local. All legitimate engine calls remain on
  one owner. Open/close must not overlap upstream callback-registry dispatch;
  the pinned upstream registry itself is not a concurrent registry.
- The extension is built in Release mode. A linker export map restricts its
  public ABI to the five declared C functions, and a separate CTest checks
  that no internal Eigen/C++ definitions are dynamically exported.
- The Python decorator checks ownership before factory/IMU/frame/close calls.
  Initialization may have no pose. Its first pose must have no point packet;
  every later pose must have one matching timestamp, contiguous native ID,
  finite points and matching rigid LiDAR pose. Failures cannot resume input.

## Recorded Correctness Check

`docs/slam-native-point-evidence.json` contains the supervised paced result.
`docs/slam-native-point-logical-evidence.json` contains the separate logical
result. Both were independently exported from private reports and checked
against private first/latest numerical samples:

| Observation | Result |
| --- | ---: |
| Original journal callbacks | 4,144 |
| Received clouds / IMU | 197 / 3,947 |
| Native frames / inserted IMU | 196 / 3,930 |
| Frames waiting for initialization | 31 |
| Estimated poses | 165 |
| Excluded initial-pose callback | 1 |
| Corrected point packets | 164 |
| Copied world-coordinate points across packets | 1,246,242 |
| Points per packet, min / max | 7,539 / 7,779 |
| Discarded pending point packets at normal close | 0 |
| Native create / close | 1 / 1 |

One cloud precedes usable IMU coverage. Trailing IMU observations remain
accounted for by the original stream; received IMU count is not the inserted
IMU count.

The logical check calls the same frozen stream using original journal arrival
timestamps, synchronously on its calling main thread. It does not execute
`SensorWorker` or `ProcessSupervisor`. Its approximately 6.92 seconds of wall
time is not a live or paced latency benchmark.

The one-times paced check executes the original `SensorWorker` and
`ProcessSupervisor`, with one non-main native owner. All 4,144 callbacks finish
with zero rejected/discarded callbacks. Native create/close each occur once,
the child exits with code 0, and the owned group closes without forced
termination. The maximum observed callback queue age is about 72.57 ms;
native-frame wall P95 is about 37.57 ms across 196 frames. These observations
are not LiDAR-to-screen latency or a live/ARM64/product performance guarantee.
Health IPC contains counters only, never points.

The extension and original core/adapter were checked in `/proc/self/maps`
against explicit paths and hashes. All returned packets were checked by the
decorator. The exporter rechecks scalar accounting and the private first/latest
samples, not every packet's bytes a second time. Only aggregate evidence and
hashes are published; XYZ, poses, CDR and private logs remain ignored/private.

## Initial Failures And Build Revision

The initial one-times and explicit half-times supervised trials both failed
the unchanged 0.5-second callback queue-age limit:

- One-times: 137 completed callbacks, 6 native frames, no pose.
- Half-times: 684 completed callbacks, 32 native frames, one initial pose.

Neither run reached a corrected-point packet. Both owned process groups were
cleaned, with forced termination recorded. These failures are preserved, not
excluded by calling the logical check a real-time success.

The initial extension exported many internal C++/Eigen definitions and had
no explicit optimized build type. The final build restricts exports to five
C functions and uses Release mode. With that build and unchanged frozen
core/adapter/configuration/worker/watchdogs, the new one-times trial completes.
The effects of ABI isolation, optimization and host variability have not been
separated in a controlled experiment; do not attribute the earlier failures
to one proven cause. The initial failed and logical trials remain private.
Default IMU-noise and two IMU-prediction warnings remain unresolved.

## Reproduction

Use the existing ordinary-user Linux virtual environment with NumPy 2.5.4
and rosbags 0.11.9. These tools never connect to the robot. Build into a new
ignored `.local` or user-cache directory; existing outputs are preserved.

```bash
python tools/run_glim_point_output_trial.py build \
  --work "$CPU_CORE" --output "$NEW_POINT_BUILD"
```

For the logical diagnostic, use the exact clean environment returned by
`run_glim_supervised_replay.child_environment(CPU_CORE)`, including the
library path, thread limits and Python flags. Enforce an external process
timeout; the completed run used GNU `timeout --signal=TERM --kill-after=5s 150s`.

```bash
python tools/run_glim_point_output_trial.py logical \
  --work "$CPU_CORE" --adapter "$FROZEN_ADAPTER" --points "$NEW_POINT_BUILD" \
  --bundle "$FROZEN_BUNDLE" --reference "$PRESERVED_REFERENCE" \
  --output "$NEW_PRIVATE_LOGICAL_OUTPUT" --hypothesis header_is_scan_start --rate 1.0
python tools/export_glim_point_evidence.py \
  --trial "$NEW_PRIVATE_LOGICAL_OUTPUT" --build "$NEW_POINT_BUILD" \
  --output "$NEW_AGGREGATE_JSON"
```

`--rate` is required by shared input verification but does not pace `logical`.
Only the separate `run` command performs paced supervised execution. Never
present `logical` as successful execution of that command.

```bash
python tools/run_glim_point_output_trial.py run \
  --work "$CPU_CORE" --adapter "$FROZEN_ADAPTER" --points "$NEW_POINT_BUILD" \
  --bundle "$FROZEN_BUNDLE" --reference "$PRESERVED_REFERENCE" \
  --output "$NEW_PRIVATE_PACED_OUTPUT" --hypothesis header_is_scan_start --rate 1.0
python tools/run_glim_point_output_trial.py export \
  --trial "$NEW_PRIVATE_PACED_OUTPUT" --output "$NEW_PACED_AGGREGATE_JSON"
```

The new native extension has 12 CTest contracts: 11 separate native executions
and one exported-ABI inspection. Python
tests cover owner checks, copying, invalid metadata/geometry, missing packets,
initialization exclusion, failed lifecycle cleanup, bounded retention and
allowlisted evidence. They do not assert physical accuracy or robot safety.

Final tools regression: Linux 610 passed; Windows 272 passed and 338 skipped
for platform/dependency conditions. All 12 native CTests pass. Independent
repeat exports of both final evidence files compare byte-for-byte equal.
The supervised evidence SHA256 is
`4be53848d08a5fc619f96d85fc00e79027631535867776b07226f1466a283bde`.

Subsequent ARM64 cross-building and local point-transport component checks
are described in `SLAM_POINT_DATA_CHANNEL.md`. ARM64 execution of this
extension, sustained live throughput, deployed API/UI wiring, map transactions,
calibration, loop closure, relocalization and navigation remain pending.
Installer, APK, vendor services and existing maps are unchanged.

The separate x86 supervised recorded-data point-channel composition has since
completed with matching native/Node content digests and natural process exit.
Its isolated API rejects recorded input as live and grants no readiness.
See `SLAM_RECORDED_POINT_CHANNEL.md`; this is not live point rendering or
target ARM64 point-channel acceptance.
