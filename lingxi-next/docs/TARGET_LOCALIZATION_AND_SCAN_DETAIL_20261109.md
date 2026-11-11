# Saved scan detail and live localization — 2026-11-09

## Deployed result

The robot at `192.168.0.128` runs the new release
`/opt/lingxi-next/releases/0.1.30-isolated-registration-retry-20261111` through
`/opt/lingxi-next/current`. The SLAM console is
`https://192.168.0.128:3444`; `lingxi-next-independent-slam.service` is active.
The previous `0.1.30-async-localization-20261109` release remains available
for rollback. The service stopped and started gracefully during this update.

This is a commissioning milestone. Moving localization accuracy, measured
scale/base calibration, physical navigation/obstacle handling, voice and Android
field acceptance remain outstanding. No movement command was sent in this trial.

## Changes

- Saved-map display can show registered, motion-compensated laser returns from
  the map's observation archive. It selects actual returns at 0.05 raw-unit voxel
  spacing, retaining up to 80,000 points with priority for the default Z band.
  This cache is display-only and never replaces the native map or localization
  point file. Missing or malformed detail falls back to the original map view.
- Cache construction runs after confirmed save on one optional background job.
  Save admission and the native mapping deadlines do not wait for rendering.
- Live localization keeps LiDAR/IMU ingestion on its native owner and runs map
  registration on a separate owner with one replaceable pending corrected frame.
  Raw sensor messages are not coalesced. Generation checks and immediate
  admission revocation prevent delayed registration results from reviving a
  paused or stopped session. Shutdown verifies both owners and frame accounting.
- Startup performs full map validation between identical content fingerprints.
  Shutdown checks the same profile, receipt, points, native-map tree and optional
  observation files byte for byte, avoiding a second reconstruction of saved
  laser geometry. Source, platform, configuration and native-library checks remain.
- A shorter runtime directory keeps nested localization Unix sockets within the
  operating system path limit. Python test contexts also support the robot's 3.10.

The previous trial exceeded the existing cleanup deadline and is retained as
failed evidence. An isolated measurement found full map loading took about
9.95 seconds, mostly rebuilding saved acquisition geometry. The new trial below
confirms clean termination after removing that repeated work; this does not prove
geometry reconstruction was the sole cause of the earlier timeout. The runtime
deadline was not increased.

## Actual ARM64 passive trial

The selected map is `现场验证_11_09`: 800 saved frames, one native submap,
11,825 localization points. The operator described an approximately 2.8 m
outward route with turns/sideways movement, followed by a return to the initial
position and heading. The current trial used the saved last LiDAR pose as seed;
the physical pose was not freshly measured or confirmed. It is a stationary
tracking and lifecycle check, not an accuracy acceptance result.

| Observation | Measured result |
| --- | --- |
| First tracking after seed submission | 0.388 seconds |
| Observed tracking interval | 15.076 seconds |
| Distinct frames observed through the API | 116 |
| Largest translation relative to first observed pose | 0.02782 raw units |
| Largest rotation relative to first observed pose | 0.31663 degrees |
| Observed frames with available base pose | 113 / 116 |
| Registration attempts accepted / rejected | 155 / 0 |
| Registration lane offered / processed / replaced / discarded | 189 / 189 / 0 / 0 |
| Maximum registration-lane processing duration | 0.136 seconds |
| Sensor messages completed / discarded at shutdown | 3,996 / 1 |
| Final queues, in-flight work and registration pending slot | Empty |
| Sensor, registration and control threads after cleanup | Stopped |
| Final service run phase | `stopped`, cleanup complete, no failure code |
| Child result | `live_localization_runtime_closed`, failures empty |
| Saved map receipt and point digest after trial | Unchanged |

The registration owner closed normally; the worker had no fault. The full
report stays private on the robot at
`/var/lib/lingxi-next/diagnostics/field-localization-20261109-clean-183256`.
The child report is under the matching owned runtime directory. Credentials,
pairing tokens, raw point clouds and site screenshots are excluded from Git.

## Validation and browser check

