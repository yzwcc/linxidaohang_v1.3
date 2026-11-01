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
| Occupancy | Pinned OctoMap acquisition-time rays and conservative 3D clearance projection; actual static/moving recorded GLIM archives evaluated with repeat identity and bounded failures | Offline declared-frame result is separate from metric navigation costmaps; physical calibration and ground-support evidence remain required | Physical scale, ground support, blind spots and real obstacle visibility |
| Local navigation | Real ROS Humble NavFn + RPP + closed-loop smoother passed six synthetic scenes; native OctoMap revisions preserve independent support; paired API/concurrent Web polling passed; DDS publication-age rejection verified in isolated Humble | Actual ground-support estimator, recorded metric localization/occupancy integration, controller-computation/physical-transport freshness and guarded AIMDK handoff | Walking dynamics, stopping distance, dynamic people/obstacles and emergency stop |
| Tours and tasks | Multi-point/round sequencing, one retry then skip, manual pause, dialogue quiet-resume, return policy; real offline Nav2 two-point tour/no-return and pause/resume epochs passed | Recorded backend integration, broader failure/retry/late-callback combinations | Actual arrival, interruption/resume and narration at each point |
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
