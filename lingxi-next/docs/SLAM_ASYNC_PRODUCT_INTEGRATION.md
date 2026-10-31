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
