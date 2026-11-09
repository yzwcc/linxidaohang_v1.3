# Android 0.1.30 target-aligned build

Date: 2026-11-09. Windows build and package verification, with read-only
comparison against the currently deployed robot Web application.

`npm run android:build` completed successfully: frontend, API and runtime
TypeScript checks, Vite production build, Capacitor sync, Gradle release
assembly, signature checks and source/package verification all passed.

Artifact: `artifacts/lingxi-next-0.1.30-commissioning-45da8f91c103.apk`
(3,298,742 bytes). The APK and adjacent `.sha256`, `.build.json` and
`.verification.json` remain local and ignored by Git.

| Identity | SHA-256 |
| --- | --- |
| APK, independently recalculated | `d221a512abb7d0b5c7e255e43307a3101fd8b0ac6e6d39d15c7ce3664ff2fc33` |
| Web sources | `45da8f91c10328ebc2e00de46626dc9ae989b67c168d1b1841eba33ef9e874b4` |
| Native/configuration sources | `d06bc78492abaa95b5ed263ecc2145f1444b6d30105e60cb6203ac8ceab23cef` |
| Local commissioning certificate | `6a209440c6cf55c8f9734549d7521755fb6915ce2f7217e0a6d86d665b526946` |

The verifier confirmed application ID `com.agibot.lingxinext`, version
0.1.30/code30, APK signatures, `sensorLandscape`, disabled backup and
cleartext traffic, and all five packaged Web assets. The build inventory
and current local source identities matched. No signing material or robot
credentials are included in the delivery artifact.

The complete `lingxi-build.json` served by the robot on HTTPS port3443 was
equal to the local build identity used for this APK. This binds the APK to
the Web source version deployed in
`0.1.30-independent-commissioning-20261106`; it does not prove Android
runtime operation. Current robot mapping was idle and ready for capture,
using live input and the 300-second limit. The only saved map at this check
was the near-static map documented in
[the target mapping record](TARGET_MAPPING_SAVE_RELOAD_20261109.md).

Nonfatal Gradle/Android SDK metadata and dependency annotation warnings
did not prevent the verified build. They do not establish or invalidate
physical device acceptance.

`classification=commissioning`, `productionAccepted=false` and
`deviceAcceptance=false` remain explicit. Physical Android installation,
robot CA trust, pairing, point-cloud interaction and background/network
recovery are pending device verification. The moving field map, measured
geometry, saved-map localization, physical navigation/avoidance and voice
acceptance remain outstanding. No robot movement or capture restart was
performed for this build milestone.
