# Independent Commissioning Release 0.1.25

Recorded 2026-11-04. Current-source deployment preparation, not robot installation
or complete-product acceptance. Baseline: `6adf291ee96361cb291d8d8c6ab7200839f66d8b`
plus version 0.1.25/code 26 and the source-filter/mandatory-source checks described
below. This record is outside the immutable archive to avoid a circular rebuild.
The preceding 0.1.24 archive and APK hashes were rechecked and remain unchanged.

## Artifact Identity

Files are local under `artifacts/`, with adjacent SHA256 and verification JSON.
Binary artifacts, private inputs and signing keys are excluded from Git.
Archive dates use the builder's UTC date.

| Artifact | Identity |
| --- | --- |
| ARM64 archive | `lingxi-next-0.1.25-independent-commissioning-20261103-linux-arm64.tar.gz` |
| Archive bytes | `131693139` |
| Archive SHA256 | `8e3c332effc7c76dabd30edfc7b520c0597c51fefaa819dedc6c31713e8b1163` |
| Verified archive files | `13212`; 86 literal native-source references |
| Android APK | `lingxi-next-0.1.25-commissioning-a3ebd9d77d32.apk` |
| APK bytes | `3298736` |
| APK SHA256 | `6853b453f4125a2e35dee068bff9e8e38bd17e24994646799e4d212568353616` |
| Android version | `0.1.25`, code `26` |
| Signing certificate SHA256 | `6a209440c6cf55c8f9734549d7521755fb6915ce2f7217e0a6d86d665b526946` |
| Web source SHA256 | `a3ebd9d77d32c410b218742db338ab08613d28bf70f9f17d43dc08aa92fc6a89` |
| Android source SHA256 | `95a5586674023417a31ae92fc5546c873570dd55032e62f9def5f76ef62705e1` |

The archive profile is `independent-navigation-commissioning`. Node/Java ELF64
AArch64 headers and executable modes pass the independent verifier. The APK
signature passes with the existing local commissioning key: `sensorLandscape`,
cleartext traffic disabled and application backup disabled. Both artifacts
retain `productionAccepted=false`; the APK also retains `deviceAcceptance=false`.

## Included Changes

- Current owned Nav2 official-component launch, lifecycle confirmation/freshness
  and isolated ROS worker boundary. See [worker verification](NAV2_ROS_WORKER_20261104.md)
  for preserved failed experiments, corrected API tests and unchanged deadlines.
- Current tour dwell/quiet-resume/idle-return corrections and installed acquisition
  input preparation. Earlier file-provisioning/native preparation remains included.
- Shell source copying is now explicit. The old allowlist omitted the isolated
  diagnostic runner, preventing that runner from being used from an extracted
  source release. Four owned shell sources are now included, while generated,
  private, test and symlink exclusions remain unchanged.
- Eight mandatory offline runtime/diagnostic sources now include the worker,
  parent service, source-time reader, preparation/scene helpers, isolated shell
  runner, lifecycle diagnostic and its native observation source. Missing any
  declared member fails the independent archive scope check.

There is no new walking teleoperation, arbitrary joint control or vendor system
replacement. Point-cloud display/clearance Z defaults remain 0.1--1.4 metres.

## Actual Extracted Package

The verified archive was extracted by an ordinary Linux user into:
`/var/tmp/lingxi-release-0.1.25-2vqlCR`.

All 368 source files match current project bytes. A fresh `-I -B` scientific
Python process, without project PYTHONPATH, imported 24 actual package modules
covering installed mapping/localization/SLAM, acquisition, product configuration,
physical bridge/lifecycle clients and offline worker/service. Each module's file
was checked to reside in the extracted source tree. Mapping CDLL/PyDLL loads,
child creation and shell execution were forbidden during the package smoke
check; rclpy was not imported. This uses host Python, not target ARM64 execution.

The extracted verifier also rechecked all 142 files in the existing external
private native-input closure against unchanged receipt
`31f42ae31c43cb2d6508a6420359884197a41b7ce3c510ed6b225af34f12ca16`.
These files are not shipped inside the archive and grant no geometric admission.
API, AIMDK bridge, Agent JAR and all three runtime entrypoints match current build
bytes. All five Web files match byte-for-byte in each of the three staged copies;
their source identity also matches the verified APK.

Source-closure report: `artifacts/lingxi-next-0.1.25-source-closure.verification.json`.
SHA256: `335c986c6872ae797ebacffc2dbef420fab919bb2563bfb37b4b3d670e6bcf62`.

The actual extracted shell runner was then used in the separate loopback-only
Humble rootfs. `service-diag-release-0-1-25-20261104-a` completed twelve running
generations and twelve idle/quiescent retirements. All 96 request/reply pairs
succeeded, with no missing replies or error. All eight diagnostic source hashes
match the extracted package. Acceptance was 1.828--2.440 s; largest recorded
parent loop gap was 6.242 ms. These are startup observations, not cloud/audio
first-response measurements or middleware root-cause proof.

Copied report: `artifacts/lingxi-next-0.1.25-nav2-diagnostic.verification.json`.
SHA256: `f48ccec009a4c0a3ef5d9152030e411e16f8d950337916761a68874c1fca8995`.

## Checks

| Check | Result |
| --- | --- |
| Independent release command and actual archive verifier | Passed, exit 0 |
| TypeScript release regression | 750 passed, 25 explicit skips |
| API Python release regression | 55 passed, one explicit skip (56 total) |
| Web/API/runtime type checks and production builds | Passed |
| Pinned AgentSDK Maven verification | 31 passed, one platform skip (32 total) |
| Release-scope contracts | 10 passed |
| Source filtering/copy contracts | Two passed, one Windows symlink skip |
| Linux installed mapping/acquisition inputs and scope tests | 32 passed, two opt-in input skips (34 total) |
| Actual private input and extracted-source checks | Passed as recorded above |
| Actual extracted Nav2 startup/retirement diagnostic | Twelve generations, 96/96 replies, no error |
| Android build, signature/version/orientation/source verifier | Passed, exit 0 |
| Old 0.1.24 archive/APK identities | Unchanged |

Existing Maven systemPath, Rollup annotation and Android SDK/path warnings remain
nonfatal. No failed product run was repeated to select a green result. Ancillary
file-discovery permission/missing-path checks did not alter source or acceptance.

## Remaining Acceptance

No robot was connected, deployed to or commanded. This is a commissioning
archive, not a self-contained native stack or a completed product. ROS Humble,
verified native dependencies, the isolated Python wheelhouse, robot configuration
and private cloud credentials remain external. Existing installation, upgrade,
backup and rollback tools are included; this turn did not execute them on ARM64.

Live sensor timing/TF, metric/ground/base calibration, moving map save/reload and
localization, ground support, real navigation/stopping/obstacles and preset
actions remain subject to target acceptance. Real AgentSDK/cloud audio,
wake/rejection statistics, audible P95 <=2 s and Android device behavior are not
established by build signatures, synthetic Nav2 or host import checks.
