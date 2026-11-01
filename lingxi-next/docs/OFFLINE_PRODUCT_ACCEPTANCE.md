# Full Product Offline Workstream

Updated 2026-11-01. This is the active scope for the independent open-source
route. Earlier vendor-only acceptance records in ACCEPTANCE.md are historical;
vendor SLAM licensing is not a prerequisite for this route. No robot connection
or deployment is authorized by completing an offline check.

| Workstream | Current implemented scope | Required offline integration | Separate field acceptance |
| --- | --- | --- | --- |
| Mapping | GLIM CPU full local/global mapping, corrected point-cloud preview, save/reload, per-return observation archive | Observed-map API save/discard passed on recorded input; moving multi-submap and overload regression remain required | Measured scale/heading, loop closure, long walking scan, ARM64 resource limits |
| Localization | Explicit seeded small-gicp tracking, pose freshness, map provenance, base projection | Replay tracking, bad seeds, loss/recovery, stale generation and current observed-map loading | Independent moving trajectory and ground-truth errors; global relocalization remains a separate capability |
| Global planning | Pinned native Nav2 NavFn over explicit costmaps | Unknown space, blocked goals, complete path identity and bounded worker | Map scale, robot clearance and actual route reachability |
| Occupancy and ground | Pinned OctoMap acquisition-time rays and Patchwork++ ground fusion; bounded corrected-window producer; repeated recorded replays and actual native terrain-to-Nav2 synthetic trials passed; moving declaration explicitly rejected | Wire supervised live registration/localization and terrain transport; declared-frame results remain separate from metric admission; current static recording has zero supported free cells | Physical scale, ground support, blind spots and real obstacle visibility |
| Local navigation | Real ROS Humble NavFn + RPP + closed-loop smoother passed six synthetic scenes; native OctoMap revisions preserve independent support; paired API/concurrent Web polling passed; DDS publication-age rejection verified in isolated Humble | Integrate recorded metric localization/occupancy/ground support, controller-computation/physical-transport freshness and guarded AIMDK handoff | Walking dynamics, stopping distance, dynamic people/obstacles and emergency stop |
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
