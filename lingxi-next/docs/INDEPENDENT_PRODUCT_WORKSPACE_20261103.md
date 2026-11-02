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
- No AIMDK adapter is constructed. Vendor maps, vendor navigation and robot
  actions are not admitted by this mode. Existing metadata can be maintained,
  but point navigation, tour execution, map activation and action confirmation
  remain unavailable. Voice cannot bypass those adapter/readiness limits.
- Saved SLAM maps remain receipt-verified `native-raw` records with
  `navigationReady: false`; they are never promoted to metric business maps.
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
  "agentTokenFile": "/home/OPERATOR/lingxi-secrets/agent-token"
}
```

The token file contains the same bearer token configured for the local Agent
bridge, at least 32 printable ASCII characters. Configuration supports no
inline model key, arbitrary bridge URL, vendor adapter or motion override.
Model/Retriever credentials are submitted through paired HTTPS settings.

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

The Agent bridge must separately target this API's `/api/v1/agent/events` and
use the matching local token. This change does not start another AgentSDK
instance, alter microphones, install vendor software or configure real cloud
credentials automatically.

## Verification And Limits

- Windows full API/Web regression: 581 passed, 18 explicit environment skips.
  Production TypeScript and Vite build passed; managed shared API compiled.
- Actual ordinary-user Linux processes: 13 workspace tests passed in 137.973 s.
  This includes HTTPS/Unix ownership, synthetic map save/reload, localization,
  restart-persistent product settings, loopback Agent HTTP playback and passive
  callback authentication. The Agent and sensor workers are labeled synthetic;
  no real speech hardware, cloud service or ARM64 execution is claimed.
- The first concurrent run had one unchanged 22-second startup deadline
  failure. The complete retry passed without deadline changes. Keep that
  first failure as a startup/resource-limit observation, not a passed run.
- Product configuration and installer tests check bounded private inputs,
  source/archive inventory, explicit opt-in, upgrade/rollback preservation and
  rejection of modified selections. These are not field-calibration evidence.

Physical scale/gravity/ground/base calibration, target loading/throughput,
guarded physical navigation, actual AgentSDK/cloud/acoustic latency, current
Android device acceptance and the complete independent-stack archive remain
open. This milestone does not modify their admission results.
