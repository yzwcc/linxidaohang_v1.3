# ARM64 Recorded Point Composition Collection

Status on 2026-10-29: after fresh onsite stationary, supervision and accessible
emergency-stop confirmation, the complete recorded producer/consumer composition
has executed on the identity-checked ARM64 robot and passed independent
verification. Both owned processes exited normally. This is recorded-input
point-channel evidence, not live rendering, navigation or product deployment.
Version 0.1.19 and the existing APK are unchanged.

## Actual ARM64 Result

`slam-point-composition-arm64-evidence.json` contains only named aggregates from
the completed run. Two separate read-only exports are byte-identical. The
collector verified 119 uploaded source files and the unchanged 7,565-file
consumer package before executing the prepared ARM64 Node 22.23.4 and native
worker. Actual loaded library bytes match the prior frozen ARM64 observations.

| Observation | Result |
| --- | ---: |
| Completed original callbacks | 4,144 |
| Received clouds / IMU | 197 / 3,947 |
| Native frames / inserted IMU | 196 / 3,930 |
| Initialization scans / estimated poses | 31 / 165 |
| Corrected packets sent / received | 164 / 164 |
| Copied points | 1,246,242 |
| Copied points per packet, min / max | 7,539 / 7,779 |
| Writer replacements / retired received packets | 0 / 0 |
| Native create / close | 1 / 1 |
| Native process / consumer exit code | 0 / 0 |
| Forced termination / surviving writer thread | false / false |

The complete producer and consumer packet-content digest is:

```text
04ebd0038beb8eeb9f9acae0e166fcea7fc651607eb8c65a448f073cd1305cde
```

This establishes equality within this ARM64 trial, not numerical equality with
the x86 trial or physical mapping accuracy. At one-times replay, native-frame
wall P95 was about 65.79 ms and maximum callback queue age about 95.74 ms.
Neither measurement is sensor-to-screen latency; their populations differ and
their percentiles must not be added.

The paired API continued to reject recorded points as live before and after
EOF. Mapping, localization and navigation readiness stayed false. There was
no new DDS subscription/capture, ROS context, speech, motion command, vendor
service/SDK/map change or production installation.

## Preserved Preflight Failure

A first Windows-host preparation check was interrupted before a collection
directory or robot connection was created because reading the Linux dependency
tree across the UNC boundary was slow. The coordinator then ran from a separate
Linux-local Python environment; project dependencies were not changed.

The first robot-side read-only preflight selected the parent live-capture
bundle instead of the expected matched-callback bundle. Its manifest mismatch
was rejected before any upload, Node or native execution. The failed collection
remains preserved. A new run selected the independently known matched bundle,
retained the original expected digest and completed successfully. No identity
guard was relaxed and no existing result directory was reused.

## Local Extraction Check

`tools/extract_glim_point_consumer.py` checks the pinned archive and manifest
digests, bounded direct relative inventory, every archive member's bytes and
metadata, and the complete unpacked consumer. It creates only a new directory
and never executes Node or a native library.

The actual previously prepared archive was successfully extracted to a new
private ordinary-user Linux directory. All **7,565 files** match the original
manifest. Its archive remains 53,036,165 bytes with SHA256:

```text
c76883e3b10d5df921311012f2b918cd5ae09e0fa082051c84991b4c7197778d
```

No ARM64 code ran during this check. The original preparations, archive and
unpacked inputs were preserved. See `SLAM_POINT_CONSUMER_PREPARATION.md` for
the unchanged runtime/dependency provenance.

## LAN Coordinator

`tools/run_glim_point_composition_target.py` composes the existing SSH identity
check, remote file hashing, ordinary-user private-directory checks and
recorded native/consumer binding. It requires current onsite stationary,
supervision and accessible emergency-stop confirmation before reading inputs
or connecting. A historical message or command-line flag alone is not current
onsite evidence.

The new coordinator:

1. Independently verifies the local prepared consumer against the published
   preparation aggregate and inventories the exact source bytes to transfer.
2. Checks the saved robot identity, ordinary-user ARM64/Python/glibc runtime,
   available disk and explicit preserved core, replay and point manifests.
3. Requires separate new private source, consumer and result directories.
   Existing source/core/recordings/extension bundles are never overwritten.
