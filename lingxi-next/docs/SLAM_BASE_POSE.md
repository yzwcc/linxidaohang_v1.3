# Timestamped LiDAR-To-Base Projection

This component supplies the coordinate conversion needed before independent
localization can feed navigation. It does not approve calibration or enable
motion. No legacy business code, vendor installation or frozen sensor worker
was changed. Product and APK remain 0.1.19.

## Coordinate And Time Contract

The pinned native adapter returns `T_world_lidar`. The point packet's `IMU`
source label describes the native point-frame convention; it must not be used
to relabel that pose as an IMU or base pose.

The X2 chain from `base_link` to `lidar_chest_front` includes three moving
waist edges. A factory matrix or the latest available TF cannot replace its
transform at the scan's effective timestamp:

```text
T_map_base(t) = T_map_lidar(t) * inverse(T_base_lidar(t))
```

`robot-slam/lingxi_slam/base_pose.py` implements this composition using the
existing rigid-pose validator and SciPy rotation support. It preserves full
3D orientation; it does not flatten the result or assume a raw map axis is
the navigation vertical axis.

`ScanTime` retains the original integer header, observed maximum point offset,
first input IMU timestamp and explicit start/end hypothesis. The effective TF
query uses integer nanoseconds. Native algorithm timestamps are checked against
the unchanged stream formula, never converted back into absolute epoch time.
Matching that formula does not select or validate the hardware time hypothesis.

The map's metres-per-unit scale is required explicitly. Only the map pose
translation is scaled; the TF lever arm is already in declared ROS metres.
Neither an explicit value of 1 nor successful composition proves the physical
scale. Missing transforms, a wrong frame/time, invalid rotation or mismatched
native scan time produces no base pose. No identity or latest-value fallback
is used, and callers receive an owned, read-only result.

## Read-Only TF2 Adapter

`Tf2BaseLookup` reads an existing buffer using an exact `ROS_TIME` request and
zero lookup wait. It creates no node, subscription, publisher or control client.
Its owner must provide a bounded buffer, a live executor and an explicit
coverage verifier for every dynamic edge. A matching tf2 response stamp alone
does not prove a sufficiently small interpolation gap. A failed coverage check
prevents the lookup. A missing TF produces no pose.

## Optional Live Integration

The existing live localization owner now supports an explicit diagnostic option:

```json
"basePose": { "metresPerMapUnit": 1.0 }
```

This belongs in the private live runtime configuration, not in a motion request.
Recorded-input configurations reject this option. Leaving it absent preserves the
previous state contract and starts no extra subscriptions. The scale is a declared
assumption, not a measured calibration.

`live_base_pose.py` reuses the existing passive ROS node and adds only `/tf` and
`/tf_static` subscriptions. Its TF2 buffer retains five seconds. `base_pose_input.py`
keeps bounded original cloud header/offset metadata and the first IMU timestamp;
the frozen worker and native time formulas remain unchanged. Only the fixed X2
chain is admitted. Each moving edge needs an exact sample or an interpolation
bracket no wider than 50 ms. Parent changes, conflicting duplicates, malformed
transforms and changed static edges invalidate coverage instead of substituting
an identity or latest transform.

The localization session projects only its current tracking frame and generation.
TF work runs outside the management lock. Pause, stop, expiry, a new frame and TF
revocation prevent a late result from republishing an old pose. Shutdown clears
timing/coverage caches and revokes the result. The paired state contract optionally
includes `basePose`, a full 3D matrix, integer nanosecond timestamps encoded as
strings and the declared scale. Both `physicalScaleValidated` and `navigationReady`
remain fixed to false. This is not an approved navigation position.

## ARM64 TF2 Verification

On 2026-10-30 the current source passed a bounded check on the robot's ARM64
Python 3.10.12 and installed ROS Humble TF2. The public, source-bound
[report](slam-base-tf2-arm64-evidence.json) contains synthetic transforms only;
the check initialized no ROS context or node, read no sensors and sent no motion.
It verifies exact integer-time lookup, midpoint interpolation, no extrapolation,
both scan-time hypotheses, full 3D composition, translation-only scale conversion,
read-only output, oversized-gap refusal and closed-coverage refusal. Maximum
matrix-element error was `4.440892098500626e-16`.

