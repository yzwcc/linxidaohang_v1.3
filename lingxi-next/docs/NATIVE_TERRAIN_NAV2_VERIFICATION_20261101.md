# Native Terrain To Nav2 Verification

Date: 2026-11-01. Host: isolated ROS Humble in WSL x86_64. The
[source-bound receipt](evidence/native-terrain-nav2-20261101.json) regrades
preserved raw controller trajectories and complete ROS costmap samples.
These trials use synthetic acquisition rays, not recorded or live robot input.

## Verified Integration

Pinned Patchwork++ ground classification and OctoMap observed-ray clearance
now feed the real Humble NavFn, regulated pure pursuit controller and closed-loop
velocity smoother. Terrain is frozen before crossing the host/rootfs ABI
boundary. No secondary occupancy oracle, obstacle cloud or clearing layer fills
the native terrain. The verifier checks that every original unknown and occupied
cell stays blocked in every sampled ROS costmap, and checks the swept 0.4 m
envelope against the original supported cells.

All four trials use the full default clearance interval Z 0.1--1.4 m. Raw
acquisition returns, including floor returns outside the display slice, remain
available to the ground classifier.

| Synthetic scene | Result | Controller commands | Maximum absolute Y |
| --- | --- | --- | --- |
| Open route | Goal reached | 236 | <0.000001 m |
| Fully blocked route | No movement commands | 0 | 0 m |
| Obstacle detour | Goal reached with supported clearance | 847 | 1.06508 m |
| Unsupported shortcut | Avoided unsupported area; goal reached | 846 | 1.06452 m |

The existing six ROS baseline scenes and queued DDS publication rejection were
rerun against the changed runner. Their receipts are
[baseline](evidence/nav2-publication-age-20261101.json) and
[publication probe](evidence/humble-publication-probe-20261101.json).
The terrain fixture tests passed seven checks with actual native libraries and
zero skips; the evidence verifier passed three checks. Final scientific Python
discovery passed 1,445 tests with 63 skips (1,508 total), zero failures/errors,
in 234.582 seconds. TypeScript passed 511 tests with 16 skips; frontend/API/runtime
type checks and production build passed.

## Limits And Next Field Gate

This verifies actual native terrain-to-controller composition on synthetic
input. It does not establish physical scale, calibrated sensor/base/gravity
frames, real floor support, live computation/transport freshness, ARM64 resource
limits, physical stopping or walking. No physical commands were sent by these
trials. The historical static recording still produces zero supported free cells;
the moving recording still fails the unchanged height gate. These results do
not repair either recording or authorize autonomous navigation.

A separate, bounded read-only robot subscription on this date confirmed one
publisher each for cloud, IMU and leg odometry, approximately 10.003, 193.377 and
167.157 Hz, with no invalid/non-increasing timestamps or cloud parsing errors.
That check did not upload code, publish ROS commands or alter services. It is
connectivity evidence only, not calibration or stationary-state acceptance.

The next commissioning step is the isolated Web map display and static
localization workflow, followed by an operator-coordinated measured scan and
ground/frame validation. Live terrain transport, metric admission, guarded
AIMDK handoff, real voice/cloud latency and the complete independent-stack
package remain open. The current SLAM commissioning package is not that full
product.

## Commissioning Package Checks

The shared Web package now inventories the GLIM acquisition-observation sources
in both JavaScript and Python. It also binds the build-identity generator and
Capacitor configuration used by Vite, and admits only the exact emitted
`web/lingxi-build.json` metadata path, not arbitrary JSON assets. Six JavaScript
build-inventory tests and fifteen Python live-bundle tests passed after these
packaging corrections. The fresh ARM64 SLAM archive passed full source,
dependency, asset and archive checks before target upload. This is still a SLAM
commissioning archive, not independent navigation or complete-product acceptance.

## Isolated Robot Web Commissioning

The fresh archive was uploaded to a new private commissioning directory without
overwriting the vendor installation, existing services or original saved maps.
The target extraction and check-only verification passed. A transient service
runs as the ordinary robot user for at most four hours; individual native
mapping sessions remain bounded to 300 seconds. It does not depend on the SSH
connection staying open. The archive SHA-256 is
`5a9a2efc374620d1e6480d736d7c22ba3d79e78dee07e3d9a6bf6343219a2082`.

At 16:16 CST, the service was active and the paired HTTPS Web page at
`https://192.168.0.128:3445` exposed the mapping start control. The browser
verified the exact self-signed certificate in an isolated profile; the system
trust store was not changed. A copy of saved map 1.2 loaded 9,771 points, with
1,668 visible in the default Z 0.1--1.4 m slice. A canvas pixel check and visual
inspection confirmed a nonblank point-cloud view. This is display/reload
evidence, not a claim of calibrated scale or a new successful field scan.

No new mapping/localization session or physical movement was started during
this deployment. Next: operator-coordinated measured acquisition, then verify
scale, floor support, frame alignment and localization before any navigation
movement trial. The original map 1.2 and existing robot services stay intact.

## Reproduce

Use `tools/verify_terrain_nav2_offline.py` with explicit native libraries,
source-bound build reports, a new run ID and private output paths. The optional
baseline output reruns the six ROS scenes and publication probe. Linux root is
used only for the isolated rootfs namespaces; no robot IO exists in this runner.
