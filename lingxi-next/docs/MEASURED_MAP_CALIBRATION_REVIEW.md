# Measured Map Calibration Review

This workflow records an operator's independently measured correspondences
between saved raw point-cloud samples and site coordinates. It calculates an
Umeyama similarity candidate (`target_m = R * (scale * raw) + translation`)
with the browser's deterministic saved-map preview and current map receipt,
point digest, and sensor profile bound into the survey. It does not move the
robot, edit a SLAM map, change runtime calibration, add navigable points, or
open any localization/navigation readiness gate.

## Field procedure

1. Select a saved live or recorded map with validated save artifacts and sensor
   provenance. Maps without a usable profile-bound trajectory do not offer
   survey calibration.
2. Agree a site frame and write down its origin, `+X` direction, and vertical
   `+Z` convention. Record an identifiable, stable point visible in the point
   cloud and measure its coordinates in metres with a tape, laser rangefinder,
   or survey instrument. Robot travel distance by itself is not a landmark.
3. Open **Measure calibration**, select the crosshair tool, and click a point
   on the displayed saved cloud. The panel records the index and raw XYZ from
   that exact bounded preview sample. Enter the independently measured XYZ,
   unique label and measurement method. Do not read a distance from the raw
   viewer scale and call it metres.
4. Add at least three well-spread, non-collinear fit landmarks and at least
   two additional, spatially separated check landmarks. Check landmarks are
   excluded from fitting. Place them across the surveyed area; do not re-use
   the start/end travel estimate as validation.
5. Calculate the candidate. Review scale, fit RMSE/maximum residual, check
   RMSE/maximum residual, and every holdout point. Current review limits are
   30 mm RMSE and 50 mm maximum error for both sets; the bounded scale range is
   0.01–100 metres per raw unit. Poor geometry, weak scale, excessive residual,
   and synthetic input fail closed.
6. If the checks pass, enter the reviewer and explicitly acknowledge
   **candidate only**, then save. The API re-loads current artifacts and
   recomputes the candidate before a new permission-restricted audit record is
   written under robot application state. The service provides no update or
   delete route, validates the saved candidate digest on read, and returns the
   existing record for repeated identical reviews.

The result is a reviewable **candidate**, not physical calibration acceptance.
Operator-entered axes do not independently prove gravity alignment, IMU/LiDAR
extrinsics, sensor timing, body footprint, obstacle clearance, or floor
support. A separate instrumented validation is still required before deriving
runtime configuration or allowing physical localization and navigation.

## API

- `GET /api/v1/slam/maps/:id/calibration`: current map-bound, verified audit
  records.
- `POST /api/v1/slam/maps/:id/calibration/candidate`: validate bounded survey
  data and calculate without persisting or applying it.
- `POST /api/v1/slam/maps/:id/calibration/review`: re-compute, compare the
  candidate digest, require operator acknowledgement, and persist only an
  audit record. HTTPS and an authenticated paired session are required.

Request bodies use strict schemas. Callers cannot submit raw cloud points,
transforms, scales, readiness flags, paths, or map receipt overrides. Stale
receipt, geometry, point digest, profile digest, failed checks, modified review
content, and linked audit directories are rejected. Audit metadata never
changes saved-map catalog readiness.

## Offline verification

`robot-api/tests/calibration.test.ts` covers 3D similarity recovery, independent
holdouts, reflections and degenerate geometry, stale identities, strict request
validation, immutable map artifacts, idempotent audit writes, tampering, HTTP
session enforcement, readiness non-promotion, and point selection on/off the
Z display slice.

For browser-only layout checks, build the web bundle then run
`npx tsx tools/fixtures/calibration-review-ui.ts`. The temporary loopback server
contains only a conspicuously synthetic point cloud and uses the local pairing
code printed by the fixture. It never contacts or commands a robot; close it
with Ctrl+C when finished.
