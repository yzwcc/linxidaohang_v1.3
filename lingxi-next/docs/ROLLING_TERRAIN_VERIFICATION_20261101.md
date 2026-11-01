# Rolling Terrain Producer Verification

Date: 2026-11-01. Offline host: WSL Ubuntu x86_64, Python 3.12.3.
No robot connection, live sensors, deployment, cloud requests or physical
commands. The [source-bound receipt](ROLLING_TERRAIN_VERIFICATION_20261101.json)
records actual libraries, source hashes, recording identities and each output
window. This is not complete independent-stack or field acceptance.

## Implemented Product Component

`robot-navigation/lingxi_navigation/terrain.py` adds a bounded corrected-window
producer on top of the existing pinned
[Patchwork++](https://github.com/url-kaist/patchwork-plusplus) and
[OctoMap](https://github.com/OctoMap/octomap) adapters. It does not implement a
new ground-classification or collision-planning algorithm.

- Every update rebuilds both ground support and observed air clearance from
  the same complete corrected window. Previous coordinates, obstacles and
  supporting-floor labels are never unioned into a later correction.
- Free cells require both observed free air and repeatedly observed support.
  Clear air without floor support stays unknown. Prohibited ground or occupied
  air blocks the cell. No interpolation fills missing ground observations.
- A producer owns one map receipt/binding/geometry epoch. Processing sequence
  and map correction revision are distinct; replay window numbering never
  fabricates a new saved-map correction revision.
- Defaults limit a window to 32 scans, 65,536 selected returns and four seconds
  of acquisition span, with a two-second computation deadline and three-second
  latest-capture lifetime. Native work units are capped and the recorded runner
  honors its explicit `maximumWorkUnits` setting (16,000,000 in this replay).
  A failing update
  invalidates the cached output; an expired result is not returned as current.
- A corrected copy of the same last source capture retains the original ingress
  receipt time. Reprocessing cannot make old sensor input fresh. Late native
  results are not published, even if the native call eventually returns.
- The caller must put native work in a supervised process. The recorded runner
  does this with a whole-job wall timeout and CPU/address-space/file-size limits.
  The in-process deadline alone cannot interrupt a blocked C++ call.
- Saved raw points and per-return origins are unchanged. Display and clearance
  defaults remain Z 0.1--1.4 m; ground classification still uses preserved floor
  returns outside that display slice.

The output remains a `DeclaredTraversability`, not a metric `CostmapSnapshot`.
The explicit offline OccupancyGrid export preserves unknown cells as -1.
No physical command transport or live navigation consumer is instantiated.

## Actual Checks

The native run executed **23 tests, all passed, zero skipped**, with the actual
pinned Patchwork++ and OctoMap libraries. It verifies full coordinate replacement,
independent floor/air requirements, loss of old floor support, correction results
matching a fresh native rebuild, bounds, expiry, failed native updates, old
captures and late results. The native floor/air fixture explicitly uses a short
synthetic Z interval 0.5--0.7 m; it is not full-body clearance acceptance.
Separate contract tests preserve the default 0.1--1.4 m configuration.

Five supervisor/report tests also passed, including worker timeout/failure,
incomplete artifacts, incorrect source/correction identities, stale/late rows,
coverage accounting, and unchanged projection/budget requirements. Default
TypeScript regression: **511 passed / 16 skipped**, zero failures.
Final scientific Python discovery over `tools/tests`: **1,498 tests total,
1,438 passed / 60 skipped**, zero failures/errors, 256.019 seconds. Three of
those skips are the new actual-library tests, all separately executed in the
23-test native run above. The final full discovery followed the explicit
work-budget binding fix; its five affected supervisor tests and both recorded
replays also passed again. Frontend/API/runtime type checks and production
build passed, with the two existing non-fatal Zod annotation warnings.

## Actual Recorded Replay

Two supervised replays of the preserved static recording produced identical
window identities and complete output bytes. Both use the unchanged earlier
offline declaration, including uncalibrated +0.6 m Z translation. A declaration
does not establish physical scale, gravity or robot-to-sensor calibration.

| Measurement | Result |
| --- | --- |
| Recording scans / represented scans | 313 / 313 |
| Original / selected / omitted returns | 2,012,279 / 130,834 / 1,881,445 |
| Coalesced full-window outputs | 20 |
| Saved map correction revision | 3, unchanged in every window |
| Output artifact bytes | 327,680 per run |
| Host processing P50 / P95 / maximum | 0.3403 / 0.3606 / 0.3624 seconds |
| Whole-worker time, first / repeat | 19.428 / 19.689 seconds |
| Peak RSS, first / repeat | 179,084 / 179,880 KiB |
| Supported traversable cells | Zero in all 20 windows |

Zero free cells means the recording/declaration does not supply enough observed
ground plus clearance. It does not prove the real floor is physically blocked.
Neither thresholds nor clearance Z limits were relaxed to obtain a path.

The moving recording is rejected before output publication at scan 563:
declared sensor-height error 0.093196 m exceeds the unchanged 0.08 m tolerance;
origin span is 0.043026 m. No accepted report, pending report or partial terrain
binary was published. The source recording/map remains untouched.

This is unpaced recorded processing, not sensor-to-output latency. The offline
runner timestamps a decoded window when it hands it to the producer. A live
adapter must supply the original acquisition-ingress monotonic time. The P95
above excludes archive preparation, live input/registration, queueing, ROS and
ARM64 execution; it is unrelated to the acoustic <=2 s response target. The
16-scan replay stride is not a local obstacle-avoidance update-rate claim.

## Reproduce And Continue

Use the existing scientific Python environment, explicit recorded configuration
and a new private output directory:

```text
python tools/check_terrain_native.py --library GROUND_LIB --sha256 GROUND_SHA --build-report GROUND_BUILD_REPORT --octomap-library OCTOMAP_LIB --octomap-sha256 OCTOMAP_SHA --octomap-build-report OCTOMAP_BUILD_REPORT --output NEW_NATIVE_RECEIPT
python tools/evaluate_recorded_terrain.py --config RECORDED_CONFIG --output NEW_PRIVATE_OUTPUT
python tools/verify_recorded_terrain.py --static-config STATIC_CONFIG --moving-config MOVING_CONFIG --native-receipt NATIVE_RECEIPT --output NEW_PRIVATE_VERIFICATION
```

The [full-product workstream](OFFLINE_PRODUCT_ACCEPTANCE.md) remains active.
Next integration requires live registration/localization and corrected ray
windows, bounded worker ownership and transport, current metric/ground admission,
and an actual terrain-to-Nav2 controller run without substituting an occupancy
oracle. Robot-height/gravity/base calibration, moving trajectories, ground/drop
coverage, ARM64 resource limits, physical stopping and complete-stack packaging
remain required. This milestone does not regenerate an APK or release package.
