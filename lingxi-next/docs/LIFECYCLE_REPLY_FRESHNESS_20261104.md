# Lifecycle Reply Freshness

Date: 2026-11-04. Product reliability correction, not robot acceptance.

## Finding

Reviewing the preserved startup failures did not establish the underlying
FastDDS discovery cause. The two failed logs in
[tour timing](TOUR_DWELL_AND_RETURN_20261104.md) remain unchanged, and the
[official startup ordering](OWNED_NAV2_STARTUP_20261104.md) remains a mitigation.

A separate current-code defect was confirmed: both the shared startup owner
and the passive action client timestamped GetState responses when poll consumed
them. A delayed response could therefore renew an old unconfigured or active
state, including after its query had exceeded the existing 0.6-second age limit.
Seven initial regression cases failed against the prior implementation before
the correction; the completed unit regression adds ten cases in total.

## Change

Every pending GetState query retains its monotonic issue time. A reply's
freshness begins no later than that time, not its later processing time.
Expired replies withdraw the affected node's state and do not increment startup
discovery rounds. Fresh read-only queries can recover readiness; the official
non-idempotent STARTUP request is never replayed.

Polling with a nonfinite, boolean or regressed clock latches a fault. Readiness
also rejects a clock earlier than the last valid poll. The passive client now
requires integer lifecycle states and still-visible services, and its pending
query cancellation preserves the original close behavior. It does not acquire
startup ownership or create a lifecycle-manager control client.

The two-round discovery requirement, official manager ACK, four active nodes,
action-server check, 0.2-second polling interval and bounded lifecycle maximum
age are unchanged. The offline five-second acceptance deadline and production
30-second prewarm deadline are unchanged. No RMW dependency, launch ordering,
motion bounds, admission gate or physical-output policy is relaxed.

## Verification

An ordinary-user Linux run passes all 165 focused tests without skips, covering
startup, offline protocol, owned producer, physical bridge/ROS/velocity,
localization guard, Nav2 profile and shared package. The five changed Python
sources parse successfully and whitespace checks pass. The full scientific
Python suite was not rerun.

The default TypeScript suite passes 750 tests with 25 explicit
environment-dependent skips. The actual four-test Nav2 execution described
below is separate from those skips. Frontend/API/runtime type checks and the
production Web build pass. Existing dependency annotation warnings do not fail
the build. The Web output is local verification only, not a new packaged release.

Actual Humble/Nav2 production-path integration passes all three tests in
58.914 seconds. The new deterministic case keeps ROS callbacks and synthetic
sensors live while postponing lifecycle polling. Managed and passive clients
each reject four real active-state responses at approximately 0.752 and 0.754
seconds of query age, above the unchanged 0.6-second limit. Both recover from
fresh queries, without a goal, armed output or nonzero output message.

The same run passes eight fresh official prewarm generations, with durations
2.075--5.359 seconds inside the existing 30-second budget. Every process group
retires. The composed action/pause/resume, localization and sensor loss, and
map-handoff case also passes. Raw results remain in the separate rootfs under
`owned-freshness-20261104-b`. The earlier two-test run under
`owned-freshness-20261104-a` passed before the deterministic case was added;
it is not a separate certification of that new test.

## Preserved Offline Integration Failure

The separate four-test actual-Nav2 API/voice run is **not passing**: three cases
pass, but the repeated-start case fails at generation zero with
`goal_acceptance_timeout`. The run takes 79.600 seconds. Diagnostics remain
under `product-offline-482bc81bc5f97c409fdd833d`, epoch
`42b74d30b18163293f134269aa1811d2`.

The official STARTUP was sent once and was never confirmed; no goal was sent.
Each GetState client completed 22 current rounds, with final states
`[2, 2, 1, 1]` and no lifecycle-client failure. The controller log again reports
`failed to send response ... controller_server/change_state (timeout)`.
This is consistent with the preserved unmatched service-reply path, not evidence
that expired GetState replies were promoted or that the new age gate timed out.
It does not prove the complete discovery cause or exclude every timing effect
of this update. No unchanged-source rerun is used to erase this failure.

The matching [RMW response implementation](https://github.com/ros2/rmw_fastrtps/blob/6.2.10/rmw_fastrtps_shared_cpp/src/rmw_response.cpp)
waits for the particular response reader to match before sending. Independent
GetState round trips do not prove that the lifecycle manager's separate
change_state reply endpoints are matched. Related upstream
[issue 842](https://github.com/ros2/rmw_fastrtps/issues/842) is a diagnostic lead,
not a verified fix for this host. Repeated offline startup reliability therefore
remains open despite the earlier successful generations.

These results do not explain or repair FastDDS itself. They do not establish
actual ARM64 execution, calibrated metric geometry or supporting free cells,
physical stopping/navigation, AgentSDK acoustics, cloud latency or Android
device acceptance. No robot connection, deployment, APK or new release archive
is part of this correction; existing 0.1.24 artifacts do not contain it.
