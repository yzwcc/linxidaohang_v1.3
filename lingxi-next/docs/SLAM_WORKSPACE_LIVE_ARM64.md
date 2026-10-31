# Shared Workspace Live ARM64 Check

Date: 2026-10-31. The r8 shared SLAM package passed a passive live lifecycle on
the actual ordinary-user AArch64 target using the isolated Python environment.
This verifies the packaged management path, not physical map accuracy, seeded
localization, walking, obstacle avoidance or final product acceptance.

## Package

- Archive: `artifacts/lingxi-next-shared-slam-20261031-r8-arm64.tar.gz`.
- SHA256: `d431eebf42ebfe9a7b227af8ebb50d4251d9588a2221f2bca71f6e331b36e446`.
- Manifest: `193af333dd40567135613eeab1eee565bcdab47769e2e4da3298c17fef8c9a7b`.
- 54,242,746 bytes, 219 source files, 7,795 package files and 95 pinned
  production packages. ARM64 Node remains 22.23.4.
- Target: Python 3.10.12; isolated NumPy 1.26.4, SciPy 1.11.4,
  small-gicp 1.0.1, PyYAML 6.0.2 and json5 0.12.1. No system-site inheritance.
- Native libraries, sensor profile and isolated Python remain external runtime
  requirements. The archive does not contain those installed libraries, maps,
  recordings, robot configuration, pairing values, certificates or secrets.

See [sanitized evidence](slam-workspace-live-arm64-evidence.json),
[Python requirements](SLAM_PYTHON_RUNTIME.md) and
[deployment boundaries](SLAM_WORKSPACE_DEPLOYMENT.md).
The earlier [r7 recorded-input check](SLAM_WORKSPACE_ARM64.md) is retained,
including its version guard; r8 is not a rerun of that recorded-input contract.

## Observed Lifecycle

1. The archive and every extracted member were verified. The checker executed
   the extracted `launch_slam_bundle.py` with the selected isolated Python,
   packaged API, web assets and Node, not a separately built development API.
2. Two owner launches used a private persistent catalog and loopback HTTPS on
   port 3444. Pairing, the HTML index and both referenced assets were checked.
   This is HTTP asset delivery evidence, not browser rendering or LAN TLS trust.
3. Through the paired management API, live mapping processed 120 scans and
   estimated 89 poses before save. Native finalization saved 90 frames, one
   submap and 2,732 points. The schema-2 live receipt confirmed fresh-process
   reload; its digest matched the map-preview API.
4. After stopping and restarting the extracted management process, the same
   map and receipt remained available through the catalog and point preview.
5. With that real saved map selected, localization start was rejected while
   mapping was active. After discarding the second mapping session,
   localization started; a simultaneous mapping start was rejected.
6. Localization processed 44 scans and estimated 13 odometry poses. Five
   increasing reference frames were observed. No seed was sent: the state
   remained unseeded and did not publish a map-relative navigation pose.
7. Pause and stop revoked admission immediately. A stale command generation
   was rejected; ten subsequent queries remained stopped. Explicit runtime
   shutdown completed, then mapping could start and be discarded again.
8. Native creation and close each occurred once during localization. All 44
   timestamped TF lookups were available; pending input and TF caches were
   cleared. The scale value 1.0 was diagnostic, not a measured calibration.
9. Both management owners and their API/service children exited with code 0,
   no forced termination and no remaining children. An independent final SSH
   observation found zero trial processes, closed test ports and the original
   port-3000 console still listening. No service unit or release was switched.

## Repeating the Check

`tools/check_shared_slam_live_package.py` is opt-in and runs on an ordinary-user
Linux AArch64 Python 3.10 environment. Execute its packaged copy with explicit
`--bundle`, `--manifest-sha256`, `--config`, `--output` and `--port`. Use a new,
private, short absolute output directory; Unix-domain socket length is bounded.
The sensor configuration must be verified, external to the package and use
`inputMode: live`. Port 3000 is rejected. Nothing is installed by this checker.

The checker uses passive ROS subscriptions and the native mapping/localization
owner, never a walking interface. It verifies and rechecks package/config/native
inputs, only saves after bounded live progress, keeps output outside frozen
inputs and attempts orderly shutdown on failure. Private reports and sensor
geometry stay in ignored commissioning storage, not Git or the package.

Focused local verification: 57 Python tests and 82 API tests passed, together
with TypeScript checks and the production build. Another 29 localization-service
unit tests were skipped on Windows because they require the isolated Linux
environment; those skipped tests are not reported as passing. The actual ARM64
package lifecycle above is separate evidence, not a replacement for those tests.

## Remaining Acceptance

Measured scale, gravity, heading, physical base-to-lidar calibration, scan timing
semantics and current-site seed binding remain open. Seeded live localization,
walking/avoidance, voice latency, Android landscape operation, LAN certificate
trust and installation/upgrade of this live successor have not been accepted.
The r8 archive is a commissioning successor, not the complete deployment product.
