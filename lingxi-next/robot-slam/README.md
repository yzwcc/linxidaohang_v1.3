# Independent SLAM Input

`lingxi_slam.stream.SensorStream` is the arrival-order input component for
the independent mapping backend. It has no ROS, network, native-library
loading, motion, map-activation or navigation interface.

The caller injects a native engine implementing `imu()` and `frame()`.
One owner thread supplies decoded, complete LiDAR packets and IMU samples,
using an explicit scan-start or scan-end hypothesis and a monotonic arrival
clock. Call `tick()` periodically even when no sensor callback arrives.
IMU samples are retained until a complete scan can be bracketed; future
samples are not inserted into the engine early.

Queues have count/byte limits. Missing input, time rollback, excessive sensor
gaps, expired packets, native errors and invalid results latch failure and
release buffered input. A failed stream cannot restart or execute retained
frames. EOF/stop releases buffers and counts trailing unbracketed scans; it
does not save a map or certify successful mapping. Engine shutdown remains
the caller's responsibility.

`messages.py` now snapshots the entire raw cloud payload and immutable IMU
vectors using an explicit frame/time profile. `worker.SensorWorker` bounds
callback count, cloud count, raw payload bytes and queue age. Its one owner
thread creates the engine, decodes clouds, calls `SensorStream`, delivers
results and closes the engine. Callback threads never call native methods.
Stop discards queued packets; failure is latched. Monotonic idle ticks detect
missing sensors. Snapshot/stop also detect overdue work and report a still-live
thread rather than claiming a native hang was terminated.

`passive_ros.run_passive` is an opt-in, bounded Humble session with its own
context and single-threaded executor. It subscribes only to explicit cloud/IMU
topics, disables rosout/parameter services and ignores global ROS arguments.
It has no application motion, TF or map publisher or service client. Standard
rclpy infrastructure can still publish parameter events; this is not a claim
that creating a ROS node produces zero network traffic.

This is not a deployed subscriber or a production service. Actual ARM64 native
worker execution has now been measured with four paced, original-recording
trials; see `docs/SLAM_ARM64_WORKER.md`. Two additional 20-second processes
now exercise actual Humble subscriptions and close cleanly; see
`docs/SLAM_HUMBLE_LIVE.md`. Native warnings, calibration, long-running
throughput and shutdown under unresponsive native code remain unresolved.
Python cannot forcibly terminate a stuck native thread. `ProcessSupervisor`
now provides external process ownership and bounded health IPC; a separate
recorded-input composition has completed real x86 native lifecycles without
altering this worker. See `docs/SLAM_SUPERVISED_NATIVE_REPLAY.md`. Target ARM64
recorded-input supervision has since completed; see
`docs/SLAM_SUPERVISED_ARM64.md`. Live composition and parent-service failure
recovery remain unverified and are still required before production.
Queue age begins at the serialized callback, not the sensor
or DDS transport, and is not an end-to-end latency guarantee.
The prior recorded-arrival trial does not model CPU queueing or prove real-time
performance, calibration, ARM64 streaming, localization or navigation.

See `docs/SLAM_STREAM_INPUT.md` for actual-recording checks. Existing AIMDK
services, installers and release artifacts remain unchanged.
See `docs/SLAM_PASSIVE_WORKER.md` for worker limits and the message-equivalence
check. No module auto-loads a native library or activates a robot backend.

`point_output.py` adds an opt-in decorator and separately built native callback
extension for bounded corrected LiDAR-point copies. Its x86 logical-clock and
one-times health-supervised recorded-input checks have completed; this is not
live/ARM64 acceptance or a UI data source. Initial paced failures are preserved.
The final extension has an exact five-function C ABI and Release build.
See `docs/SLAM_NATIVE_POINT_OUTPUT.md` and its separate aggregate evidence.

The extension has since cross-linked for ARM64 with host-only exported-ABI
inspection, not target execution. `point_data.py` and `point_pipe.py` now
provide a separate session-bound codec and bounded Linux point-pipe writer.
An optional Node pipe source passes isolated API WebSocket tests without
changing mapping/navigation readiness. It is not selected by normal startup
and is not connected to the production supervisor or real sensors. See
`docs/SLAM_POINT_DATA_CHANNEL.md` for executed checks and remaining work.

`mapping_session.MappingSession` adds explicit-save lifecycle composition for
the existing mapping ABI. Plain stop, revoked save or unhealthy input only
close owned native components. Saving closes odometry once, flushes the map,
shuts down mapping callbacks, checks inventory and requires the existing
loader in a separate process before atomically publishing `map-ready.json`.
Incomplete output stays private without a ready receipt. Input classification
is retained; a saved map never becomes localization/navigation-ready merely
because it round-trips. Synthetic lifecycle tests cover this component.
It is not yet selected by the live owner, API or APK. Its caller must provide
the verified mapping library, existing inventory/reload helpers, worker-health
predicate and a separate save/shutdown time budget; the current short-sequence
native mapper's loop-travel restriction is unchanged.

The 15 synthetic lifecycle checks pass on Linux, including actual
`SensorWorker` save and failed-input composition with a fake native engine.
Windows passes 13 and explicitly skips the two NumPy-dependent worker cases.
The subsequent real-library composition is documented in
`docs/SLAM_MAPPING_SESSION.md`.

`mapping_worker.MappingWorker` connects that lifecycle to the existing bounded
sensor worker. It has explicit save/discard control, revocable save intent,
single-session ownership and a separate bounded save shutdown budget. The
odometry engine is initialized before loading the mapping extension, matching
the original native diagnostic's order. Two new Linux x64 recorded runs now
exercise actual native save/reload and plain discard; neither uses ROS or a
robot connection. The live owner, API and APK still do not select this backend.
