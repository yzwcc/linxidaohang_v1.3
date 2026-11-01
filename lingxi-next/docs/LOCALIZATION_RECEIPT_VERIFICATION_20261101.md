# Original Localization Ingress Receipt

Date: 2026-11-01. Offline WSL x86_64 verification. No robot connection,
sensor subscription, deployment or motion command occurred.

## Change

The point callback previously supplied its completion-time monotonic clock to
`LocalizationSession.observe`. This hid queue, IMU-wait and native-computation
age. Localization now retains the original `SensorWorker` callback receipt
through the immutable `SensorStream.Scan`, the supervised replay owner and
`LocalizedNative.frame_received`. The point callback must match the active
scan stamp, execute synchronously on its owner thread, and occur at most once.
Plain `frame` calls without an original receipt are rejected for localization;
ordinary non-localization native adapters retain their existing frame API.

The frame captures the localization generation before native processing.
`observe` checks that generation under the session lock after processing any
pending command. A pause, stop, reseed or expiry cannot turn an old frame into
an observation belonging to the new generation. Native/consumer failure
revokes admission, clears the active frame context, and cannot fall back to
processing the same frame through the old API.

The localization source identity now also binds the stream, corrected-point
adapter and supervised replay owner. Historical evidence remains historical;
changed source identities require new verification before deployment.

## Verification

The receipt-specific suite has 16 tests. It covers original-receipt expiry,
native and registration delays, invalid/missing contexts, duplicate and
wrong-stamp callbacks, callbacks from another thread, generation changes,
IMU waiting and replay-owner error propagation without duplicate processing.

One test composes the actual `SensorWorker`, stream, replay owner, localization
wrapper and session on their real worker thread, with explicitly synthetic
native and registrar peers. A controlled monotonic clock advances through a
0.2-second startup queue, further IMU waiting and 0.4-second native processing.
The cloud receipt remains 10.05 at completion time 10.7. Mutating the original
message after enqueue does not change the owned point/time snapshot. The frame
runs once and native cleanup runs on the same non-main worker thread.

Focused regression: **177 tests passed, zero skips**, covering receipt,
localization control/session/tracking/seeding, stream, worker, supervised replay
and live-localization contracts. These tests use the existing isolated Linux
localization environment with NumPy, SciPy and small-gicp installed. They do
not execute a new real native SLAM trial or prove physical localization.

Full Python regression after the historical-source audit update: **1565 tests
discovered, 1498 passed, 67 skipped, zero failures** (291.603 seconds). Skips
retain their environment/device prerequisites and are not acceptance evidence.
Two old reports now verify their source digests against their frozen Git
revisions, explicitly reporting current stream drift instead of rewriting the
historical evidence or treating it as a current ARM64 run. The historical
auditor verified all seven catalogued reports.

## Boundaries

- Receipt means local callback ingress, not a LiDAR hardware capture time or
  an estimate of upstream DDS/network delay. Hardware time remains separate.
- The corrected point callback is still deskewed odometry geometry, not a
  per-return acquisition-ray export. No ray origin is synthesized here.
- Delayed finalized-submap observation windows remain historical input and
  receive no new receipt merely because they were read or corrected recently.
- The low-latency acquisition-ray export, registration into the saved-map
  frame and live `TerrainLane` wiring remain open. This fix alone does not
  make localization, terrain or physical navigation ready.
- No frontend/APK source changed. Default display/clearance Z remains
  0.1--1.4 m and saved raw ground returns are preserved. ARM64 runtime,
  physical calibration, actual obstacle stopping and full-product acceptance
  remain separate requirements.
