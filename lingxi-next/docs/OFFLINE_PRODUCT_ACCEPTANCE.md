# Full Product Offline Workstream

Updated 2026-11-03. This is the active scope for the independent open-source
route. Earlier vendor-only acceptance records in ACCEPTANCE.md are historical;
vendor SLAM licensing is not a prerequisite for this route. No robot connection
or deployment is authorized by completing an offline check.

| Workstream | Current implemented scope | Required offline integration | Separate field acceptance |
| --- | --- | --- | --- |
| Mapping | GLIM CPU full local/global mapping, corrected point-cloud preview, save/reload, per-return observation archive, bounded corrected-history windows and immediate per-frame acquisition export | Observed-map API save/discard, two-submap moving replay and actual recorded immediate-ray export passed; original receipt/same-frame registration and bounded terrain-source composition passed offline; production live wiring remains required | Measured scale/heading, loop closure, long walking scan, ARM64 resource limits |
| Localization | Explicit seeded small-gicp tracking, original-ingress pose freshness, generation-bound callbacks, same-frame acquisition registration, map provenance, base projection | Replay tracking, bad seeds, loss/recovery, stale generation, current observed-map loading and registration/retirement tests; combined real GLIM/terrain replay remains required | Independent moving trajectory and ground-truth errors; global relocalization remains a separate capability |
| Global planning | Pinned native Nav2 NavFn over explicit costmaps | Unknown space, blocked goals, complete path identity and bounded worker | Map scale, robot clearance and actual route reachability |
| Occupancy and ground | Pinned OctoMap acquisition-time rays and Patchwork++ fusion; actual GLIM/registration/terrain 1x recorded diagnostic fits unchanged default work limits by retaining whole scans; current-source native/registered ROS trials pass | Diagnose zero supported free cells with calibrated geometry; wire production live factories; validate ARM64 timing/overload; declared-frame results remain separate from metric admission | Physical scale, ground support, blind spots and real obstacle visibility |
| Local navigation | Real ROS Humble NavFn + RPP + closed-loop smoother passed six synthetic scenes; native OctoMap revisions preserve independent support; registered localization pause/stop/reseed/loss revoke actual costmaps; paired API/Web polling and DDS publication-age rejection passed | Integrate recorded metric localization/occupancy/ground support, controller-computation/physical-transport freshness and guarded AIMDK handoff; concurrent startup remains unaccepted | Walking dynamics, stopping distance, dynamic people/obstacles and emergency stop |
| Tours and tasks | Multi-point/round sequencing, one retry then skip, manual pause, dialogue quiet-resume, return policy; correlated narration-stop barriers and late-callback tests; real offline Nav2 two-point tour, voice pause/resume and Q&A/no-return passed | Recorded backend integration and actual AgentSDK completion/stop correlation | Actual arrival, interruption/resume and narration at each point |
| Voice and AI | AgentSDK passive-event gating, deduplication, allowlists, evidence-bound retrieval, timeout/streaming; cloud cancellation, failed-playback stop barriers and superseded-turn regressions passed | Real AgentSDK playback correlation, live cloud matrix and end-to-end latency; offline workspace deliberately stubs playback and disables cloud | 100-wake/8-hour negatives, zero forbidden execution, acoustic P95 first response <=2 s |
| Web and Android | Shared Chinese React console, point-cloud map inspection, landscape Capacitor project; real Nav2 offline point-cloud workspace; source-bound signed APK 0.1.19/code20 verified | Desktop/landscape workflow and connection-loss regressions; APK current-source checks passed, not device acceptance | Real CA trust, Android devices, LAN reconnection and point picking |
| Deployment | Isolated services, restricted credentials, migration/backup; core-service install/rollback failure recovery and concurrent-install fencing verified in isolated Linux | Complete independent-stack composition and dependency/license audit; current verified core archive explicitly excludes GLIM/localization/OctoMap/Nav2 | Real ARM64 installation with existing vendor system preserved |

## Release Rules

- Keep default point-cloud display and clearance Z limits at 0.1--1.4 m. Do not
  discard saved raw points to implement a display filter.
- The user drives mapping with the external remote. Browser/APK mapping controls
  never send walking commands. Autonomous navigation uses a separate owned path.
- Do not label planning, stopped synthetic commands or mock ROS tests as working
  physical navigation. Explicitly retain unaccepted flags until field checks pass.
- Never substitute map point clouds for ray-based free-space evidence, infer
  physical scale from approximate walked distance, or assume a point-free cell
  is traversable ground.
- Work through the full product offline, not only mapping. Request the robot
  only for a bounded field test after the relevant offline integration passes.
