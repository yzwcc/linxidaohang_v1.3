# Supervised Recorded Point Channel

Status on 2026-10-29: a complete x86 recorded-input native GLIM worker now
delivers corrected point packets through a private same-host Unix stream to
a separate Node consumer and an isolated paired management API. Both owned
processes exit naturally. This is offline composition evidence, not a live
point-cloud display, target ARM64 point-channel acceptance, or navigation.
The deployed product and APK remain 0.1.19.

## Composition

`tools/run_glim_point_pipe_trial.py` composes existing components without
changing the frozen core, native replay adapter, input worker, stream,
configuration or process-supervision limits:

- The original `ProcessSupervisor` owns the native process and its health
  stdout. That channel contains only bounded health events, never point bytes.
- The native owner opens a separate Unix socket and passes its detached FD
  to `PointPipeOutput`, using an explicit session and `recorded` mode.
- The existing bounded writer sends binary records without blocking the
  native callback. This diagnostic requires every packet to arrive; any
  replacement, writer failure or incomplete delivery fails the whole trial.
- `tools/slam-point-api-consumer.ts` runs as a separate process. It verifies
  contiguous native IDs and hashes each accepted packet's native frame ID,
  algorithm timestamp, native point count, XYZ bytes and world-LiDAR pose
  bytes in the same order as the Python producer.
- The consumer injects this source into an in-process paired Fastify API.
  An offline adapter supplies no sensors, maps or readiness. The authenticated
  LiDAR WebSocket must return `lidar-unavailable` during replay and after EOF.
  Recorded points are decoded and checked, not rendered or relabelled live.
- The API has no TCP listener. The output directory is mode 0700 and the new
  socket is mode 0600. No ROS node, robot connection, capture, deployment,
  speech or robot command is used.

The ready handshake follows API initialization and its first unavailable
event, before the consumer accepts the native connection. EOF triggers the
API guard check. Both owned injected WebSocket peers are terminated before
closing Fastify; the socket/source and deadline are also closed. Success
requires natural process exit, not merely a written success report.

## Actual Recorded Result

`slam-native-point-pipe-evidence.json` is the allowlisted evidence from the
second complete trial, independently exported twice with identical bytes.

| Observation | Result |
| --- | ---: |
| Completed original callbacks | 4,144 |
| Received clouds / IMU | 197 / 3,947 |
| Native frames / inserted IMU | 196 / 3,930 |
| Initialization frames / estimated poses | 31 / 165 |
| Corrected packets sent / received | 164 / 164 |
| Copied points across packets | 1,246,242 |
| Copied points per packet, min / max | 7,539 / 7,779 |
| Writer replacements / retired received packets | 0 / 0 |
| Native create / close | 1 / 1 |
| Native process / consumer exit code | 0 / 0 |
| Forced termination / surviving writer thread | false / false |

The producer and consumer packet-content digest is
`b3f2f0f57cb7194b6fef1117c172ea0c4e3353b56042aba2d0eda9ee64039b21`.
This compares the entire delivered packet sequence, not only first/latest
samples. It is not an accuracy comparison with physical ground truth.

The one-times replay observes a maximum callback queue age of about 54.36 ms
and native-frame wall P95 of about 37.86 ms across 196 frames. These are
recorded-worker observations, not sensor-to-screen or physical robot latency.
The scan-start timestamp hypothesis remains an unvalidated diagnostic choice.

The point frame remains `lingxi_glim_odom`, with a world-LiDAR pose, not a
robot-base pose. This channel does not produce a saved global map or validate
loop closure, base alignment, localization or independent navigation.

## Preserved Failure

The first complete trial delivered the same packet/point counts and matching
producer/consumer content digests within that trial, but its Node consumer
remained alive beyond the five-second post-native closing limit. The parent
failed the trial and terminated that owned process. Its reports, private logs
and runtime snapshot remain preserved; trial A is not successful.

Subsequent isolated checks distinguished startup from post-ready closing:
a cold mounted-filesystem module import took 6.80 seconds. Startup uses the
existing 15-second consumer budget; post-ready natural-exit regression still
uses three seconds, and the native trial still allows five seconds after the
native process finishes. API readiness ordering and owned WebSocket cleanup
both changed. No single cause of the original hang has been proven.

