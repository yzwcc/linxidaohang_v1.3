# Recorded Native Mapping Session

Date: 2026-10-29. The reusable `MappingSession` and `MappingWorker` now execute
the preserved GLIM mapping library against the existing complete callback
journal. This is Linux x64 recorded-input integration, not live sensors,
ARM64, calibration, map quality, localization or navigation acceptance.
The product/APK remains 0.1.19 and still uses the existing default adapters.

## Runtime Behavior

`MappingWorker` owns one bounded `SensorWorker`. Native creation, input,
odometry closure, map flush, mapper shutdown and verification run on the same
non-main thread. `stop(save=True)` explicitly requests a save; ordinary stop,
cancelled save and failed input never create a ready receipt. A session cannot
restart, and repeated/concurrent stops close the engine once.

The mapper is created from a factory after odometry initialization. This
retains the original validated program's library initialization order. Save
has a separate 90-second shutdown budget; input queue, processing, sensor
silence and timing-hypothesis checks are unchanged. The caller still needs
external process ownership before this becomes a production service.

`robot-runtime/python/replay_mapping.py` binds the controller to the existing
verified native libraries, profile, configuration, journal decoder, inventory
checker and fresh-process reload helper. It preserves recorded classification
and validates all inputs again after completion. Output is new/private;
original recordings, maps, libraries and vendor services are not modified.

## Actual Results

Both final runs use identical source and input hashes at an explicit 1x
recorded arrival rate. Both finish 4,144 callbacks: 3,947 IMU and 197 clouds.
There are no rejected or discarded callbacks. Native creation/closure each
occur once; the worker thread is no longer alive after stop.

| Result | Save C | Discard A |
| --- | ---: | ---: |
| Native scans / initialized poses | 196 / 165 | 196 / 165 |
| Saved frames / submaps / points | 165 / 1 / 2,655 | No map created |
| Map files / bytes | 12 / 549,482 | No ready receipt |
| Maximum observed queue age | 73.994 ms | 115.933 ms |
| Final state | saved | stopped |

Saving closes the original mapper before a separate process reloads its map.
All 2,655 points are compared in original order; maximum serialization
displacement is `4.91769829766834e-7` raw coordinate units, below the previously
fixed `1e-4` tolerance. The map inventory and saved points remain unchanged.
An independent audit repeated inventory, point comparison and source/input
hash checks. This tolerance is not a physical accuracy claim.

Earlier save attempts A and B failed the unchanged queue-age limit after
117 completed callbacks and five initialization scans. They closed the native
instance and worker without a map or ready receipt. B's added timing/prefetch
did not resolve the failure. After restoring odometry-first library loading,
C succeeds; the earlier failures are retained and the low-level cause is
not established by this comparison alone.

The 22 focused Linux tests pass: 15 session lifecycle and seven controller
cases. Windows runs those 22: 13 pass and nine NumPy cases explicitly skip.
The complete Windows tools regression runs 784 tests: 397 pass and 387 skip
for unavailable platform/dependencies. Actual native runs are separate evidence.

## Remaining Work

The current native mapper retains its short-sequence loop-travel restriction;
these runs do not establish loop closure or general long-session mapping.
Live owner/API integration, saved-map management, target ARM64 save/reload,
base alignment, localization, navigation, voice concurrency and final packages
remain open. No readiness flag is raised by map serialization.

See `slam-mapping-session-evidence.json` for aggregate identities and counts.
Real maps, points, logs and reports remain in the private Linux cache under
`mapping-worker-save-20261029-c` and `mapping-worker-discard-20261029-a`.
