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
| Local navigation | Real ROS Humble NavFn + RPP + closed-loop smoother passed four synthetic scenes; paired product API/backend and concurrent Web polling passed | Recorded localization/occupancy, support-preserving clearing, source-stamped command freshness and guarded AIMDK handoff | Walking dynamics, stopping distance, dynamic people/obstacles and emergency stop |
| Tours and tasks | Multi-point/round sequencing, one retry then skip, manual pause, dialogue quiet-resume, return policy; real offline Nav2 two-point tour/no-return and pause/resume epochs passed | Recorded backend integration, broader failure/retry/late-callback combinations | Actual arrival, interruption/resume and narration at each point |
| Voice and AI | AgentSDK passive-event gating, deduplication, allowlists, evidence-bound retrieval, timeout/streaming; awakened voice dispatch/stop and Q&A preserving real offline navigation passed | Full playback correlation, cloud-failure matrix and end-to-end latency; offline workspace deliberately stubs playback and disables cloud | 100-wake/8-hour negatives, zero forbidden execution, acoustic P95 first response <=2 s |
| Web and Android | Shared Chinese React console, point-cloud map inspection, landscape Capacitor project; real Nav2 offline point-cloud workspace | Desktop/landscape workflow, connection loss, current APK build and package-source consistency | Real CA trust, Android devices, LAN reconnection and point picking |
| Deployment | Isolated services, restricted credentials, migration/backup/rollback scripts | Complete independent-stack manifest, dependency/license audit, clean-install and failed-upgrade rollback | Real ARM64 installation with existing vendor system preserved |

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
  includes six unresolved broad Python checks. Passing focused navigation
  tests does not waive source-bound evidence or clean-source release checks.
- [Recorded clearance evidence](RECORDED_CLEARANCE_VERIFICATION_20261101.json)
  covers actual archived rays, not merged point-cloud visibility. Selected-ray
  counts and omitted returns remain explicit; no navigation acceptance is granted.