## Independent Export And Tests

`tools/export_glim_point_pipe_evidence.py` reads and hashes preserved inputs
without executing native code. It requires:

- The successful parent result and matching report-file hashes.
- Identical current inputs, source inventories and staged runtime-source bytes.
- The same owned session, replay rate, native libraries and point extension.
- Complete worker/native counters, zero callback rejects/discards, normal
  process cleanup, no writer failure or replacement, and a closed writer thread.
- Equal producer/consumer packet counts, contiguous IDs, point counts and
  packet-content digest, plus the recorded/live API isolation checks.
- No readiness elevation, robot contact, service change or deployment claim.

The exporter serializes explicit aggregate fields only. Private XYZ, poses,
CDR, session IDs, filesystem paths and logs are not exported. Synthetic
report tests cover inconsistent counts/digests, incomplete cleanup, changed
source/input identity, altered staged source, report-byte changes, numeric
versus Boolean fields, private-field exclusion and repeated exports.

Optional Linux process tests require explicit `LINGXI_NODE_LINUX_BIN` and
`LINGXI_POINT_CONSUMER_BUNDLE`. They exercise a real Node process, private
socket and paired API using clearly isolated synthetic data: successful EOF,
empty input, truncated input and expired input. Failure cases must exit
naturally without a success report. No native SLAM or robot is used by them.

Final local regression: TypeScript 146 passed; Linux tools 648 passed with
the explicit Node/bundle and clean pinned upstream override; Windows tools
287 passed and 361 skipped for platform/dependency requirements. The bridge
Python suite adds 39 passes. Strict consumer TypeScript checking and both
frontend/API builds pass. These are local checks, not a rebuilt APK or
installed robot release.

Build the diagnostic bundle with the existing locked dependencies:

```bash
rtk proxy npx --no-install esbuild tools/slam-point-api-consumer.ts \
  --bundle --platform=node --target=node22 --format=esm --packages=external \
  --outfile="$NEW_CONSUMER_BUNDLE"
```

Use an ordinary-user Linux Python environment with the already verified
NumPy/rosbags dependencies, a verified explicit Linux Node binary, preserved
inputs and a new private output directory. Enforce an external process-group
timeout; the completed trial used TERM at 160 seconds with a ten-second
kill-after bound. Keep all input/output arguments for subsequent export.

```bash
rtk proxy python tools/run_glim_point_pipe_trial.py run \
  --work "$FROZEN_CORE" --adapter "$FROZEN_ADAPTER" --points "$POINT_BUILD" \
  --bundle "$FROZEN_BUNDLE" --reference "$PRESERVED_REFERENCE" \
  --node "$NODE_LINUX" --consumer "$NEW_CONSUMER_BUNDLE" \
  --output "$NEW_PRIVATE_TRIAL" --hypothesis header_is_scan_start --rate 1.0
rtk proxy python tools/export_glim_point_pipe_evidence.py \
  --work "$FROZEN_CORE" --adapter "$FROZEN_ADAPTER" --points "$POINT_BUILD" \
  --bundle "$FROZEN_BUNDLE" --reference "$PRESERVED_REFERENCE" \
  --node "$NODE_LINUX" --consumer "$NEW_CONSUMER_BUNDLE" \
  --trial "$NEW_PRIVATE_TRIAL" --output "$NEW_AGGREGATE_JSON" \
  --hypothesis header_is_scan_start --rate 1.0
```

## Remaining Work

The cross-built point extension still needs actual ARM64 loading/contracts,
then a recorded point-channel composition on that target. Production sensor
supervision, FD ownership/startup configuration, real-sensor point output,
web/Android rendering and measured sensor-to-screen latency remain pending.
Calibration/noise/prediction warnings, map transactions, loop closure,
relocalization, base alignment, independent navigation and end-to-end voice
acceptance remain unresolved. Existing vendor services/maps, installers and
Android artifacts are unchanged by this milestone.
