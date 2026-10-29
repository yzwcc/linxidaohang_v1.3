# Opt-In Inherited Point API

Status on 2026-10-29: a separate management API entrypoint can adopt an already
inherited Linux point-stream socket. The default API, installers and APK remain
unchanged at 0.1.19. This is not a production SLAM supervisor or a navigation
backend, and this entrypoint has not been installed on the robot.

## Ownership And Configuration

`robot-runtime/src/main.ts` calls the existing paired management API with an
owned `SlamPointPipeSource`. It requires all three explicit settings:

| Setting | Required Value |
| --- | --- |
| `LINGXI_SLAM_POINT_FD` | Canonical integer 3 through 1023 |
| `LINGXI_SLAM_SESSION` | 32 lowercase hexadecimal characters from the owner |
| `LINGXI_SLAM_INPUT_MODE` | `live`, `recorded` or `synthetic`, with no default |

Only a descriptor that the Linux kernel identifies as a socket is adopted.
The descriptor must already be inherited from the local owner. A filename,
FIFO, stdin, stdout, health stream or public point-upload endpoint is not a
replacement. The entrypoint does not open a listener for producers or launch,
restart or supervise a SLAM process.

The existing decoder still checks packet/session/mode identity, bounded
payloads, finite geometry and monotonic freshness. Only fresh `live` snapshots
can reach the authenticated LiDAR WebSocket. Recorded and synthetic packets
remain unavailable as live data. Malformed input, expiration or EOF invalidate
display data without falling back to demo/vendor LiDAR or elevating readiness.
The points have a world-LiDAR frame, not a validated robot-base pose.

The adopted input closes on API-construction failure and application shutdown.
SIGINT and SIGTERM close the application normally; point bytes never use API
stdout. Startup emits one small listening-port JSON event, not point payloads.
Failures print a fixed diagnostic without dumping environment values.

Non-demo startup still requires the existing TLS key/certificate and pairing
configuration. Demo mode binds only loopback. Existing data/static directories,
host/port configuration and authenticated HTTP/WebSocket routes are reused.
No unauthenticated point endpoint or alternate robot control path is added.

## Build And Test

Use the locked project dependencies:

```bash
rtk npm run typecheck:runtime
rtk npm run build:point-runtime
rtk npm test
```

The optional real-process check requires Linux, an explicit Node runtime and
`LINGXI_POINT_API_BUNDLE` pointing to the generated `main.mjs`. For example,
when using the verified Linux Node 22 control:

```bash
rtk proxy env LINGXI_POINT_API_BUNDLE="$POINT_API_BUNDLE" \
  "$NODE_LINUX" --test tools/check-point-runtime.mjs
```

Seven Vitest tests check explicit configuration, platform/descriptor rejection,
paired delivery without readiness elevation, recorded/synthetic isolation,
owned-input closure and API-construction failure. Eight additional actual
Linux x64 Node 22.23.4 process tests exercise an inherited FD, real loopback
HTTP pairing and WebSocket, stale/EOF invalidation, normal signal shutdown,
malformed input and invalid startup. All use explicitly isolated synthetic
fixtures; none start native SLAM, ROS, speech or a robot connection.

The first process run failed: the harness expected pairing HTTP 200 instead
of the existing 201 contract, and its five-second failure-startup bound included
cold imports from a Windows-mounted dependency tree. The corrected harness
keeps startup and shutdown limits separate and uses a new Linux-local generated
bundle with the existing dependency tree. This does not establish robot startup
latency. The final complete TypeScript suite has 153 passing tests. Strict
frontend/API/runtime type checks and frontend/API/point-runtime builds pass.
The tools regression passes 727 tests on Linux with no skips and 364 on
Windows with 363 explicit platform/dependency skips. No Java, APK or installer
build is claimed.

## Frozen Evidence And Remaining Work

The wrapper lives outside `robot-api/src` deliberately. The previously prepared
ARM64 recorded consumer inventories every TypeScript file in that source tree.
Those sources, the lockfile and the frozen consumer are unchanged; the new
entrypoint is not retroactively part of its evidence.

The separate actual ARM64 recorded composition is documented in
`SLAM_POINT_COMPOSITION_TARGET.md`. Its acceptance does not establish this new
entrypoint's ARM64 execution, live DDS-to-screen delivery, display latency,
sensor calibration, base alignment, map persistence, localization or navigation.
Production parent ownership, sensor supervision, real web/Android rendering
and end-to-end physical acceptance still need implementation and verification.
