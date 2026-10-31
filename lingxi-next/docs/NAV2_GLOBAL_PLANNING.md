# Independent Nav2 Global Planning

Updated 2026-10-31. This is a reusable global-planning component, not a complete
navigation stack and not a replacement for the active mapping Web service.

## Implementation

- Use unmodified NavFn source from Navigation2 Humble commit
  `3c3db59d6969d8ecee8e68468693d006397f4a0c`, with both source hashes recorded
  in `robot-navigation/navfn.lock.json`. The small C ABI calls the real upstream
  Dijkstra potential propagation and path extraction; it does not reimplement
  global planning. Upstream source notices are retained during preparation.
- A planning call owns a fresh NavFn instance. An explicit Nav2 uint8 costmap
  snapshot binds map ID, receipt digest, revision, dimensions, metric resolution,
  lower-left origin and all cost bytes. The adapter will not reinterpret an
  occupancy-grid encoding or raw chest-LiDAR coordinates as that costmap.
- Unknown, inscribed and lethal cells remain blocked. Start and goal must be
  valid interior cells. Work and output sizes are bounded. Exhausted path
  extraction returns no path, not a partial route or a previous success.
- Results preserve the costmap identity and declared coordinate frame. Native
  loading checks the binary hash, ABI and pinned upstream commit. Importing or
  running this component creates no ROS nodes and opens no actuator interfaces.
  Before connection to live tasks, native planning must have process supervision.

The component does not create free space from point-cloud gaps. Its caller must
provide a current, correctly encoded costmap with validated scale, ground and
body-envelope handling. A global path alone does not validate footprint clearance,
dynamic-obstacle avoidance, local control, stopping, or walking feasibility.

## Actual ARM64 Check

The sources were uploaded with per-file digest checks, compiled against the
robot's existing Humble rclcpp 16.0.14 in a new private user directory, and run
on aarch64 / Python 3.10.12. All 13 native/contract tests passed with zero skips.
They cover direction and world/grid conversion, a wall detour, solid and unknown
barriers, inscribed obstacles, lower-cost routing, a blocked goal after a prior
success, same-cell goals, bounded path exhaustion, owner and binary identity.

- Source preparation manifest SHA-256:
  `f43d4681c22add3bf2fff1afd01192ebce0943d6a33604619f02a3f8fdf1532c`.
- Actual tested shared-library SHA-256:
  `1a1ff8ac11fb251e125de2cdc5a80864d80b0dfafad0d43145a54118275552e1`.
- Input classification: isolated synthetic costmaps. No real map, sensor
  subscription, motion command, service change or vendor-file modification.
  Local source-preparation checks also passed; Windows correctly skipped native
  tests instead of claiming to have executed the ARM64 library.

The target's existing runtime dependencies were reused, not frozen into a final
delivery image. Raw build logs, the collected binary and target reports remain
outside Git. The active Web runtime and saved map 1.2 were not restarted or changed.

## Remaining Integration

The product's business adapter still uses the optional vendor PNC route. This
component is not yet connected to its navigation start/control endpoints.
Independent navigation still requires trustworthy occupied/free/unknown map
construction, live local costmaps and avoidance, moving localization, measured
scale and body geometry, lifecycle/task cancellation, and supported AIMDK
walking-control ownership and stop semantics. The inspected saved GLIM map has
fused submap points and poses; these do not by themselves establish the original
per-beam visibility needed to mark free space. Do not raycast the entire merged
cloud from a fabricated single sensor origin. The Web remains point-cloud based;
internal planning costmaps do not change that display requirement.

`navigation_ready` remains false in every global-plan result. This milestone is
native global-planner compatibility, not field navigation or product acceptance.

## Reproduction

Obtain the two files at the exact commit in the lock file, retaining their names
and license headers, then run `tools/prepare_navfn.py --upstream DIRECTORY
--output NEW_PRIVATE_DIRECTORY`. Preparation verifies both hashes before copying.
Use `tools/run_navfn_target.py` with an explicit host, known identity, ordinary
user, source-manifest hash and new private output to repeat the target check.
No root installation, container, vendor replacement or service restart is needed.

Upstream: <https://github.com/ros-navigation/navigation2/tree/3c3db59d6969d8ecee8e68468693d006397f4a0c/nav2_navfn_planner>.
