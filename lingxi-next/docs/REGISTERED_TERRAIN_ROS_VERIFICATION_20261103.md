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
| Pause | paused | 0.045245 s | [pause](evidence/registered-terrain-ros-pause-20261103.json) |
| Stop | stopped | 0.019964 s | [stop](evidence/registered-terrain-ros-stop-20261103.json) |
| Reseed | tracking in a newer generation | 0.011550 s | [reseed](evidence/registered-terrain-ros-reseed-20261103.json) |
| Corrected-point input expires | lost | 0.041834 s after the original localization deadline | [loss](evidence/registered-terrain-ros-loss-20261103.json) |

Before the trigger, the actual planner returns a path and the actual controller
produces nonzero synthetic output. Afterwards the source publishes full
unknown, a late DDS subscriber receives unknown, and both costmaps block
every formerly supported cell. Planning fails and FollowPath aborts. After
the existing 300 ms queued-command drain, no nonzero output was observed.
Reseeding restores the session to tracking but cannot revive the old terrain
source; a new source composition is still required for the new generation.

Final oldest-ingress-to-result samples were 1.581--1.637 seconds. During native
work, 20--21 ROS map publications continued per trial. These are host samples, not an
ARM64 throughput guarantee, P95 estimate, voice latency result, physical stop
acknowledgement or moving-obstacle acceptance. The fixture uses a larger trial
budget of 131,072 rays and 15-second terrain TTL; the separate
[actual recorded replay](REGISTERED_TERRAIN_REPLAY_20261103.md) now exercises
default budgets. Target-runtime throughput still needs validation.

One concurrent pause attempt failed a Nav2 lifecycle service-response deadline
before terrain submission. Its private failure report/log remain preserved in
`registered-ros-pause-20261103-b`; the first standalone pause pass was `-c`.
After adding projection-work-bounded windows, all four modes were rerun
sequentially as `registered-ros-MODE-20261103-d`. After the saved-map terrain
alignment update, all four were actually rerun sequentially as
`registered-ros-MODE-20261103-e`. Following the exact-double packing and native
buffer optimization, all four modes were actually rerun sequentially as
`registered-ros-MODE-20261103-f`; the committed reports and table now bind
these current-source runs. All prior outputs remain under the existing
private Humble rootfs. Passing the standalone reruns does not
establish the cause or prove reliable startup under concurrency. No timeout was
relaxed and no failed attempt was relabeled as passing evidence.

## Regression And Reproduction

The shared raw-costmap regrader is reused without changing the older report's
purpose or source requirements. Nine new evidence tests check all four actual
reports and reject retained free cells, changed original receipts, incomplete
windows, wrong generations/states, missing sources and unsupported physical
acceptance flags. The nine tests pass on both scientific Linux and Windows.
Existing terrain ROS evidence tests remain unchanged and passing.

The final expanded throughput follow-up completed 177 actual-native focused
tests: 176 passed, one NavFn-library skip, zero failures/errors, 25.238 seconds.
This includes all eight current-source recorded replay checks, all nine
registered ROS checks, all eight generic ROS checks and four benchmark checks.
The eleven packing/digest tests, four benchmark evidence tests and nine
registered ROS evidence tests pass on Windows. See the
[throughput report](TERRAIN_THROUGHPUT_20261103.md) for exact-output comparisons
and the host-only timing limitations. These collections overlap and are not
added together as an independent coverage total.

The historical projection-work/replay follow-up passed all 246 focused scientific Linux
tests with actual native terrain libraries enabled, zero skips and zero
failures, in 28.591 seconds. The previous 222-test result in 38.531 seconds
belongs to the earlier ROS milestone. This is not a new full-suite run; the
previous 1,630-test full-suite result also remains historical. All nine
registered ROS evidence tests were rerun on Windows and passed.

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

The new [saved-map terrain alignment](SAVED_MAP_TERRAIN_ALIGNMENT_20261103.md)
has separate actual-Humble transport tests with synthetic terrain results.
This larger declared-frame fixture does not establish the new 300 ms input
deadline. The separate recorded replay has its own source-bound evidence and
does not establish live throughput or supported floor.

The registered source is now verified with actual isolated ROS consumers.
The combined actual GLIM/registrar/terrain replay now completes a paced
default-budget diagnostic, but observes zero supported free cells; see the
linked replay report. Production live factory/configuration wiring and
current ARM64 execution remain open. Physical scale, ground/gravity/extrinsic/base alignment,
blind spots, moving people, stopping distance and field commissioning are not
inferred from this fixture. Startup reliability under concurrency also remains
unaccepted. The full product, APK and complete independent-stack deployment
package have not been declared finished.
