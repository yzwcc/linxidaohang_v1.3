# Async GLIM Product Integration

Updated 2026-10-31. The product now explicitly selects the separately verified
full CPU local/global mapper instead of silently using the baseline mapper.
Existing sensor and odometry configuration and vendor installations are retained.

- Schema-3 receipts preserve the mapping engine, sensor provenance, full graph
  inventory, complete input accounting and independent-process reload result.
- Management health includes optimization backlog, capacities and historical
  queue/task duration. The input owner alone calls native mapping functions.
- Provisional scan preview uses `lingxi_glim_odom`. Revisioned optimized submaps
  use `lingxi_glim_map` and replace, rather than accumulate over, old geometry.
- The private atomic preview contains at most 8,000 display points. Saved maps
  keep their complete point set. A fresh scan and current management session
  remain necessary to display a live corrected-map revision.
- Final snapshot comparison permits only 64 double-precision epsilons per scaled
  coordinate, accounting for core/adapter SIMD evaluation differences. It does
  not relax the separate predeclared fresh-reload tolerance of 1e-4 raw units.

## Actual ARM64 Stationary Check

Through the packaged HTTPS Web application, passive real sensor acquisition,
stop/save, catalog listing and saved point-cloud rendering succeeded. The saved
map contains 485 frames, one submap and 1,992 points. The schema-3 receipt reports
successful independent-process reload. Maximum snapshot coordinate difference
was 1.7763568394002505e-15 raw; maximum reload point displacement was
3.605575420116852e-7 raw. Browser canvas pixels were checked as nonblank for both
live acquisition and saved-map review. No motion commands were sent.

The preceding ARM64 attempt failed an exact floating-point equality assertion
and did not publish a ready receipt. That failure is not counted as a pass.
Raw reports, point clouds, credentials and certificates remain outside Git.

Local checks: 444 Vitest tests passed, 14 skipped; TypeScript checks and the Web
build passed. Focused Linux numerical tests cover the adapter, preview writer,
bundle/health contracts and mapping lifecycle. Earlier recorded service/API
save/discard and full dynamic map/reload checks remain separate evidence.

## Short Out-and-Back Field Trial

The user saved field map 1.2 after an estimated 3 m outward trip with small
turns/side-stepping, an approximately 3 m return to the starting location, and
restoration of the initial heading. These are user estimates, not measured
ground truth. Read-only SFTP inspection retained the original map unchanged;
no motion commands or service restarts were issued.

- The recorded sensor stream accepted 493 clouds and 9,862 IMU messages with
  zero rejected or discarded worker messages. One initial cloud preceded usable
  IMU coverage; 31 scans served initialization. The saved map contains all 461
  estimated frames, one submap and 9,771 points. Its trajectory spans 46.002 s.
- The final optimized LiDAR trajectory has a 0.093886 raw-unit endpoint XY gap
  and a 3.7016-degree endpoint heading difference. The odometry-only endpoint
  XY gap is 0.195840 raw units. These are trajectory consistency observations,
  not measured robot positioning errors or a verified centimetre accuracy claim.
- Maximum XY distance from the first saved pose is 2.693500 raw units. Cumulative
  LiDAR XY travel is 7.927839 raw units. Turning arcs, sensor offset, body sway and
  possible drift prevent equating this to the estimated 6 m robot-base route.
  No scale factor was fitted to the user's approximate distances.
- Fresh-process reload passed with maximum point displacement 6.2091e-7 raw
  units, below the unchanged 1e-4 threshold. A Web canvas snapshot confirmed the
  selected saved map rendered. The active display-only Z range 0.3 to 1.5 showed
  1,511 of 9,771 points; the saved points were neither filtered nor overwritten.
- The map has zero inter-submap matching factors and no non-adjacent matching
  pair. This short one-submap trial therefore does not verify loop closure or
  live multi-submap revision delivery. The plot supports recognizable wall
  outlines, not a ground-truth check of wall thickness or obstacle geometry.

Receipt SHA-256: `dcb5d2339120af5ee76e77f9a078aed4a514d24d18aaf04a9c7a1c46d5f5c08e`.
Raw coordinates, trajectories, map files, private collection reports and images
remain outside Git and delivery archives.

This is a short moving mapping/save/reload observation, not geometry or product
acceptance. Live multi-submap updates, measured scale/heading, loop closure,
localization, navigation, Android and voice remain unaccepted. The next product
priority is stationary saved-map localization, followed by supervised short-route
navigation only after localization and motion-interface prerequisites pass.

## Saved-Map Localization Integration

The service resolver now admits strictly typed receipt versions 2 and 3. Version
3 requires `glim-full-async-v1`; version 2 must not carry a mapping engine. It
retains receipt identity, point digest, fresh reload and current sensor-provenance
checks. Full inventory and point loading remain in the child. The earlier
schema-2-only resolver rejected map 1.2 before launching a localization child;
that integration failure is not a localization-quality result.

