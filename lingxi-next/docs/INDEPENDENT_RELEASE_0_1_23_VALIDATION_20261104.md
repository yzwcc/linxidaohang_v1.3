# Independent Commissioning Release 0.1.23

Recorded 2026-11-04 after successful archive, extracted-source and APK checks.
This is an offline packaging milestone, not robot deployment or complete product
acceptance. The source baseline is `4dfd1ed61861d83a6f66201b6709e83287503fe9`
plus the 0.1.23 version and mandatory Nav2 startup-source checks. This record is
outside the immutable archive to avoid a circular checksum rebuild. Earlier
artifacts and their validation records remain unchanged.

## Artifact Identity

Generated files are local under `artifacts/`, with adjacent SHA256 and JSON
verification reports. Archives, APKs and signing keys are not committed to Git.
The archive filename uses the builder's UTC date, not the workstation date.

### Robot ARM64 Archive

- Filename: `lingxi-next-0.1.23-independent-commissioning-20261103-linux-arm64.tar.gz`
- Bytes: `131641308`
- SHA256: `68858db73ddb1331cb46de69d96c2e69dd60c2b3fd625a4fbe804f91fdba6ad0`
- Profile: `independent-navigation-commissioning`.
- Verified file checksums: `13189`; literal native-source references: `86`.
- Bundled Node/Java headers: ELF64 AArch64; executable modes verified.
- `classification=commissioning`, `completeProduct=false`, `productionAccepted=false`.

### Landscape Android APK

- Filename: `lingxi-next-0.1.23-commissioning-4a9ffe47937d.apk`
- Bytes: `3298740`
- SHA256: `53885a855f7b362278e022d5e9349cb4bd76c9e3ae52e8948dddf45c8b41d330`
- Version: `0.1.23`; Android version code: `24`.
- Signature verified using the existing local commissioning key.
- Certificate SHA256: `6a209440c6cf55c8f9734549d7521755fb6915ce2f7217e0a6d86d665b526946`
- Web source SHA256: `4a9ffe47937dacfbf1a3775a535a94c9addbd8e721646bccbf552d7359cc50db`
- Android source SHA256: `3230f969e877bb712f8d8f677bfec23b494de1da6f231b16d27d5890d7437ec9`
- `sensorLandscape`, cleartext traffic disabled, application backup disabled.
- `productionAccepted=false`, `deviceAcceptance=false`.

Core, mapping and shared-SLAM Web assets match the APK Web identity. The staged
API and three runtime entrypoints match their current builds. The staged Agent
JAR matches the current Maven build, SHA256
`cf377385d259746a71895f94f4c9587bb7f2db497bd46d4d46829da9acda7092`.

## Changes Included Since 0.1.22

- Explicit metric navigation frame and a measured survey calibration review
  workflow. A review candidate does not apply calibration or admit navigation.
- Actions-only AIMDK isolation, expression dispatch cancellation, shared robot
  operation leases and installed workspace diagnostics. Physical action
  acceptance is still pending; mapping/navigation interlocks remain enforced.
- Map-bound owned localization inputs, multi-map Nav2 handoff and fresh output
  retirement before a new generation can be admitted.
- Fresh localization evidence for spoken robot coordinates, preserved tour
  dwell across interruptions and point idle-return deferred through dialogue.
- Confirmed lifecycle startup in offline and production-owned Nav2: two current
  state-discovery rounds, one official STARTUP request, positive ACK, active
  nodes and an available action server. Missing/refused replies grant no ready
  state; old generations and cancelled callbacks cannot revive readiness.
- Required source payload now explicitly includes the Nav2 action adapter and
  shared lifecycle-startup helper. Missing either rejects the independent scope.

Point-cloud display and Z defaults of 0.1 to 1.4 metres are retained. Mapping
movement stays on the external remote; Web/APK add no walking teleoperation.
See [installation](INDEPENDENT_INSTALL_20261103.md) and the
[workspace contract](INDEPENDENT_PRODUCT_WORKSPACE_20261103.md).

## Verification In This Milestone

| Check | Result |
| --- | --- |
| `npm run release:independent-commissioning` | Passed, exit 0 |
| Release Vitest | 750 passed, 25 explicit skips; 42 files passed, 2 skipped |
| API Python in release build | 56 run, 55 passed, 1 explicit skip |
| Web/API/runtime TypeScript and production builds | Passed |
| Current pinned-SDK Maven verify on Windows | 32 run, no failures/errors, 1 platform skip |
| Release-scope tests | 8 passed, including missing startup-module rejection |
| Source filtering | 2 passed, 1 Windows symlink skip |
| Final archive integrity, inventory, source references and runtime headers/modes | Passed |
| Staged source equality | All 357 source files match current project bytes |
| Actual archive source extraction on ordinary-user Linux | Passed, exit 0 |
| Extracted source equality | All 357 source files match current project bytes |
| Extracted executable source smoke check | 16 actual modules imported under isolated Python, no project PYTHONPATH |
| `npm run android:build` and APK verification | Passed, exit 0 |
| Three archived Web copies and APK Web identity | Match |

The source smoke check imports the actual packaged production bridges and Nav2
startup modules without importing ROS or executing native code. This checks
packaging/import closure, not ARM64 native loading or physical functionality.
The preceding actual isolated Nav2 startup/integration results were not rerun
or recounted as new tests in this packaging milestone.

Windows tar extraction emitted two unreadable-filename warnings and exited 1;
that attempt is not counted as a pass. Final source extraction and import checks
used Linux tar and ordinary-user isolated Python. Maven/TypeScript builds,
independent archive verification and APK verification exited successfully.
Nonfatal Rollup annotation and Android tool warnings remain in local logs.

## Remaining Target Acceptance

No robot connection, installation, cloud voice session, microphone capture or
movement IO was used. No service was installed, enabled or replaced on a robot.
The full scientific Python suite and target installer/rollback were not rerun.

ROS Humble/Nav2, verified GLIM/terrain native dependencies, robot-specific
configuration/calibration and private credentials remain external. This archive
is not a self-contained native stack. Current ARM64 execution/throughput,
sensor timing and TF, metric/gravity/ground/base calibration, real map
save/reload/localization, physical navigation/stopping and preset actions remain
unaccepted. Preserved real terrain data still has zero supported free cells;
neither a saved map nor a running management endpoint admits navigation.

The confirmed startup sequence is a bounded mitigation, not a proven FastDDS
root-cause repair. Original middleware failures remain preserved. Production
30-second prewarm and offline 5-second acceptance limits were not increased.

Real AgentSDK/cloud voice, wake/rejection accuracy, audible P95 <=2 seconds and
current Android device behavior remain unaccepted. Proceed with controlled
target validation, not unrestricted product delivery.