- The [2026-11-01 verification record](OFFLINE_WORKSPACE_VERIFICATION_20261101.md)
  preserves the earlier six failing checks and their subsequent reconciliation.
  The current scientific Python run passed 1372 tests with 51 skips. Historical
  report integrity is separate from current target acceptance: 15 changed source
  files still fail `audit_historical_evidence.py --require-current` (exit 2).
  Passing offline regressions does not waive current ARM64 or field acceptance.
- [Recorded clearance evidence](RECORDED_CLEARANCE_VERIFICATION_20261101.json)
  covers actual archived rays, not merged point-cloud visibility. Selected-ray
  counts and omitted returns remain explicit; no navigation acceptance is granted.
- [Voice failure verification](VOICE_FAILURE_VERIFICATION_20261101.md) records
  offline cancellation and playback tests. The full TypeScript suite passed
  498 tests with 16 skips; this is not a substitute for real cloud or acoustic tests.
- [Android verification](ANDROID_SOURCE_VERIFICATION_20261101.md) records a
  signed landscape commissioning APK, exact compiled version and web/native
  source identities. Updated TypeScript tests: 502 passed / 16 skipped;
  Android verifier tests: four passed. No Android device or robot was contacted.
- [Core deployment verification](DEPLOYMENT_RECOVERY_VERIFICATION_20261101.md)
  records real filesystem/certificate/backup failure injection and the actual
  ARM64 archive audit. Stubbed systemd/health checks are not target execution;
  this core package is explicitly not the independent-stack product.
- [Support-preserving navigation verification](SUPPORT_FUSION_VERIFICATION_20261101.md)
  records two new actual ROS controller scenes and native OctoMap revision tests.
  All four original ROS scenes were rerun against current sources; the new
  six-scene receipt is exported from preserved reports and regrades raw samples.
  The ROS scenes use explicitly synthetic occupancy/support, not an end-to-end
  real-sensor producer. Unknown air is not evidence of supporting ground.
- [Publication freshness verification](PUBLICATION_FRESHNESS_VERIFICATION_20261101.md)
  records a real 250-ms queued DDS publication rejection, source-generation
  retirement, all six actual ROS scene reruns and two passing product API
  integrations. DDS write time is not controller computation time or physical
  command age; this reader remains restricted to the verified offline runtime.
  Final scientific Python regression: 1390 passed / 51 skipped (1441 total),
  zero failures/errors. TypeScript: 502 passed / 16 skipped; build passed.
- [Ground-support verification](PATCHWORK_GROUND_VERIFICATION_20261101.md)
  records 21 actual Patchwork++/OctoMap native fixture tests with zero skips,
  identical repeated static replay, zero supported free cells, and moving-frame
  rejection without accepted/partial artifacts. Ground support is an offline
  candidate producer, not a released metric costmap. Latest full scientific
  Python run: 1407 passed / 57 skipped (1464 total), zero failures/errors;
  six subsequently added build/evidence-verifier tests also passed. The six
  native-ground skips in that full run passed separately with actual libraries.
  TypeScript: 502 passed / 16 skipped; typecheck and production build passed.
- [Tour/voice verification](TOUR_VOICE_VERIFICATION_20261101.md) records
  correlated stop/replay/failure retention, three paired product API dialogue
  scenarios and two passing actual isolated Nav2 integrations. The latter
  include voice-controlled manual pause held for 5.3 seconds, Q&A preserving
  navigation, new epoch on resume, ordered visits and no tour auto-return.
  Latest TypeScript regression: 511 passed / 16 skipped; production build
  passed. Speech is stubbed in the real-ROS tests; no robot, cloud, acoustic
  latency, new APK or complete-stack package acceptance is implied.
- [Rolling terrain verification](ROLLING_TERRAIN_VERIFICATION_20261101.md)
  records 23 passing actual native tests with zero skips, five supervisor checks,
  two identical 20-window static replays covering all 313 source scans and moving
  frame rejection with no partial terrain publication. Host native-pipeline
  processing P95 was 0.3606 seconds, not a real-time or acoustic latency claim.
  All 20 static windows have zero supported traversable cells; metric admission,
  live worker/registration, actual terrain-to-Nav2 control and ARM64 integration
  remain required. No robot connection, APK build or deployment occurred.
  Final current scientific Python regression: 1,438 passed / 60 skipped
  (1,498 total), zero failures/errors. The three new actual-library skips passed
  separately in the 23-test native run. TypeScript: 511 passed / 16 skipped;
  frontend/API/runtime checks and production build passed.
