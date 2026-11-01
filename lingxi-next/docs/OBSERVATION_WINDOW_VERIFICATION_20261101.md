# Continuous Corrected Observation Windows

Date: 2026-11-01. Offline WSL x86_64 verification, using actual GLIM native
libraries and the existing private moving recording. No robot was contacted,
no sensor was subscribed and no motion command was sent.

## Implemented

- Added `lingxi_observation_window` and an owner-thread Python reader. Only
  globally corrected, completed submaps are eligible. Pending scans are not
  presented as corrected observations.
- A window contains the latest complete scans, bounded by 8 scans, 262,144
  returns and 16 MiB. The payload reuses `LXOBS002`; the header preserves the
  original scan ordinal and submap offset. Complete archive validation runs
  before any decoded geometry is exposed.
- Native publication holds an immutable snapshot. Revision-aware query/copy,
  capacity checks and bounded retries prevent mixed or partially copied
  snapshots. Shutdown rejects subsequent reads. The Python owner checks worker
  health before and after reading.
- Correcting a submap changes registered geometry, not its source acquisition
  timestamps or source digest. Window readers and the required ABI are included
  in product bundle/source identity checks and ARM64 build verification.

These windows are explicitly `delayed_finalized_submaps`, in `native-raw`
coordinates, with `navigation_ready=False`. There is no fabricated
`received_at`. Reading a recently published correction does not make the
original observation fresh. This interface is not connected to `TerrainLane`
and is not an immediate obstacle sensor.

## Actual Native Tests

All **54 observation tests passed with zero skips** in 17.106 seconds, loading
the newly built native library. Tests exercise original acquisition geometry,
source binding, save publication, Python ownership/worker failure, malformed
payload rejection and concurrent native reading.

The actual native window fixture exercises three modes:

| Mode | First ordinal | Window scans | Window returns |
| --- | --- | --- | --- |
| Scan rollover | 4 | 8 | 24 |
| Per-window point bound | 8 | 4 | 262,144 |
| Joint point/trajectory bounds, 4,096 trajectory samples per scan | 8 | 4 | 262,144 |

It verifies unchanged destination buffers on revision/capacity rejection,
coherent concurrent copies, a +5 X submap correction, exact final-archive
geometry and shutdown rejection. The correction is synthetic, not physical
calibration.

## Recorded Moving Replay

The original 1x input replay completed in 122.324 seconds. Of 1,232 recorded
scans, 11 lacked the required acquisition-time brackets and 31 were odometry
initialization scans; the remaining 1,190 estimated frames entered mapping.
All 1,190 mapping frames and 24,434 IMU samples completed, with zero rejected
tasks or discarded admitted inputs. Mapping drained before persistence; total
completion was not limited to the input replay interval.

- A nonempty corrected window was read before save, after 784 input frames
  had been admitted: revision 1, original ordinals 672--679, 8 scans and
  53,099 returns. Every source timestamp/digest matched the final archive.
- The final archive contains 2 submaps, 1,190 scans and 7,709,062 preserved
  preprocessed returns. Independent observation reload passed.
- The independently reloaded display cloud contains 18,788 points. Its maximum
  round-trip displacement was `1.067984276436557e-6` raw units, below the
  predeclared `1e-4` serialization tolerance. This is not geometric accuracy.
- Worker queue peaks were 49/128 frames and 436/8,192 IMU samples. Host peak
  resident memory was 674.52 MiB; maximum worker task time was 2.156 seconds.
  These are single x86_64 replay measurements, not ARM64 resource acceptance.

Private replay evidence remains in
`observed-replay-dynamic-window-20261101-a` under the local offline cache.
Archive SHA-256:
`498377885f7374f148b54a57ebb71150464bc7ddb00642e7052f9fd9da240122`.
Source-bound implementation SHA-256:
`bebdb343b4e088bc7a5b5ad82b22fd5c0022c47a8e4ebba938a85f32ea091055`.
The private recording and geometry are not committed.

## ARM64 and Regression

The current sources were cross-compiled, then independently verified against
the frozen sources, exported ABI and ELF artifacts. No ARM64 binary was run:

- `liblingxi_glim_full_mapping.so` SHA-256:
  `5c68c9ac5f78d9fbe30d0cb4d047c0f4da457c4b8cff5bab3a4076d345d39ae6`.
- `lingxi_observation_contract` SHA-256:
  `2a104b7b2f8e2cac8da52216243175004c3658175cb2763e7b83273ae100b2d4`.
- ELF inspection reports AArch64/ELF64. Maximum GLIBC requirements are 2.32
  for the library and 2.34 for the fixture; target compatibility still needs
  an actual target check.

Full scientific Python regression: **1,379 passed / 168 skipped**, 1,547 total,
zero failures/errors (257.021 seconds). That run did not configure the optional
native/target environments; the 54-test run above separately used the actual
observation library with no skips. Fault-injection stderr was expected. A
Windows collection attempt lacked numerical/JSON5 dependencies and is not
counted as passing verification. No TypeScript/UI code changed or frontend
regression was rerun in this milestone.

## Remaining Work

Continuous corrected history is now available during mapping. Low-latency raw
observation delivery with original monotonic receipt ownership, localization
ownership and terrain integration remain open. Physical scale, base/floor and
sensor calibration, real obstacle/stop behavior and ARM64 runtime limits are
not accepted by this result. Default display/clearance Z remains 0.1--1.4 m;
raw ground returns are preserved. No deployment, APK rebuild or full-product
release occurred.
