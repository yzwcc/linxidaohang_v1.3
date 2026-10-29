# Corrected Point Transport Components

Status on 2026-10-29: the corrected-point extension has cross-linked for
ARM64 without target execution. A separate bounded point codec, Linux pipe
writer, Node pipe receiver and optional management-API source now pass local
isolated tests. This is not a deployed live SLAM channel or a navigation
backend. Product/APK version remains 0.1.19.

A subsequent x86 recorded-input composition now connects the unchanged
supervised native worker to a separate Node point consumer and an isolated
paired API. All 164 packets match their producer content digest, both
processes exit naturally, and recorded points remain rejected as live input.
See `SLAM_RECORDED_POINT_CHANNEL.md` for the actual result, preserved first
failure and independent evidence. It does not validate live/UI/ARM64 use.

## ARM64 Build Evidence

`tools/build_glim_point_arm64.py` reuses the frozen ARM64 core, checking its
library hashes against the previous target execution evidence. It snapshots
the point-extension source and builds in Release mode using the existing
sysroot/toolchain. It does not rebuild or replace the vendor AIMDK.

`docs/slam-native-point-arm64-build-evidence.json` was independently rechecked
against the private build report, current source and both actual ELF hashes:

- Both the extension and contract executable are ELF64 AArch64.
- The host ran one ABI-inspection CTest using cross `nm`, confirming exactly
  five exported C functions.
- Zero ARM64 native-contract executions occurred. This must not be described
  as the 12 native contracts passing on ARM64.
- The extension requires up to GLIBC 2.17; the separate contract executable
  requires up to GLIBC 2.34. Metadata is not a successful target load/run.
- No robot was contacted; no runtime release package or deployment was
  approved or produced by this cross-build.

Reproduction uses a new ignored output directory on the ordinary-user Linux
cross-build host. It never executes the generated ARM64 binary:

```bash
rtk proxy python tools/build_glim_point_arm64.py build \
  --cross-work "$FROZEN_ARM64_CORE" --native-work "$FROZEN_X86_TOOLS" \
  --output "$NEW_PRIVATE_POINT_BUILD"
rtk proxy python tools/build_glim_point_arm64.py export \
  --build "$NEW_PRIVATE_POINT_BUILD" --output "$NEW_AGGREGATE_EVIDENCE"
```

## Wire Contract

`point_data.py` and `robot-api/src/slam-points.ts` share the version-1
little-endian contract. Records are binary and separate from health stdout.
There is no remote upload endpoint or new listening port.

| Offset | Field |
| ---: | --- |
| 0 | Eight exact bytes `LXSPOINT` |
| 8 | uint16 version = 1 |
| 10 | uint8 input mode: live=1, recorded=2, synthetic=3 |
| 11 | uint8 flags = 0 |
| 12 | uint32 copied point count, 1 through 8,000 |
| 16 | uint32 native point count, copied count through 2,000,000 |
| 20 | uint32 reserved = 0 |
| 24 | uint64 native frame ID, 1 through 2^53-1 |
| 32 | float64 algorithm timestamp in seconds |
| 40 | float64 same-host monotonic emission time in seconds |
| 48 | 16 session bytes, externally 32 lowercase hexadecimal characters |
| 64 | 16 float64 values, row-major world-LiDAR pose |
| 192 | Copied count times three float64 world-coordinate XYZ values |

Exact length is `192 + count * 24`, at most 192,192 bytes. Frame 0 is
excluded. The encoder requires contiguous native IDs and increasing
algorithm timestamps; visual transport may drop records, so the receiver
allows gaps but never duplicate or decreasing IDs, timestamps or emission
times. Invalid data latches failure and clears the current snapshot.

Coordinates mean `lingxi_glim_odom`; underlying points come from the pinned
post-initialization, deskewed IMU-frame output transformed by `T_world_imu`.
The supplied pose is `T_world_lidar`, never a robot-base pose. The protocol
does not represent a global map, loop closure, localization or robot heading.
Both languages validate finite data, positive millisecond-representable
algorithm time, rotation Frobenius error <=1e-5, determinant error <=1e-5 and
bottom-row component error <=1e-7.

