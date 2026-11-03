# Isolated ROS Worker Verification

Date: 2026-11-04. This closes the offline watchdog/ROS blocking milestone,
not robot navigation or complete-product acceptance. No robot was connected,
deployed to or commanded.

## Implementation

The offline HTTP/API runtime no longer imports or initializes rclpy. Each goal
generation owns a separate Python ROS worker and the official Humble Nav2
component launch. The main process owns input/velocity deadlines, synthetic
integration and task state. Native publish, spin, construction or destruction
cannot hold that process's GIL. The earlier thread/session-node approach was
insufficient; its failed experiments remain in
[the component-startup record](NAV2_COMPONENT_STARTUP_20261104.md).

Communication uses an inherited private Unix SOCK_SEQPACKET pair. Strict JSON
packets are limited to 8192 bytes, bound to task/epoch, monotonically sequenced
and stamped at their source. There is no pickle or unbounded user-space queue.
Optional 50-Hz pose snapshots are dropped on backpressure. Old/future events
cannot renew readiness, and velocity retains its original source timestamp for
the parent's independent validation. A worker success event is also checked
against the parent's synthetic goal position and heading.

Cold construction can leave queued old snapshots. Before acceptance, the worker
waits/drains without dispatch until a current snapshot arrives; it does not renew
old data or extend the parent's startup deadline. Once running, expired parent
inputs still fail the worker. The same freshness check is repeated after native
callbacks before lifecycle/goal dispatch.

| Contract | Unchanged bound |
| --- | --- |
| Input age | 300 ms |
| Velocity age/output gap | 150 ms |
| Goal startup | 5 s |
| Generation retirement | 2 s |

Retirement first closes the private channel and fences late events, then signals
the entire worker process group: INT immediately, TERM after 0.2 s and KILL
after 0.4 s. The isolated PID-1 owner reaps adopted dead descendants. An output
quiescence ACK requires both worker exit and absence of the entire process
group, not just successful signal delivery. The obsolete launch-PID group check
has been removed from the ROS-local implementation. Logs that fail to open
cannot leak either socket or start a worker.

The parent writes `startup.json`; the worker's optional best-effort native
shutdown report is `ros-startup.json`. Missing worker shutdown output is not a
substitute for parent retirement proof. No vendor AIMDK/DDS files, launch-time
clock/QoS/publication overrides or physical output paths were changed.

## Preserved Trials

Outputs below are retained under the separate loopback-only rootfs:
`/home/admin1/.cache/lingxi-next/nav2-humble-20261031/rootfs`.

| Output | Result |
| --- | --- |
| `product-offline-6e0e437eb3744961d992f2f5` | First worker API run: voice scenario failed before acceptance with `parent_snapshot_expired`; main-loop gap was 5.444 ms. The finite initial drain had not caught up with cold-start snapshots. Other three cases passed, including twelve starts at `product-offline-d642ac8e6054f5080963066f`. |
| `product-offline-e5a40c35340640b633c00ac8` | After correcting pre-acceptance draining, full API suite passed 4/4 in 109.71 s. Voice/task generations are retained here. |
| `product-offline-49c4e3ef683e684e18ec8555` | Same corrected run: detour completed with independently checked goal arrival. |
| `product-offline-326f65d6a53600070f5c7754` | Same corrected run: twelve fresh starts and confirmed retirements; acceptance 2.376--2.533 s. |
| `product-offline-35f7ea7653a2a93ce95ca0ab` | Final-source full API run passed 4/4 in 111.66 s. Voice/manual pause, fresh resume epochs, question-preserved navigation, ordered tour completion and no tour auto-return passed. Six task generations are retained here. |
| `product-offline-1431446c52f99c61e6a66b8f` | Final-source detour completed; parent main-loop gap at retirement 6.369 ms. |
| `product-offline-84b1f881fcb5b3a934c894de` | Final-source twelve-start stress case: acceptance 2.321--2.551 s, maximum parent main-loop gap 5.876 ms. All twelve explicitly stopped and confirmed quiescent; retired poses/command counts remained unchanged. |

Across the final voice/detour/stress generation snapshots the largest recorded
parent loop gap was 7.444 ms. This is observed offline timing, not a real-time
scheduling guarantee, a measurement of cloud speech latency or a FastDDS fix.
The earlier failed cold-start run was not removed or counted as a passing run.

`service-diag-worker-20261104-a/report.json` separately verifies twelve running
generations and twelve idle/quiescent retirements. All 96 request/reply pairs
succeeded, with no missing replies or runtime error. Acceptance was
2.309--3.165 s; the maximum recorded parent loop gap was 9.137 ms. All eight
source hashes, including the worker, match the final inspected source. Report
SHA-256: `0bdb74762f8b2edd4dcc1656f4d6bf1cc13302ad4790d227a34c281ca3d418b6`.
This passing diagnostic does not establish a middleware root cause.

## Regression

- New Linux worker contracts: 18/18 pass in 3.547 s. Includes a 400-ms blocked
  worker, independent 150-ms output expiry, pre-acceptance pause/late callbacks,
  new resume epochs, unexpected exit, stubborn descendants, packet bounds,
  replay/epoch/source-time validation and socket cleanup on construction failure.
- Combined Linux runtime/lifecycle/worker/producer/composition/diagnostic/bundle
  selection: 149 passed, two environment-dependent bundle skips (151 total).
- Scientific-environment bundle/async-bundle/Linux-wrapper selection: 18 passed,
  one host-Node-dependent skip (19 total). Windows shared-bundle selection:
  11 passed, one scientific-dependency skip (12 total); its host-Node case passed.
- Default TypeScript regression: 750 passed, 25 explicit skips (775 total).
  Real-Nav2 API integration is opt-in and was run separately as recorded above.
- Frontend/API/runtime type checks, production Web build and API bundle pass.
  Two existing dependency PURE-annotation warnings remain; no build errors.

The first direct Python module invocations lacked `tools/tests` on PYTHONPATH;
the Windows async-bundle attempt also lacked json5. Corrected test invocation
and the existing scientific virtualenv resolved those environment errors, with
no unrelated product changes. They are not silently counted as product passes.

## Scope Boundary

This establishes a bounded offline process boundary with actual x86-64 Humble
Nav2 and synthetic sensors/in-memory output. It does not prove or replace:
ARM64 installed execution, metric/ground/base calibration, live saved-map
localization, walking/stopping and obstacle behavior, real cloud/Agent SDK audio,
acoustic wake/rejection, Android device tests or a current full release archive.
Both `physicalMotionSent` and `navigationReady` remain false in this workbench.
No further vendor middleware experimentation is required for this milestone;
the next integration stage should use the product/target acceptance checklist.
