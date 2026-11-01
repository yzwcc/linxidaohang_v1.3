# Offline Tour and Voice Lifecycle Verification

Date: 2026-11-01. No robot connection, sensor subscription, deployment,
physical commands, cloud requests or acoustic playback. The full product
remains in offline development, not field acceptance.

## Implementation

Tour narration now retains its correlated playback until completion or a
confirmed stop. Wake interruption, manual pause and explicit stop share an
in-flight stop request. A successful stop can retire a playback promise whose
completion callback never arrives. A late completion or rejection cannot
advance another point, replay speech or affect a new task.

Resume waits for the pending stop. A failed or missing cancellation adapter
keeps the playback ID and a visible fault requiring explicit stop. An ambiguous
narration failure stops subsequent points/rounds instead of silently proceeding.
Explicit stop retries the same playback ID. Five quiet seconds are required
after dialogue, and manual pause never automatically resumes.

The upstream reference reviewed for task lifecycle was the Apache-2.0
[Nav2 Humble Simple Commander](https://github.com/ros-navigation/navigation2/blob/humble/nav2_simple_commander/nav2_simple_commander/robot_navigator.py):
action acceptance, cancellation and terminal result are distinct states.
No upstream business code was copied into the task manager. Cancellation
acknowledgement alone is not physical stopping acceptance.

## Checks

- Task-manager tests: 35 passed. New cases cover slow/failed/missing speech
  stop, absent old completion callbacks, retained speech IDs, late callbacks
  and shared cancellation across wake/pause/stop.
- Paired API plus passive voice tests: three passed, with controlled adapters
  and no cloud or physical I/O. They verify correlated stop before Q&A,
  replay after five quiet seconds, manual pause, rejected late completion,
  failed-stop recovery and two-point completion without automatic return.
- Full default TypeScript suite: **511 passed, 16 skipped**, 33 passing files
  and two skipped files. Skips are not passes and retain their external
  environment requirements.
- Frontend, API and runtime TypeScript checks and Vite production build passed.
  Two existing Zod annotation warnings are non-fatal.
- Explicit isolated ROS Humble Nav2 integration: **two passed, zero skipped**.
  These use actual NavFn/RPP/smoother, the paired product API and synthetic
  inputs in a separate network/IPC/mount namespace. Agent events are injected
  and speech is stubbed. They are not robot or cloud tests.

The actual Nav2 product workflow verified point navigation, ordinary Q&A
without navigation cancellation, pause retirement, new epoch on resume,
voice navigation dispatch and explicit stop. The two-point tour additionally
verified Q&A preserving the navigation epoch, voice pause, no pose/output
progress over 5.3 seconds, voice resume creating a new epoch, ordered visits
and no tour auto-return. It observed three tour epochs: two visits plus one
resume. The detour test passed with concurrent scene/state polling, 563
controller outputs, final synthetic pose approximately (1.9852, 0.1415,
-0.1236), and maximum lateral displacement 1.0453 m. These are synthetic
scene values, not physical accuracy measurements.

The first explicit ROS run passed the detour but failed an existing exact
pose-equality assertion: repeated zero-Twist angle normalization changed yaw
by about 1e-21. The revised assertion requires unchanged controller-output
count, unchanged epoch and confirmed output retirement, with only 1e-12
floating-point pose tolerance. The complete explicit run then passed both
tests. Both raw reports remain in the private local workspace; the sanitized
[receipt](evidence/tour-voice-nav2-20261101.json) binds their hashes and tested
source files. Neither a skipped environment invocation nor the earlier
failed run is counted as a successful real-ROS integration.

## Reproduce

From `lingxi-next/`, with the existing separate Humble rootfs:

```text
npm test
npm run build
node node_modules/cross-env/src/bin/cross-env.js LINGXI_OFFLINE_NAV2_ROOTFS=/absolute/separate/rootfs node node_modules/vitest/vitest.mjs run robot-api/tests/offline-nav2-integration.test.ts
```

The rootfs must be on the local WSL host. The test does not create an AIMDK
adapter or a physical command transport. No robot address is used.

## Remaining Product Work

Continue the [full-product offline workstream](OFFLINE_PRODUCT_ACCEPTANCE.md),
especially recorded mapping/localization/observed-ground composition,
bounded incremental costmaps, guarded physical-transport integration and
complete independent-stack packaging. Real AgentSDK stop/playback timing,
cloud service behavior, wake acoustics, P95 audible latency <=2 s and physical
walking/obstacle performance require separate acceptance. This change does
not regenerate an APK or claim that the existing commissioning archive is
the complete independent-stack delivery.
