# Offline Nav2 Controller Integration

Verified 2026-10-31. This is an actual ROS Humble integration in a synthetic
planar world, not an X2 dynamics test and not permission to deploy or walk.

## Selection

- Global planner: Nav2 NavFn; controller: Regulated Pure Pursuit (RPP).
- Closed-loop Nav2 VelocitySmoother limits forward/yaw speed and acceleration.
  This first composition does not command reverse or lateral motion.
- DWB was evaluated first. Low-speed startup, oscillation rejection, local
  minima and final-heading failures were not repeatable enough for this
  fixture. One successful detour did not meet the repeatability criterion.
- Upstream reference: [Navigation2](https://github.com/ros-navigation/navigation2/tree/3c3db59d6969d8ecee8e68468693d006397f4a0c),
  package version 1.1.20. Installed Humble binaries also report 1.1.20; exact
  equivalence between those distribution binaries and the source commit has
  **not** been verified. No ROS package or vendor installation was changed
  on the robot.

## Execution and Independent Checks

The runner publishes synthetic occupancy, PointCloud2, TF, odometry and clock.
All four lifecycle nodes must be active before sending a goal. Commands flow
through the real controller and velocity smoother, then an epoch-owned observer
and a synthetic kinematic integrator. No physical transport exists in this
composition. Topics and TF frames belong to a fresh private generation.

The independent grader checks swept segment-to-obstacle distance, full envelope
support on the explicitly free fixture map, speed bounds, actual goal/heading,
and source retirement. It does not accept Nav2's success result by itself.
The synthetic circular envelope is 0.4 m including margin; it is not a measured
X2 profile. Goal tolerances are 0.15 m and 0.15 rad.

| Scene | Final result | Independent observation |
| --- | --- | --- |
| Open, goal (2, 0, 0) | Arrived | Position error 0.1464 m; 236 commands |
| Full wall at x=1 | Rejected | Stayed at start; zero nonzero commands |
| Box across direct path | Arrived by detour | Minimum swept obstacle distance 0.7228 m; 563 commands |
| Wall appears after x=0.25 | Stopped, action failed | Final x=0.3013 m; minimum distance 0.6987 m |

Two complete RPP rounds passed. The public evidence file records the final
round and its exact source/report hashes; the first round used an earlier
runner before final cleanup. Full local reports also retain plans, commands
and trajectories, but are not bundled with business data or workstation paths.

## Offline Reproduction

Use a separate Ubuntu 22.04 rootfs containing ROS Humble, rclpy, ros2launch,
nav2_bringup, nav2_regulated_pure_pursuit_controller and nav2_velocity_smoother.
Do not run this against a robot's host ROS graph. From a Linux project checkout:

```bash
sudo bash tools/nav2-offline-isolated.sh /absolute/separate/rootfs open /trial-open
sudo bash tools/nav2-offline-isolated.sh /absolute/separate/rootfs blocked /trial-blocked
sudo bash tools/nav2-offline-isolated.sh /absolute/separate/rootfs detour /trial-detour
sudo bash tools/nav2-offline-isolated.sh /absolute/separate/rootfs appearing-wall /trial-wall
```

The wrapper creates private mount/network/IPC namespaces and a loopback-only
network, mounts the checkout read-only at `/work`, and writes results inside
the separate rootfs. Output paths must be new for each run. The runner refuses
a network namespace with any interface other than loopback. No package download
or physical interface is part of the wrapper.

## Deliberate Limits and Next Integration

- Obstacles latch: marking is enabled; automatic ray/footprint clearing and
  clearing recovery trees are disabled. Clearing must preserve an independently
  verified ground-support mask before it can be introduced.
- Default obstacle Z is 0.1--1.4 m. The display remains point-cloud based; Nav2's
  internal costmap is not a substitute for the user's LiDAR map view.
- A ROS Twist has no source timestamp or task identity. The synthetic observer
  owns its subscription and drains/retires its generation, but this does **not**
  prove production source age. A physical handoff requires a source-stamped,
  bounded command path and separate stopping/dynamics acceptance.
- Runtime checks cover input/command expiry, unique task epochs, late callbacks,
  pause/resume ownership, and action-plus-velocity retirement. A retirement
  timeout stays faulted even after a late drain. JobManager halts instead of
  retrying/skipping when the backend requires an explicit stop.
- Product API/backend wiring, recorded localization plus occupancy integration,
  support-preserving dynamic clearing, ARM64 budgets, physical scale/clearance
  and ground support remain incomplete. `navigationReady` stays false.

Evidence: [nav2-offline-rpp-20261031.json](evidence/nav2-offline-rpp-20261031.json).
