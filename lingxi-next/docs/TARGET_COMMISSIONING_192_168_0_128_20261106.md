# Robot Target Commissioning - 2026-11-06

Target: `192.168.0.128`. This is a partial commissioning record for the
independent console, live sensor preview and isolated Python dependency runtime.
It is not a mapping, localization, navigation, voice or Android acceptance.

## Deployed And Verified

- Release `0.1.26-independent-commissioning-20261106` is installed at
  `/opt/lingxi-next/current`; `lingxi-next-api.service` serves HTTPS on port
  `3001`. The existing vendor console on port `3000` remains installed and
  running.
- The console returned HTTP 200 over the robot LAN using the robot CA.
- `lingxi-next-aimdk.service` is active. Its read-only sensor subscription uses
  `/aima/hal/sensor/lidar_chest_front/lidar_pointcloud` (`PointCloud2`). The
  authenticated live lidar endpoint returned fresh frames with 6,174 points.
- The isolated ARM64 CPython 3.10 runtime is installed at
  `/var/lib/lingxi-next/python-runtimes/cp310-v1/env/bin/python`. The source kit
  SHA256 is `4eb8c2dca4f4873546bd7dd3694a60bd759da9eb6570a66dc6274cc90dc1d412`;
  the installed dependency lock SHA256 is
  `71d74391a6edc9e6e87a4e79ba7f91a60c82daac1b57506fc3041bd9a6915cd9`.
  The ordinary-user runtime preflight passed for NumPy, SciPy, small-gicp,
  PyYAML, json5 and ROS Humble imports.

## Remaining Gates

- The scan is in raw `lidar_chest_front` coordinates. No sample-time TF to the
  configured `map` frame was available, so the bridge explicitly reports that
  it will not accumulate a world-frame point trail. The live display is useful
  for inspecting the current scan; it does not yet establish a consistent map.
- AIMDK `GetStoredMapByName` is unavailable to the bridge. The latest state
  reports AIMDK, SLAM, odometry and navigation as not ready; `/v1/maps` returned
  HTTP 500. The stored map catalog has not been changed or migrated.
- The independent mapping, shared SLAM/localization, Nav2 and voice owners
  remain inactive. The installed Python environment is not selected in
  `robot.env`; the current-source native input closure, measured geometry and
  ground/calibration evidence have not passed the installed-owner verifiers.
- Android was not rebuilt from 0.1.26 and has not passed device trust, pairing,
  landscape or sensor-stream acceptance.

No mapping start, navigation, preset action or other movement request was sent.
The AIMDK vendor installation and vendor map files were left unchanged. The
`AIMDK_POINTCLOUD_TOPIC` selection and its pre-change configuration backup are
in root/service-restricted `/etc/lingxi-next` files on the robot.