- The actual ARM64 service account ran the localization test suite: 235 cases,
  208 executed successfully and 27 skipped for declared environment conditions.
  It includes blocked-registration ingestion, buffer ownership, pause/reseed,
  shutdown accounting, callback failure and map-fingerprint mutation regressions.
- The earlier checks for this update passed: Web/API/runtime build; saved-map
  catalog and preview API tests (25 executed, one Windows symlink test skipped);
  live acquisition tests (38); saved scan preview tests (2); and mapping-service
  tests in the local Git checkout (23 cases, five skipped).
- The catalog/detail regression suite was reviewed alongside the Python changes.
  `git diff --check` passed.
- The paired browser opened the robot console and the saved field map. It drew
  the laser scan detail, reported 80,000 preview points with 60,000 visible in the
  default 0.1–1.4 raw Z band, and exposed the localization controls. The nominal
  `metresPerMapUnit=1.0` remains unmeasured; the UI continues to identify raw units.

## Next field step

Use the same console's `定位验证` page and select `现场验证_11_09`. Confirm the
robot's current position/heading before choosing `建图末帧` as its initial pose;
otherwise pick the matching pose on the map. Start localization, submit the seed,
wait for tracking, then use the external controller for a measured short straight
route and return. Record actual distance, sideways/turning motion, endpoint and
heading. Compare the localization trace and base-frame orientation against those
observations before admitting physical navigation. The initial nominal scale
and a static TF lookup alone do not validate base calibration.

## Follow-up: isolated registration and recovery, 2026-11-11

The operator confirmed that the chassis had remained at the saved-map endpoint
and retained the same heading, while some obstacles had changed. A 45-second
passive trial after isolating native GICP in a spawned process kept the raw
sensor worker within its original queue and processing deadlines, but one late
registration did not converge. Metrics showed full scan/map overlap and small
seed correction, but the native optimizer reached 79 iterations without
convergence. The prior tracker immediately revoked the whole session on that one
rejected scan. No map contents changed.

The tracker now withholds its pose on the first rejected registration and
returns to `acquiring`; it requires three fresh accepted matches before exposing
a pose again. A second consecutive rejection still marks the track lost, and a
stale pose is never reused. Synthetic lifecycle tests cover transient recovery,
continued failure, expiry, and registration exceptions.

The updated immutable release is
`/opt/lingxi-next/releases/0.1.30-isolated-registration-retry-20261111`.
Its health endpoint returned `ok` after activation. The robot ARM64 environment
ran 138 localization, tracking, worker, control, and registration-process tests
successfully. A second 45-second passive trial completed with these results:

| Observation | Measured result |
| --- | --- |
| First tracking after seed submission | 0.443 seconds |
| Observed tracking interval | 45.101 seconds |
| Distinct frames observed through the API | 349 |
| Registration attempts accepted / rejected | 456 / 0 |
| Largest translation relative to first observed pose | 0.05378 raw units |
| Largest rotation relative to first observed pose | 0.3144 degrees |
| Observed frames with available base pose | 344 / 349 |
| Registration lane offered / processed / replaced / discarded | 491 / 491 / 0 / 0 |
| Maximum registration-lane processing duration | 0.105 seconds |
| Maximum sensor queue age | 0.3734 seconds, below the existing 0.5-second limit |
| Maximum IMU / cloud operation duration | 0.1034 / 0.1033 seconds |
| Sensor callbacks rejected / discarded at shutdown | 0 / 4 |
| Final sensor queues and in-flight work | Empty |
| Worker, localization lane and child processes after cleanup | Stopped |
| Saved map receipt and point digest after trial | Unchanged |

This confirms sustained stationary localization at the confirmed starting pose,
including after the transient-rejection recovery change. It remains a field
localization check, not a moving accuracy, map-scale, base calibration, route,
obstacle-avoidance, or navigation acceptance. No movement command was sent.

The desktop can now reach TCP port 3444 at `192.168.0.128`; the earlier browser
connection refusal occurred during service restart. Reload the console at
`https://192.168.0.128:3444` to inspect the saved LiDAR map. The next field gate
is still an externally controlled, measured short A-to-B-to-A route with its
distance, turns/sideways movement, return position, and heading recorded.
