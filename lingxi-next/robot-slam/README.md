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
Python cannot forcibly
terminate a stuck native thread; process isolation/supervision is still needed
before production. Queue age begins at the serialized callback, not the sensor
or DDS transport, and is not an end-to-end latency guarantee.
The prior recorded-arrival trial does not model CPU queueing or prove real-time
performance, calibration, ARM64 streaming, localization or navigation.

See `docs/SLAM_STREAM_INPUT.md` for actual-recording checks. Existing AIMDK
services, installers and release artifacts remain unchanged.
See `docs/SLAM_PASSIVE_WORKER.md` for worker limits and the message-equivalence
check. No module auto-loads a native library or activates a robot backend.
