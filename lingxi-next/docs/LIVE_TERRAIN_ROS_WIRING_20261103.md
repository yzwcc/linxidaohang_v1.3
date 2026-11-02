# Passive Live Terrain ROS Output

Date: 2026-11-03. This adds an owned ROS output to the existing passive live
localization/acquisition composition. No robot was authenticated, subscribed,
deployed to or moved for this verification. It is not physical navigation
acceptance or a complete independent-stack release.

## Runtime Integration

The operator-only acquisition sidecar can explicitly add:

```json
{"rosOutput": {"mode": "private_declared_terrain"}}
```

This is an optional member of the existing complete sidecar, not a standalone
configuration. Verified `acquisition` and `terrain` selections are required.
Missing terrain, alternate modes and extra ROS settings are rejected. Existing
sidecars without this member keep their previous behavior. No topic name,
external ROS peer, command transport or physical-readiness override is exposed.
No production sidecar or calibration was generated.

`AcquisitionSettings` freezes the selected mode and hashes the added output,
terrain publisher and expiring-feed sources. The shared launcher/installer
already preserves the complete sidecar and its digest; no installer change is
needed to forward this member. Upgrade, rollback and preflight retain their
existing immutable-input checks.

`LiveTerrainRosOutput` creates a fresh `lingxi_offline_<32hex>` namespace in the
existing sensor ROS context and single-threaded executor. The sensor worker
keeps ownership of native acquisition; the bounded terrain child keeps its
existing ownership. Neither performs ROS work. The existing base-TF setup is
composed with the optional output, and base-only startup retains its original
setup path without importing navigation modules.

The unchanged `TerrainRosPublisher` emits full replacements on a steady 50-ms
timer. Before native initialization, after expiry and after source withdrawal,
the output is unknown rather than the previous terrain. Its private map frame
does not establish physical scale, base alignment or ground calibration.

Shutdown revokes the source before publishing the final unknown grid, then
destroys the timer/publisher, removes the node and destroys it on the ROS owner.
Every cleanup is attempted even if another fails. Revocation is latched before
calling the source, so even a failing callback cannot expose a late result.
Malformed replacement identity retires the output and latches a failure.

Terminal verification requires the explicit selection, configured terrain,
started and closed output, at least the initial/final publications, no failure,
and false physical-scale/ground-support/motion/navigation flags. Malformed
terminal payloads fail validation instead of raising an attribute error.
Publication counts are not subscriber receipts or physical-stop acknowledgements.

## Verification

Focused checks were run on the current sources, not a historical ARM64 archive:

- Windows output-owner and combined-setup tests: 12 passed, no skips.
- Ordinary-user Linux acquisition admission/lifecycle tests: 31 passed,
  no skips, 28.556 seconds. Native acquisition/terrain and sensor inputs are
  synthetic doubles in this suite.
- Ordinary-user Linux localization, registered acquisition/terrain, ownership,
  transport and shared deployment regression: 179 discovered, 168 passed,
  11 skipped, zero failures/errors, 30.762 seconds. Skips cover unsupplied
  preserved private reports, actual native terrain builds and Linux host Node.
  These are overlapping suites, not additive acceptance counts.
- After extending the extracted-source assertions, shared bundle tests were
  rerun separately: Linux 10 passed/one host-Node skip, Windows 10 passed/one
  scientific-dependency skip. The actual isolated Linux `python -I` check imports
  ten production modules, including the new output owner, only from a freshly
  extracted test package. Web/API/dependency assets are synthetic in that test;
  no native module or ARM64 executable is loaded. Windows runs the all-profile
  JavaScript/Python source-inventory parity check that skips on Linux.
- JavaScript source inventory checks: all six passed. `git diff --check` passed.
- The preceding unchanged Web/API baseline has 560 passed/17 skipped and passing
  frontend/API/runtime typechecks plus Vite build, as recorded in the portable
  workspace milestone. It was not rerun for these Python-only runtime edits.

Actual ROS tests use the installed isolated Humble runtime and the production
output owner/publisher. They use synthetic `TerrainGrid` bytes, not recorded
native terrain or robot sensors. All three passed in 1.461 seconds:

1. Expiry continues with paused ROS simulation time and publishes unknown.
2. Revocation reaches current and late subscribers before owned node teardown.
3. A mismatched-session grid retires the output, publishes unknown and cannot
   be revived by a late result.

Reproduce from `lingxi-next` with a prepared separate Humble rootfs:

```sh
rtk proxy python -B -m unittest discover -s tools/tests -p test_live_terrain_ros.py -v
rtk proxy python -B -m unittest discover -s tools/tests -p test_live_acquisition.py -q
rtk proxy sudo -n bash tools/nav2-offline-service-isolated.sh --verify-live-terrain-output ROOTFS open /fresh-live-output-check
```

The wrapper creates fresh mount/network/IPC/PID namespaces, read-only project
sources and loopback-only ROS. It mounts no robot devices or vendor sockets.
The new verification mode does not change ordinary service or preflight mode.
This check does not rerun Nav2 consumer acceptance; earlier registered-terrain
consumer trials remain separate evidence.

## Remaining Product Work

Actual ARM64 acquisition execution, passive sensor throughput, native ground
support under measured geometry and complete independent-stack deployment
remain outstanding. The preserved recorded diagnostic still produces zero
supported free cells with its unmeasured declarations. This integration does
not manufacture free space, install fitted plane hints or admit metric maps.

No new APK, complete-stack archive, physical transport, AgentSDK/cloud/audio
latency result or Android-device acceptance was produced here. Point-cloud UI
and the default Z interval of 0.1--1.4 m are unchanged; ROS occupancy is internal
planning data, not a replacement for the requested point-cloud display.

See [passive acquisition selection](LIVE_ACQUISITION_WIRING_20261103.md),
[registered ROS consumers](REGISTERED_TERRAIN_ROS_VERIFICATION_20261103.md),
[recorded terrain diagnostic](REGISTERED_TERRAIN_REPLAY_20261103.md) and the
[full-product workstream](OFFLINE_PRODUCT_ACCEPTANCE.md).
