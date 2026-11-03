# Independent Commissioning Release 0.1.22

Recorded 2026-11-03 after successful archive and APK verification. This is an
offline integration/package milestone, not robot deployment or complete product
acceptance. The source baseline is `fd149862b0e846960928effddae252158aa6d74b`
plus the 0.1.22 package/version-code update. This record is outside the immutable
archive to avoid a circular checksum rebuild. The 0.1.21 artifacts and their
[validation record](INDEPENDENT_RELEASE_VALIDATION_20261103.md) remain unchanged.

## Artifact Identity

Both outputs are in the local `artifacts/` directory and have adjacent SHA256
and JSON verification reports. Generated packages are not committed to Git.

### Robot ARM64 Archive

- Filename: `lingxi-next-0.1.22-independent-commissioning-20261103-linux-arm64.tar.gz`
- Bytes: `131246065`
- SHA256: `01cf836a2572963ed51b4daa55f4bb19943676ba6988472036ff735325acbfe7`
- Profile: `independent-navigation-commissioning`
- Verified file checksums: `13075`
- Verified literal native-source references: `84`
- Bundled Node/Java runtime headers: ELF64 AArch64; executable modes verified.
- `classification=commissioning`, `completeProduct=false`, `productionAccepted=false`.

### Landscape Android APK

- Filename: `lingxi-next-0.1.22-commissioning-4d64433f6cbb.apk`
- Bytes: `3294137`
- SHA256: `f899a6379ee4d9df9b63743764447134407fa9e45ce314a2c83405fadc54254b`
- Version: `0.1.22`; Android version code: `23`.
- Signature verified using the existing local commissioning signing key.
- Certificate SHA256: `6a209440c6cf55c8f9734549d7521755fb6915ce2f7217e0a6d86d665b526946`
- Web source SHA256: `4d64433f6cbb79d38d4b09f405d7b97f392c7948d328641a02556937ff26c063`
- Native source SHA256: `f239e77aca3bc5f0c9626639fb89d0597f89c5899616cdef01b9c8ebe3d367fc`
- `sensorLandscape`, cleartext traffic disabled, application backup disabled.
- `productionAccepted=false`, `deviceAcceptance=false`.

The staged archive Web identity and verified APK Web source identity match.
Matching assets and a valid signature do not establish Android device acceptance.

## Changes Included Since 0.1.21

- ARM64 terrain trial preparation and explicit receipt-bound selection in passive
  live acquisition. A synthetic loader receipt is not live terrain admission.
- Optional private physical-navigation Unix socket selection through the managed
  product configuration, launcher and API. Missing/disconnected transport leaves
  management available but does not reconnect or replay work. Existing readiness
  gates remain mandatory; this setting does not start physical navigation.
- Installed passive Agent service binding to the selected shared-SLAM API,
  token, credentials and playback port. Hash/schema/private-path/port failures
  exit 78 before SDK startup without falling back to the core owner. The service
  does not endlessly restart configuration errors. Unselected core behavior is
  retained; no microphone source or vendor SDK installation is changed.
- Matching current Web assets in the archive and signed commissioning APK.

Point-cloud Z defaults remain 0.1 to 1.4 metres. Mapping movement remains on the
external remote; Web and APK do not add walking teleoperation. See
[installation](INDEPENDENT_INSTALL_20261103.md) and the
[product workspace contract](INDEPENDENT_PRODUCT_WORKSPACE_20261103.md).

## Verification

| Check | Result |
| --- | --- |
| `npm run release:independent-commissioning` | Passed, exit 0 |
| Full release Vitest regression | 621 passed, 24 explicit skips; 38 files passed, 2 skipped |
| API Python regression in release build | 39 passed |
| Web/API/runtime TypeScript and production Vite/assets builds | Passed |
| Pinned-SDK Maven verify on Windows before packaging | 32 tests; no failures/errors, one Linux permissions skip |
| Ordinary-user Linux JVM Agent verification | 32 passed, no skips/failures |
| Targeted API/product/shared-workspace regression before packaging | 40 passed, 4 explicit skips |
| Release-scope checks before packaging | 7 passed |
| Source inventory on Windows before packaging | 2 passed, 1 explicit symlink skip |
| Isolated Linux install/upgrade/rollback and installed Agent entrypoint | 2 methods passed; repeated successfully during packaging |
| Final archive checksum/inventory/source references/runtime headers/modes | Passed |
| `npm run android:build` and independent APK verification | Passed, exit 0; signature/version/assets/settings verified |
| Staged archive and APK Web source identity comparison | Passed |

The Linux JVM checks include real loopback HTTPS callback operations. The
installation harness uses real Bash/filesystem/certificate operations with
synthetic systemd/health/native owners and a synthetic Java launcher. Neither
is an ARM64 AgentSDK/cloud/microphone acceptance test. Environment skips are
not passed target checks. Nonfatal Rollup comment and Android build-tool
warnings did not invalidate the successful builds and independent verifiers.

## Remaining Target Acceptance

No robot, cloud voice session, microphone or movement IO was used for this
release milestone. No robot software was installed or enabled. The last known
robot IP is not proof of a currently usable SSH connection.

Current ARM64 acquisition/terrain native loading and throughput, sensor/TF and
metric/gravity/ground/base calibration, real map save/reload/localization,
guarded navigation and stopping remain open. Preserved terrain observations
have zero supported free cells; they do not admit navigation. A saved map or
running HTTPS endpoint must not be reported as navigation readiness.

ROS Humble/Nav2, verified GLIM/terrain native dependencies, robot configuration
and private credentials remain external. The archive includes integration
adapters and verification inputs, not a self-contained field-accepted stack.
Independent-workspace actions remain unavailable pending the required interlocks.

Real AgentSDK/cloud speech, wake/rejection accuracy, audible P95 <=2 seconds
and current Android device operation remain unaccepted. Commissioning packages
are ready for controlled target validation, not unrestricted product delivery.
