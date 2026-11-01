# Android Source-Bound Commissioning Build

Date: 2026-11-01. Offline Windows build only. No robot or Android device contact.

## Changes

- Android versionName/versionCode now come from validated product metadata,
  replacing the stale hardcoded 0.1.14/code15 with 0.1.19/code20.
- Vite emits `lingxi-build.json`, identifying current web/shared sources and
  build configuration. Source changes during compilation fail the build.
- Android packaging rejects missing/stale web builds and unsynchronized assets.
  Native/configuration/build-tool inputs are recorded before and after Gradle.
- The APK has checksum, source inventory and verification sidecars. Verification
  checks the compiled manifest, modern signature, landscape, disabled backup and
  cleartext, local CA trust, exact web assets and embedded source identity.
- Independent verification recomputes current source and sync inventories. These
  are local build provenance checks, not remote attestation or reproducible-build
  guarantees. The signing certificate digest is reported for owner comparison;
  it is not a production certificate allowlist.

## Evidence

Artifact: `artifacts/lingxi-next-0.1.19-commissioning-34066d326ccc.apk`

| Check | Result |
| --- | --- |
| APK SHA-256 | `1df3bf8af24c959e26962bbf02c58ef90968bdc3fa3920b7b1bb093f106419b0` |
| Web source SHA-256 | `34066d326cccd2721e24f68ae6bd9b109aba5fa48a478e25bd75d222898deb09` |
| Native/config source SHA-256 | `70d356506870399155a4045cbe81961f9222ded876fc1fa607d48ae2777f6971` |
| Local commissioning certificate SHA-256 | `6a209440c6cf55c8f9734549d7521755fb6915ce2f7217e0a6d86d665b526946` |
| Product metadata / compiled manifest | 0.1.19 / code20, matched |
| Signature / orientation | verified v2/v3 / sensorLandscape |
| TypeScript suite | 502 passed, 16 skipped |
| Focused build-identity tests | 4 passed, included above |
| Python Android verifier rejection tests | 4 passed |
| Frontend/API/runtime typechecks and Vite | passed via `npm run android:build` |
| Gradle release / packaged assets | passed / 5 web assets matched |

Nonfatal tool warnings: Zod/Rollup comment annotations, Android flatDir/experimental
path check, SDK XML-version mismatch, and an aapt2 android.jar stat warning.
Gradle completed successfully and independent compiled-resource inspection
passed; Android runtime behavior is not inferred from these checks.

Generated APK, sidecars, dependencies and signing material remain local and
ignored by Git. The package has `productionAccepted: false` and
`deviceAcceptance: false`. Device CA import, LAN connection/reconnection,
landscape UI and point picking remain explicit field acceptance items.
