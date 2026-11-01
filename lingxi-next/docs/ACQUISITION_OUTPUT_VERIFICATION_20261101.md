# Immediate Per-Return Acquisition Export

Date: 2026-11-01. Offline WSL x86_64 verification only. No robot connection,
ROS node, live sensor subscription, deployment or motion command occurred.

## Implementation

The independent `native-acquisition-output` extension copies each initialized
GLIM odometry frame's complete preprocessed returns and original intra-scan
IMU trajectory inside `on_update_new_frame`. It does not wait for a finalized
submap and does not change the existing corrected-point extension ABI.
`NativeAcquisitionOutput` composes with the existing `PointOutputNative` owner.

The extension has one owner, one pending frame and one open per process. A
second unconsumed frame, discontinuous frame ID, missing/incomplete trajectory,
invalid pose, non-unit quaternion, unbracketed return or oversized input causes
failure, not a partial observation or an invented scan origin. The initial
GLIM frame ID 0 has no intra-scan trajectory and is explicitly excluded.
Bounds are 65,536 returns and 4,096 trajectory samples per frame. There is no
extra downsampling or Z filtering in this export.

The owned single-frame `LXOBS002` bytes pass the existing strict observation
parser. Each return gets an interpolated IMU pose and the LiDAR extrinsic,
then the current odometry correction. Both endpoints and origins are in
`lingxi_glim_odom`; the returned pose is the LiDAR pose, not the robot base.
Ground and high returns remain available. This is neither a saved-map frame
nor a physical calibration or an acquisition-ingress freshness receipt.

## Verification

- All **29 C++ CTest contracts passed**, using the actual pinned GLIM callback
  bus and explicitly synthetic frames. A real native byte export was decoded
  in Python and checked against independently composed transforms, including
  moving/rotating origins, nonzero LiDAR extrinsics and noncommuting correction.
- All **28 focused Python tests passed, zero skips**: decoder and metadata
  validation, query/copy consistency, single delivery, owner threads, closure,
  consumer failure, bounded accounting, frozen native inputs, commented JSON5
  configuration, duplicate keys and disabled trajectory rejection.
- Full Python regression: **1,593 discovered, 1,526 passed, 67 skipped, zero
  failures**, in 330.214 seconds. Environment/device-gated skips are not
  acceptance evidence. The focused 28-test acquisition suite was also rerun
  independently with zero skips, and the build's source hashes still match.
- A **new actual native recorded replay passed**. It used the unchanged
  preserved journal with 4,144 callbacks: 197 cloud and 3,947 IMU messages.
  All input, configuration and loaded-library identities were checked before
  and after replay. `save_imu_rate_trajectory` was explicitly true.
- Stream accounting: 196 scans processed, one excluded before IMU availability,
  31 initialization scans and 165 estimated poses. The frame-0 exclusion left
  **164 contiguous exported packets and 1,246,242 returns**, with 7,539--7,779
  returns per packet. Native counters were 165 callbacks, one initialization
  skip, 164 copies, 164 takes and zero discarded on close. One native instance
  was created and closed on its calling owner thread.
- Only the first and latest packets were retained as private samples. Every
  packet's geometry and source identity entered the aggregate digest:
  `c2dfb7614595c97c44f7db19bf45603b9ac78f16e4c34b8940098e00165fb571`.
  No raw points, trajectories, poses, journals or sample files are committed.

The replay follows original journal arrival order synchronously. Its
13.6115-second host wall time is **not** a paced replay, SensorWorker or live
latency measurement. The static recording is not a new walking test. Native
logs still warn about IMU prediction quality and possible extrinsic/bias
inaccuracy; this verification does not resolve or hide those warnings.

## Reproduction And Evidence

Use `tools/run_glim_acquisition_trial.py build --work <pinned-core> --output
<new-private-build>` followed by its `recorded` subcommand with `--work`,
`--adapter`, `--acquisition`, `--bundle`, `--reference`, `--output`,
`--hypothesis header_is_scan_start --rate 1.0`. The replay requires the explicit
`run_glim_supervised_replay.child_environment` environment and an ordinary
user's isolated Linux x86_64 scientific Python. `--rate` retains the frozen
input verifier's contract; this synchronous replay does not pace at that rate.
Existing private output directories are never overwritten.

Private build: `acquisition-output-20261101-c`.
Private replay: `acquisition-recorded-20261101-a`.
Both live under the user's ignored `.cache/lingxi-next` directory in WSL.

| Evidence | SHA-256 |
| --- | --- |
| Build report | `1d67810597c9406bc9169253356e4837df37528c3b3925acae0a92f7e9472a7e` |
| CTest XML | `3e5b8dfddcc14848e4cba498fd4ec18c42f66c95e249eefb0dbdc4e81a6d701f` |
| Recorded replay report | `3eee6f74e01f626fae66c56044769e252931d4c7423e38bdc39f42bcca40219b` |
| Actual acquisition library | `90041f66a0ba1a16c29c402412436dadb730deba284c64e24a95cc77d85c70d8` |

The reports also bind every acquisition source file, parser, decorator, replay
tool, pinned core and actual loaded dependency. Later source changes need new
verification; these reports must not be rewritten as new runtime evidence.

## Remaining Integration

This closes the per-frame acquisition export check, not the full live terrain
or navigation chain. Still required: pair each packet with the original scan
receipt and localization generation, register both ray ends into the saved-map
frame, retain bounded coherent windows and feed the existing `TerrainLane`
without re-stamping historical observations as fresh. A declared scale is not
metric navigation admission. ARM64 execution, physical scale/extrinsic checks,
real obstacle stopping and the full product remain unaccepted. Default display
and clearance Z remain 0.1--1.4 m. No frontend or APK changes were made.
