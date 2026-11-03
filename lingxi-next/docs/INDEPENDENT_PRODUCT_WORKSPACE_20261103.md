# Independent Product Workspace

Updated 2026-11-03. This composes the owned GLIM mapping/localization service
with the existing paired product API, persistent settings and passive Agent
voice pipeline. It is not a complete robot release or physical navigation
acceptance. No robot was contacted or changed for this milestone.

## Runtime Composition

- Default shared SLAM startup remains commissioning-only. It still rejects
  business writes, Agent callbacks and all robot control.
- An explicit hash-bound external product configuration opts the same managed
  runtime into `independentProduct`. Web modules share one HTTPS origin and
  pairing session: overview, point-cloud mapping, saved maps, localization,
  navigation status, tour records and settings.
- Product state and write-only cloud credentials live in a separate private
  persistent directory, not the per-start owner output, map catalog or release.
  Restart, upgrade, rollback and commissioning-only selection do not copy or
  remove that directory.
- Passive voice uses the existing real-wake/session/deduplication gate,
  evidence-bound Retriever/LLM path and correlated playback/stop interface.
  Agent HTTP is explicitly loopback-only. Its bearer token is read from a
  separate private file; it is not embedded in the configuration or package.
- No full AIMDK adapter is constructed. Vendor maps, vendor navigation and
  physical robot actions are not admitted by this mode. Without an explicit `navigationSocket`,
  point navigation, tour execution and map activation remain unavailable.
  Selecting a separately supervised ROS 2/Nav2 bridge connects the existing
  evidence-bound navigation adapter; it does not make its readiness gates true.
  Without an explicit `actionsBridgeEndpoint`, action confirmation remains
  unavailable. The optional actions-only bridge permits allowlisted expressions
  and operator confirmation, not physical actions. Voice cannot bypass these limits.
- Saved SLAM maps remain receipt-verified `native-raw` records with
  `navigationReady: false` in the catalog. Only an explicitly selected bridge
  can activate a map through its separate evidence contract; saving a map or
  connecting a socket never admits it automatically.
  Overview and mapping use the point-cloud renderer, not a raster fallback.

## Explicit External Configuration

The operator prepares the owned state directory with mode `0700`, and both
configuration and token files with mode `0600`. Use direct absolute paths;
symlinks and overlap with runtime/catalog/native inputs are rejected.

```json
{
  "schemaVersion": 1,
  "stateDirectory": "/home/OPERATOR/lingxi-product-state",
  "agentUrl": "http://127.0.0.1:18180",
  "agentTokenFile": "/home/OPERATOR/lingxi-secrets/agent-token",
  "actionsBridgeEndpoint": "http://127.0.0.1:18082/v1"
}
```

The token file contains the same bearer token configured for the local Agent
bridge, at least 32 printable ASCII characters. Configuration supports no
inline model key, arbitrary bridge URL, vendor adapter or motion override.
Model/Retriever credentials are submitted through paired HTTPS settings.

The optional `actionsBridgeEndpoint` selects only the fixed loopback AIMDK
actions-only bridge. It enables allowlisted expression playback and explicit
idle confirmation; preset motions and LinkCraft execution remain rejected.
The installed API and bridge read their shared private bearer token from the
robot environment file. Enable `lingxi-next-actions.service` explicitly only
after the actions-only bridge and its AIMDK dependencies have been verified;
it conflicts with the full AIMDK bridge and is never automatically started.
Mapping, localization lifecycle, map activation and navigation writes share a
fail-fast API operation lock. Expressions are hidden and rejected while mapping,
navigation, or localization is active or not confirmed clean. A missing or
unavailable localization status fails closed. Stop paths remain available for
recovery. This is process-local API serialization, not a substitute for the
separate GLIM/Nav2 owner's generation-bound motion interlock.

An optional `navigationSocket` field selects an existing independently supervised physical
navigation bridge on Linux:

```json
"navigationSocket": "/home/OPERATOR/lingxi-navigation-control/nav.sock"
```

Leave it out for the default non-actuating product workspace. The direct parent
directory must already exist, belong to the runtime UID and have no group/other
permissions (`0700`). The normalized absolute path must be under 100 UTF-8
bytes. An existing endpoint must be a real owned private Unix socket (`0600`),
not a symlink or regular file. Preflight permits a missing socket, but not a
missing parent. Both Python preflight and the API validate this selection.
The entire socket parent must be separate from configuration, secrets, product
state, catalog, run output and packaged/native inputs. Do not place it inside
the managed per-start output directory.

