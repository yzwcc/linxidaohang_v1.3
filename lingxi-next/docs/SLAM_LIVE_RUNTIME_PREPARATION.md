# Isolated ARM64 Live Point Runtime Preparation

Date: 2026-10-29. A source-bound runtime archive now combines the bounded live
sensor owner, inherited-point HTTPS API and compiled web console. This phase
only builds, packages, extracts and checks those files offline. It does not
execute this composition on ARM64, subscribe to robot sensors or validate
live rendering. The product/APK version remains 0.1.19; default services,
installers, AIMDK, maps and the frozen native adapters are unchanged.

## Package Contents

`robot-runtime/build-live.mjs` snapshots the explicit frontend, API, runtime,
SLAM Python, shared contracts, tool helpers and required metadata. It bundles
the API and builds the existing Vite frontend without loading environment
files or exposing custom environment variables. It checks that source bytes
did not change during the build.

`robot-runtime/python/prepare_live_bundle.py` reuses the previously verified
ARM64 Node 22.23.4 binary and all 95 locked production packages. It neither
installs dependencies nor executes ARM64 code. It includes mirrored sources,
compiled API/web assets, dependency identities and build references.

Native GLIM/core/point bundles, factory or diagnostic profiles, TLS keys,
pairing credentials, recordings, maps and business data remain external.
The archive is a private diagnostic runtime, not the final product installer.

Two independent builds produced identical manifests and assets. One build
had an explicit synthetic `VITE_` environment marker; the marker was absent
from every compiled asset. Two subsequent independent preparations produced
byte-identical archives, manifests and independently exported aggregates:

| Field | Value |
| --- | --- |
| Source files | 139 |
| Compiled assets, including API | 5 |
| Runtime files, including manifest | 7,715 |
| Locked production packages | 95 |
| Archive bytes | 54,004,306 |
| Target executions in this phase | 0 |

Build manifest SHA256:

```text
5d7b9e84002c4883568a65e4a73d323ab5c761318e4b96f7c031d768f395307a
```

Runtime manifest SHA256:

```text
7f33db2277eef8d38dbd3d9e8700eb0282af25e48ee3a7fd26ef386fac3887b2
```

Archive SHA256:

```text
df741b3d6917532049411576f8e0110d726c0fdaeda46a4bae155ec58a908cf3
```

The aggregate is `slam-live-runtime-preparation-evidence.json`. Preparation
directories and archives remain outside Git. Earlier packages remain
preserved, but their source snapshots are stale and must not be substituted.

## Verification And Extraction

`live_bundle.py` verifies exact source/build/dependency identities, the Node
ELF architecture, installed-package inventory and local HTML asset references.
Every archive member is checked for content, size, regular-file type, mode,
deterministic ownership/time and complete membership. Published dependency
dotfiles are retained byte-for-byte. Long paths permit only canonical
path-only PAX metadata.

Extraction requires externally supplied archive and manifest hashes and a
new private destination. It checks every member before extraction, then
rechecks the extracted tree against its mirrored sources. It does not invoke
Node, ROS or native libraries.

```bash
rtk proxy python -B robot-runtime/python/prepare_live_bundle.py verify \
  --preparation "$PRESERVED_PREPARATION" --output "$NEW_AGGREGATE_JSON"

rtk proxy python -B robot-runtime/python/prepare_live_bundle.py extract \
  --archive "$VERIFIED_ARCHIVE" --archive-sha256 "$ARCHIVE_SHA256" \
  --manifest-sha256 "$MANIFEST_SHA256" --output "$NEW_PRIVATE_RUNTIME"
```

Actual offline extraction completed successfully. A fresh isolated Python
process then loaded the extracted launcher without inherited `PYTHONPATH`,
reverified the package and reached the expected x64 platform rejection before
creating trial output or executing ARM64 code.

## Read-Only Trial Boundary

`launch_live_bundle.py` verifies both the extracted package and the executing
source tree before delegating to the existing bounded owner. Helper imports
resolve from that source root, not the caller's working directory. Output
cannot overlap the package or frozen native inputs.

It requires a new private output, packaged web root, fresh trial data, a
nonzero explicit port other than 3000, production TLS/pairing configuration
and current onsite stationary/supervision/emergency-stop confirmation.
Credentials are supplied privately outside source control and never included
in the package or this command template:

```bash
rtk proxy /usr/bin/python3 -B \
  "$VERIFIED_RUNTIME/source/robot-runtime/python/launch_live_bundle.py" \
  --bundle "$VERIFIED_RUNTIME" --manifest-sha256 "$MANIFEST_SHA256" \
  --root "$VERIFIED_LIVE_SENSOR_BUNDLE" --core-root "$VERIFIED_CORE_BUNDLE" \
  --point-bundle "$VERIFIED_POINT_BUNDLE" \
  --point-manifest-sha256 "$POINT_MANIFEST_SHA256" \
  --hypothesis header_is_scan_start \
  --output "$NEW_PRIVATE_TRIAL" --seconds 60 --operator-confirmed
```

The scan-start choice remains an explicit diagnostic hypothesis, not accepted
timing calibration. Runtime is bounded to 5-240 seconds plus startup/cleanup.
This template is not evidence of target execution or current onsite conditions.

The launcher forces `LINGXI_POINT_READ_ONLY=1`. This optional API mode permits
GET/HEAD/OPTIONS, pairing and session revocation; all other mutation requests
return 403 before dispatch. Default API behavior is unchanged. The trial has
no UI/API path to start mapping, navigation, actions or configuration changes.
Point delivery still uses a paired WebSocket and the separate owned socket.

## Local Checks

Fourteen new Python contracts pass on Windows and Linux, including deterministic
preparation, complete extraction, source/dependency identity, independent
evidence counts, standalone imports and launcher isolation. Fixtures use
synthetic assets and a nonexecutable ARM64 header; they do not run target code.

The extracted API and its packaged production dependencies also pass all
23 Linux x64 Node checks: 20 actual process/lifecycle cases and three source
inventory checks. Sensors and packets in these cases are explicitly synthetic.
The suite covers paired point delivery, read-only dispatch, stale/EOF handling,
peer/parent failure and bounded natural shutdown. It is not live GLIM evidence.

TypeScript passes 155 tests; strict frontend/API/runtime type checks and the
production frontend build pass. Complete Python tools regressions pass 762
tests on Linux with no skips. Windows runs 762: 384 pass and 378 are explicitly
skipped for unavailable platform/dependencies. The Linux run uses the existing
geometry environment, clean pinned upstream checkout, explicit Linux x64
Node and the verified recorded consumer.

## Still Required

An actual identity-checked ARM64 live trial, sensor-to-screen latency,
desktop/Android rendering, native failure cleanup on target, calibration and
robot-base alignment remain unverified for this composition. Map transactions,
loop closure, relocalization, independent navigation, voice concurrency and
final product acceptance also remain open. No readiness flag is raised by
packaging, successful process closure or synthetic point delivery.

See `SLAM_LIVE_POINT_OWNER.md` for ownership and `SLAM_POINT_COMPOSITION_TARGET.md`
for the separate completed recorded ARM64 composition.
