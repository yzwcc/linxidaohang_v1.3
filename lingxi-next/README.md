# Lingxi Next

A clean-room local console and Android companion for AgiBot Lingxi X2. This
project does not include or modify the V1.3.9/R19 business code.

## Independent navigation evaluation

On 2026-10-28 the user confirmed this X2 does not include optional native SLAM.
An independent open-source mapping/navigation path is now being evaluated.
See [the comparison and read-only target results](docs/OPEN_SOURCE_NAV_EVALUATION.md).
Sensor capture, isolated CPU builds and real KISS/GLIM odometry diagnostics
have been completed. Real GLIM point-cloud maps have also been saved and
reloaded in fresh CPU processes. Four CPU modules also loaded successfully on
the real ARM64 robot, where a synthetic timing executable passed.
Four actual ARM64 replays of the static/dynamic recordings have now completed,
using the installed Humble bag reader without ROS nodes or live inputs.
Four additional ARM64 global-map save and fresh-process reload trials also
passed full-file and full-point round-trip checks on the actual robot.
Timing calibration, map geometry, loop closure, relocalization, real-sensor
realtime operation and physical navigation are not accepted.
See [the odometry limits](docs/SLAM_GLIM_OFFLINE.md) and
[the map persistence evidence](docs/SLAM_GLIM_MAPPING.md), and
[the isolated ARM64 target check](docs/SLAM_ARM64_TARGET.md), plus
[the actual-target recorded replays](docs/SLAM_ARM64_RECORDED.md), and
[the ARM64 map persistence limits](docs/SLAM_ARM64_MAPPING.md).
The eight existing maps were subsequently compared on exact common integer
header timestamps. The scan-end dynamic runs differ from their second submap;
the underlying cause and geometric acceptance remain open.
See [the read-only per-frame comparison](docs/SLAM_ARCHITECTURE_COMPARISON.md).
Twelve separate-process native preprocessing observations then showed
run-to-run sampled-point differences on the same x86 host with the original
two-thread settings; the single-thread diagnostic controls repeated exactly.
See [the repeatability limits](docs/SLAM_PREPROCESS_REPEATABILITY.md).
The current 0.1.19
runtime described below still uses native AIMDK interfaces and has not been
silently switched to an open-source backend. Existing release archives and APKs
were not rebuilt.

## Runtime shape

- The robot hosts the HTTPS console and API on port `3000`.
- A typed ROS 2 adapter runs on the robot and talks only to configured AIMDK
  interfaces. The browser never receives a general-purpose ROS publish/service
  endpoint.
- The console is React/TypeScript. Capacitor packages the same UI as a
  landscape Android app; only the robot address is retained on the client.
- The Java LinkSoul adapter and AI orchestration are separate from map/navigation
  management. Credentials are write-only through the authenticated HTTPS API
  and live in a robot-local mode-0600 file.

## Local development

```sh
npm ci
npm run dev:api
npm run dev
```

The development API binds to loopback and uses the explicitly simulated
adapter. It is not a robot connection. The simulator accepts the pairing code
`lingxi-dev-only`; production startup refuses to use it.

```sh
npm test
npm run build
```

## Robot runtime

The production API requires a robot TLS key/certificate, a one-time pairing
code hash, a restricted internal-agent token, and the local ROS 2 bridge. The
bridge is configured with the topic names found by `ros2 topic list` on the
target AIMDK image. Defaults are documented in `deploy/robot.env.example`;
readiness remains false until live sensor, SLAM, localization, and optional
navigation interfaces are observed.

The X2 mapping interface publishes `start_mapping` and
`stop_mapping:<map_name>` to `/integrated_command` using reliable,
transient-local QoS. Saving is not reported as successful until
`GetStoredMapByName` confirms it. The stop command itself requests save, so
the UI asks for a map name before stopping. Robot movement during map creation
is always controlled by an external remote.

The mapping screen renders live LaserScan/PointCloud2 samples as an XY point
cloud, not an occupancy grid. A separate authenticated WebSocket delivers up to
five latest frames per second. Timestamp-aligned TF supplies the common frame
and robot heading; missing TF explicitly falls back to sensor coordinates with
no accumulated trace. See `docs/LIDAR.md` for target configuration.

Native navigation is disabled unless the AIMDK optional navigation component
is available and its state topic is fresh. Only point-to-point targets,
pause/resume, and stop are exposed; no velocity, arbitrary joint, or raw ROS
controls are implemented here.

If a navigation pause/resume request cannot be confirmed, the task remains
faulted until an explicit stop is confirmed. Late arrival events cannot restart
tour narration, dispatch another point, or trigger idle return. This interlock
does not claim that an unconfirmed native navigation command has stopped.

AIMDK preset actions and expressions use fixed X2 enum allowlists. LinkCraft
resources are loaded from the robot's live resource service and execution
accepts only a short-lived opaque ID from that list. Preset motions require a
matching terminal state before a tour advances. AIMDK's LinkCraft response has
no playback-completion field, so the console records service acceptance only
and permits such a resource only at the last step of a single-pass tour. See
`docs/AIMDK_ACTIONS.md`.

## Security and delivery boundaries

- Import the robot's local CA certificate into desktop/mobile trust before
  pairing. The Android network security configuration trusts user-installed
  CAs, not arbitrary certificates.
- Pairing and session tokens are memory-only in the client. The robot address
  may be remembered. API keys and LinkSoul credentials are never returned,
  logged, or stored by the browser.
- Map assets remain in AIMDK's existing map directory. Migration copies
  configuration backups and references only; it never moves map files.
- The build output is not an acceptance claim for physical navigation,
  acoustic wake-up, cloud credentials, or the target robot's optional AIMDK
  components. See `docs/ACCEPTANCE.md`.

## Useful commands

```sh
npm run android:add
npm run android:sync
npm run android:build
npm run migrate -- --source-file /path/to/legacy-config.json --destination /var/lib/lingxi-next
```
