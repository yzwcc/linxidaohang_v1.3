# ARM64 Ground And Clearance Components

Date: 2026-11-03. Current-source cross-build and static link verification on an
ordinary-user Linux x86_64 workstation. No robot authentication, target code
execution, sensor subscription, deployment or motion occurred. This prepares
missing target binaries for the independent stack, not a complete release.

## Implementation

`tools/build_terrain_arm64.py` builds the existing ground and clearance C ABI
adapters for AArch64 using the already-pinned Jammy cross toolchain. It reuses
the actual verified host-native input copies rather than obtaining another
algorithm revision or modifying ground/clearance behavior:

- Patchwork++ revision and all compiled upstream files are verified against
  `patchworkpp.lock.json`; Eigen headers match the host build inventory.
- OctoMap headers and adapter match the tested host build. Target libraries
  match the exact AArch64 binary hashes in `octomap.lock.json`.
- The current 21-test real host-native ground/fusion report is required, with
  unchanged tested source identities and no skipped or failed checks.
- A dedicated CMake recipe and linker export lists live under
  `tools/terrain-arm64`. The user's unrelated untracked
  `robot-navigation/native-ground/CMakeLists.txt` is not read, edited or bundled.
- A separate new private output preserves copied build inputs, build logs,
  compiled adapters, runtime dependencies and a source-bound report. No system
  package installation or vendor file modification is performed.

Each adapter exposes exactly its four expected C functions. The linker rejects
unresolved symbols. ELF architecture, GLIBC/GLIBCXX/CXXABI requirements, declared
shared dependencies and the absence of host paths in dynamic links are checked.
The clearance adapter resolves private dependencies through `$ORIGIN/lib`;
relative SONAME aliases accompany both exact OctoMap distribution libraries.
The original upstream Patchwork++ license, OctoMap distribution copyright and
project third-party notices accompany the prepared libraries.

Both JavaScript and Python shared-SLAM source inventories now include the new
recipe/export lists, OctoMap lock and third-party notices. Point/mapping profile
ownership remains distinct. Existing immutable archives are not rewritten or
relabeled as containing these current sources.

## Actual Prepared Build

Private output: `terrain-arm64-build-20261103-c` in the existing WSL user's
`.cache/lingxi-next`. Earlier builds are retained separately. Generated binaries,
private paths and logs are not committed to Git.

| Adapter | Architecture | GLIBC | GLIBCXX | CXXABI |
| --- | --- | --- | --- | --- |
| `liblingxi_ground.so` | ELF64 AArch64 | 2.17 | 3.4.29 | 1.3.9 |
| `liblingxi_octomap.so` | ELF64 AArch64 | 2.17 | 3.4.29 | 1.3.9 |

Ground SHA256:
`d9990b6e8346762853d423817f1190586dc05e5a757a0318a88db840ae9cadf5`

Clearance SHA256:
`464cd6641404dbae64a0adbea85d4d8e5ffd28cd50efde63b563149ff6246c63`

Private build-report SHA256:
`4faa65303a76de2ac413715dc263b3119c23a4914a082c83056d3aff26700c82`

The official Ubuntu package `liboctomap1.9_1.9.7+dfsg-3_arm64.deb` was downloaded
and extracted into a separate user-cache directory, not installed. Its SHA256 is
`41ff906e38834ff9832bc29861d649f48953d2b42fc12cf7e95d0aa3c59f7eb5`.
Both extracted library hashes match the existing target lock; the distribution
copyright is copied unchanged. Source URL:
<https://ports.ubuntu.com/pool/universe/o/octomap/liboctomap1.9_1.9.7+dfsg-3_arm64.deb>.

## Verification

- Actual cross configure/compile/link passed for both modules. A separate
  `verify` invocation rechecked current inputs, frozen compiled copies, target
  binaries, exact exports, dependency aliases and license copies.
- Current x86_64 real native ground/fusion tests: all 21 passed, zero skips.
  This is host algorithm evidence, not target execution or physical calibration.
- Final cross-build unit suite: Linux all eight passed, zero skips; Windows
  seven passed and the Linux SONAME-symlink lifecycle check skipped.
- Shared/point/mapping package and deployment regression: 61 discovered,
  60 passed, one Linux host-Node skip, zero failures/errors, 23.198 seconds.
  This run precedes the additional pure ABI unit test; the final eight-test
  unit suite was rerun separately. Shared package assets are synthetic except
  the explicitly tested extracted production-source import path.
- Windows shared bundle regression: ten passed, one scientific-import skip;
  its all-profile JavaScript/Python source-inventory parity check passed.
- JavaScript source inventory suite: all six passed. `git diff --check` passed.
  Web/API business behavior and Android assets were not changed or rebuilt.

Invoke `build_terrain_arm64.py build` or `verify` from `lingxi-next`, supplying
explicit `--cross-work`, `--native-work`, `--ground-host`, `--octomap-host`,
`--octomap-arm64`, `--host-check` and `--output` paths. `build` requires a new
private output; `verify` is read-only. No target executable is launched by either.

## Remaining Gates

The source-bound [target-trial tool](TERRAIN_ARM64_TARGET_PREPARATION_20261103.md)
now prepares these actual adapters and existing native ground/fusion tests for
bounded ordinary-user execution without ROS. Preparation is complete; actual
target execution remains separate and does not grant live runtime admission.

The report keeps `targetExecutionValidated`, `freshProcessLoadPassed`,
`physicalScaleValidated`, `groundSupportValidated` and `navigationReady` false.
It is deliberately not a live runtime's adjacent successful load report and
cannot satisfy `AcquisitionSettings` ground/clearance admission. Actual target
loading, contracts and current-source throughput must be verified before
selecting these libraries in the passive live composition.

The LAN recheck again connected to TCP/22 but received EOF before an SSH banner;
no authentication, upload, route change or robot command occurred. The complete
independent-stack installer, guarded physical command path, real-sensor ground
calibration, cloud/audio and Android-device acceptance remain unfinished.
No fresh scan is needed for this cross-build milestone. The point-cloud display
and default Z interval of 0.1--1.4 m remain unchanged.

See [live terrain integration](LIVE_TERRAIN_ROS_WIRING_20261103.md),
[recorded ground diagnostic](ACQUISITION_GROUND_INSPECTION_20261103.md) and
[full product scope](OFFLINE_PRODUCT_ACCEPTANCE.md).