4. Uploads and rehashes every source and the unchanged consumer archive, with
   restrictive modes and a 30-second SFTP operation timeout. A separate bounded
   process verifies/extracts the archive.
5. Executes only the prepared Node's version/architecture observation before
   starting the recorded composition, using a clean explicit environment.
6. Executes the unchanged ARM64/Humble binding with an external 180-second
   INT deadline and a ten-second kill-after bound. Native health stdout and
   point bytes remain on separate channels.
7. Collects all six parent/child/point/consumer reports and private logs,
   checking remote versus downloaded bytes. Partial results are retained on
   failure, with no automatic retry or output-directory reuse.
8. Requires the same source bytes, prepared consumer, native libraries,
   complete input accounting, matching point content and natural process
   exits before declaring a complete collection. The SSH connection closes.

The target uses the preserved callback journal, not current DDS subscriptions.
No new sensor capture, ROS initialization, robot command, vendor-service
change, map transaction or installer is part of this command. The API
consumer is isolated without a TCP listener; recorded points must continue
to be unavailable through its authenticated live LiDAR WebSocket.

## Independent Result Verification

`tools/export_glim_point_composition.py` rechecks the completed collection,
all private report/log hashes, current source inventories and prepared archive.
It also checks:

- Current onsite-confirmation and host-identity collection fields.
- Matching preflight/runtime manifests, Python/Node architecture and versions.
- The prior independently observed ARM64 native library digests.
- Exact uploaded/staged/local source equality and unchanged supervision limits.
- Complete original callback/native counters, single native owner and closed
  worker, writer, native process and consumer process without forced exit.
- Contiguous point IDs and equal full producer/consumer content digests.
- Recorded classification and unchanged paired-API readiness.

Only named aggregates are exported. Private filesystem paths, host address,
session IDs, credentials, CDR, XYZ, poses and log text are excluded. Live point
rendering, latency, map/localization/navigation and product acceptance remain
false despite the successful recorded target composition.

The exporter performs reads/hashes only:

```bash
rtk proxy python tools/export_glim_point_composition.py \
  --trial "$COLLECTED_PRIVATE_RESULT" \
  --preparation "$ORIGINAL_PRIVATE_CONSUMER_PREPARATION" \
  --output "$NEW_AGGREGATE_JSON"
```

## Test Boundary

Twenty-two new tests use synthetic nonexecutable ARM64 headers, small package
archives, explicit synthetic reports and in-memory SSH peers. They cover
extraction, identity/path/input gates, upload/download checking, preserving
partial failures, cleanup requirements, content matching and aggregate-only
export. They are not robot execution evidence.

Initial focused runs on both platforms had one failed assertion because a
digest-mismatch test assigned its fixture's existing digest. That no-op
mutation was corrected; the production comparison was unchanged.

The collector implementation milestone passed 725 tests on Linux and
362 on Windows with 363 platform/dependency skips. Two subsequent read-only
published-result tests cover the actual ARM64 counters, source identities,
cleanup, private-field exclusion and explicit non-acceptance boundaries.

Final full tools regression now passes **727 tests on Linux with no skips**.
Windows passes 364 and explicitly skips 363 for platform/dependency requirements.
Linux uses the existing clean pinned upstream and numerical environment,
x64 Node 22.23.4 control runtime and the extracted consumer directory. All
four real consumer-process tests pass against that directory. These local
tests execute the x64 control, not the prepared ARM64 binary. The separate
new entrypoint adds 7 unit tests and 8 Linux process checks; all 153 TypeScript
tests, strict frontend/API/runtime checks and all three builds pass. Java,
APK and installers were not rebuilt or changed.

The actual ARM64 result above is distinct from the preparer, extraction check,
synthetic peers and earlier extension-loading contracts. The separate opt-in
HTTP/WebSocket startup wrapper and its Linux x64 process checks are documented
in `SLAM_POINT_RUNTIME.md`; it is not installed on the robot by this trial.
Live sensor ownership, web/Android rendering, measured display latency,
calibration warnings, map transactions, loop closure, base alignment,
relocalization, independent navigation and end-to-end voice acceptance remain
outstanding.
