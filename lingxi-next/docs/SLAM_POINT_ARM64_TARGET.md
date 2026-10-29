# Actual ARM64 Point Extension Loading

Date: 2026-10-29. The previously prepared point extension has now loaded on
the identity-checked ARM64 robot, and all **11 native synthetic contract
scenarios exited normally**. This establishes target loading and isolated
native interface behavior only, not a recorded/live point channel, mapping,
navigation or product acceptance. The runtime and APK remain 0.1.19.

## Actual Target Result

`slam-point-arm64-target-evidence.json` contains allowlisted aggregate evidence
from the collected private reports. Two independent exports produced identical
bytes. It contains no robot address, home directory, credentials, sensor data,
XYZ, poses, session IDs or private log text.

The first successful run is retained with its coordinator-source snapshot
and independent exports. After adding a local output/input overlap guard,
a second fresh-directory run completed the same 11 scenarios. The public
evidence describes this second run only. Native artifacts and inputs were
unchanged; neither run processed sensor recordings.

| Observation | Result |
| --- | --- |
| Target | Linux aarch64, Python 3.10.12, glibc 2.35 |
| Uploaded files individually hashed | 5 |
| Actually mapped library paths/digests checked | 7 |
| Empty C ABI open / close | 1 / 1 |
| Empty C ABI callbacks / copied packets | 0 / 0 |
| Native contract scenarios completed | 11 |
| Contract exit codes | All 0 |
| Timeouts / forced termination / surviving contract processes | None |
| SSH connection after collection | Closed |

The unchanged native scenarios cover copy, overflow, invalid frame,
wrong native-owner thread, wrong API thread, invalid timestamp, invalid points,
invalid pose, missing points, skipped ID and closing with pending data. Their
inputs are isolated synthetic fixtures, not captured LiDAR/IMU data.
This is not the cross-build host's separate exported-symbol CTest.

The original six core libraries and the point extension were observed through
actual process mappings and verified by hash. Loading the CPU odometry module
does not create an odometry engine. The probe creates no ROS context, node,
subscription, capture, motion request or service installation.

## Collection And Verification

`tools/run_glim_point_target.py` requires fresh onsite stationary, supervision
and accessible emergency-stop confirmation before local input inspection or
SSH connection. It reuses the existing known-host identity check and
memory-only password prompt; it never accepts an unknown replacement key.

Before uploading, it checks the ordinary-user target identity, architecture,
Python/glibc, explicit preserved-core manifest digest, available disk and
private directory ownership. Both remote input and output directories must
be new. The five existing prepared files are uploaded without rebuilding,
then checked individually for exact size and SHA256 with restrictive modes.

Execution has a clean explicit environment, nice 10 and an external timeout.
The unchanged target probe owns each bounded native contract process.
Collection retains failed/partial results rather than rerunning or overwriting
them. Successful collection requires every expected private result, matching
download hashes, unchanged local inputs and the complete target report.

`tools/export_glim_point_target.py` independently verifies the closed
collection, current coordinator source, original manifest and upload hashes,
all downloaded files, actual library digests, empty ABI accounting, exact
scenario order, normal exits and false readiness boundaries. It exports only
named aggregate fields; private reports and logs remain outside Git.

The bundle manifest remains:

```text
06c021f7c12591f68b49ef4883636e3f58268d0b849b3f721bcf19663c168285
```

See `SLAM_POINT_ARM64_PREPARATION.md` for the unchanged archive identity and
preparation history.

## Local Checks

The 13 new coordinator/export tests use synthetic nonexecutable artifacts,
in-memory SSH peers and explicitly synthetic reports, never robot access.
They cover confirmation before reads/connection, preserving existing outputs,
path isolation, clean bounded commands, file limits/ownership, wrong core
identity before upload, changed downloads, retained failures, connection
cleanup, aggregate-only export and rejection of incorrect counts/digests,
forced exit, Boolean counter aliases and readiness claims.

Together with the previous probe tests, the focused suite has 29 tests;
Windows passes 28 and explicitly skips the Linux process test. These test
fixtures are not evidence that ARM64 native code executed. The actual target
result above comes from the separately collected device run.

Final full tools regression passes 677 tests on Linux with no skips, using
the existing numerical/geometry environment, clean pinned upstream checkout
and explicit Linux Node/consumer bundle. Windows passes 315 and explicitly
skips 362 for platform/dependency requirements. Frontend/API, Java and Android
sources are unchanged; their builds were not rerun or relabelled as new device
acceptance. The previous recorded x86 composition's 50 current source digests
also match its preserved parent and child reports without reexecuting native
SLAM.

## Next Integration

Read-only inspection found neither the expected product Node binary nor
`/usr/bin/node`. This was a two-path presence check, not an exhaustive search
or Node version/dependency validation. Do not substitute the cached Linux
x64 runtime for ARM64. The recorded target consumer needs its own explicitly
verified compatible ARM64 runtime and dependencies.

Next prepare the separate ARM64/Humble recorded point-channel binding using
the already verified decoder and worker, separate health/point channels and
explicit recorded classification. Its producer/consumer content comparison
and normal shutdown still need actual target execution.

Live supervision, web/Android point rendering, sensor-to-screen latency,
calibration/prediction warnings, global map transactions, loop closure,
relocalization, base alignment, independent navigation and final voice/product
acceptance remain outstanding. This milestone changes no frozen native core,
adapter, worker, stream or configuration, vendor SDK/service/map, installer,
release archive or APK.
