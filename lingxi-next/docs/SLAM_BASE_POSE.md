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

The live owner must additionally bind timing, TF, profile and pose to the same
session, enforce freshness and management generation, and retire all resources.
These responsibilities are deliberately not replaced by this stateless helper.
The adapter has synthetic contract tests, not a target tf2 execution result.
It is not yet wired into the live localization owner or exposed as an approved
navigation position. The current diagnostic localization state remains unchanged.

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

The final regression scope covers 219 Linux Python cases, including 15 new
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

Next integration is to carry original scan timing alongside live input and
consume this projection within the same localization owner/generation. Field
dimension/direction references and current-map association are still required
before using a base pose for controlled navigation.