With the user confirming the robot stationary at A, live map-1.2 localization
started and accepted a single explicit seed from the saved final LiDAR pose.
The diagnostic run entered tracking about 0.453 seconds after seed submission,
then stopped tracking when GICP exhausted its 40-iteration budget. The private
quality summary recorded 118 accepted estimates followed by one rejection:
native convergence was false, despite overlap 0.998228, inlier RMS 0.141654 raw,
inlier P95 0.230138 raw, and nondegenerate geometry. This trial did not meet its
sustained-tracking requirement. Its owned process cleaned up and map digests
remained unchanged; no movement commands were sent.

Registration now has an 80-iteration computation ceiling, within the existing
bounded native worker and process supervision. Explicit native convergence,
overlap, residual, correction, geometry and freshness requirements are unchanged.
A regression test confirms that even a high-overlap estimate exhausting the
larger budget has no accepted pose. Live child reports retain bounded scalar
quality summaries, including at most eight initial rejected estimates, without
point coordinates or poses. This diagnostic record does not enable readiness.

Focused Linux checks: 146 tests passed, with 11 environment-dependent skips
(157 discovered). The source-bound shared API/Web build also passed. Real-robot sustained
tracking with the larger budget is a separate acceptance check, not implied by
these tests or the earlier short tracking observation.

## Sixty-Second Stationary Tracking Observation

The rebuilt source-bound ARM64 runtime was tested with the same map 1.2, the
robot stationary at A, unchanged sensor provenance, and one explicit seed from
the saved final LiDAR pose. No automatic reseeding or motion was used.

- First diagnostic tracking was observed 0.328 seconds after seed submission.
  Across 60.047 seconds, all 604 native registration estimates were accepted,
  with zero registration rejections. The HTTPS observer sampled 429 distinct
  tracked frames and saw a timestamped base projection on all 429 samples.
- Maximum sampled displacement from the first tracked LiDAR pose was 0.036526
  raw units, and maximum sampled relative rotation was 0.526995 degrees. These
  are stationary repeatability observations, not absolute position accuracy,
  physical scale validation, or measured robot-base displacement.
- The child reported normal live-runtime closure with 639 accepted clouds and
  12,783 accepted IMU callbacks. The manager confirmed owned process cleanup.
  Receipt and saved-point digests remained identical after the trial.
- Runtime manifest SHA-256:
  `e8f69ea53ae5e2739b571533fe92ecc3f56ba9e13694394397c157109cc4ba04`.

This passes the bounded local stationary-tracking trial only. It does not prove
that the larger iteration budget eliminates every convergence failure, and it
does not replace moving localization, measured frame/scale checks, loop closure,
navigation or product acceptance. Both `localizationReady` and `navigationReady`
remain false. Private trial reports and map/sensor data are not committed.

## Offline Observed-Map Product Integration

On 2026-10-31 the management product explicitly selected the source-bound
`glim-full-observed-v1` bundle. Its native mapper retains preprocessed returns
and acquisition-time trajectories before submapping discards the input scans.
The existing full-async mapping engine and schema-3 map receipt remain in use;
an observation implementation digest distinguishes maps with this extra data.

Publication now writes a pending receipt, independently checks the saved map
and observations, binds the archive to the exact receipt and sensor provenance,
then publishes the single ready receipt. Failed binding cannot admit a map.
The API catalog checks binding identity, bounded archive header/counts and
digest; localization independently verifies the archive before loading points.
Old maps without observations remain viewable but do not acquire ray evidence.

The real recorded service/API save and discard trial ran locally on x86_64,
at input rate 1.0, without contacting the robot or subscribing to live sensors:

- Both sessions consumed 4,144 messages and processed 196 scans. The save run
  finalized and saved all 165 estimated frames, one submap and 2,280 map points.
- The bound archive retains 1,253,782 returns in 165 scans. Acquisition times
  are bracketed; 164 scans use smoothed IMU trajectories and one original LIO
  IMU trajectory. Independent reload returned the identical observation summary.
- Maximum fresh-reload point displacement was 4.9328e-7 raw units, below the
  unchanged 1e-4 tolerance. Four missing/mismatched provenance cases were rejected.
- The service exited normally and removed its owned socket after save/discard.

Private trial report SHA-256:
`22410649ac9d16dc60f03717e220d3284a24565acbfb7cd43f5ec6b4cfa09a5c`.
Observed bundle manifest SHA-256:
`5ab73211a928359d1e6621533c1c6b06e731b3a14b61b64e79f0f0ebf0faa471`.

Checks: 449 Vitest tests passed, 14 skipped; frontend/API/runtime typechecks and
Web build passed. The numerical Python mapping suites ran 218 tests with one
environment-dependent skip. Observed-product tests ran 13/13 without skips,
localization-control 29/29, and observation-binding 6/6.
The broader Python regression still has previously reproduced historical
source/evidence-consistency failures; it is not reported as a full pass.

This is product persistence and admission evidence, not physical scale,
geometry, moving localization, local avoidance, ARM64 observed-product execution
or full-product acceptance. No robot data, credentials or raw archives are
included in Git. Navigation readiness remains false.
