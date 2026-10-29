# ARM64 Recorded Point Consumer Preparation

Date: 2026-10-29. The ARM64/Humble recorded-point binding and its separate
Node consumer runtime are now prepared offline. **Neither the new ARM64 Node
binary nor this target producer/consumer composition has executed on the
robot during this preparation.** The later actual recorded execution and
independent result are in `SLAM_POINT_COMPOSITION_TARGET.md`.
The product and APK remain 0.1.19; no robot contact, sensor capture,
ROS initialization, motion, service installation or vendor-map change occurred
in this preparation.

## Prepared Consumer

`tools/prepare_glim_point_consumer.py` reuses the exact consumer bundle and
transport sources from `slam-native-point-pipe-evidence.json`, rather than
rebuilding or changing the previously verified consumer. It checks the pinned
ARM64 Node archive and copies only its binary and license.

The ordinary-user Linux x64 preparer runs the existing locked production
dependency install with lifecycle scripts disabled and a separate private
npm home, configuration paths and cache. Native Node addons, dependency
symlinks and `.bin` entries are not included. Each installed package must
match the production lock entry; the project lockfile is unchanged.

Two independent preparations produced byte-identical manifests and archives:

| Field | Value |
| --- | --- |
| Prepared Node version / architecture | 22.23.4 / AArch64 |
| Archive bytes | 53,036,165 |
| Bundle files, including manifest | 7,565 |
| Installed production packages | 95 |
| ARM64 Node executions in this phase | 0 |

Manifest SHA256:

```text
e35d411cf7cde7b8bca4b76717accaf2a137ca5512c2f1fe8fcf3f6f1a106521
```

Archive SHA256:

```text
c76883e3b10d5df921311012f2b918cd5ae09e0fa082051c84991b4c7197778d
```

This is a private diagnostic archive, not a product installer. It contains
no recordings, XYZ, poses, robot business data or credentials. Dependency
trees, archives and installation logs remain outside Git.

`tools/export_glim_point_consumer.py` independently rehashes the unpacked
bundle and every archive member, checking inventory, sizes, regular-file
type, modes and deterministic owner/time metadata. It exports only named
aggregate fields to `slam-point-consumer-preparation-evidence.json`.
Exports from the two independent preparations are also byte-identical.
All target-execution, live-display, mapping and navigation acceptance flags
remain false.

## Target Binding

`tools/glim_point_supervised_target.py` composes the existing recorded session,
worker, supervisor, native point extension and transport. It does not modify
the frozen native core, replay adapter, worker, stream or configuration.

- The installed Humble message deserializer and type support decode the
  preserved callback journal without calling `rclpy.init` or subscribing.
- The same explicit native library paths and OpenMP/OpenBLAS 2/2 environment
  are required. Actual mapped native library bytes must match the frozen
  target evidence, including the separately verified point extension.
- Health stdout remains separate from the private Unix point socket.
  Both producer and consumer explicitly classify all input as `recorded`.
- The standalone consumer uses its own pinned ARM64 Node and locked
  dependency tree. Architecture/version, complete packet-content equality,
  unchanged paired-API readiness and normal owned-process cleanup are required.
- New private staging/output must not overlap the preserved inputs. The
  parent retains reports and closes both owned peers on failure. Fresh onsite
  stationary, supervision and accessible emergency-stop confirmation is
  required before input checks; a command-line flag alone is not evidence
  of those conditions.

These were implemented requirements, not claims of target execution. The
binding is not an installer or an SSH upload/collection coordinator. Target
transfer, extraction, current identity checks, an external execution deadline
and independent collected-result verification were still required at the
end of this preparation.

Those follow-up tools and the later actual target run are documented in
`SLAM_POINT_COMPOSITION_TARGET.md`. Neither outcome is inferred from this
preparation-only evidence.

## Local Verification

The 26 new tests use synthetic nonexecutable ARM64 headers, package fixtures,
archives, decoders and process peers. They check binding, cleanup, input/source
identity, dependency inventory and preparation-only evidence. They do not
load ARM64 code or contact a robot. Linux passes all 26; Windows passes 25
and skips the POSIX symlink fixture.

A separate official Linux x64 Node 22.23.4 control runtime executed the
unchanged consumer with the newly prepared dependency tree. All four existing
real-process tests pass: successful EOF, empty input, truncated input and
expired input. This validates the x64 consumer/dependency control and natural
shutdown, not ARM64 binary compatibility or native SLAM composition.

The full tools regression passes **703 tests on Linux with no skips**, using
the existing numerical/geometry environment, clean pinned upstream checkout,
explicit x64 Node 22 runtime and prepared consumer. Windows passes 340 and
explicitly skips 363 for platform/dependency requirements. No frontend, API,
Java or Android source changed; their builds were not rerun or described as
new acceptance.

Reproduce the independent preparation-only export without running ARM64 code:

```bash
rtk proxy python tools/export_glim_point_consumer.py \
  --preparation "$PRESERVED_PREPARATION" --output "$NEW_AGGREGATE_JSON"
```

## Remaining Work

Next validate the prepared ARM64 Node and recorded native producer/consumer
together on the identity-checked robot, using new private directories and
preserving failed results. The earlier extension loading/contracts are
documented in `SLAM_POINT_ARM64_TARGET.md`; the x86 recorded composition is
documented in `SLAM_RECORDED_POINT_CHANNEL.md`.

Live process supervision, point rendering in web/Android, sensor-to-screen
latency, calibration/prediction warnings, global-map transactions, loop closure,
relocalization, robot-base alignment, independent navigation and final
voice/product acceptance remain outstanding. No production backend or
existing release artifact has been switched by this milestone.
