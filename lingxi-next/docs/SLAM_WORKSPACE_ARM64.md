# Shared Workspace ARM64 Commissioning

Date: 2026-10-31. Result: the r7 shared SLAM package passed its recorded-input
lifecycle on the actual ordinary-user AArch64 target. This is not live mapping,
physical localization accuracy, navigation or full-product acceptance.

## Package and Target

- Archive: `artifacts/lingxi-next-shared-slam-20261031-r7-arm64.tar.gz`.
- SHA256: `5d0928cc4f9ee29dde86c24771f3885554452651ecbd820fa157147ebfe725c7`.
- Manifest: `aec5ac3da86c80e77e4853c9a91edb1720807d339d431d6bef2be4354927d387`.
- Target: Linux AArch64, Python 3.10.12, ordinary user, actual user systemd.
- The extracted archive and installed release inventory were verified on target.
  The packaged ARM64 Node, API, web assets and production dependencies were used.
- Native libraries, recorded input, configuration and explicit recorded seed
  came from the existing isolated validation environment, not vendor services.
- HTTPS was private loopback port 3443, with a generated private certificate and
  pairing authentication. This does not establish LAN certificate or Android trust.

See [the sanitized evidence](slam-workspace-arm64-evidence.json) and
[installation and package requirements](SLAM_WORKSPACE_DEPLOYMENT.md).
Private recordings, geometry, seed matrices, certificates, pairing values and
command logs are not published or included in the archive.

## Observed Lifecycle

1. Cold extraction and full inventory verification succeeded without the
   development source tree. The real user service reached verified readiness.
2. Unpaired state access returned 401; pairing, the HTML index and its referenced
   script were served by the installed package. This is not a browser rendering test.
3. Mapping completed all 4,144 callbacks: 3,947 IMU and 197 raw cloud callbacks.
   The worker processed 196 scans and estimated 165 poses. Raw callback counts
   and processed-scan counts are intentionally distinct.
4. Native saving and fresh-process reload produced a schema-v2 receipt, one
   submap, 165 saved/finalized frames and 2,655 saved points. The receipt checksum
   matched the paired preview response. The coordinate units remain native-raw.
5. After a real service stop/start, the map catalog and pairing code persisted.
6. On-demand localization loaded the selected map and explicit recorded seed.
   Tracking was observed at frame 5. Concurrent mapping start returned 409.
7. Pause revoked the pose immediately. Reusing the old seed generation returned
   409. Replay then completed all 4,144 callbacks before explicit shutdown.
   The actual native child report passed the unchanged full-journal accounting,
   timing and single-owner checks, with no rejected or discarded callbacks.
8. Localization cleanup completed, and a new mapping session could start and
   be discarded. Both workspace service runs stopped with exit code 0, no forced
   termination, complete adopted-child cleanup and zero remaining children.

The final independent read-only audit found the service inactive, MainPID 0,
an empty control group, no port-3443 listener and no remaining validation processes.
The unit is linked, not enabled for boot. The existing port-3000 console still
listens. AIMDK and vendor services were not changed; there were no live sensor
subscriptions or robot motion commands in this check.

## Findings and Fixes

- R2 cold extraction first encountered a cached missing tools import path.
  Invalidate import caches before verifying newly extracted sources.
- The resumed r2 inventory passed, but startup discarded the reviewed native
  environment. Forward it to the native service only, keeping the API environment
  separate. Regression tests verify this boundary.
- R3 then exposed missing native adapter provenance sources in the package.
  JS and Python inventories now include replay, mapping and point-output sources.
- R4 completed mapping and saving, but the checker assumed that the public map
  session ID was its storage directory. Resolve by both receipt identity and hash.
- R5 reached restart persistence, seeded tracking and pause, but the checker
  stopped after only 804 callbacks. The full-journal diagnostic correctly marked
  it interrupted. That result is retained, not relabeled as a complete replay.
- R6 stopped before installation because its checker equated 197 raw clouds with
  196 processed scans. These counters are now checked separately. Elapsed playback
  time only schedules stop; the actual complete native report establishes completion.

All earlier extracted releases, reports and saved diagnostic maps were retained.
Only the verified, stopped units belonging to these trials were unlinked before
fresh trials. No recursive deletion or vendor service replacement was performed.

## Regression and Limits

The current focused Linux/Node checks passed 65 cases: eight shared-package,
eight mapping-package, fourteen read-only-package, eleven extracted-workspace,
ten target-checker preflight/receipt/journal, eight deployment and six asset
inventory tests. The thirty package and ten checker cases also passed on Windows.
The full historical r2 suite is documented separately; it was not rerun here.

The retained target environment reports NumPy 1.26.1, SciPy 1.8.0 and small-gicp
1.0.1. SciPy warns that this NumPy version exceeds its supported range. This trial
did not replace those dependencies or vendor packages; production dependency
compatibility still requires resolution in the isolated deployment environment.

The seed is derived from the recording, not independent position truth. This
check does not accept scale, calibration, orientation, geometric accuracy, loop
closure, global relocalization, live latency, local avoidance, walking, voice
concurrency, Android operation, upgrade/rollback on target or long-run reliability.
Recorded diagnostic early stop still means interrupted full-journal validation.
All mapping/localization/navigation readiness and product acceptance flags remain
false. The full product goal remains unfinished.