The field is covered by the same configuration digest and existing
install/upgrade/rollback selection. The managed launcher still discards ambient
`LINGXI_NAVIGATION_SOCKET`; environment injection is not a selection mechanism.
This option does not install/start ROS, publish velocity or configure the
physical bridge. A missing or disconnected bridge leaves HTTPS settings
available but reports navigation unavailable. Transport failure permanently
retires that client: restoring the socket does not reconnect or replay work.
Follow [the physical bridge recovery procedure](PHYSICAL_NAVIGATION_BRIDGE.md)
before a controlled restart. No configuration option bypasses the seven gates.

`managed_slam.py` and `launch_slam_bundle.py` accept the paired arguments
`--product-config ABSOLUTE_FILE --product-config-sha256 SHA256`. The launcher
keeps the existing external TLS, verified bundle, ordinary-user ARM64/Python
requirements, separate map catalog and reserved port 3000 restrictions.

`slam_deploy.py install --product-config ABSOLUTE_FILE` records the digest and
preflights this selection before switching services. An upgrade without a new
selection inherits it. `--without-product` explicitly selects commissioning
only and retains product data. Rollback restores that release's selection;
changed configuration bytes block the switch before service changes. Mutable
cloud credentials and token rotation stay external to immutable releases.

The installed `deploy/run.sh agent` consumes the same product configuration and
digest. It targets `/api/v1/agent/events` on local HTTPS `LINGXI_SLAM_PORT`
(default 3444), uses `agentTokenFile`, reads cloud credentials only from
`stateDirectory/credentials/ai.json`, and binds the configured loopback
`agentUrl` port instead of hardcoding 18180. In this mode the unrelated core
API URL, token, credentials path and Agent URL are ignored. Invalid selections
fail before SDK initialization, with no fallback to core. Java validates the
bounded hash-bound schema, relevant direct/private paths and port consistency;
the owner still independently validates navigation and native/runtime exclusions.

The Agent service must still be started explicitly after paired settings and
robot/cloud verification. Stop it before changing callback owners, then restart
after changing token/credentials/configuration. No second Agent instance,
microphone change, vendor installation or automatic credential copying is added.
Unmanaged launcher users must coordinate the API port themselves; installed
service port derivation is not discovery or proof of an active owner.

## Verification And Limits

- Installed passive Agent binding update: the pinned-SDK Maven verification
  built the current JAR on Windows (32 tests, zero failures/errors, one Linux
  permissions case skipped). The ordinary-user Linux JVM rerun passed all 32
  tests without skips. These include real loopback HTTPS callbacks, selected
  token/playback binding, redirect/rejection/disconnect handling and a child
  JVM rejecting an invalid selection before SDK startup. No live SDK session
  or cloud/microphone input is used by these tests.
- For that update, targeted API/product/shared-workspace regression passed
  40 tests with four explicit environment skips; production TypeScript/Vite
  build passed. The isolated Linux deployment suite passed both lifecycle
  cases, including installed Agent shell selection with a synthetic Java
  executable. Release scope passed seven cases; source inventory passed two
  with one Windows symlink skip. The shell fixture is not SDK execution, and
  the JVM tests are not ARM64 target acceptance. No new archive or APK was
  produced; the existing 0.1.21 artifacts remain unchanged.
- Windows full API/Web regression: 621 passed, 24 explicit environment skips.
  Production TypeScript and Vite build passed; managed shared API compiled.
- Linux targeted API/configuration/Unix transport regression: 67 passed and one
  explicitly non-Linux case skipped. Linux product configuration (13), installer
  (16), shared bundle (12) and source inventory (6) checks passed.
- Actual ordinary-user Linux processes: 15 workspace tests passed in 216.136 s.
  This includes HTTPS/Unix ownership, synthetic map save/reload, localization,
  restart-persistent product settings, loopback Agent HTTP playback and passive
  callback authentication. New selected-navigation checks use a real private
  Unix socket with explicitly synthetic state: all seven readiness gates are
  false, no motion is sent, a missing bridge preserves paired management, and
  disconnect permanently retires transport without reconnect/replay. The Agent
  and sensor workers are also synthetic; no real speech hardware, cloud service
  or ARM64 execution is claimed.
- Earlier attempts included one selected-socket startup deadline failure and a
  concurrent full run with 13 passes and two existing product startup failures.
  The standalone complete retry passed without changing the 22-second startup
  deadline. Bounded owner/child logs now accompany startup failures. These
  earlier failures remain observations, not passed runs or diagnosed causes.
- Product configuration and installer tests check bounded private inputs,
  source/archive inventory, explicit opt-in, upgrade/rollback preservation and
  rejection of modified selections. These are not field-calibration evidence.

Physical scale/gravity/ground/base calibration, target loading/throughput,
guarded physical navigation, actual AgentSDK/cloud/acoustic latency, current
Android device acceptance and the complete independent-stack archive remain
open. This milestone does not modify their admission results.