- [Native terrain-to-Nav2 verification](NATIVE_TERRAIN_NAV2_VERIFICATION_20261101.md)
  records four passing actual native/ROS scenes using synthetic rays and default
  Z 0.1--1.4 m. Every sampled costmap preserves native unknown/occupied cells;
  there is no secondary occupancy oracle. All six baseline ROS scenes and the
  queued-publication probe were rerun. Final scientific Python regression:
  1,445 passed / 63 skipped (1,508 total), zero failures/errors. Seven native
  fixture checks passed separately with actual libraries and zero skips;
  three evidence checks passed. TypeScript: 511 passed / 16 skipped; build passed.
  A separate read-only target subscription confirmed current sensor connectivity.
  Live terrain transport, metric admission, ARM64 composition and physical
  navigation remain unaccepted. Per current user steering, prioritize bounded
  commissioning checks over non-critical offline polish.
- [Native terrain transport verification](TERRAIN_TRANSPORT_VERIFICATION_20261101.md)
  adds a bounded persistent child, parent-death cleanup and ROS-neutral full-grid
  expiry/revocation. All 39 actual native tests and five supervisor checks passed.
  Repeated source-bound recorded replay remains separate from live input or
  metric acceptance. The ROS publisher/timer, actual idle-expiry propagation,
  continuous corrected acquisition windows and live owner integration remain
  open; a neutral feed is not a completed ROS adapter.
- [Live native terrain ROS verification](TERRAIN_ROS_VERIFICATION_20261101.md)
  closes the preceding ROS publisher/timer and actual idle-expiry propagation
  checks. Three isolated actual-native Humble trials passed all 14 checks for
  expiry with paused ROS time, native child death and explicit revocation.
  Both actual costmaps revoked every formerly supported cell; late DDS readers
  received unknown, planning failed and the controller aborted. Single host
  source-withdrawal samples were 0.0195--0.0406 seconds, not physical stopping
  or ARM64 P95 results. Five lane tests and eight raw-evidence tests accompany
  the trials. Final focused Linux regression passed all 62 tests with actual
  native libraries and zero skips; Windows passed 47 with 15 expected skips.
  Continuous corrected acquisition windows, real localization,
  metric admission, ARM64 composition and physical navigation remain open.
  No robot contact, new deployment, APK or complete-stack release occurred.
- [Continuous observation-window verification](OBSERVATION_WINDOW_VERIFICATION_20261101.md)
  adds revision-coherent bounded corrected-history reads during mapping. All
  54 observation tests passed with the actual native library and zero skips.
  Moving recorded replay saved/reloaded 1,190 mapping scans across two submaps;
  a window read before save matched the final archive's source identities.
  Current ARM64 artifacts were cross-built and independently verified, not
  target-executed. Full scientific Python regression: 1,379 passed / 168
  optional-environment skips (1,547 total), zero failures/errors. These delayed
  corrected windows have no fabricated receipt time and remain non-navigation
  input. Raw low-latency receipt ownership, localization and terrain integration,
  metric admission, ARM64 runtime and physical acceptance remain open.
- [Localization ingress-receipt verification](LOCALIZATION_RECEIPT_VERIFICATION_20261101.md)
  preserves the original callback receipt through worker queues, IMU waiting
  and native computation, and rejects callbacks from retired generations.
  All 177 focused tests passed with zero skips, including 16 new receipt tests
  and an actual worker-thread composition with synthetic native/registrar peers.
  This is not acquisition-ray export or live terrain integration. No robot
  connection, deployment, APK or physical-localization acceptance occurred.
  Full Python regression: 1565 discovered, 1498 passed, 67 skipped, no failures.
  Seven immutable historical reports passed frozen-Git source verification;
  current-source drift remains explicit and is not new ARM64 acceptance.
- [Immediate acquisition-export verification](ACQUISITION_OUTPUT_VERIFICATION_20261101.md)
  adds a same-owner, single-slot per-frame export before submap finalization.
  All 29 actual native synthetic contracts and 28 focused Python checks passed
  with zero skips. Actual preserved-journal replay delivered all 164 eligible
  contiguous frames, covering 1,246,242 preprocessed returns with per-return
  interpolated origins and endpoints; zero pending frames were discarded.
  Geometry is still in odometry, with no fabricated receipt or metric claim.
  The static replay retains its IMU/extrinsic quality warnings. Original receipt,
  localization generation, saved-map registration and live terrain composition
  remain open, as do ARM64 runtime and physical navigation acceptance.
