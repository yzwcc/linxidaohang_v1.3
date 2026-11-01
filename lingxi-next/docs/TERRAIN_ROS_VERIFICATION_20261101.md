# Live Native Terrain ROS Verification

Date: 2026-11-01. Host: WSL Ubuntu x86_64; isolated Ubuntu 22.04 ROS Humble
rootfs, Python 3.10, g++ 11.4. This extends the
[supervised transport](TERRAIN_TRANSPORT_VERIFICATION_20261101.md) with an actual
ROS publisher and actual Nav2 consumers. "Live" here means a running native
process connected to ROS, not live robot sensors or physical navigation.

## Implemented

- `TerrainLane` creates and owns the native process on one dedicated thread.
  A single pending operation provides explicit backpressure; there is no queue
  or automatic restart. Snapshot reads do not wait for native computation.
  Accepted new work immediately hides the previous grid. Idle health polling,
  original monotonic expiry and explicit retirement withdraw stale results;
  a late computation cannot revive a retired source.
- `TerrainRosPublisher` samples the lane and expiring feed every 50 ms using
  `STEADY_TIME`, independently of ROS simulated time. It publishes reliable,
  transient-local, depth-one full `OccupancyGrid` replacements. Startup,
  pending work, failure, expiry and retirement publish unknown, not old free
  cells. The adapter is restricted to isolated offline namespaces and exposes
  no velocity interface or metric navigation admission.
- `probe_terrain_ros.py` connects the actual Patchwork++/OctoMap child through
  this publisher to actual Humble global/local costmaps, NavFn and RPP. The
  synthetic pose remains fixed; synthetic commands terminate at an observer,
  with no robot, sensor, actuator or physical bridge.
- Preserved reports contain source identities, pinned native build provenance,
  native grid bytes and raw before/after ROS samples. The independent verifier
  checks geometry, cell contents and every formerly supported cell, rather
  than trusting passing summary flags.

## Actual Trials

All three trials passed all **14 checks**. Their actual native input has
1,354 free cells, zero occupied cells and 2,486 unknown cells, with default
clearance Z 0.1--1.4 m. Native processing completed in 1.48--1.54 seconds on this
host while 29--30 map publications continued. This is not an ARM64 throughput
or end-to-end latency acceptance result.

| Scenario | Observed source withdrawal after expiry/trigger | Raw report |
| --- | --- | --- |
| Original TTL expires while ROS `/clock` is paused | 0.040531 s | [expiry](evidence/terrain-ros-expiry-20261101.json) |
| Actual native child receives SIGKILL, without another input | 0.019517 s | [death](evidence/terrain-ros-death-20261101.json) |
| Explicit source retirement, equivalent to map revocation | 0.020929 s | [revoke](evidence/terrain-ros-revoke-20261101.json) |

Each trial first obtains a real planned path and nonzero synthetic controller
output. After withdrawal, a newly attached DDS reader receives an unknown map;
both actual costmaps block all formerly supported cells. The planner rejects
the route and FollowPath aborts. After the documented 300 ms queued-command
drain, no nonzero output was observed. A map update is not a physical stop
acknowledgement; these single samples are not a stopping-time or P95 claim.
Only the expiry trial freezes ROS time; it resumes time for consumer processing.

Five lane tests cover thread ownership, nonblocking reads/backpressure, pending
retirement, idle expiry/child death, invalid ordering and terminal startup
failure. Eight evidence tests regrade all three preserved trials and reject
retained free cells, wrong geometry, altered bytes, source drift, invalid action
statuses, nonzero commands and unsupported physical acceptance flags.

Final focused Linux regression loaded the actual pinned native libraries and
passed **62 tests, zero failures/errors/skips** (16.508 s). Windows ran the same
62-test collection: **47 passed / 15 platform or native-library skips**, zero
failures/errors. Python compilation and shell syntax checks passed. No
TypeScript/UI source changed; its broader regression was not rerun here.

## Reproduction

Use a separate Humble rootfs, the pinned Patchwork++ source and verified Eigen
headers from the existing offline native build. Rootfs prerequisites include
`g++`, `git`, `python3-json5`, NumPy, ROS Humble and its existing Nav2 packages.
Host GCC 13 binaries are not substituted for rootfs-compatible builds.

```bash
sudo bash tools/terrain-ros-isolated.sh ROOTFS PATCHWORK_SOURCE EIGEN /new-trial expiry
# Repeat with distinct output directories and modes death and revoke.
python3 tools/verify_terrain_ros_evidence.py docs/evidence/terrain-ros-*-20261101.json
python3 -m unittest discover -s tools/tests -p 'test_terrain*.py' -v
```

The wrapper creates separate mount/network/IPC namespaces with loopback only,
binds project/source/headers read-only, and compiles native libraries inside
the rootfs under the trial's private cache. It does not contact the robot or
change AIMDK/vendor services. Each invocation requires a new output directory.

## Remaining Acceptance

This closes the previously open actual ROS timer and idle-expiry propagation
check, not the full product. Continuous corrected acquisition-window input,
real localization ownership, physical scale/floor/gravity/base calibration,
ARM64 composition and resource limits remain required. Existing field map 1.3
is preserved; its approximate walked distance is not metric calibration.

The adapter remains offline-only. Real transport freshness, independent command
fencing, stopping/obstacle tests and AgentSDK/cloud/acoustic acceptance are not
inferred from these trials. No robot connection, deployment, APK rebuild or
complete-stack package was produced in this milestone.
