# Portable Offline Nav2 Workspace Verification

Date: 2026-11-03. Scope: offline commissioning entrypoint and process ownership,
not an independent-stack robot deployment or physical-navigation acceptance.

## Implemented

- One shared launch plan supports Windows/WSL and ordinary-user Linux, with
  caller-independent checkout/assets/state paths and fresh random output IDs.
- Configuration validation precedes any child construction. A bounded isolated
  preflight verifies namespace tools, Humble imports and seven Nav2 packages.
- Actual backend scene confirmation precedes the loopback HTTP listener.
  Startup errors and interrupted construction retire owned services. Repeated
  API/backend close calls share the same close operation; pending/queued work
  is rejected and closure must be observed, not inferred from a kill request.
- The API stays ordinary-user on Linux. Only the namespace wrapper uses existing
  `sudo -n` authorization. No permission changes, installations or sudoers rules
  are introduced. Production privilege-broker provisioning remains separate.
- Entrypoint/backend environment allowlisting and a clean chroot environment
  exclude robot/cloud configuration and host ROS peers. The ROS graph remains
  private, loopback-only, with no physical transport or vendor adapter.

## Verification

- Final TypeScript regression: 560 passed, 17 optional-environment skips;
  35 files passed, two skipped, in 22.61 seconds. The three real Nav2 tests
  are included in those skips and were separately opted in as recorded below.
- Frontend, API and runtime typechecks plus production Vite build passed.
  Existing dependency annotation warnings are not build failures.
- Thirty-one new launch-plan/lifecycle checks cover both platforms, invalid
  rootfs/port/scene, environment filtering, preflight/construction/scene/listen
  failures, shared close hooks and interruptions. Two backend close regressions
  cover pending/queued requests and spawn failure.
- Focused Python regression: all 33 tests passed, zero skips, across
  `test_offline_nav2_service.py` (5), `test_nav2_profile.py` (17) and
  `test_nav2_trial.py` (11). The full Python suite was not rerun for this scope.
- Final explicitly opted-in Windows/WSL real Humble/Nav2 run: all three tests
  passed in 71.62 seconds. These exercise occupied-port cleanup, fresh HTTP
  launch, pairing/state read, repeated shutdown, task/tour/voice epochs and
  actual detour with concurrent point-cloud/state polling.
- One earlier concurrent build/full-regression/real-Nav2 run failed the detour
  with `retirement_timeout`. Its output was preserved, not overwritten. A
  standalone detour rerun passed in 33.71 seconds, followed by the complete
  three-test passing run. No deadlines or acceptance assertions were relaxed.
  This is not a concurrent-load reliability or P95 claim.
- Native Linux Node 22 entry was exercised as an ordinary user: missing existing
  noninteractive sudo permission fails preflight and exits nonzero. Running the
  API as root is explicitly refused. Successful native Linux namespace startup
  remains unverified because the workstation has no approved sudo setup; policy
  was not changed to obtain a passing result. Matching Linux esbuild used for
  this entry check lives only in ignored local tooling, not project dependencies.

## Remaining Product Work

Recorded LiDAR/registration/terrain replay still yields zero supported free
cells under the current declared geometry. Metric/gravity/ground/base
calibration and actual ARM64 acquisition execution remain open. This milestone
does not add physical command transport, complete independent-stack deployment,
real AgentSDK/cloud/audio acceptance or Android device acceptance. It does not
change the default 0.1--1.4 m point-cloud band or mark navigation ready.

The final read-only LAN check could establish TCP/22 but received EOF before an
SSH banner or identity exchange; the selected route still used the workstation
TUN interface. No authenticated robot session, sensor subscription, upload,
deployment or movement occurred. Routing/proxy settings were not changed.
`physicalMotionSent=false`, `navigationReady=false`, and the core-only release
scope remain unchanged. See [workspace operation](OFFLINE_NAVIGATION_WORKSPACE.md)
and [full-product workstream](OFFLINE_PRODUCT_ACCEPTANCE.md).
