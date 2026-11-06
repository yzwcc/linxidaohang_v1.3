# Target TF And Sensor Diagnostic - 2026-11-06

Target: `192.168.0.128`, X2 Ultra, ARM64, release
`0.1.26-independent-commissioning-20261106`.

This was a read-only inspection while the robot was stationary. It started no
mapping session, saved no map, and sent no motion or vendor navigation command.

## Observed Inputs

- `/aima/hal/sensor/lidar_chest_front/lidar_pointcloud` publishes
  `sensor_msgs/msg/PointCloud2`; a fresh message had
  `frame_id: lidar_chest_front`.
- The point-cloud topic ran at about 10.0 Hz in the five-second sample
  (individual observed intervals were approximately 96-106 ms).
- `/aima/hal/sensor/lidar_chest_front/imu` ran at approximately 189-214 Hz
  over its short sample.
- `tf2_echo base_link lidar_chest_front` returned a transform. During this
  stationary sample its translation and rotation were stable. This is an
  observation of the current posture, not a measured or approved sensor
  calibration; the waist chain contains moving joints and may change with
  posture.

## Missing World Localization

- `/slam/localization/odometry` had zero publishers.
- `tf2_echo map lidar_chest_front` reported that `map` was absent and the
  frames belonged to disconnected TF trees.
- `/map` had a publisher named `costmap_ros_node`; that topic alone does not
  prove a saved or active SLAM map.

The missing ROS `map` transform does not by itself prevent starting the
independent GLIM mapper. The product's separate live SLAM preview is bound to
its own `lingxi_glim_odom` coordinate frame, as defined by
`robot-api/src/slam-points.ts`, rather than borrowing the vendor `/map` frame.
However, those GLIM coordinates are `native-raw`: this check establishes no
metres-per-unit scale, gravity alignment, map accuracy, loop closure or
localization readiness. A moving, paired live mapping session is still needed
to evaluate its corrected point stream and geometry.

## Service State And Next Check

The same-day target record reports the API, read-only AIMDK bridge and
independent mapping owner active and idle; shared localization, navigation and
voice owners remain inactive. The mapping console requires an authenticated
pairing session. Next, pair from the console and inspect the live GLIM preview
during a short externally controlled sweep. Keep navigation disabled until
map calibration, localization, clearance and stop behavior pass separate
target checks.
