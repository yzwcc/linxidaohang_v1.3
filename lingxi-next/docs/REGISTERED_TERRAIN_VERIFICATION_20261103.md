# Same-Frame Registered Acquisition And Terrain

Date: 2026-11-03. Offline Linux x86_64 verification only. No robot connection,
sensor subscription, deployment, physical motion, frontend or APK change.

The original test totals below describe the first registered-source milestone.
Current projection-work bounds and actual recorded replay results are documented
in [the follow-up](REGISTERED_TERRAIN_REPLAY_20261103.md).

## Implemented Flow

`LocalizedNative` can now pair `NativeAcquisitionOutput` with its current
corrected-point callback. Both callbacks must belong to the same scan; a
missing counterpart fails instead of substituting a previous frame. The
original `Scan.received_at` and localization generation survive the native
call. Closing or failed construction cleans up the acquisition extension on
its original owner thread.

`LocalizationSession.register_acquisition` checks tracking, original receipt,
current frame and generation both before and after the geometry copy. It
transforms both measured ray origins and endpoints with:

```text
map_from_odom = map_from_lidar @ inverse(odom_from_lidar)
```

The two LiDAR poses must match that same frame. The output stays explicitly in
saved-map **native raw units**, with `navigation_ready=False`. Registering a
ray is not a metric calibration, ground alignment or physical-navigation permit.

`RegisteredRayWindow` retains one immutable map binding and localization
generation. Default limits are 32 scans, 65,536 original returns, four seconds
of scan time and three seconds of original receipt age. Entire old scans are
dropped to respect those limits; an oversized single scan is rejected. The
follow-up also requires an explicit projection and caps conservative estimated
OctoMap work at the unchanged default of four million units, evicting only
complete old scans. The
existing explicit `OfflineGeometry` and bounded `RaySelection` are used for
declared-frame evaluation only. Selection retains paired measured origins and
endpoints, records selected indices and source identities in its digest, and
does not claim complete obstacle visibility.

No Z filter is applied to the input window: raw ground echoes remain available
to Patchwork++. Default display and clearance Z remain **0.1--1.4 m**. Saved
maps and raw observation archives are unchanged. A window's latest frame ID
serves as its replacement revision, not as an optimized-map artifact revision.

`RegisteredTerrainSource` composes this window with the existing supervised
`TerrainLane`. Native terrain processing stays on its own owner; busy work is
not queued or retried. New scans only advance the bounded window until a later
submission is possible. Grid freshness starts at the **oldest retained raw
receipt**, never transform completion or native return time.

Every snapshot rechecks localization. Pause, stop, reseed, loss or worker
closure retires the source, including when no more scans arrive. A blocked
native job cannot republish after retirement. The result must match the
submitted observations digest, window revision, lane session, map epoch,
original receipt, projection and bounded expiry. A new generation requires a new explicit
composition; there is no automatic replay of an old job.

## Verified Scope

- All **37 new tests passed, zero skips** with actual pinned terrain libraries.
  They cover noncommuting transforms on both ray ends, source frame/pose/receipt
  validation, generation changes during native work or geometry copying,
  bounded windows, backpressure, expiry, wrong owners and altered grid identity.
- The combined localization receipt/control/session/tracking, acquisition,
  registered-source and terrain regression passed **212 tests, zero skips**.
- Full scientific Python regression: **1,630 discovered, 1,562 passed, 68
  skipped, zero failures/errors**, in 379.054 seconds. Environment/device-gated
  skips are not acceptance evidence; the actual-native focused suite above was
  run separately with its dependencies enabled.
- One composition exercises the real `LocalizedNative` callback, session,
  window and lane together with explicitly synthetic native/registrar peers.
  Stop immediately withdraws its accepted terrain grid.
- Another composition uses a real `TerrainProcess` child and actual pinned
  Patchwork++/OctoMap libraries, after real session registration and window
  construction. Its rays and registrar are explicitly synthetic. It preserves
  the oldest receipt, produces both supported free and unknown cells using
  default clearance Z, withdraws on pause and confirms lane cleanup.

The native libraries were independently rehashed before recording this result:

| Native Component | SHA-256 |
| --- | --- |
| Patchwork++ adapter | `0f0f3b74dee2acccb520e6f7a95a668c268fe22a53f165821a5b8427dd4955a6` |
| OctoMap adapter | `26dd1f8965a84a12a786263b82717724267d47f436a1adcbd2d3a5d0aea8d2e3` |

## Reproduction

From the repository root, use the isolated scientific Python environment with
NumPy, SciPy and small-gicp. Set the five `LINGXI_TEST_GROUND_LIBRARY`,
`LINGXI_TEST_GROUND_SHA256`, `LINGXI_TEST_OCTOMAP_LIBRARY`,
`LINGXI_TEST_OCTOMAP_SHA256` and `LINGXI_TEST_OCTOMAP_DEPENDENCIES` variables
to the pinned actual builds. The dependencies variable is the JSON hash object
from the OctoMap build report, not a search path. Then run:

```sh
rtk proxy python -m unittest discover -s lingxi-next/tools/tests -p 'test_registered*.py' -v
```

Without the native-library variables, the actual-native composition is skipped;
that run alone does not reproduce the zero-skip result. The tested WSL Python
environment is `continuous-localization-env-20261030-a`; native build directories
are `ground-offline-20261101-c` and `octomap-offline-20261101-d` under the user's
private `.cache/lingxi-next`. No raw observations or private logs are committed.

## Not Yet Accepted

This closes the offline same-frame registration and declared-terrain source
composition, not the full product or live navigation chain. Production live
factories do not enable the new acquisition/terrain composition yet. The linked
follow-ups now verify combined actual recorded GLIM/localization/terrain and,
separately, actual ROS consumption with synthetic input. Current ARM64 execution,
real-recording-through-ROS integration, physical scale and ground/extrinsic
calibration, moving obstacle stopping and field navigation remain unaccepted.

Adding the registration dependencies to `controlled_localization.SOURCES`
makes current-source drift explicit. Existing frozen runtime reports remain
historical; none has been rewritten or promoted to current ARM64 acceptance.
