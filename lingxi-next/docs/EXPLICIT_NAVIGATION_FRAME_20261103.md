# Shared Explicit Metric Navigation Frame

Date: 2026-11-03. Offline implementation with synthetic-frame and recorded-data
verification only. No robot connection, deployment or physical command. Web and
Android remain point-cloud viewers; default display Z is **0.1--1.4 m**.

## Declaration

Live ARM64 configuration may now declare a rigid transform alongside the map
unit scale:

```json
{
  "basePose": {
    "metresPerMapUnit": 1.0,
    "navigationFromRaw": [
      [1, 0, 0, 0],
      [0, 1, 0, 0],
      [0, 0, 1, 0],
      [0, 0, 0, 1]
    ]
  }
}
```

The matrix is an explicit `navigation_from_raw` rigid transform. Its translation
is in metres. Raw-map translations are scaled once, then the transform is left
composed; the exact-time inverse base-to-LiDAR TF is applied afterwards:

`navigation_from_raw @ scaled_raw_map_from_lidar @ inverse(base_from_lidar)`

An omitted matrix remains identity for legacy configurations. Non-numeric,
non-rigid and translations beyond 500 m are rejected. The option is allowed
only with an explicit positive map scale and live ARM64 input.

Native registration poses, map files, seeds, recorded acquisitions, sensor-local
cloud points and body-frame twist are not rewritten. Saved-map terrain requires
the exact same scale and transform; both ray origins and endpoints receive the
same raw-to-metric geometry. Localization status, completion checks, Web schema
and navigation activation include the declared frame. Changing it revokes the
existing activation and requires a new activation.

## Calibration Boundary

This is a shared coordinate convention, **not a measured robot calibration**.
The present declaration defaults to identity and does not adopt the earlier
inconclusive floor-height estimate. It does not establish map scale, floor or
gravity alignment, LiDAR-to-IMU extrinsics, physical localization accuracy,
supported free cells, target ARM64 throughput or navigation readiness. No
physical navigation is admitted by this change. In particular, the retained
recording is still not an independent localization-accuracy sample.

## Verification

- Numerical composition and malformed-frame tests passed for scale, 3D rotation
  and translation, dynamic TF ordering, raw-pose preservation, copied state and
  rigid-transform limits.
- Actual isolated ROS Humble transport passed **16 tests** with default identity
  and a non-identity 3D frame. Odometry, point cloud, TF, terrain, original scan
  timestamps and terminal withdrawal were checked through actual ROS messages.
  Synthetic poses and terrain results are not a real robot acceptance test.
- Four registered-source actual Humble retirement runs were repeated as
  `registered-ros-MODE-20261103-j`; all four reports pass source and raw-grid
  regrading. The older generic terrain ROS reports are unaffected.
- The unchanged recorded-input chain was rerun as private
  `registered-replay-20261103-f`: 4,144 sensor messages, 196 processed scans,
  165 estimated poses, 161 registered packets and 1,223,474 measured returns.
  It observed 27 fresh terrain results; its 28th pending job was revoked at
  shutdown. **Zero supported free cells** were observed. This still does not
  demonstrate a usable route or navigation acceptance.
- Frontend/API regression: 622 passed, 24 skipped; production TypeScript/Vite
  build passed. The relevant scientific Python localization and acquisition
  suites and source-bound report checks also passed.

The shared geometry is wired and regression-tested, but selecting a non-identity
matrix requires a measured transform from the actual sensor/map setup. Do not
derive or deploy one from the synthetic trial or the old floor-height estimate.
