# Official Nav2 Component Startup

Date: 2026-11-04. Owned-process changes, not vendor middleware replacement or
physical-navigation acceptance.

## Design

Both prepared launches now use Humble's official
`rclcpp_components/component_container_isolated`. Planner, controller, BT
navigator, velocity smoother and lifecycle manager share one DDS participant,
with an isolated executor per component. Their internal lifecycle calls no
longer depend on inter-participant discovery. This follows Nav2 1.1.20
[bringup](https://github.com/ros-navigation/navigation2/blob/1.1.20/nav2_bringup/launch/bringup_launch.py)
and [navigation launch](https://github.com/ros-navigation/navigation2/blob/1.1.20/nav2_bringup/launch/navigation_launch.py).
No unmerged RMW code, service replay or automatic respawn is introduced.

The full hash-checked parameter file is supplied to both container and
components, including nested costmap/BT nodes. Ticket namespaces, map/TF topics,
support/collision checks, controller limits and raw/observed velocity handoff
are unchanged. Intra-process message transport is not enabled, preserving DDS
publication-age observation. The single official STARTUP still needs a positive
manager acknowledgement and fresh active-state evidence.

Container shutdown has bounded signal escalation (0.2 s per stage). The existing
two-second retirement deadline, process-group exit proof and independent
physical output fence are not relaxed. Signal delivery alone is not a stop ACK.

The offline callback batch yields after 40 calls or 5 ms. A passive session
node keeps the Python participant alive between goals: native node construction
can hold the GIL even in a worker thread. It contributes no readiness evidence
or velocity output and is destroyed after generation cleanup.

Sensor and clock QoS, simulation-time configuration and middleware publication
mode are unchanged in the retained implementation. Three experimental changes
(ClockQoS, async fixture publishing and system-time workspace) did not resolve
the failure and were withdrawn. The last one exposed blocking in ROS callback
dispatch instead of publishing. Per-stage and per-publisher maximum counters
remain; they do not swallow failures, retry writes or refresh input evidence.
The 5-ms callback budget is cooperative between calls, not a hard bound on one
native `spin_once(timeout_sec=0)` invocation.

## Preserved Failures

Reports live inside the separate loopback-only Humble rootfs. Old trials are
not rewritten to match later source; partial passes are not reliability rates.

| Output | Outcome |
| --- | --- |
| `service-diag-composed-20261104-a` | Component-only parameters omitted nested costmap settings; startup failed. |
| `service-diag-composed-20261104-b` | Parameter inheritance fixed; first goal started, but shutdown exceeded retirement limits. Eight replies succeeded. |
| `service-diag-composed-20261104-c` | Initial shutdown bound; next request refused after three starts. No final retirement snapshots existed yet. |
| `service-diag-composed-20261104-d` | Nine starts passed, then generation ten expired inputs before STARTUP. 72 replies succeeded. |
| `product-offline-538bc312dec7ea42408e8f55` | Three API cases passed; repeated startup failed at generation four with expired inputs. |
| `product-offline-a5c8f8e1c9daab8b626fcdc2` | Callback budget revision passed twelve starts/retirements, acceptance 2.619--4.802 s. Predates the session node. |
| `service-diag-composed-20261104-e` | Callback budget alone was insufficient: generation two expired inputs before STARTUP. Eight replies succeeded. |
| `product-offline-57652681d0ff9fe3b4bcd5aa` | Session-node retention passed three API cases but repeated startup expired inputs at zero-based generation three. |
| `product-offline-da3aebe4c93eca8f23382a13` | Added construction/main-loop timing; three API cases passed, repeated startup still failed. |
| `product-offline-6e00817b2dcd29e3b08e560b` | Added phase timing; failed zero-based generation three, with aggregate publish blocking 335.040 ms before STARTUP. |
| `product-offline-56ae85449e4c9e23b7dfafe1` | Per-publisher timing alone passed twelve starts/stops in the isolated repeated-start test; not a fix. |
| `product-offline-8a977641950ef4a85b949d0b` | Full API run passed three cases but expired inputs at zero-based generation two; `/clock` write blocked 333.493 ms. |
| `product-offline-67708fd78da5c5f965809a71` | ClockQoS-only full run: tour restart timed out before STARTUP, without observed lifecycle states. |
| `product-offline-27e77bdedcf5a1af92e7e2ae` | Same ClockQoS-only full run: repeated startup expired inputs at zero-based generation nine; `/clock` blocked 333.676 ms. Two of four API cases passed. |
| `product-offline-7546dfe32b3a67e5ad384c84` | Async-input-owner trial passed three API cases but still expired inputs at zero-based generation four; `/clock` blocked 321.344 ms. The ineffective async override was removed. |
| `product-offline-ee2ea14e1e3a8f5c7c0e7d96` | System-time trial passed three API cases but expired inputs at zero-based generation four; callback dispatch blocked 331.169 ms while aggregate publishing remained below 1.8 ms. This clock-mode change was also withdrawn. |

The diagnostic now records retirement/final states and explicit start/retirement
errors. Successful middleware replies or a quiescent flag alone cannot turn a
failed generation into a pass.

## Verification

`producer-composed-20261104-a`: all three actual Humble production-path tests
pass in 51.160 s, using synthetic sensors/localization and an in-memory AIMDK
output. Includes eight fresh official-manager prewarms with no nonzero output,
stale replies/recovery, pause/resume, and localization/sensor loss. All owned
generations are retired; no hardware is exercised.

`service-diag-composed-20261104-f`: after retaining the session node, twelve
starts and retirements pass. All 104 observed request/reply pairs succeed.
Every related reply reader is RMW `YES`, and all 104 request/reply endpoint
pairs share a participant GUID prefix. Acceptance spans 2.041--4.863 s inside
the unchanged five-second deadline. All seven recorded source hashes matched
the inspected source at that run; later timing-only instrumentation is a distinct
revision. Report SHA-256:
`3f1bbcb4c446d023be8abd820b35e8973d9a50fa04b6e9ee806d8c847f29a504`.
The subsequent full API rerun passed three cases but failed repeated startup at
generation three with `navigation_inputs_expired`, preserved at
`product-offline-57652681d0ff9fe3b4bcd5aa`. Session-node retention is not a proven
input-expiry fix. Construction-phase timings and maximum main-loop gap are now
recorded for diagnosis; they do not renew input timestamps or grant readiness.

Per-publisher instrumentation subsequently identified the blocking call in
`product-offline-8a977641950ef4a85b949d0b`, epoch
`f366c02753c8daa03b50f448e1a790e4`: `/clock` took 0.333492596 s, aggregate
publish took 0.335319869 s and each other publisher took less than 0.34 ms.
The previous maximum loop-gap counter was only 13.035 ms because it is sampled
at tick entry, before the currently blocking tick finishes. STARTUP and goal
dispatch were both false. The unchanged 300-ms input-age watchdog correctly
retired this generation as `navigation_inputs_expired`. This identifies the
blocked fixture call, not the internal FastDDS waiting mechanism or a vendor
middleware defect. Matching upstream
[ClockQoS](https://github.com/ros2/rclcpp/blob/humble/rclcpp/include/rclcpp/qos.hpp)
and using the official
[asynchronous mode](https://github.com/ros2/rmw_fastrtps/blob/6.2.10/README.md#change-publication-mode)
were tested separately and did not eliminate the wait. Neither is retained as a
claimed fix. In the failed system-time generation, callbacks took
0.331169245 s while every publisher stayed below
0.205 ms. This is evidence against a clock-topic-only explanation. In all these
failed generations STARTUP and goal dispatch were false.

Final-source offline Python regressions pass 104 tests. The separate source-bound
runtime bundle suite passes 15. The Linux wrapper argument check passes one.
Actual isolated production-path integration passes all three tests in 52.345 s.
Default TypeScript tests pass 750 with 25 explicit environment-dependent skips;
all 42 selected test files pass and two files skip. TypeScript/API/runtime checks,
Web production build and API entrypoint bundle succeed; Vite reports two existing
dependency annotation warnings. The opt-in full API suite intentionally remains
red in preserved experiments above and is not hidden by these separate passing
regressions.

The remaining engineering work is to separate potentially blocking ROS/DDS IO
from the runtime watchdog owner using bounded, ticket/source-time-bound events
and confirmed retirement. Simply moving work to a thread is insufficient when
native code holds the GIL. That process separation is not implemented or accepted
by this milestone. Repeated full API startup remains failing; the passing service
diagnostic and production-path tests must not be presented as its replacement.

These observations do not repair FastDDS or certify its general discovery
behavior. ARM64 loading, metric/ground/base calibration, walking/stopping,
acoustic wake/rejection, cloud first-audio latency, Android devices and the
complete installation package remain separate open acceptance work. No robot
connection or deployment occurred.
