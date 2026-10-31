# Observed-Ray Occupancy For Independent Navigation

Updated 2026-10-31. This component connects real OctoMap occupancy computation
to the independent Nav2 global planner. It is not live navigation acceptance.
The mapping Web view remains a laser point cloud, not a grid image.

The user's default Z interval is 0.1 to 1.4 metres. `ClearanceProjection`
uses those metric defaults when no height override is supplied. Map preview
and native-coordinate mapping display start with Z slicing enabled and numeric
bounds 0.1 / 1.4, including after changing or reopening a map. Display controls
remain adjustable and can be disabled; source points are not deleted. Until
map scale and the ground reference are validated, the UI keeps its existing
raw/unvalidated coordinate label: these defaults do not certify ground-relative
height or physical calibration.

## Contract And Algorithm

`ObservationBatch` requires individually recorded LiDAR return origins and
endpoints in the saved map's metric frame, ordered scan boundaries, source
digests, map/receipt identity, a pose revision, and a metric-evidence digest.
It does not accept a display `PointPacket`, fused cloud or arbitrary occupancy
image. Declaring evidence is not physical calibration or readiness approval.

The adapter links the robot's existing system OctoMap 1.9.7, not its separate
ROS OctoMap 1.9.8 package. `octomap.lock.json` fixes the two actual ARM64 library
hashes. Loading verifies the adapter hash, ABI, version and the dependencies
actually mapped into the process; a conflicting ROS library is rejected.

- Use upstream `computeUpdate` for ray traversal, maximum-range clipping and
  hit computation, then upstream probabilistic `updateNode` updates.
- Aggregate by scan, with occupied cells winning over free traversals from
  other returns in that scan. Clipped returns create no artificial hit.
- Distinguish unobserved, observed free and occupied voxels. A projected column
  is free only if every voxel in its explicitly chosen vertical interval is
  known free. Any occupied voxel blocks it; unknown height is not clearance.
- Emit Nav2 costs 0 / 254 / 255 with a lower-left, voxel-aligned metric origin.
  A newly built volume replaces, rather than accumulates on, prior revisions.
  Corrected poses cannot leave old free space in a persistent stale volume.
- Bound scans, returns, per-scan cells, tree nodes, projection volume and
  cell-based work. Build off-side and return no partial output on exhaustion.

This does not yet validate supporting ground, stairs/drop-offs, the robot's
footprint, body height, inflation, live obstacles, moving localization or stop
semantics. The native operation still needs process supervision before live
product integration. `navigation_ready` remains false.

## Field Map 1.2 Is Not A Ray Archive

Inspection of pinned GLIM explains why the merged saved cloud is insufficient:

- `EstimationFrame::raw_frame` holds preprocessed LiDAR-frame points and point
  time offsets. The corrected display packet instead contains sampled,
  deskewed world points plus a scan-reference LiDAR pose.
- A deskewed endpoint and one scan-reference pose do not establish the sensor's
  acquisition-time origin for each return while the sensor moves.
- `SubMapping::insert_frame` produces a smoothed IMU-rate trajectory but drops
  point data from older non-keyframes. `SubMap::save` writes fused points and
  frame/trajectory metadata, not the original per-frame return archive.
- Submap-global corrections update `T_world_origin`. Any future observation
  export must retain submap-local acquisition geometry and apply that same
  corrected origin to both ray ends before rebuilding occupancy.

Do not raycast map 1.2's fused points from A, the map center, or successive saved
frame poses. Do not promote blank display space to free space. The existing
map and its receipt remain unchanged. A separate source-backed observation
extension now passes offline recorded save/reload checks; see
[SLAM_OBSERVATION_ARCHIVE.md](SLAM_OBSERVATION_ARCHIVE.md). ARM64 execution,
live package wiring and metric registration remain unfinished. Do not request
another field scan until the actual product producer is ready.

## Reproduction

`tools/prepare_octomap.py --output NEW_PRIVATE_DIRECTORY` freezes current
adapter/test sources and records their digests. `tools/run_octomap_target.py`
requires an explicit robot host, known identity, ordinary user, new private
target/output directories, the preparation manifest digest and the previously
verified NavFn binary path/digest. It does not install packages, create ROS
nodes, subscribe to sensors, modify services or open an actuator interface.

The real ARM64 tests exercise return origins, free/hit/unknown distinctions,
scan conflict precedence, maximum range, unobserved and overhanging height,
negative origins, probabilistic clearing, pose-revision replacement, bounded
failure and actual dependency identity. An integration test sends the generated
costmap to the actual NavFn library and checks unknown goals stay blocked.
All test observations are explicitly isolated synthetic data, not field scans.

## Actual ARM64 Verification

The final source preparation and adapter were built and tested on the robot's
aarch64 / Python 3.10.12 environment. All 19 occupancy/contract/integration tests
passed with zero failures, errors or skips. Three additional local preparation
tests passed. On Windows, the 13 native occupancy tests correctly skip rather
than pretend to execute the ARM64 library. The existing NavFn source/contract
regressions also passed locally, with their eight native tests explicitly skipped.

- Frozen source manifest SHA-256:
  `0f3eec2cd236d30bd52c4d7e0d145eb6cf8a293f0e4136bf93e37029257ebd47`.
- Actual OctoMap adapter binary SHA-256:
  `1298beff580611f88b332446d488bc11a992569e5afcc0d4dfd820a1c0be4347`.
- Actual NavFn binary used in the integration test:
  `1a1ff8ac11fb251e125de2cdc5a80864d80b0dfafad0d43145a54118275552e1`.

The compiler emitted no warnings. These tests do not measure field accuracy,
real-map traversability, or product latency. Raw logs and binaries remain in
private local/target work directories, not Git. No packages were installed,
services changed, sensor subscriptions created, or motion commands sent.
The system packages remain runtime dependencies, not a frozen delivery image.

Upstream: <https://github.com/OctoMap/octomap/tree/v1.9.7> and
<https://github.com/ros-navigation/navigation2>.
