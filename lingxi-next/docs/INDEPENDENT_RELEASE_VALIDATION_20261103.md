# Independent Commissioning Release 0.1.21

Recorded 2026-11-03 after final archive verification. This is an offline
integration and packaging milestone, not robot deployment or product acceptance.
This record is intentionally outside the already-built archive so its checksum
does not require a circular rebuild.

## Artifact Identity

- Archive: `lingxi-next-0.1.21-independent-commissioning-20261103-linux-arm64.tar.gz`
- Local directory: `artifacts/`
- Bytes: `131217721`
- SHA256: `922a60fae9ac9e149af74c5299a7758aa5507c904392595a926d92f7dc1a118a`
- Profile: `independent-navigation-commissioning`
- Verified file checksums: `13070`
- Verified literal native-source references: `77`
- Bundled runtime headers: ELF64 AArch64; executable modes verified.
- `completeProduct=false`, `productionAccepted=false`.

The adjacent `.sha256` and `.verification.json` identify this exact archive.
Earlier candidates remain preserved under `.local/`; they are not releases.

## Implemented Scope

- Separate release profile packages core management/Agent services, mapping and
  shared-SLAM assets, runtime adapters, native adapter sources and verification
  inputs. Native libraries and verified target configuration remain external.
- Installed mapping/shared-SLAM owners use explicit opt-in, reciprocal service
  conflicts, separate persistent map catalogs and retained per-run diagnostics.
- Optional product and passive acquisition selections require direct absolute
  files plus explicit matching SHA256 values. Incomplete/changed selections
  block launch. No credential or microphone configuration is changed automatically.
- Installer, upgrade, rollback and diagnostics account for all six product
  units. Installation/upgrade/rollback starts only the core management API.
- The source filter excludes caches, test fixtures and private/generated
  material, rejects symlinks, and retains required runtime verification scripts.
  Static reference checks now run before compression and on the final archive.
- Point-cloud Z defaults remain 0.1 to 1.4 metres. Mapping movement stays on the
  external remote, with no Web/APK walking teleoperation.

## Verification

| Check | Result |
| --- | --- |
| Final release command | Passed, exit 0 |
| Web/API/runtime TypeScript and Vite/assets builds | Passed |
| Vitest | 614 passed, 21 explicit skips |
| API Python suite | 39 passed |
| Ordinary-user Linux tools discovery | 1816 total: 1741 passed, 75 skipped, no failures |
| Final mapping/release-scope focused regression | 13 passed |
| Source-copy Node regression on Windows | 2 passed, 1 explicit symlink skip |
| Isolated Linux install/upgrade/rollback and installed Bash entrypoints | 2 methods passed; successful harness repeated |
| Staged Python source syntax | 240 files parsed without executing imports |
| Archive identity, inventory, ARM64 headers and modes | Passed |

The Linux discovery ran before the final source-filter correction; the final
focused regressions and release/archive checks cover that correction. Fault
messages in the discovery output are intentional injected failure scenarios.
The initial final-candidate archive was rejected for an omitted
`tools/check_ground_native.py`; its failure is preserved, not counted as a pass.

## Remaining Target Acceptance

No robot was contacted, deployed to or moved for this milestone. The Linux
installation harness uses actual Bash/filesystem/certificate operations with
synthetic systemd, health and native owners. Header inspection is not ARM64
execution, and static reference checks do not execute archived code.

Real sensor topics/TF/scale/ground/base calibration, current ARM64 native
loading and throughput, map save/reload, localization, guarded navigation and
stopping still require target verification. Native GLIM/terrain dependencies,
ROS Humble/Nav2 and private cloud credentials are not made self-contained by
this archive. Saving a map does not admit physical navigation or tour actions.

No APK was rebuilt in this milestone. Android device operation, real
AgentSDK/cloud speech, wake/rejection accuracy and audible P95 <=2 s remain
unaccepted. See `INDEPENDENT_INSTALL_20261103.md` for installation boundaries.
