# Registered Terrain Source To Actual ROS Consumers

Date: 2026-11-03. Isolated Ubuntu 22.04 ROS Humble x86_64 rootfs, Python 3.10,
NumPy and SciPy. No robot connection, sensor subscription, deployment or
physical command bridge. The registrar, acquisition rays and stationary robot
pose are explicitly synthetic; terrain libraries and ROS/Nav2 nodes are real.

## Integration

The trial composes the production `LocalizationSession` registration transform,
`RegisteredRayWindow`, `RegisteredTerrainSource`, supervised `TerrainProcess`,
`TerrainRosPublisher` and actual Humble global/local costmaps, NavFn, RPP and
velocity smoother. No implementation of these components is replaced by a
mock. Only the registrar's estimate and input ray/pose geometry are synthetic.

Each trial retains 12 original acquisition receipts and all 84,816 paired
returns. The source uses the oldest receipt, not completion time. The fixed
open-ground fixture produces 1,354 supported free and 2,486 unknown cells,
with default clearance Z **0.1--1.4 m**. Raw ground echoes reach Patchwork++.
These occupancy grids are internal Nav2 data; Web/Android point-cloud display
is unchanged and is not replaced by a raster mapping view.

The workload exposed repeated declared-geometry construction while the native
lane was starting or busy. `RegisteredRayWindow.offer(materialize=False)` now
only validates and retains the bounded raw measured pairs. The source builds
the complete declared submission only when the lane is ready. It preserves
frame ordering, original receipts, source selection, limits and terminal
retirement; it does not accumulate native jobs. A dedicated regression fails
if any declared-frame transform is attempted while a native job is blocked.

## Final Actual Trials

All four final current-source trials passed all 14 common native/ROS checks
and the registered-source regrade. ROS simulated time is frozen while testing
source withdrawal; the steady-clock publisher continues running. No new raw
ray packet is submitted after the accepted window.

| Trigger | Final Localization State | Source Withdrawal After Trigger/Expiry | Evidence |
| --- | --- | --- | --- |
| Pause | paused | 0.043858 s | [pause](evidence/registered-terrain-ros-pause-20261103.json) |
| Stop | stopped | 0.034974 s | [stop](evidence/registered-terrain-ros-stop-20261103.json) |
| Reseed | tracking in a newer generation | 0.010849 s | [reseed](evidence/registered-terrain-ros-reseed-20261103.json) |
| Corrected-point input expires | lost | 0.047886 s after the original localization deadline | [loss](evidence/registered-terrain-ros-loss-20261103.json) |

Before the trigger, the actual planner returns a path and the actual controller
produces nonzero synthetic output. Afterwards the source publishes full
unknown, a late DDS subscriber receives unknown, and both costmaps block
every formerly supported cell. Planning fails and FollowPath aborts. After
the existing 300 ms queued-command drain, no nonzero output was observed.
Reseeding restores the session to tracking but cannot revive the old terrain
source; a new source composition is still required for the new generation.

Final oldest-ingress-to-result samples were 1.824--1.996 seconds. During native
work, 24--26 ROS map publications continued. These are host samples, not an
ARM64 throughput guarantee, P95 estimate, voice latency result, physical stop
acknowledgement or moving-obstacle acceptance. The fixture uses a larger trial
budget of 131,072 rays and 15-second terrain TTL; the default production budget
and timing still need representative recorded and target-runtime validation.

One concurrent pause attempt failed a Nav2 lifecycle service-response deadline
before terrain submission. Its private failure report/log remain preserved in
`registered-ros-pause-20261103-b`; the final standalone pause run is `-c`.
Stop/reseed/loss final outputs are `registered-ros-MODE-20261103-b`. All are
under the existing private Humble rootfs. Passing the standalone rerun does not
establish the cause or prove reliable startup under concurrency. No timeout was
relaxed and no failed attempt was relabeled as passing evidence.

## Regression And Reproduction

The shared raw-costmap regrader is reused without changing the older report's
purpose or source requirements. Nine new evidence tests check all four actual
reports and reject retained free cells, changed original receipts, incomplete
windows, wrong generations/states, missing sources and unsupported physical
acceptance flags. The nine tests pass on both scientific Linux and Windows.
Existing terrain ROS evidence tests remain unchanged and passing.

The final focused scientific Linux regression passed all 222 tests with the
actual native terrain libraries enabled, zero skips and zero failures, in
38.531 seconds. This is the current change's focused regression, not a new
full-suite run; the previous 1,630-test full-suite result belongs to the prior
registered-source milestone.

Use a separate Humble rootfs with SciPy and the existing hash-verified,
rootfs-compatible Patchwork++/OctoMap cache. Host GCC 13 libraries are not
substituted for the rootfs build. The wrapper checks native build provenance,
binds the project and native cache read-only, and creates mount/network/IPC
namespaces with loopback only. Each attempt needs a new output directory:

```sh
rtk proxy sudo bash lingxi-next/tools/registered-terrain-ros-isolated.sh ROOTFS NATIVE_CACHE /new-trial pause
rtk proxy python lingxi-next/tools/verify_registered_terrain_ros.py lingxi-next/docs/evidence/registered-terrain-ros-pause-20261103.json
rtk proxy python -m unittest discover -s lingxi-next/tools/tests -p 'test_registered_terrain_ros.py' -v
```

Repeat the wrapper with distinct output directories for stop, reseed and loss.
Source-bound reports include actual native grid bytes, before/after costmaps,
late-reader samples, original receipts, generations and native provenance.
Only synthetic reports are committed, not native logs or robot business data.

## Remaining Work

The registered source is now verified with actual isolated ROS consumers.
Production live factory/configuration wiring, combined actual GLIM/registrar/
terrain recorded replay, default-budget overload/timing and current ARM64
execution remain open. Physical scale, ground/gravity/extrinsic/base alignment,
blind spots, moving people, stopping distance and field commissioning are not
inferred from this fixture. Startup reliability under concurrency also remains
unaccepted. The full product, APK and complete independent-stack deployment
package have not been declared finished.