Same-host emission age must be no more than two seconds, with 50 ms future
tolerance. Cross-host clocks are not interchangeable. Production Linux clock
compatibility and actual LiDAR-to-screen latency still require measurement
in the eventual supervised target composition.

## Ownership And Backpressure

- `PointPipeOutput` is an opt-in callback for `PointOutputNative`. Create,
  call and close it on the native owner thread using an explicit session,
  input mode and dedicated supervisor-provided FD >=3.
- `LatestPointPipeWriter` owns that FD and a separate Linux writer thread.
  The native callback offers immutable encoded bytes without waiting for
  the consumer. There is at most one in-flight record plus one replaceable
  pending record, in addition to bounded operating-system pipe buffering.
  A partial record is finished before another begins; records never
  interleave. The writer does not validate geometry: the encoder does.
- Writer close discards pending/in-flight work and joins the thread with a
  bounded wait; close is not an acknowledgement of delivery. Broken-pipe
  errors latch failure. A persistently slow consumer may receive an expired
  record, which invalidates the receiver rather than refreshing stale data.
- `SlamPointPipeSource` owns an already-provided Node Readable. It parses
  fragmented or concatenated records with at most one bounded parser buffer.
  `LatestSlamPointSource` retains one decoded snapshot, not a replay queue.
  EOF, close, truncation or malformed data invalidate the source and destroy
  the input pipe. Reopening requires a new source/session; no auto-replay.
- The live-only `getLidarFrame()` omits `robotPose`. Its `receivedAtMs`
  preserves emission age, rather than pretending each API poll is new input.
  Recorded and synthetic modes can decode records but cannot serve them as
  live `LidarFrame` data.

The trusted caller may pass this source to `buildServer({ lidarSource })`.
Only LiDAR WebSocket polling changes; the default AIMDK adapter remains the
default source. Pairing, WebSocket authentication, bounded slow-client output,
mapping checks and navigation readiness are unchanged. No HTTP request can
select this source or elevate a point stream into mapping readiness.

## Executed Local Checks

- TypeScript: 142 tests pass, including 15 point codec/source, real Python
  child-pipe and authenticated API integration tests.
- Python tools: Linux 628 pass; Windows 275 pass and 353 explicitly skip for
  platform/dependency requirements.
- Frontend and API typechecks/builds pass. These are local build checks, not
  a new Android APK or robot installation.
- Linux tests exercise real pipes, partial large writes, replacement under
  backpressure, native-owner encoding, broken readers and bounded shutdown.
- Both codecs use `shared/fixtures/slam-point-v1.synthetic.json`, explicitly
  labeled isolated synthetic data. The child and API tests exercise a live
  mode byte only inside isolated tests; they are not recordings, real robot
  results or permission to label recorded input live.
- API tests prove points reach the paired WebSocket without using the native
  LiDAR method, without grids or a fabricated robot pose, and without changing
  failed mapping checks/navigation state. Expired/closed input returns
  `lidar-unavailable`.

## Still Required

There is no production supervisor-to-producer FD wiring, startup/environment
configuration, deployment integration or automatic source selection yet.
The synthetic child test uses a dedicated stdout for test point bytes, not
the real worker's health stdout. The real supervised worker must retain its
separate health channel. Native point output, pipe components and API source
have not yet executed together with real sensors or on ARM64.

The isolated supervised recorded-input composition and lifecycle tests are
now described in `SLAM_RECORDED_POINT_CHANNEL.md`. That diagnostic uses its
own private Unix stream FD; it is not production supervisor FD wiring.
Next are actual ARM64 extension loading/contracts and target recorded-data
validation under currently confirmed onsite conditions. Live composition
and UI acceptance remain separate later checks.

Factory-calibration discrepancies, IMU-noise/prediction warnings, sustained
throughput, global map transactions, loop closure, relocalization, base-frame
alignment and the independent navigation backend remain unresolved. Frozen
GLIM core/adapter/worker/stream/configuration, vendor services/maps, installers
and Android artifacts are unchanged by this milestone. No robot contact,
deployment, new sensor capture or motion occurred.