`tools/run_base_pose_tf2_target.py` uses an explicit ordinary-user target and a
fresh private directory, verifies uploaded/downloaded SHA-256 hashes, applies a
30-second limit and separates JSON output from warning logs. It does not install
dependencies, modify vendor services or start the live localization owner. The
private collection and full warnings stay outside version control.

The existing target environment reported NumPy 1.26.1 with SciPy 1.8.0, whose
declared NumPy requirement is below 1.25. The computation passed, but that warning
is an unresolved dependency-compatibility limitation. No vendor or existing
environment was changed to hide it. Synthetic TF2 success does not establish
live base-pose acceptance, physical accuracy or navigation readiness.

## Recorded Verification

`tools/project_recorded_base_poses.py` reuses the accepted static and walking
native trajectories from `SLAM_STREAM_INPUT.md`. It hashes the accepted reports
and NPZ files, verifies the original rosbag checksums/topic counts before and
after processing, and joins each native header to that same bag's scan timing
and TF. It does not rerun the engine or mix the newer callback journal with
unrelated older TF. Native input and helper source identities are retained in
the generated [aggregate](slam-base-pose-evidence.json).

The existing recorded TF helper performs linear translation interpolation and
SciPy SLERP, with a predeclared 50 ms maximum dynamic interval and no extrapolation.
Unavailable initialized poses are counted, not filled. Original bag leading
scans that produced no native pose remain outside this projection domain.
The four conditions retain both timing hypotheses without choosing the better
result. Private base matrices, row joins and integer timestamps stay in `.local/`.

These archived native poses are in `lingxi_glim_odom`, not a relocalized saved
map. The private output is explicitly `odom_from_base_metres_at_declared_scale`
and the aggregate declares that coordinate frame. This tool does not supply
`map_from_odom` or claim a saved-map base pose.

| Recorded Condition | Native Poses | Projected Base Poses | Missing TF |
| --- | --- | --- | --- |
| Static, scan-start hypothesis | 313 | 313 | 0 |
| Static, scan-end hypothesis | 311 | 311 | 0 |
| Walking, scan-start hypothesis | 1,190 | 1,190 | 0 |
| Walking, scan-end hypothesis | 1,189 | 1,189 | 0 |

All 3,003 initialized poses have bracketed TF in this archive. The largest
used dynamic interval is 23.9995 ms. Reconstructing the original LiDAR matrix
from the projected base pose and the same-time transform has maximum element
error about 1.55e-15. This is an algebraic consistency check, not physical error.

The earlier recorded-projection regression scope covered 219 Linux Python cases, including 15 new
projection/recorded-join cases and two portable evidence checks. Windows passes
10 collector/evidence cases; the 15 scientific cases are explicitly skipped
without those dependencies. No frontend
or Android code changed, and no APK was rebuilt.

To reproduce with the existing isolated scientific Python environment:

```sh
python -B tools/project_recorded_base_poses.py \
  --data-root "$PRIVATE_RECORDINGS" \
  --stream-results "$ACCEPTED_STREAM_RESULTS" \
  --output .local/base-projection-new-run \
  --metres-per-map-unit 1
```

This explicit scale is a diagnostic assumption, not a measurement. The result
does not establish physical accuracy, gravity alignment, factory calibration,
current-site association, live tracking or navigation readiness. It does not
extend the newer ARM64 map receipt into a physical-frame acceptance result.

Original scan timing and same-generation projection are now integrated as the
optional diagnostic path described above. The current Linux Python regression
passes 1,018 cases with 14 explicit skips; the four additional target-report
contract tests also pass. This is software regression coverage, not live sensor
acceptance. Field dimension/direction references, hardware scan-time semantics,
gravity alignment and current-map association are still required before using a
base pose for controlled navigation. Product and APK remain 0.1.19.
