# Production Nav2 Map Handoff

Date: 2026-11-03. Source milestone after the existing 0.1.22 artifacts.
No robot connection, cloud call, physical movement, new APK or deployment.

## Product Behavior

The production physical navigation owner can now select another verified saved
map without restarting the navigation service. `LINGXI_ACTIVE_MAP_ID` supplies
only its startup map. A service restart still requires explicit stop and map
confirmation; the last selection is not silently restored as an active task.

The existing paired map activation API and private Unix protocol are reused.
The operator must stop navigation, select and seed the new map through the
localization workflow, and wait for tracking. If retiring the old localization
has revoked navigation, confirm navigation stop again before activating the
new navigation map. Activation does not start localization, infer a chest-LiDAR
seed from a planar pose, or submit a navigation goal.

The transition checks and retires the following in order:

1. Verify the target map receipt, points/profile binding, footprint and explicit
   Nav2 profile. The target must already have current matching localization.
   Missing tracking is a side-effect-free preflight rejection.
2. Reject active/reserved tasks. Close any unclaimed old prewarm, including its
   complete process group and ROS subscriptions, before releasing its lease.
3. Recheck candidate tracking and the old output's confirmed idle ownership.
   Retire the old localization reader, bind the selected identity/factory/guard,
   clear the old task, pose, readiness checks and stop receipt.
4. Publish a new zero command under a new local nonce and wait for a subsequent
   matching AIMDK source state. The old map/task's receipt cannot confirm it.
   The socket holds the activation response until this separate stop completes.
5. Only then acknowledge the selected map. A new unclaimed Nav2 epoch may warm
   up; actual navigation still requires all seven current admission checks and
   a separate new goal. No old goal, callback or velocity source is replayed.

Reconfirming the same map follows the same output handoff in the production
loader. This clears a finished task's output ticket before another stop or goal.
Partial cleanup, candidate loss after cleanup, or missing new stop evidence
leaves the operation unconfirmed and requires explicit recovery.

The API activation deadline is 7 seconds to cover bounded old-producer cleanup
followed by the unchanged 3-second retirement deadline. Sensor/command ages,
stop evidence, velocity limits and physical calibration gates are not relaxed.
The state schema now represents taskless `stopping` for initial stop and map
handoff, but rejects taskless running/paused/finished and quiescent stopping.

## Offline Verification

- Windows full Vitest: 707 passed, 24 environment skips, zero failures.
- Ordinary-user Linux full Vitest: 726 passed, five environment skips, zero
  failures. The skips remain three opt-in Nav2 integration cases, one selected
  Python-process fixture and one non-Linux permissions case.
- Windows and Linux frontend/API/runtime type checks and production Web builds
  passed. The Windows SLAM API bundle build passed.
- Linux focused producer/profile/localization-guard/DDS-reader/map-loader/release
  scope regression: 89 passed, zero skips or failures.
- Linux physical suites: 90 discovered, 75 passed, 15 opt-in Humble skips. Real
  private Unix tests include withholding a map activation reply until its new
  map-bound stop confirms. No complete Python-suite pass is claimed.
- The broad `test_nav2*.py` run also found two pre-existing historical source
  binding failures: the 2026-11-01 receipts name earlier `nav2_profile.py` and
  `humble_velocity_source.py` hashes. Both current files match unchanged Git
  HEAD bytes, so this milestone does not rewrite the receipts or claim those
  two checks passed. That run was 61 passed, one skip, two failures.
- A separate broad localization discovery found 132 cases, with 65 passed and
  67 explicit opt-in skips. Those skips are not localization acceptance.

Actual isolated ROS Humble/Nav2 trial `nav2_maps_20261103_a` passed in 18.775 s.
It executes real planner/controller/BT/smoother processes, the production saved
map loader/profile checks, new-map stop handoff, a new-map action with at least
ten nonzero in-memory commands, and same-map reconfirmation after old producer
exit. Localization input, terrain, odometry and AIMDK publication are synthetic.
The localization socket is not executed by this ROS trial; a current synthetic
snapshot is read by the real generation-bound guard and background monitor.

Trial `nav2_maps_20261103_b`, run concurrently with other host checks, failed
before the map-switch portion after localization authorization was revoked.
The output was stopped and the producer retired. Concurrency is an observation,
not an established cause. The 300 ms input age and all other deadlines remain
unchanged; a successful run does not establish that this intermittent issue is
resolved. Standalone trial `nav2_maps_20261103_c` passed the complete scenario
in 24.986 s without changing code, evidence ages or deadlines between attempts.

## Remaining Limits

This is not an end-to-end live GLIM/terrain/Nav2/AIMDK or ARM64 acceptance test.
Measured scale/gravity/base transforms and supporting ground are still required;
the preserved real recordings currently have zero supported free cells under
the declared, unmeasured geometry. Selecting a map cannot make those cells free.
Target loading/throughput, physical stopping distance, obstacle avoidance,
AgentSDK acoustic behavior and Android device acceptance remain open.
The point-cloud UI and default Z range 0.1-1.4 m are unchanged.
