# Managed Mapping Control Integration

Date: 2026-10-30. This milestone connects GLIM mapping and its same-engine
corrected points to the persistent paired management API and shared web UI.
The shared platform prepares x86 recorded input and ARM64 Humble recorded/live
input. Actual native execution in this milestone used recorded data on x86-64.
It is not live mapping acceptance, ARM64 acceptance, localization, independent
navigation or a production deployment.
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
- All native SLAM points use the separate `slam-preview` event with explicit
  live/recorded input provenance and `coordinateUnits: native-raw`. Live input
  alone does not establish metres or a calibrated robot-base pose. Genuine
  vendor LiDAR events retain their metre contract. The UI labels native
  coordinates as uncalibrated. These are odometry-corrected points, not a
  loop-closed global map.
- Independent mapping mode rejects navigation dispatch/resume, preset motions and
  LinkCraft playback. Explicit navigation stop remains available. Saved
  GLIM maps remain separate from AIMDK maps and are never auto-activated.
- `mapping_platform.py` centralizes verified environment, decoder, native
  libraries and independent reload. ARM64 bindings check the existing
  odometry/core/mapper/point manifests and actual loaded libraries; they require
  ordinary-user Linux AArch64 Python 3.10. The new combination has not run on
  ARM64. Input loss, parent pipe loss or changed frozen inputs revoke saving.

The optional API settings are `LINGXI_MAPPING_SOCKET` and
`LINGXI_SAVED_SLAM_DIR`; both are required together. The service requires an
explicit local config and existing frozen inputs. Recorded input accepts only
rate 0.5 or 1.0. The prepared ARM64 `platform: arm64-humble` config selects
recorded or passive live input; live sessions are bounded to 5-300 seconds.
HTTP clients cannot supply executable paths or native configuration.
Start is currently enabled only in development/demo API mode. The production
robot path is deliberately not enabled by these settings.

The bounded diagnostic catalog is not long-term map retention. The recorded
child now sends corrected points from the same GLIM instance that feeds its
mapper. The saved preview is independently loaded from the verified map.
Recorded runs do not claim live sensor acquisition. The prepared live branch
uses the existing passive subscription and the same explicit finish path;
unexpected input closure cannot create a ready map.

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
maximum displacement was `4.917703308982532e-7` raw units, below the existing
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
Aggregate evidence for the shared platform run is in
`slam-mapping-platform-control-evidence.json`; its 19 source hashes match the
tested sources. The earlier `slam-mapping-control-evidence.json` is retained
as historical evidence. Maps, recordings, logs, private reports and session
identities are excluded from Git.

## Checks

- 204 Vitest tests in 17 files pass; frontend/API/runtime type checks and production web
  and API builds pass.
- Complete Linux Python tools regression: 836 tests, 832 passed and four
  explicit dependency/platform skips. This includes private point socket,
  shared platform configuration, save revocation, service lifecycle and
  unchanged historical ARM64 evidence checks.
- Actual Linux process checks pass: eight owner cases and twelve inherited
  API cases. These use synthetic peers, not robot sensor acquisition.
- The compiled production frontend passes ten `agent-browser` stages against
  an isolated HTTPS synthetic fixture using the production mapping point
  consumer and private Unix socket. Desktop 1440x900 and landscape 844x390
  show nonblank changing points with recorded provenance, stale clearing,
  resume, WebSocket reconnect, named save, a replacement session, zoom, EOF
  clearing and confirmed discard. The saved map remains visible afterward.
  Neither viewport overflows horizontally; landscape controls remain
  accessible by vertical scrolling. Browser errors are empty and local
  storage contains only the robot address. Private reports/screenshots stay
  ignored. The latest private report is
  `.local/mapping-point-ui-20261030-platform-b/report.json`; the earlier
  published `slam-mapping-point-ui-synthetic-evidence.json` is unchanged.
- The live/raw synthetic browser fixture also passes ten stages with explicit
  raw-coordinate provenance. Neither browser fixture is Android acceptance.
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

Compose the prepared shared ARM64 mapping/point platform, persistent API and
management service under one owner, then validate it on target when available.
The service currently lacks owner-stdin lifetime linkage; the existing live
bundle remains read-only and is not a managed mapping launcher.
Base-frame alignment, calibration/timing validation, localization, long-session
mapping/loop closure, independent navigation, voice concurrency and deployable
robot/APK packages remain outstanding. Map serialization never raises those
readiness flags.
