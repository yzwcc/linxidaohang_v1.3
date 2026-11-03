# Tour Dwell and Idle Return

Date: 2026-11-04. Offline product correction, not robot acceptance.

## Behavior

Point navigation and tour dwell intentionally use different timers:

- Point navigation waits for an active answer to finish, then starts the full
  configured no-interaction return interval. An ongoing question or spoken
  reply cannot cause an idle return. Q&A still does not cancel navigation.
- Tour dwell preserves its unconsumed configured duration across dialogue,
  the five-second quiet-resume gate, and manual pause. Dialogue does not replace
  a 20-second remainder with five seconds or add a second quiet-resume gate.
- Manual pause never resumes automatically. Tour completion never initiates
  idle return, including a tour with skipped points.
- A disconnected navigation adapter or `requiresStop` fault halts the runner
  even when a replaced bridge has lost the original task ID. Such faults do
  not count as an ordinary failed visit and cannot trigger retry or skip.
- Skipped visits commit their failure record before advancing. Failed record
  storage halts the task instead of silently losing history. Action/speech
  uncertainty still halts immediately; record I/O cannot postpone the halt or
  the speech-stop request.

No arrival thresholds, navigation admission checks, motion limits, startup
timeouts, or physical-output rules were changed.

## Checks

Six new regression cases failed against the prior implementation: two dwell
durations, a long answer overlapping idle return, point and tour faults without
a task ID, and an arrived result received from a disconnected adapter. Their
expectations pass after the correction. Manual waiting pause is also checked.

The actual JobManager, PhysicalNavigationAdapter and JsonStateStore are composed
with an explicitly synthetic bridge, without ROS or robot transport. Tests
verify two-round visit ordering, exactly one retry after an accepted task later
reports an explicit failure, one skipped visit, record persistence after
reopening the store, all 20 rounds/40 unique visit task IDs, fresh epochs,
no tour automatic return, and stop-before-restart after bridge loss. The
synthetic bridge distinguishes command acceptance from later terminal failure;
an unconfirmed command acknowledgement is not made retryable.

Paired HTTP/passive-event tests hold answer playback open beyond the configured
idle interval and verify a full new idle interval after playback ends. All four
focused lifecycle suites pass: 58 tests, including blocked and failed record
writes. The complete default TypeScript suite passes 750 tests with 24
environment-dependent skips. Frontend/API/runtime type
checks, the production Web build and the API entrypoint bundle pass.

## Actual Nav2 Rerun

The existing actual Humble/Nav2 product API and voice integration was run in
the separate loopback-only root filesystem, with synthetic sensors and no
robot access. The first run failed with `goal_acceptance_timeout` after tour
resume. In its final generation the controller logged failure to send its
`change_state` service response; lifecycle activation did not finish before
the unchanged bounded startup deadline. This observation does not establish
the underlying cause or attribute it to the timer correction.

The first run's raw output remains in private local storage under
`product-offline-bdf5837f3aa735de7108ea34`; its last generation is
`ee4ee11a57550786dc2ac8b40c447a5e`. No time limit or readiness requirement was
relaxed.

A second unchanged-source run, without the concurrent default Vitest run,
also failed with `goal_acceptance_timeout`, this time during the first tour
visit. Its planner logged a `change_state` response-send timeout. The private
output is `product-offline-f68cb170a8629b36173d351e`, final generation
`4f0a31bb7e4b5c1fb52415f3ec02f764`. Neither run is counted as passing, and
neither establishes that the timer change caused the middleware failure. Both
runs preceded the record-write follow-up and do not certify final-source
whole-stack acceptance.

Upstream [rmw_fastrtps issue 842](https://github.com/ros2/rmw_fastrtps/issues/842)
and [service discovery issue 392](https://github.com/ros2/rmw_fastrtps/issues/392)
describe related request/reply discovery races. Their reports are a diagnostic
lead, not proof of this host's cause. Lifecycle startup reliability remains
open; a successful future rerun alone must not erase these two failures.

Actual robot arrival, speech acoustics, cloud latency, physical map calibration,
terrain admission, ARM64 resource limits, APK and complete-stack release
acceptance remain outside this offline correction.
