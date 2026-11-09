# Target Mapping Save And Reload - 2026-11-09

Target: `192.168.0.128`, ARM64 Lingxi X2. This records real live-input
mapping through the installed Web console. Moving-map accuracy, saved-map
localization and physical navigation acceptance remain pending.

## Installed Selection

- Installed release: `0.1.30-independent-commissioning-20261106` at
  `/opt/lingxi-next/current`.
- Mapping console: `https://192.168.0.128:3443`. Health returns success with
  `demoMode=false`; protected state returns HTTP 401 without pairing.
- `lingxi-next-independent-mapping.service` was inactive after a robot reboot.
  Starting this existing service restored the console. No boot enablement,
  release selection, input selection or other service restart was performed
  during this recovery.
- The selected live-input closure is
  `/var/lib/lingxi-next/native-inputs/mapping-20261106-c`. Its manifest SHA256 is
  `b9e5e62b1e27dffec7795ac7132b5d491e2b17ad84d8bb5551414e61c6eaec4e`;
  `mapping.json` SHA256 is
  `14d14dfcbf18e2b7e0350c009cd882f9e018c78957537cf2f75a54a0c0f8df95`.
  Both were reread and verified on the target on November 9. The directory is
  service-owned with mode 0700.
- This 300-second capture profile was prepared and selected on November 6.
  It supersedes the earlier 90-second `mapping-20261106-b` selection, which is
  retained. The reviewed source configuration is outside the frozen closure;
  its SHA256 is
  `82907603d97bb02d3cceaac29f370b7e34129df10224d0d85e2e39c0cb22fa35`.
  The environment backup is under `/var/lib/lingxi-next/backups`, outside the
  input closure. See [input preparation](INSTALLED_NATIVE_MAPPING_INPUTS.md).

## Actual Web Capture

The paired Web console was used to start a live capture, inspect the drawn
point cloud, stop and save, and open the saved cloud in map management.
No robot movement command was sent. Pairing credentials, browser sessions,
point-cloud contents and screenshots remain private and are not delivery or
source-control contents.

| Observation | Result |
| --- | --- |
| Input and mapping engine | `live`, `glim-full-async-v1` |
| Live frame | Approximately 7,444 points; `lingxi_glim_odom` |
| Default displayed Z range | 0.1 to 1.4, explicitly uncalibrated raw coordinates |
| Acquisition progress | 301 processed scans, 270 estimated poses, 6,335 completed messages |
| Finalized / saved frames | 270 / 270 |
| Saved submaps / points | 1 / 2,273 |
| Independent fresh-process reload | Passed |
| Saved-cloud display | Drawn in the Web map-management view |
| State after save | Idle, cleanup complete, ready for another capture |

The mapper completed all 270 submitted frames and its final queue was empty.
Observed maximum queue wait was about 3.075 ms and maximum task duration about
108.571 ms. These are measurements of this short run, not general performance
guarantees. The saved cloud had 379 points inside the displayed Z layer out of
2,273 total points; filtering the display does not discard the stored cloud.

## Remaining Field Checks

The capture above is a near-static functional check. Its catalog correctly
reports `coordinateUnits=native-raw`, `geometricAccuracyValidated=false`,
`localizationReady=false` and `navigationReady=false`. The configured
`metresPerMapUnit=1.0` is not a measured scale calibration. A rendered cloud or
successful reload does not change these flags.

The operator has been asked to capture a short externally controlled out-and-back
route, approximately three metres each way, return to the initial position and
heading, and report approximate travel and visible duplication/drift. That
capture was not yet present at the final read-only catalog check for this
record. It is not counted as a passing field test.

After that capture: inspect trajectory and return error, verify map geometry
against the measured scene, establish gravity/base alignment and supporting
ground, then exercise live saved-map localization before supervised short
navigation and obstacle tests. Native ground labels alone must not admit
unsupported free space. Voice acoustics/latency and Android device acceptance
remain separate outstanding field checks.

The offline ROS-worker/Nav2 isolation milestone is already complete; its
actual Humble API and lifecycle results are in
[the ROS-worker record](NAV2_ROS_WORKER_20261104.md). Earlier startup-failure
records are historical evidence, not a reason to repeat that implementation.

This record updates the deployment state described in the historical
[November 6 commissioning](TARGET_COMMISSIONING_192_168_0_128_20261106.md) and
[mapping-owner](TARGET_MAPPING_OWNER_20261106.md) records without changing
their original test outcomes. No physical navigation, preset action, vendor
map modification or voice takeover occurred in this validation.
