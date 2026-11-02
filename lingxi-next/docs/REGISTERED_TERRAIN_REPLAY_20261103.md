# Actual Recorded Localization And Native Terrain

Date: 2026-11-03. Offline Linux x86_64 only. No robot IO, sensor subscription,
ROS node, deployment, physical command, Web/Android change or new recording.
The retained user recording is replayed at 1x; no synthetic geometry replaces
its corrected-point, acquisition or registration output.

## Composition

`run_registered_terrain_replay.py` composes actual GLIM, the native corrected
point and per-return acquisition extensions, `LocalizedNative`,
`LocalizationSession`, small-gicp `SeededMapRegistration`, `RegisteredRayWindow`,
`RegisteredTerrainSource`, `TerrainLane`, and a real supervised Patchwork++ /
OctoMap child. Native libraries, saved-map binding, recorded seed, configuration
and runtime sources are pinned and rechecked. Outputs must preserve the native
paired counters and original replay-ingress receipts. The parent regrades
actual private grid bytes, not only reported hashes or summary cell counts.

This recording may also have contributed to the saved reference map. Its first
recorded pose is used as the single explicit seed. This is **not independent
localization accuracy**, physical scale, ground/base alignment, live throughput
or target ARM64 evidence. The geometry uses the existing offline declaration,
not a newly measured robot transform. Replay ingress is not historical freshness.

## Failure And Fix

The preserved baseline `registered-replay-20261103-b` completed the entire
sensor/localization stream but correctly withdrew terrain after
`octomap_work_budget_exhausted`: a third submission contained eight scans and
60,938 measured rays. It satisfied the ray-count limit but exceeded four million
native work units. No partial grid was accepted. The earlier `-a` attempt also
exposed a parent report tuple/list comparison bug; `-b` fixes that representation
check without relabeling the workload failure.

`RegisteredRayWindow` now requires the actual projection. Before native
submission, its estimate includes every projected height voxel and **every
measured ray**, maximum-range clipping and one extra unit per ray for rounding.
Rigid-transform-invariant lengths avoid declared-frame reconstruction while
the native lane is busy. Oldest complete scans are evicted until this bound
fits. A single complete scan that cannot fit fails and retires its source;
there is no partial scan, automatic retry, larger default budget or receipt
refresh. Explicit offline selection is still charged for all original rays.

Every native update rebuilds from the retained window, so forgotten evidence
does not survive as old free space. The source also rejects a grid for a
different projection. Point-cloud display and default Z **0.1--1.4 m** remain
unchanged; ground echoes are not removed from terrain input.

## Current Recorded Result

Final private run: `registered-replay-20261103-c`. The committed
[aggregate](evidence/registered-replay-20261103.json) contains source identities,
counts, timing ranges and private artifact hashes, not recorded coordinates,
map identities, raw grids, private log text or credentials. The public aggregate
is not a substitute for independently regrading the private input/output files.

| Measurement | Result |
| --- | --- |
| Complete input | 4,144 messages: 3,947 IMU and 197 clouds |
| Processed scans / estimated poses | 196 / 165 |
| Paired corrected-point / acquisition packets | 164 / 164 |
| Point states | 1 unseeded, 2 acquiring, 161 tracking; no lost state |
| Registered packets / measured returns | 161 / 1,223,474 |
| Terrain submissions / observed fresh results | 19 / 18 |
| Retained submission sizes | First: 1 scan; later: 4 complete scans, up to 30,518 rays |
| Estimated work | 1,067,431--3,654,793; default cap unchanged at 4,000,000 |
| Original oldest-ingress to observation | 0.300301--1.301143 seconds |
| Supported free cells | **0 in every observed result** |
| Stop / cleanup | Source retired, grid absent, native sensor owner closed, terrain owner joined and child cleaned |

The nineteenth terrain job was in flight at EOF and was intentionally revoked
during stop. It is **not counted as a completed result**. `defaultBudgetAccepted`
means no observed native failure during this paced recorded diagnostic; it does
not certify all jobs completed, a sustained-live/P95 benchmark or navigation.
These timings concern terrain, not audible voice response.

Zero supported free cells means this recording plus declared geometry still
does not establish a usable traversable region. It does not prove the actual
floor is blocked. No unknown cell is promoted to free to manufacture a path.

## Checks And Reproduction

The focused Linux suite enables actual pinned native terrain libraries and
includes window/source, acquisition, localization, terrain transport, raw-grid
grading and current-source evidence checks. Work estimates are also compared
against actual OctoMap counters for scaled, transformed and range-clipped rays.
Final result: **246 passed, zero skips/failures, 28.591 seconds**. Windows also
passed all nine current registered ROS evidence tests. No new full-suite,
frontend, Java or Android build result is claimed.
See the updated [ROS verification](REGISTERED_TERRAIN_ROS_VERIFICATION_20261103.md)
for current-source pause/stop/reseed/loss consumer trials.

Use the existing isolated scientific Python environment and the original
hash-verified private artifacts. Every run needs a new private output directory:

```sh
rtk proxy python -B tools/run_registered_terrain_replay.py run \
  --work GLIM_WORK --adapter GLIM_ADAPTER --points POINT_EXTENSION \
  --acquisition ACQUISITION_EXTENSION --bundle PRIVATE_BUNDLE \
  --reference PRIVATE_REFERENCE --saved-map SAVED_MAP \
  --point-reference PRESERVED_POINT_TRIAL --terrain-config DECLARED_TERRAIN_CONFIG \
  --output NEW_PRIVATE_OUTPUT --hypothesis header_is_scan_start --rate 1.0 \
  --acknowledge-recording-reuse
```

Remaining: diagnose the missing supported free region with calibrated geometry,
wire the production live composition, then run target ARM64 and field checks.
The separate actual-ROS trials use synthetic input; they are not a combined
real-recording-through-ROS navigation acceptance. No complete product, APK or
independent-stack deployment package is declared delivered by this milestone.
