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

The failed sample strengthens the evidence that client match counts and the
server listener's view can disagree or remain inconclusive at response time;
it does not establish the deeper cause. The response-send timeout is
reproduced, so the earlier conclusion that it was not reproduced is superseded.
The 12 successful generations remain useful comparison data, not a fix or a
reliability claim. No deadline was increased, and these results do not justify
a middleware substitution or patch.

The wrapper parser is now mutually exclusive, its usage includes the new mode,
and generic preflight dispatch occurs before diagnostic compilation or any
Nav2 launch path. A regression test rejects a combined `--check` and
`--diagnose-lifecycle` invocation before isolation can start.

These synthetic x86-64 observations do not certify ARM64, calibrated maps,
supporting ground, physical motion/stopping, AgentSDK speech, cloud latency,
Android devices or the complete product. No robot connection or deployment
occurred.