- [Registered acquisition and terrain verification](REGISTERED_TERRAIN_VERIFICATION_20261103.md)
  pairs immediate acquisition with the original scan receipt and same-frame
  localization generation, transforms both ray ends into saved-map raw units,
  and composes bounded declared-frame windows with the supervised terrain lane.
  All 37 new tests and 212 focused regression tests passed with actual terrain
  libraries and zero skips. This includes a complete synthetic callback chain
  and a real Patchwork++/OctoMap child after synthetic session registration.
  Full Python regression: 1,630 discovered, 1,562 passed, 68 optional-environment
  skips, zero failures/errors; skipped checks are not acceptance evidence.
  Pause, stop, reseed, loss, expiry and altered result identity cannot revive
  old terrain. Production live wiring, combined real GLIM/terrain replay,
  registered-source ROS consumption, metric admission, ARM64 execution and
  physical navigation remain open. No robot contact or deployment occurred.
- [Registered terrain ROS verification](REGISTERED_TERRAIN_ROS_VERIFICATION_20261103.md)
  composes the registered source with the actual native terrain child, steady
  ROS publisher and actual Humble costmaps/NavFn/RPP. Four final synthetic-input
  trials pass all 14 common checks and registration regrading: pause, stop,
  reseed and localization loss withdraw old free cells, stop synthetic controller
  progress and prevent revival by a newer tracking generation. Busy/starting
  lanes now retain raw pairs without repeatedly materializing declared geometry.
  Final focused scientific Linux regression passed all 222 tests in 38.531
  seconds with actual native libraries and zero skips; Windows passed all nine
  registered ROS evidence tests. The full Python suite was not rerun for this
  milestone; the preceding full-suite result is historical, not a new result.
  Final source-withdrawal samples are 0.0108--0.0479 seconds, not physical-stop
  or P95 measurements. One concurrent startup service timeout remains preserved;
  standalone retry passed without relaxed deadlines. Combined real GLIM replay,
  production live wiring, default-budget timing, ARM64 and field acceptance
  remain open. No robot contact, UI change or deployment occurred.
- [Actual recorded registered-terrain replay](REGISTERED_TERRAIN_REPLAY_20261103.md)
  completes all 4,144 messages with real GLIM, corrected/acquisition extensions,
  small-gicp registration and native terrain. A preserved default-budget failure
  led to whole-scan projection-work bounds, not relaxed budgets or partial rays.
  The current diagnostic observes 18 fresh terrain results from 19 submissions;
  the final in-flight job is explicitly revoked at stop. All observed supported
  free counts remain zero, so this is not navigability or independent accuracy
  acceptance. Four actual-ROS synthetic-input consumer trials were regenerated
  against the current sources. Final focused Linux regression: 246 passed,
  zero skips/failures; Windows: nine current registered ROS evidence tests pass.
  Production live wiring, calibrated support/clearance, ARM64 and field checks
  remain open. No robot connection, UI change, APK or deployment occurred.
- [Read-only acquisition ground inspection](ACQUISITION_GROUND_INSPECTION_20261103.md)
  repeats real native segmentation in two bounded independent children on the
  preserved first/latest samples. Both contain zero native ground labels in
  the unchanged declared floor band and mixed above-sensor native ground labels.
  Lower-surface plane fits converge but fail residual consistency; no candidate
  height/tilt is installed or accepted as physical calibration. Complete numeric
  imports and source identity are checked. This narrows the ground-evidence
  failure, not air-clearance, full registered-window or field acceptance. Final
  focused Linux regression: 258 passed, zero skips/failures, actual native enabled.
- [ARM64 per-return acquisition component](ACQUISITION_ARM64_COMPONENT_20261103.md)
  compiles and links the current host-tested adapter and fixture using the
  existing pinned Jammy core. Exact five-function exports and all 29 configured
  target contracts are checked without target execution. A separate component
  manifest binds source, library and core identities before native loading.
  Acquisition Python regression: 47 passed; existing actual x86 native contracts:
  29 passed; Windows component checks: seven passed. Live factory wiring,
  ARM64 execution, calibration and physical acceptance remain open.
- [Passive live acquisition wiring](LIVE_ACQUISITION_WIRING_20261103.md)
  adds explicit local sidecar selection to managed/on-demand live localization.
  Acquisition hooks stay on the sensor owner; optional declared terrain uses a
  supervised child. Pause, stop, reseed and loss retire old results even without
  another callback, and terminal checks account for native callbacks and cleanup.
  New wiring tests: 25 passed; acquisition regression: 89 passed, no skips.
  Full Python regression: 1,706 discovered, 1,637 passed, 69 skipped,
  zero failures/errors in 413.646 seconds; skipped checks are not acceptance.
  Web/API regression: 511 passed, 16 skipped; production build passed.
  These synthetic lifecycle tests do not execute ARM64 code or certify terrain
  support. Shared-package/installer forwarding, target loading and throughput,
  metric/gravity/ground/base calibration and field acceptance remain open.
  No robot connection, sensor subscription, motion or deployment occurred.
