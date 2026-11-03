# Bounded Offline Nav2 Startup

Date: 2026-11-04. Actual Humble/Nav2 with synthetic sensors, not robot acceptance.

## Finding and Scope

The two failures recorded in [tour timing](TOUR_DWELL_AND_RETURN_20261104.md)
remain preserved. A planner or controller completed configuration, then logged
`failed to send response ... change_state (timeout)` before the unchanged
five-second navigation startup deadline expired.

The isolated rootfs contains rmw_fastrtps 6.2.10, FastDDS 2.6.12 and Nav2 1.1.20.
In the matching [RMW response source](https://github.com/ros2/rmw_fastrtps/blob/6.2.10/rmw_fastrtps_shared_cpp/src/rmw_response.cpp),
that timeout is emitted when the service reply reader has not matched within
the writer's blocking interval. This identifies the failed response path; it
does not establish why discovery failed on this host. Upstream
[issue 842](https://github.com/ros2/rmw_fastrtps/issues/842) remains a related
diagnostic reference, not proof of a complete root cause or vendor fix.

The new twelve-generation test also passed once against the unchanged original
automatic-start implementation. The failure is intermittent; comparison runs
are not a statistically controlled claim that the mitigation eliminates it.
An initial new-test fixture used `taskId` instead of the protocol's `task_id`
when composing stop. That request failure was corrected in the test and is not
counted as a Nav2 startup failure or successful reliability run.

## Change

Only the isolated product workspace selects `autostart:=false`. The prepared
launch retains its previous automatic-start default for standalone scene trials.
The product client now:

1. Confirms two successful GetState round trips from all four managed nodes.
2. Requires their unconfigured states to remain current and services visible.
3. Sends exactly one official ManageLifecycleNodes STARTUP request.
4. Requires a positive manager response and current active states for every
   node, plus the NavigateToPose action server, before sending the goal.

Missing, expired, malformed, refused or failed replies do not grant readiness.
An uncertain STARTUP request is never replayed. No fixed startup sleep, custom
lifecycle transitions, alternate middleware or modified ROS package is used.
This is a bounded bringup mitigation, not a repair of FastDDS itself.

The five-second startup limit, controller watchdogs, retirement confirmation,
motion bounds and all physical admission rules are unchanged. Every retired
generation records private `startup.json` diagnostics beside `nav2.log`, including
the state observations, official startup confirmation, goal dispatch, runtime
detail and measured acceptance interval when one exists.

Production OwnedNav2Backend/physical launch is unchanged. It uses its separate
30-second prewarm path and is not certified by these offline-workspace results.
Any transfer of this startup policy requires its own production-path tests.

## Verification

Focused Python checks pass all 73 tests, with no skips: 12 product protocol and
startup checks, 17 preparation/profile checks, 33 runtime checks and 11 independent
scene-grading checks. These include missing services, lost manager responses,
explicit refusal, stale observations, clock regression, malformed state IDs,
late acceptance, strict controller freshness and confirmed retirement.

The actual isolated integration covers the paired HTTP workspace, port-conflict
cleanup, point navigation, awakened command routing, Q&A preserving navigation,
manual pause/epoch retirement, resume, a two-point narrated tour, no automatic
manual resume after 5.3 seconds, no tour return, and an actual Nav2 detour.

All four final-source integration tests passed in 105.17 seconds. The new
startup/retirement case passed 12 consecutive generations, each with a unique
epoch and confirmed output retirement. Observed client intervals from request
to running were 1,357--3,843 ms; this is navigation startup, not acoustic response
latency. Final raw startup diagnostics remain under
`product-offline-9ac7bbf19dc99d8a5bfa876f` in the isolated rootfs. Two earlier
mitigation revisions also passed 12 generations each, under
`product-offline-be1b990f13b461bbfea173de` and
`product-offline-9d9c2ea5fdad9de884d25e08`. Only the last 12 certify the final
freshness implementation; the aggregate 36 must not be called 36 final-source
trials. None of those three sets contains a response-send timeout log.

The final full API/voice trial observed 134 controller commands before the
first point's arrival. Its two tour points used three epochs including resume;
manual pause stayed latched for 5.3 seconds. The separate detour observed 567
controller commands and maximum absolute lateral displacement 1.0451 m in the
synthetic world. Arrival, bounds and retirement assertions passed. These are
functional observations, not independently surveyed physical measurements.

The final default TypeScript suite passed 750 tests with 25 environment-dependent
skips, including four actual-Nav2 tests that require separate explicit opt-in.
The opt-in four-test result above is a separate execution, not a reinterpretation
of those skips. Frontend/API/runtime type checks, the production Web build and
API entrypoint bundle all passed. The full scientific Python suite was not
rerun; only the 73 focused Python tests are new Python evidence for this update.

## Remaining Acceptance

The later [owned producer startup milestone](OWNED_NAV2_STARTUP_20261104.md)
transfers this policy to the production path and verifies it separately. The
production-unchanged statement above describes this original offline milestone,
not the later implementation.

All ROS trials use synthetic map/sensor inputs in separate network, IPC, mount
and PID namespaces with loopback only. `physicalMotionSent` and `navigationReady`
remain false. They do not establish ARM64 execution, calibrated scale/ground
support, robot stopping distance, actual AgentSDK speech or cloud latency.
Previous failed logs remain intact; no startup reliability claim should erase
them. Full-product release and physical acceptance remain incomplete.
