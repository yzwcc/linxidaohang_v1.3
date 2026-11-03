# Native OctoMap Scan Accumulation Throughput

Date: 2026-11-03. Offline Linux x86_64 only. No robot connection, live sensors,
deployment or physical motion. Web/Android point-cloud display and default Z
**0.1--1.4 m** remain unchanged. APK/package remain 0.1.22.

## Implementation

The adapter reuses one `octomap::KeyRay` and calls upstream `computeRayKeys`
and `coordToKeyChecked` without constructing a one-point cloud and two temporary
sets for every measured return. Scan-wide free/occupied sets still apply only
one probabilistic update per cell per scan, with hits taking precedence over
free traversal. Individual ray origins and upstream float-based range clipping
are preserved. Work/cell/node limits, geometry, projection and atomic failure
publication are unchanged. Unknown cells are not promoted to free.

The pinned upstream is [OctoMap 1.9.7](https://github.com/OctoMap/octomap/tree/v1.9.7).
Calling one-point `computeUpdate` repeatedly with scan-wide sets is not used:
that function also revisits the entire accumulated free set to erase occupied
keys. Its behavior must not be mistaken for append-only accumulation.

## Actual Native Measurements

Both runs use the same current Python sources, unchanged benchmark/fixture,
`f64le` transport, ground library, OctoMap headers/dependencies, GCC 13 compiler,
four synthetic geometries, three scan counts and five trials per case. Only
the native adapter build differs. Full reports and build identities are retained
in [before](evidence/terrain-native-before-20261103.json) and
[after](evidence/terrain-native-after-20261103.json).

All twelve observation, clearance, support, fused traversability, raw-costmap
identities and native statistics match exactly; wire sizes are unchanged.
Native OctoMap median time decreases **59.3%--65.4%**, and measured total pipeline
median time decreases **15.8%--27.5%** on this host:

| Fixture | Scans / Rays | Total Before ms | Total After ms |
| --- | --- | --- | --- |
| Open | 2 / 8,208 | 60.81 | 51.21 |
| Open | 4 / 24,416 | 219.84 | 171.22 |
| Open | 12 / 84,816 | 821.73 | 613.39 |
| Blocked | 2 / 8,208 | 60.86 | 49.92 |
| Blocked | 4 / 24,416 | 190.62 | 154.08 |
| Blocked | 12 / 84,816 | 680.66 | 550.56 |
| Detour | 2 / 8,208 | 67.68 | 56.10 |
| Detour | 4 / 24,416 | 222.75 | 166.62 |
| Detour | 12 / 84,816 | 806.93 | 605.82 |
| Unsupported shortcut | 2 / 7,824 | 63.72 | 50.26 |
| Unsupported shortcut | 4 / 23,648 | 228.76 | 165.94 |
| Unsupported shortcut | 12 / 82,404 | 806.38 | 586.30 |

These totals include encoding/framing/parsing/reconstruction and native terrain
production, not process startup, IPC scheduling, localization, ROS transport or
physical stopping. The earlier [Python/transport benchmark](TERRAIN_THROUGHPUT_20261103.md)
is preserved separately; its measurements are not replaced or added to these
percentages. Twelve-scan cases use diagnostic budgets. Even the new four-scan
host totals do not establish target ARM64 capacity or the complete saved-map
0.2-second processing / 0.3-second ingress deadlines.

## Current-Source Reruns

The actual baseline/candidate differential collection completed 23 tests:
22 passed, one unrelated actual-NavFn-library skip, zero failures/errors.
It includes 48 deterministic randomized comparisons with moving origins,
height/range changes, scan order and revisions, six unchanged native failure
cases with untouched output/statistics, and 8,192 duplicate returns without
overweighting scan evidence.

The expanded current-source actual-native regression completed **185 tests**:
184 passed, one actual-NavFn-library skip, zero failures/errors, in **30.237 s**.
It covers terrain production/transport, registered and saved-map sources,
recorded regrading, both ROS evidence families and both throughput evidence
families. The separate scientific Linux live-acquisition collection passed
**all 36 tests**, zero skips/failures/errors, in **38.258 s**. ARM64 build-tool
contracts passed all eight tests in 0.074 s; these do not execute target code.
The collections overlap and are not summed into an independent coverage total.

All three generic and all four registered-source actual Humble ROS trials were
rerun sequentially with a fresh rootfs-compatible GCC 11 native build. All 14
common checks and raw-costmap regrades passed in each. See the updated
[generic](TERRAIN_ROS_VERIFICATION_20261101.md) and
[registered](REGISTERED_TERRAIN_ROS_VERIFICATION_20261103.md) records. Host GCC 13
binaries were not substituted into the Humble rootfs.

The actual [recorded chain](REGISTERED_TERRAIN_REPLAY_20261103.md) was replayed
again as private run `registered-replay-20261103-e`, with only the native build
selection changed in a new private terrain config. The previous config/output
remain untouched. It processed all 4,144 messages, submitted 28 jobs and observed
27 fresh results, with the final pending job revoked at EOF. Raw grids were
regraded; **zero supported free cells** remain. The aggregate now also preserves
the actual native build report/hash. This is not a floor calibration or physical
navigation pass. Submission counts are scheduling-dependent, not a speed metric.

## Reproduction And Boundaries

Build baseline and candidate in separate directories with the same pinned
headers/libraries; never overwrite the baseline binary. Run the unchanged
`tools/benchmark_terrain_pipeline.py` with `--encoding f64le` for both, selecting
each actual native library/build report. Set `LINGXI_TEST_OCTOMAP_LIBRARY`,
`LINGXI_TEST_OCTOMAP_SHA256`, `LINGXI_TEST_OCTOMAP_DEPENDENCIES` and corresponding
`LINGXI_TEST_OCTOMAP_BASELINE_LIBRARY` / `LINGXI_TEST_OCTOMAP_BASELINE_SHA256` for
the differential tests. Source-bound evidence tests check raw identities,
build provenance and all five samples; there is no machine-dependent speed gate.

Ground/gravity/extrinsic/base geometry, supported floor, ARM64 throughput, full
physical localization/Nav2/AIMDK stopping and field acceptance remain open.
No ground offset or threshold relaxation is adopted. This milestone is not
complete product delivery or audible voice-latency evidence.
