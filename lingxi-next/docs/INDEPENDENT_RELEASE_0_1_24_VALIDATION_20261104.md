# Independent Commissioning Release 0.1.24

Recorded 2026-11-04 after successful archive, extracted-source and APK checks.
This is an offline deployment-preparation milestone, not robot installation or
complete product acceptance. The source baseline is
`ed9744ad0b9f884f735d79d8e07c873698c158fb` plus the 0.1.24 version and mandatory
installed provisioning helpers in the release-scope checker. This record is
outside the immutable archive to avoid a circular checksum rebuild. The earlier
0.1.23 archive/APK and their records remain unchanged.

## Artifact Identity

Generated files are local under `artifacts/`, with adjacent SHA256 and JSON
verification reports. Archives, APKs and signing keys are not committed to Git.
The archive filename uses the builder's UTC date, not the workstation date.

### Robot ARM64 Archive

- Filename: `lingxi-next-0.1.24-independent-commissioning-20261103-linux-arm64.tar.gz`
- Bytes: `131665019`
- SHA256: `ec79d017e6017c43a09a92a506947dc36ae11f46a0f1b7bece6a922a1ebec67e`
- Profile: `independent-navigation-commissioning`.
- Verified file checksums: `13198`; literal native-source references: `86`.
- Bundled Node/Java headers: ELF64 AArch64; executable modes verified.
- `classification=commissioning`, `completeProduct=false`, `productionAccepted=false`.

### Landscape Android APK

- Filename: `lingxi-next-0.1.24-commissioning-5cb0d7744db8.apk`
- Bytes: `3298735`
- SHA256: `15ca6f0fe9c7f6878af26c54e191e64a327891b8bbd84b80e3515e72d7e92956`
- Version: `0.1.24`; Android version code: `25`.
- Signature verified using the existing local commissioning key.
- Certificate SHA256: `6a209440c6cf55c8f9734549d7521755fb6915ce2f7217e0a6d86d665b526946`
- Web source SHA256: `5cb0d7744db8f722a91733f8dfda3708dc513f330f7ed837c81a54235dc79b3d`
- Android source SHA256: `2c4f6cc6b0eb8e9054d42d1970c7ab324011a6740582885a1cd949ab62689059`
- `sensorLandscape`, cleartext traffic disabled, application backup disabled.
- `productionAccepted=false`, `deviceAcceptance=false`.

The core, mapping and shared-SLAM Web identities match the APK. The staged API
and three runtime entrypoints match their current builds. The staged Agent JAR
matches the current Maven build, SHA256
`cf377385d259746a71895f94f4c9587bb7f2db497bd46d4d46829da9acda7092`.

## Changes Included Since 0.1.23

- Explicit isolated Python selection for installed mapping/shared-SLAM owners,
  bounded import preflight and no implicit system-Python fallback. The existing
  offline wheel provisioner creates its venv at the final service-owned path.
- File-only preparation of verified observed ARM64 mapping inputs into a new
  private service-accessible directory. Original receipts remain byte-identical;
  only five wrapper directory paths change, not calibration or acceptance.
- Optional paired native-input selection in installed startup, with a pinned
  completion receipt, matching config and bounded file verification before an
  owner is created. The existing explicit manual-config route remains available.
- Installer creation of the private native-input parent, installed lifecycle
  regression coverage and mandatory packaging of all three provisioning helpers.
- Earlier acquisition ARM64 emulation and frozen-ground-evidence source checks
  from `a8229c3` are included. Those native runs were not repeated in this
  packaging milestone and are not hardware acceptance.

Point-cloud display and Z defaults of 0.1 to 1.4 metres are retained. Mapping
movement stays on the external remote; Web/APK add no walking teleoperation.
See [installation](INDEPENDENT_INSTALL_20261103.md),
[isolated Python](INSTALLED_SLAM_PYTHON.md) and
[native input preparation](INSTALLED_NATIVE_MAPPING_INPUTS.md).

## Verification In This Milestone

| Check | Result |
| --- | --- |
| `npm run release:independent-commissioning` | Passed, exit 0 |
| Release Vitest | 750 passed, 25 explicit skips; 42 files passed, 2 skipped |
| API Python in release build | 56 run, 55 passed, 1 explicit skip |
| Web/API/runtime TypeScript and production builds | Passed |
| Current pinned-SDK Maven verify on Windows | 32 run, no failures/errors, 1 platform skip |
| Release-scope tests | 9 passed, including missing installed-helper rejection |
| Source filtering | 2 passed, 1 Windows symlink skip |
| Native-input tests on Linux, with selected real private inputs | 11 passed |
| Isolated-Python/provisioner tests on Linux | 23 passed |
| Disposable-chroot installed deployment lifecycle | 2 passed |
| Shared-SLAM bundle tests on Linux | 12 run, 11 passed, 1 host-Node skip |
| Installer/launcher Bash syntax and Git whitespace | Passed |
| Final archive checksums, inventory, source references, ARM64 headers/modes | Passed |
| Staged source equality | All 360 source files match current project bytes |
| Actual archive source extraction on ordinary-user Linux | Passed, exit 0 |
| Extracted source equality | All 360 source files match current project bytes |
| Extracted executable source smoke check | 19 actual modules imported under isolated Python |
| Extracted native-input verification | 142 actual private files reverified, original receipt pin unchanged |
| `npm run android:build` and APK verification | Passed, exit 0 |
| Three staged Web copies and APK Web identity | Match |

The extracted-source check used a fresh Linux process with no project
PYTHONPATH. All 19 modules came from the actual extracted archive. It also
checked 34 source-bound hashes and the five-package runtime lock. Mapping
`ctypes.CDLL` loads and child creation were forbidden throughout that process;
`rclpy` was not imported. The private native-input completion receipt remains
`31f42ae31c43cb2d6508a6420359884197a41b7ce3c510ed6b225af34f12ca16`.
All its acceptance boundaries remain false. Those private files are external
test inputs, not contents of the delivery archive.

An initial extraction did not run because its `/tmp` parent no longer existed;
it is not counted as a successful check. A fresh `/var/tmp` directory was then
created and the actual extraction and smoke check exited successfully. Ancillary
memory-command and XML-summary parsing errors were corrected/not counted;
Maven text reports supply the test totals. Nonfatal Maven systemPath, Rollup
annotation and Android tool/SDK path warnings remain; the builds and independent
archive/APK verifiers exited successfully.

## Remaining Target Acceptance

No robot connection, installation, cloud voice session, microphone capture or
movement IO was used. No robot service was installed, enabled or replaced.
The full scientific/native suite was not rerun. These results prove packaging,
import closure and file preparation, not installed ARM64 native execution.

ROS Humble/Nav2, verified native dependencies, the isolated Python wheelhouse,
robot-specific configuration/calibration and private credentials remain
external. This archive is not a self-contained native stack. Current sensor
timing/TF, metric/gravity/ground/base calibration, real map save/reload and
localization, physical navigation/stopping and preset actions remain unaccepted.
Preserved real terrain data still has zero supported free cells. Neither a
prepared input directory nor a running management endpoint admits navigation.

Real AgentSDK/cloud voice, wake/rejection accuracy, audible P95 <=2 seconds and
Android device behavior remain unaccepted. The Nav2 startup mitigation is not
a demonstrated FastDDS root-cause repair. Proceed with controlled target
validation, not unrestricted product delivery.
