# Isolated Nav2 Service Endpoint Diagnostic

Date: 2026-11-04. Native endpoint observation in a loopback-only, separate
Humble rootfs. It is not a middleware fix or robot acceptance.

## Why This Probe

The preserved API/voice failure at generation zero sent one official manager
STARTUP request, completed 22 current GetState rounds per node and never
confirmed the manager response. The controller again logged a
`change_state` response-send timeout. RMW 6.2.10 constructs that response path
by looking up the request's related client response-reader GUID in the server
service listener and waiting for that reader to match the response writer.

The RMW service availability implementation compares request-side and
response-side match counts on the client. It does not prove that the lifecycle
server has already observed the matching response reader when it sends a
transition reply. Client GetState success likewise is not proof of that
separate `change_state` response-reader match.

## Diagnostic

The isolated service wrapper has an explicit `--diagnose-lifecycle` mode. It
requires the pinned Humble/FastDDS/Nav2 versions, x86-64, loopback as the only
network interface, ROS localhost-only domain 174, a new private rootfs output
and the `open` synthetic scene. It compiles a temporary preload observer from
the installed headers into that new output. The observer forwards the original
`rmw_send_request` and `rmw_send_response` once, without retrying, changing
payloads, ROS state, middleware configuration or readiness. It records only
bounded, correlated `/lingxi_offline_<epoch>/` lifecycle service names, GUIDs,
sequence numbers and endpoint-match metadata. There are no vendor or robot
endpoints in its allowlist. The default wrapper and deployed product do not
select or install it.

The observer queries the RMW server listener for the exact related reader and
also checks FastDDS' public matched-subscription list API where available.
Request-side declared and actual reader/writer GUIDs are compared with the
server's related reader. Logs and source/version/library hashes remain in the
separate rootfs in a unique directory such as
`service-diag-20261104-e/report.json`.

Wrapper modes are mutually exclusive. `--check` only imports the required ROS
and Nav2 packages, prints `lingxi_offline_nav2_preflight_ok_v1`, and exits
before compiling the observer or starting Nav2. Combining it with another mode
is rejected before rootfs validation or namespace creation. The internal
diagnostic tool's separate `--check` validates its pinned runtime ABI only.

## Observations

The first actual Humble diagnostic run completed twelve distinct production-
path synthetic Nav2 generations. All reached `running` within the unchanged
five-second offline acceptance deadline, were stopped and retired, and armed
no physical output. For each manager startup, all four managed nodes were
configured and activated. The observer recorded 96 correlated request/response
pairs: all returned successfully, all server listener checks reported the
exact client response reader present, and all client-declared reader/writer
GUIDs matched the actual endpoints. The FastDDS public matched-subscription
query did not return a usable matched list in these samples. This is one
controlled run, not a reliability rate. Its report remains preserved at
`service-diag-20261104-e/report.json`.

A subsequent invocation intended as a wrapper `--check` accidentally included
both `--check` and `--diagnose-lifecycle`. Before the parser fix, the latter
silently overwrote the former, so this did **not** validate preflight. It
instead produced a second genuine isolated diagnostic report at
`service-diag-check-20261104-a/report.json`. This report must not be mistaken
for a successful check or discarded:

- Seven generations reached `running`; generation eight failed while
  `bt_navigator/change_state` returned code 2.
- At that failure, the client's matched request and response counts were both
  1, and its declared reader/writer GUIDs matched the actual endpoints.
- The server RMW listener returned `MAYBE` for the related reader; the public
  FastDDS matched-reader list was unavailable, so it could not independently
  confirm that reader.
- The run ended with `retirement_timeout` and
  `TimeoutError:bounded_diagnostic_observation_expired`. The diagnostic did
  not resend a transition, and the report records `physicalMotionSent=false`.

The failure now identifies the concrete failure mechanism: when the service
attempted to send its reply, the server-side RMW listener still reported
MAYBE for the client response reader. In the installed Humble header,
`client_present_t::MAYBE` means the client endpoint pair is still registered,
but its response reader is not in the server's matched-reader set. The
instrumented `check_for_subscription(..., 0ns)` snapshots show this state
before and after the real send call. The official `rmw_send_response` path
waits for that exact reader only up to the response writer's QoS
`max_blocking_time`; on `MAYBE` it returns `RMW_RET_TIMEOUT` with
`client will not receive response` before calling the response writer. This
matches the captured `returnCode: 2` and server log exactly. The client's
1/1 match counters and matching GUIDs establish its local view, not that the
server listener has processed the reciprocal match callback.

Thus the service-discovery race causing this failed reply is established; why
the reciprocal discovery callback was late in that generation is not. The
12-success run remains comparison data, not a reliability claim. The Humble
rootfs has `rclcpp 16.0.21`; its merged no-crash handling for send-response
errors does not retry or recover the missing response. The related upstream
[rmw_fastrtps issue #842](https://github.com/ros2/rmw_fastrtps/issues/842)
describes the same Nav2/lifecycle response-discovery race and remains open in
the current upstream issue listing. The upstream [service-discovery redesign
PR #418](https://github.com/ros2/rmw_fastrtps/pull/418) proposes holding
requests until both response endpoints are matched, but it is unmerged and is
not a drop-in Humble patch. No RMW, Fast-DDS, or timeout configuration has
been changed.

One product-level experiment staggered the five Nav2 process launches at
150ms intervals. It never produced a service request/response pair: the
lifecycle manager remained in `Creating and initializing lifecycle service
clients` until the unchanged startup deadline and never advertised its
management services. Its report is preserved at
`service-diag-stagger-20261104-b/report.json`; the experiment was rejected and
both launch descriptions were restored. This is not a failure of the service
race probe and is not counted as a passing or failing service sample.

The wrapper parser is now mutually exclusive, its usage includes the new mode,
and generic preflight dispatch occurs before diagnostic compilation or any
Nav2 launch path. A regression test rejects a combined `--check` and
`--diagnose-lifecycle` invocation before isolation can start.

These synthetic x86-64 observations do not certify ARM64, calibrated maps,
supporting ground, physical motion/stopping, AgentSDK speech, cloud latency,
Android devices or the complete product. No robot connection or deployment
occurred.
