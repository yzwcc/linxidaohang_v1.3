# ARM64 Recorded Point Composition Collection

Status on 2026-10-29: the LAN transfer/execution/collection and independent
verification tools are implemented. **They have not yet executed this
producer/consumer composition on the robot.** The actual ARM64 consumer
runtime, point channel and target process cleanup remain unvalidated.
This is not a product deployment or a new APK; version 0.1.19 is unchanged.

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
false even if the recorded target composition eventually passes.

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

Final full tools regression passes **725 tests on Linux with no skips**.
Windows passes 362 and explicitly skips 363 for platform/dependency
requirements. Linux uses the existing clean pinned upstream and numerical
environment, x64 Node 22.23.4 control runtime and the newly extracted consumer
directory. All four real consumer-process tests pass against that directory.
They execute the x64 control, not the prepared ARM64 binary. No frontend,
API, Java or Android source changed and those builds were not rerun.

Actual ARM64 recorded execution and independent collected evidence are the
next gate. They must not be inferred from the preparer, extraction check,
synthetic peers or the earlier extension-loading contracts.
