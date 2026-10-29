# Recorded Mapping Control Integration

Date: 2026-10-30. This milestone connects recorded-data GLIM mapping and its
same-engine corrected points to the persistent paired management API and
shared web UI. It is not live mapping, ARM64
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
- A private same-UID Unix point socket follows the authoritative mapping
  session. The API stays running across save, discard and replacement sessions;
  browser reconnect does not restart the native consumer. Initial connection
  may retry before the producer appears; an accepted stream never reconnects
  within the same session after EOF or invalid data.
- Saving and stopping hide points while the API continues draining the socket.
  This keeps native finalization from failing because its visual consumer
  disconnected. Idle, failure, EOF and stale data clear the browser's point
  history. No demo or vendor LiDAR fallback is used when this backend is present.
- Recorded points use the separate `slam-preview` event, explicit recorded
  provenance and raw coordinate units. They cannot satisfy the live-only
  point interface. They are odometry-corrected points, not a loop-closed global
  map or a verified robot-base pose.
- Recorded mode rejects navigation dispatch/resume, preset motions and
  LinkCraft playback. Explicit navigation stop remains available. Saved
  GLIM maps remain separate from AIMDK maps and are never auto-activated.

The optional API settings are `LINGXI_MAPPING_SOCKET` and
`LINGXI_SAVED_SLAM_DIR`; both are required together. The service requires an
explicit local config with `inputMode: recorded`, existing frozen inputs,
and rate 0.5 or 1.0. It never accepts executable paths or native configuration
from HTTP clients. Start is currently enabled only in development/demo API
mode. The production robot path is deliberately not enabled by these settings.

The bounded diagnostic catalog is not long-term map retention. The recorded
child now sends corrected points from the same GLIM instance that feeds its
mapper. The saved preview is independently loaded from the verified map.
Neither path claims live sensor acquisition.

The recorded child uses the shared close/drain lifecycle described in
`SLAM_MAPPING_INPUT.md`. The service/API native trial and aggregate were
repeated for this persistent point transport. The earlier same-engine
mapping/point trial used a synthetic subscription boundary with preserved
recordings, not live DDS; its historical aggregate remains unchanged.

## Native Result

The final source-bound save/discard trial used Linux
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
maximum displacement was `4.917700371519592e-7` raw units, below the existing
`1e-4` serialization tolerance. This is not physical geometric accuracy.

Each child sent 164 corrected-point packets, with zero replacements, writer
failures or surviving writer threads. The paired WebSocket observed 82 unique
frames per session at its bounded display cadence, reconnected in each
session and became unavailable after stop. One API instance served both
native sessions without a demo point fallback.

The paired API also confirmed same-session save retry, stale-session rejection,
discard without another ready map, and unchanged AIMDK maps/active selection.
The service exited with code zero and removed its socket.
Original recordings, libraries and existing robot/vendor files were unchanged.
Aggregate evidence is in `slam-mapping-control-evidence.json`; maps, recordings,
logs, private reports and session identities are excluded from Git.

## Checks

- 196 Vitest tests in 17 files pass; frontend/API/runtime type checks and production web
  and API builds pass.
- Complete Linux Python tools regression: 823 tests, 819 passed and four
  explicit dependency/platform skips. This includes private point socket,
  service lifecycle and unchanged historical ARM64 evidence checks.
- The compiled production frontend passes ten `agent-browser` stages against
  an isolated HTTPS synthetic fixture using the production mapping point
  consumer and private Unix socket. Desktop 1440x900 and landscape 844x390
  show nonblank changing points with recorded provenance, stale clearing,
  resume, WebSocket reconnect, named save, a replacement session, zoom, EOF
  clearing and confirmed discard. The saved map remains visible afterward.
  Neither viewport overflows horizontally; landscape controls remain
  accessible by vertical scrolling. Browser errors are empty and local
  storage contains only the robot address. Private reports/screenshots stay
  ignored. See `slam-mapping-point-ui-synthetic-evidence.json`.
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

Prepare the new ARM64 mapping/point composition offline, then validate it on
target when available. Live sensor acquisition and explicit map save still
need to be connected to these management controls under the same owner.
Base-frame alignment, calibration/timing validation, localization, long-session
mapping/loop closure, independent navigation, voice concurrency and deployable
robot/APK packages remain outstanding. Map serialization never raises those
readiness flags.
