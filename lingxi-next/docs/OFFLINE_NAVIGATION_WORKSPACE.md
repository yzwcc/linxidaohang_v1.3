# Offline Navigation Workspace

Updated 2026-11-01. This workspace exercises the product rather than a separate
navigation demo: paired HTTP API, WebSocket point cloud, point management,
task manager, voice routing and tours share the real isolated Nav2 backend.
Inputs are synthetic and no physical command transport is instantiated.

## Start On Windows With WSL

Prerequisites: project Node dependencies and a separate Ubuntu 22.04 rootfs with
ROS Humble/Nav2 as described in [controller reproduction](NAV2_OFFLINE_CONTROLLER.md).
The launcher does not install these dependencies or modify a robot.

```powershell
$env:LINGXI_OFFLINE_NAV2_ROOTFS = '/absolute/separate/rootfs'
$env:LINGXI_OFFLINE_NAV2_CASE = 'detour'
$env:LINGXI_OFFLINE_PORT = '3018'
npm run build
npm run dev:offline-navigation
```

Open `http://127.0.0.1:3018/` and pair using the development-only pairing code
`lingxi-dev-only`. The listener is loopback-only, not a LAN robot deployment.
Select an unused port when necessary. Available scenes: `open`, `blocked`,
`detour`, `appearing-wall`. Each launch creates a fresh private state directory
under `.local/` and isolated ROS output inside the rootfs.

Create a point on the synthetic map at X=2, Y=0, heading=0, disable idle return,
then start point navigation. The detour scene contains a box across the direct
path. The Web view shows synthetic obstacle points and moving pose, not a
simulated scan of a real venue. Default display Z is 0.1--1.4 m.
Mapping, map switching, preset motion, expressions and Linkcraft execution are
denied here. Map/point/tour management operates only on this private workspace.

## Boundaries

- WSL process communication is bounded newline JSON over stdio, not TCP to a robot.
- Requests bind map hashes, session, monotonic state sequence and task epoch.
  Uncertain replies are not replayed; navigation requires an explicit stop.
- Pause/stop must confirm output retirement. Resume creates a new epoch.
  An old callback cannot re-enable a retired task.
- ROS runs in private mount/network/IPC namespaces with only loopback, and the
  project is mounted read-only. The API never creates the AIMDK adapter,
  cloud HTTP client or live Agent speaker in this mode.
- Speech output is a test stub. Cloud calls are disabled. Voice integration
  tests inject authenticated passive events; they do not establish wake-word
  acoustics, knowledge-cloud connectivity or <=2 s audible feedback.
- Obstacles latch; automatic dynamic clearing is intentionally not enabled.
  Synthetic pose/free-space/ground assumptions must not enter real map admission.
- `physicalMotionSent=false` and `navigationReady=false` remain mandatory.

## Checks

```powershell
npm test
$env:LINGXI_OFFLINE_NAV2_ROOTFS = '/absolute/separate/rootfs'
npx vitest run robot-api/tests/offline-nav2-integration.test.ts
```

The opt-in test uses actual ROS Humble Nav2. It covers product API pause/resume,
frozen paused pose, new epoch on resume, awakened voice dispatch and stop, a
knowledge question that preserves navigation, a two-point tour without return,
voice pause/resume during a two-point tour, no manual auto-resume after 5.3
seconds, and a real detour with concurrent state/point-cloud polling. Default Vitest
runs skip tests requiring this explicitly provided external environment.
Controller scene receipts are separate from these product API tests and from
future physical acceptance. Full-product gaps are tracked in
[OFFLINE_PRODUCT_ACCEPTANCE.md](OFFLINE_PRODUCT_ACCEPTANCE.md).
The dated [verification record](OFFLINE_WORKSPACE_VERIFICATION_20261101.md)
preserves the earlier six broad Python failures and their subsequent
reconciliation; see the active full-product workstream for later test counts.
The [tour/voice verification](TOUR_VOICE_VERIFICATION_20261101.md) records the
latest full TypeScript regression and actual isolated Nav2/API checks, with
explicit stubbed-speech and physical-acceptance limits.
