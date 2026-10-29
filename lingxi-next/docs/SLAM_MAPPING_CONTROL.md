# Recorded Mapping Control Integration

Date: 2026-10-29. This milestone connects recorded-data GLIM mapping to the
paired management API and shared web UI. It is not live mapping, ARM64
acceptance, localization, independent navigation or a production deployment.
The APK remains 0.1.19; it was not rebuilt in this milestone.

## Implemented

- A same-UID, private Linux Unix socket controls one supervised mapping child.
  Session IDs bind save/discard requests; repeated identical completion is
  idempotent and a retired session cannot stop a replacement session.
- `MappingWorker` retains odometry-before-mapper initialization. A save-time
  name is synchronized even if native initialization overlaps the request.
  The existing input watchdogs remain unchanged.
- Saving stops admission, drains accepted input, closes native mapping once,
  reloads the map in a separate process, then validates the ready receipt.
  Unconfirmed cleanup preserves output but withdraws its ready marker.
  Parent pipe loss revokes an outstanding save.
- Paired `mapping/start`, `mapping/stop-save` and `mapping/stop-discard` routes
  expose the lifecycle. Save/discard require the current session ID. Save
  requires an independently validated catalog entry, not just a backend ACK.
- The UI provides named save and explicit discard. The latter requires
  confirmation. Client deadlines cover the corresponding cleanup budgets.
- Recorded mode rejects navigation dispatch/resume, preset motions and
  LinkCraft playback. Explicit navigation stop remains available. Saved
  GLIM maps remain separate from AIMDK maps and are never auto-activated.

The optional API settings are `LINGXI_MAPPING_SOCKET` and
`LINGXI_SAVED_SLAM_DIR`; both are required together. The service requires an
explicit local config with `inputMode: recorded`, existing frozen inputs,
and rate 0.5 or 1.0. It never accepts executable paths or native configuration
from HTTP clients. Start is currently enabled only in development/demo API
mode. The production robot path is deliberately not enabled by these settings.

The bounded diagnostic catalog is not long-term map retention. This service
does not currently compose the mapping session with the separate live point
producer. A demo LiDAR view is synthetic; it is not the native replay's
point stream. The saved preview, however, is loaded from the verified map.

The recorded child now uses the shared close/drain lifecycle described in
`SLAM_MAPPING_INPUT.md`. The service/API native trial and aggregate were
repeated for that revision. A separate same-engine mapping/point trial uses a
synthetic subscription boundary with preserved recordings, not live DDS.

## Native Result

Two complete local trials passed. The final source-bound trial used Linux
x86-64 and the existing pinned native libraries at 1x recorded arrival rate.
No robot, ROS node, live sensor subscription or motion command was involved.

| Operation | Completed callbacks | Native scans | Estimated poses |
| --- | ---: | ---: | ---: |
| Save | 4,144 | 196 | 165 |
| Discard | 4,144 | 196 | 165 |

Both native instances were created/closed once on a single non-main thread,
with no rejected/discarded callbacks and no remaining control/worker threads.
The save produced 165 frames, one submap and 2,655 points. All points were
available through the authenticated preview. Independent reload comparison
maximum displacement was `4.917702644451365e-7` raw units, below the existing
`1e-4` serialization tolerance. This is not physical geometric accuracy.

The paired API confirmed same-session save retry, stale-session rejection,
discard without another ready map, and unchanged AIMDK maps/active selection.
The service exited with code zero and removed its socket.
Original recordings, libraries and existing robot/vendor files were unchanged.
Aggregate evidence is in `slam-mapping-control-evidence.json`; maps, recordings,
logs, private reports and session identities are excluded from Git.

## Checks

- 187 Vitest tests pass; frontend/API/runtime type checks and production web
  and API builds pass.
- Windows tools regression: 795 tests, 405 pass and 390 explicit dependency/
  platform skips before the additional evidence/socket cases.
- Linux focused suites: 15 session, 10 controller and 10 manager/evidence tests
  pass with the existing numerical environment. The manager suite includes
  a real private Unix socket lifecycle. Windows runs that final suite with
  nine passes and one explicit Linux socket skip.
- `agent-browser` synthetic checks pass at 1440x900 and 844x390: named save,
  saved-map visibility, confirmed discard and map preservation all work.
  Point canvases are nonblank and neither viewport overflows horizontally;
  landscape controls remain accessible by vertical scrolling. Screenshots
  and fixture reports remain ignored, not native/hardware evidence.
- Actual native execution is separate from these synthetic fixtures.
- A prior ARM64 evidence test now verifies revised API files at that run's
  immutable Git revision, rather than claiming later API code ran on ARM64.
  Historical hardware evidence is not rewritten.

`tools/run_mapping_control_trial.py` stages new private Linux sources, launches
the service and runs the bundled `tools/check-mapping-control.ts` probe.
It preserves trial output, verifies complete child cleanup and independently
checks saved-map inventory and point reload. `--evidence` exports only
allowlisted aggregates and source hashes.

## Next Mainline

Compose live sensor acquisition, corrected-point display and explicit map
save under the same owner, then validate the new composition on ARM64.
Base-frame alignment, calibration/timing validation, localization, long-session
mapping/loop closure, independent navigation, voice concurrency and deployable
robot/APK packages remain outstanding. Map serialization never raises those
readiness flags.
