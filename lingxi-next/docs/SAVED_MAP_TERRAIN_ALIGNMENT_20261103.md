# Saved-Map Terrain And Original Scan Time

Date: 2026-11-03. Source milestone after the 0.1.22 commissioning package.
No robot connection, sensor subscription, deployment or physical command.
The Web/Android point-cloud view and default display Z 0.1--1.4 m are unchanged.

## Explicit Composition

The acquisition sidecar can opt in to both outputs together:

```json
{
  "localizationRosOutput": {"mode": "private_saved_map_observations"},
  "rosOutput": {"mode": "private_saved_map_terrain"}
}
```

This is a fragment, not a deployable configuration. Existing verified ARM64
acquisition/terrain inputs and explicit live base-pose options are required.
The existing `private_declared_terrain` mode remains a separate diagnostic.
The new mode shares the observation owner's random private namespace and emits
`traversable_map` in `lingxi_saved_map`. It does not start Nav2 or admit motion.

Geometry must use exactly the explicit `basePose.metresPerMapUnit` scale and
the shared `navigationFromRaw` transform (identity when omitted). Scale mismatch
or differing frame geometry is rejected. Both ends of each measured ray use the
same rigid frame. This establishes mathematical agreement with the saved-map
observation output, not measured physical scale, floor/gravity alignment or
obstacle completeness. See [the shared explicit-frame follow-up](EXPLICIT_NAVIGATION_FRAME_20261103.md).

## Native And ROS Ownership

The native acquisition callback retains one pending terrain packet. Only after
the same frame's `LiveBasePose.frame` projection does `capture_terrain` submit
it to `RegisteredTerrainSource`. Missing/mismatched frame, algorithm stamp,
scan header or base projection cannot borrow the latest available projection.

The source preserves the exact integer `effectiveScanNs` for every retained
scan, including scans received while the asynchronous native lane is busy.
Accepted submissions retain their own oldest/latest scan times. The returned
grid must still match its sequence, observation digest, epoch, projection and
oldest ingress receipt before it receives immutable timestamp metadata.
Newer scans or base poses cannot change an in-flight result's original time.
Repeated snapshots reuse the same annotated object, avoiding false replacement
or repeated receipt renewal in the ROS feed.

This mode uses a 0.2 s maximum scan span, 0.2 s native processing deadline and
0.3 s oldest-ingress lifetime. Existing ray-count and work caps are unchanged.
The original integer latest scan time is the known grid's ROS header, matching
the corresponding odometry/TF/cloud time. Both oldest and latest acquisition
times must also be no more than 300 ms old in the ROS clock. Future timestamps,
old contributing scans, expired ingress, absent results or revoked localization
produce full unknown, never freshly timestamped free cells. Unknown withdrawal
messages use publication time because they revoke validity, not renew it.
Steady-clock publication and terminal source retirement remain unchanged.

These bounds are admission deadlines, not a throughput claim. Existing native
recorded diagnostics took roughly 0.3--1.3 s and the larger synthetic fixture
takes roughly 1.9 s. Neither proves a usable fresh grid under the new bounds.
Do not enable physical navigation until target throughput, calibrated geometry
and the complete localization/Nav2/AIMDK stop chain have been measured.

## Checks

- New numerical/asynchronous lane tests: 9 passed, 0.038 s. Synthetic poses,
  paired rays and native-process doubles, not real GLIM or native segmentation.
- Actual isolated Humble transport: 8 passed, 6.028 s. The 3 new cases compose
  the actual source/lane/publishers and check shared frame/scale/original time,
  oldest-scan age, future/expired input and terminal withdrawal. Native terrain
  results are explicitly synthetic. The initial attempt correctly tripped a
  test-fixture clock-regression latch; all final tests use one clock origin.
- Scientific Linux `test_live*.py`: 118 tests, 110 passed / 8 skipped.
- Scientific Linux `test_localization*.py`: 119 tests, 105 passed / 14 skipped.
- Existing registered source/window tests: 27 tests, 25 passed / 2 skipped.
- Shared runtime source archive/import checks: 12 tests, 11 passed / 1 skipped.
- Release scope checks: 7 passed.
- Existing actual-Humble declared-output lifecycle tests: 3 passed, 1.474 s.
- Regenerated source-bound evidence: 9 registered-mode tests and 8 generic-mode
  tests passed. Windows also passed the 9 registered-mode tests and 11 shared
  source archive checks, with 1 archive check skipped.

Collections overlap and must not be added as independent coverage totals.
No new full-suite, voice latency, Android-device or target ARM64 result is claimed.

The changed common sources initially invalidated older source-bound reports.
All four registered-source native/ROS trials were actually rerun sequentially
as `registered-ros-MODE-20261103-e`; all 14 checks and regrading passed, and
their committed reports were replaced only with these actual outputs. The
three generic native/ROS trials were also rerun as
`terrain-ros-MODE-20261103-a`, each passing all 14 checks. These are regressions
of the existing declared-frame modes, not fresh-grid throughput evidence.

The recorded aggregate temporarily became historical after this update: its
8-test collection then had 7 passes and 1 source-identity assertion failure.
The [throughput follow-up](TERRAIN_THROUGHPUT_20261103.md) actually reran the
recorded chain as `registered-replay-20261103-d`, regraded the private raw grids
and regenerated the aggregate. It observed 24 submissions / 23 fresh results,
still zero supported free cells. This resolves the old source-identity gap,
not floor calibration or the new live admission deadlines. Registered ROS
trials were actually rerun as `registered-ros-MODE-20261103-f` and generic
trials as `terrain-ros-MODE-20261103-g`; no old failure was relabeled as a pass.

The subsequent [native accumulation follow-up](TERRAIN_NATIVE_THROUGHPUT_20261103.md)
actually reran recording `-e` (28/27, still no supported free cells), registered
ROS `-i` and generic ROS `-h`. The [explicit-frame follow-up](EXPLICIT_NAVIGATION_FRAME_20261103.md)
then reran recording `-f` and registered ROS `-j` against the shared-frame
implementation. Current committed aggregates bind those real runs; the earlier
outputs described above remain historical and preserved.

## Reproduction

```sh
rtk proxy python -B -m unittest discover -s tools/tests -p test_saved_map_terrain.py -v
rtk proxy sudo bash tools/nav2-offline-service-isolated.sh --verify-localization-output ROOTFS open /new-unique-output
```

Use the existing isolated scientific Python environment and loopback-only
Humble rootfs. The full independent-stack product remains in development.
