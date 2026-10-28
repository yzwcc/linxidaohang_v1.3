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

This is not yet a ROS subscriber or a production service. A live adapter must
also bound its callback-to-worker queue, check a real monotonic watchdog,
validate the sensor profile and isolate native processing from callbacks.
The recorded-arrival trial does not model CPU queueing or prove real-time
performance, calibration, ARM64 streaming, localization or navigation.

See `docs/SLAM_STREAM_INPUT.md` for actual-recording checks. Existing AIMDK
services, installers and release artifacts remain unchanged.
