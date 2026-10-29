# Bounded Live Point Process Owner

Status on 2026-10-29: an opt-in owner composes the passive Humble worker,
frozen GLIM engine, corrected-point extension and separate inherited-socket
API. Only isolated synthetic local execution has been checked for this new
composition. It has not run on ARM64 or subscribed to actual robot sensors.
Default services, installers and the 0.1.19 APK remain unchanged.

## Ownership

`robot-runtime/python/live_points.py run` creates a mode-0700 directory and owns
one sensor child and one API process. It creates a private Unix socket before
starting the sensor, verifies its PID/UID with `SO_PEERCRED`, then passes the
accepted descriptor to the API with an explicit session and live input mode.
Point bytes and health JSON never share stdout.

The sensor reuses `ProcessSupervisor` and its separate process-group cleanup.
Point connection, API startup, runtime and shutdown have deadlines. Failures
end the session without automatic restart. The owner stops the sensor before
closing the API's private lifetime pipe; cleanup errors do not silently skip
the other peer. `owner-report.json` is mode 0600. Forced termination,
incomplete cleanup and nonzero peer exit cannot be reported as successful stop.

The API's optional `LINGXI_POINT_PARENT_STDIN=1` closes the application and
point input on parent EOF/error. The sensor control thread detects parent
EOF too, requests worker shutdown and exits unsuccessfully rather than
pretending to have received a valid stop acknowledgement. Parent-death tests
use synthetic sensors and the actual API, not native GLIM.

## Sensor Child

`live_child.py` reuses `run_passive` and `SensorWorker`. It adds no publishers,
motion commands, map activation or navigation controller. Native creation,
IMU insertion, point correction and closure stay on one non-main worker.
The control thread reads snapshots and the private stop pipe only.

Before native creation, the child checks existing live replay/core manifests,
DDS profile, diagnostic timing hypothesis and point-extension loading evidence.
Mapped libraries are checked against frozen ARM64 evidence, and input
manifests are rechecked after a completed session. These identity checks do
not establish calibration. Writer/worker failure ends the session.

The decoder still checks session, mode and freshness. Stale data and EOF
become unavailable; recorded input is not relabeled as live. The output is
an odometry point cloud, not a saved map or validated robot-base pose.

## Invocation Boundary

Production requires ordinary Linux AArch64, Python 3.10, explicit Node/API
SHA256 values, verified frozen bundles and production TLS/pairing settings.
Arbitrary environment variables and demo mode are not inherited.

This is an operator template, not a deployable package or completed robot
test. The later offline archive and read-only launcher are documented in
`SLAM_LIVE_RUNTIME_PREPARATION.md`; neither establishes live target acceptance.
Supply approved paths/hashes and private HTTPS configuration outside
source control. Do not put credentials in commands. Use a free isolated port
and fresh output directory, not an existing service. Actual sensor execution
requires fresh onsite stationary/supervision/emergency-stop confirmation;
automatic continuation is not confirmation.

```bash
rtk proxy /usr/bin/python3 -B robot-runtime/python/live_points.py run \
  --root "$VERIFIED_LIVE_BUNDLE" --core-root "$VERIFIED_CORE_BUNDLE" \
  --point-bundle "$VERIFIED_POINT_BUNDLE" \
  --point-manifest-sha256 "$POINT_MANIFEST_SHA256" \
  --hypothesis header_is_scan_start \
  --node "$VERIFIED_NODE" --node-sha256 "$NODE_SHA256" \
  --api-bundle "$POINT_API_BUNDLE" --api-sha256 "$POINT_API_SHA256" \
  --output "$NEW_PRIVATE_OUTPUT" --seconds 60 --operator-confirmed
```

Duration is 5 through 240 seconds, with separately bounded startup/cleanup.
Scan-start is an explicit diagnostic hypothesis, not accepted calibration.
The `child` subcommand is internal process composition, not a management API.

## Verification

The 21 new Python tests use fake workers, local pipes and mocked artifact
verification. They cover EOF/malformed control, sensor/writer/runner failure,
terminal health failure, cleanup exceptions, private permissions, bounded
status parsing, configuration and artifact mismatch rejection. Production
configuration tests mock the owner; they do not execute ARM64 code.

The Linux Node owner suite starts an explicitly synthetic Python sensor and
the actual inherited API. It checks paired WebSocket point delivery, normal
and duration-limited stop, either peer's sudden exit, abrupt parent death,
failure before connection, connection timeout and API startup failure.
Live-mode fixture packets are synthetic protocol tests only.

```bash
rtk proxy env LINGXI_POINT_API_BUNDLE="$POINT_API_BUNDLE" \
  LINGXI_POINT_OWNER_PYTHON="$PYTHON_LINUX" \
  "$NODE_LINUX" --test tools/check-point-runtime.mjs tools/check-live-point-owner.mjs
rtk proxy "$PYTHON_LINUX" -B -m unittest discover \
  -s tools/tests -p test_live_point_runtime.py
```

The process suites pass 18 tests. TypeScript passes 153 tests. Strict
frontend/API/runtime type checks and frontend/API/point-runtime builds pass.
The complete Linux tools regression passes all 748 tests with no skips.
Windows tools regression runs 748 tests: 370 pass and 378 are explicitly
skipped for unavailable platform/dependencies.

The first full Linux tools run failed: its environment lacked SciPy, and
Linux Git rejected CRLF files in the existing upstream cache as dirty.
The cache was not rewritten. A separate local LF checkout of the identical
pinned commit retains the original cleanliness guard. The final Linux run
uses the existing complete geometry environment and explicit recorded-consumer
runtime; this is not a rerun of the actual native or robot trials.
The new Python tests account for 21 of the 748 tools tests. The 18 Node process
tests are a separate suite, not part of that count.

## Not Yet Accepted

Mapping, localization and navigation readiness stay false. Process closure
alone does not prove point delivery or accuracy. Remaining work includes
independently packaged/verified ARM64 live execution, actual web/Android
rendering and latency, native failure cleanup on target, calibration/base
alignment, map persistence, localization and navigation. Long-running service
recovery, multi-client operation and voice concurrency are also unverified.

See `SLAM_POINT_RUNTIME.md` for the API boundary and
`SLAM_POINT_COMPOSITION_TARGET.md` for the separate completed **recorded**
ARM64 composition. Neither earlier acceptance proves this new live composition.
