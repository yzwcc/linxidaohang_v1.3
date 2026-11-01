# Native Terrain Transport Verification

Date: 2026-11-01. Host: WSL Ubuntu x86_64, Python 3.12.3.
This extends the earlier [rolling producer](ROLLING_TERRAIN_VERIFICATION_20261101.md)
with a persistent supervised process and a ROS-neutral expiring grid feed.
It does not authorize physical navigation or complete the live ROS integration.

## Implemented

- `terrain_process.py` owns one Linux child and map/receipt/geometry epoch.
  Patchwork++ and OctoMap load inside the child, not an API or ROS executor.
  Calls are serialized by a single owner; there is no backlog, automatic restart,
  network listener or new motion permission.
- `terrain_protocol.py` uses bounded length-prefixed JSON over private stdio,
  with a 32 MiB message limit. Reconstruction validates scan/ray counts, finite
  coordinates, per-return acquisition origins/endpoints and source identities.
  It does not use pickle or turn corrected map endpoints into synthetic rays.
- Nonblocking pipe reads and writes share a monotonic deadline. Full input
  pipes, partial replies, oversize messages and native hangs cannot bypass the
  deadline. Failure invalidates the result, terminates the owned process group
  and reaps its leader. The worker sets Linux parent-death SIGKILL before native
  loading, with an expected-parent race check, so an abruptly killed outer
  supervisor does not leave the native computation running.
- `TerrainGrid` binds every immutable full replacement to a process session,
  sequence, map correction revision, source digest and original receipt/expiry.
  Another correction of the same source capture cannot extend its lifetime.
- `ExpiringTerrainFeed` replaces, rather than unions, all cells. Invalid epochs,
  revisions, order, clocks and explicit revocation clear the accepted grid.
  Sampling after idle expiry returns a full unknown replacement, including
  cells that used to be free. Offline OccupancyGrid encoding preserves unknown
  as -1, occupied as 100 and free as 0.
- The recorded evaluator now uses this persistent child and feed, closes the
  source after replay and verifies complete replacement/revocation and cleanup.
  Default display/clearance bounds remain Z 0.1--1.4; raw floor returns are not
  discarded by the display slice.

## Actual Checks

The [source-bound receipt](evidence/terrain-transport-20261101.json) contains the
current native tests, repeated recorded replay and frame rejection. Earlier
receipts remain unchanged historical evidence, not verification of these new
sources.

The native checker passed **39 tests, zero failures/errors/skips**, loading the
actual pinned Patchwork++ and OctoMap libraries. It covers the producer, typed
transport, native replacement, child death, blocked input, startup hangs, invalid
replies, parent death while the child is not reading, epoch retirement, expiry
and source revocation. Five recorded supervisor/report tests also passed.

The broad scientific Python discovery before the final two parent-lifetime
checks ran **1,523 tests: 1,356 passed, 167 skipped**, zero failures/errors,
237.882 seconds. The final 39-test native run includes the subsequently added
parent-lifetime checks. The broad skip count is reported as observed; it is not
evidence that optional native, ROS, ARM64 or deployment checks ran. TypeScript
regression passed **511 tests / 16 skipped**, zero failures. No frontend/API
source was changed in this milestone.

## Acceptance Boundaries

This feed is **ROS-neutral**, not an implemented ROS publisher, timer or Nav2
layer. Its owner must poll process health, revoke on failure and sample the feed
on every publication timer, including idle periods. No independent monitor
performs those steps automatically. Actual Humble idle-expiry propagation and
controller consumption still need their own integration and tests. The terrain
feed never replaces the independent command/stop watchdog.

The repeated static recording remains an uncalibrated declared-frame replay,
not paced live input. Its explicit +0.6 Z translation is retained without tuning.
Replay handoff receipt times are not original sensor-ingress times; native
processing timings exclude archive preparation, child startup, IPC, ROS and
ARM64 work. Outer-worker peak RSS excludes the persistent native child's memory.
No latency or memory acceptance for the complete live pipeline is implied.

The deployed field mapper's schema3 point-cloud snapshots do not provide a
continuous corrected per-return ray API. Source-bound acquisition windows,
metric scale, floor/gravity/base frames, live localization, ARM64 composition
and physical stopping/obstacle tests remain required. Neither this replay nor
the concurrent read-only review of field map 1.3 changes metric admission,
ground support or navigation readiness. No new deployment, APK or complete-stack
release was produced by this milestone.
