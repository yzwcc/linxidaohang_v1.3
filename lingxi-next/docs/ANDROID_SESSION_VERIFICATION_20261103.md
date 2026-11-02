# Web And Android Session Verification

Date: 2026-11-03. Offline Windows tests and a loopback demo API only.
No robot connection, deployment, sensor access, motion, Android device or
emulator was used for this milestone.

## Changes

- A paired connection owns its requests, polling, state subscription and resume
  listeners. Disconnecting or switching retires that ownership immediately.
- Aborted or late state, settings, control responses and authentication failures
  from a retired session cannot update or expire its replacement.
- Concurrent polls are coalesced. Newer pushed state takes precedence over an
  older in-flight poll; older state timestamps are rejected within a session.
- Foreground/network return replaces suspended refreshes and state sockets.
  Control requests are never retried or replayed by reconnection.
- Caller cancellation retains the request timeout. State sockets have a
  7.5-second liveness budget, including an incomplete opening handshake, and
  failed sockets immediately stop delivering messages.
- Successful mutations force a new poll. Android metadata is 0.1.20 / code21.

## Evidence

| Check | Result |
| --- | --- |
| Session/API tests | 37 passed in two files |
| Full TypeScript suite | 527 passed, 16 explicitly skipped; 34 files passed, 2 skipped |
| Frontend/API/runtime typechecks and Vite | passed |
| Android sync, Gradle release, source and package verification | passed |
| Browser desktop / landscape | inspected at 1280x800, 960x540 and 844x390 |
| Browser offline / online | interrupted status, then online again without re-pairing |
| Explicit disconnect / re-pair | returned to connection entry; pairing again opened a fresh online overview |
| Browser uncaught script errors | none recorded |

Browser checks used the current built UI at `http://127.0.0.1:3037`, with
development-only synthetic data. Screenshots and temporary state remain in
ignored local directories. The demo server and dedicated browser are stopped
after inspection. These checks do not establish phone WebView lifecycle or
physical Wi-Fi acceptance.

Artifact: `artifacts/lingxi-next-0.1.20-commissioning-e958275d582e.apk`

| Identity | SHA-256 |
| --- | --- |
| APK | `669338e5a56d45d889fea753539023e9c954ff32e4654fd6236fef71bfe46c52` |
| Web sources | `e958275d582e025448a33230e52c22a336943fec71631dfbe3807e7c69b4bf31` |
| Native/configuration sources | `b5d916840b84b73241e2c1ba90aad4a48249ad525bbcac38b54557d1414a2f23` |
| Local commissioning certificate | `6a209440c6cf55c8f9734549d7521755fb6915ce2f7217e0a6d86d665b526946` |

Compiled manifest matches 0.1.20/code21, `sensorLandscape`, disabled backup
and cleartext traffic; APK v2/v3 signatures and all five packaged web assets
were verified. Nonfatal build warnings remain documented in
`ANDROID_SOURCE_VERIFICATION_20261101.md`; successful packaging is not device
runtime acceptance.

The APK, build inventory, verifier report, dependencies and signing material
remain local and ignored by Git. `productionAccepted=false` and
`deviceAcceptance=false`. CA installation, physical Android LAN operation,
background/foreground and Wi-Fi switching still require device acceptance.
Robot acquisition, geometry calibration, navigation/avoidance and acoustic
voice acceptance remain separate outstanding work; `navigationReady` is not
enabled by these tests.
