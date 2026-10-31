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

This is readiness for a supervised field mapping trial, not geometry or product
acceptance. Live optimized-map updates during movement, measured scale/heading,
loop closure, localization, navigation, Android and voice remain unaccepted.
