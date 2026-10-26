# AimDK v1.1.0 Integration Status

## Confirmed SDK interfaces

The supplied `aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip` documents and includes generated interfaces for:

- Navigation requests: `/aima/te/pnc_task_request`, `aimdk_msgs/msg/PncTaskRequest`.
- Navigation responses: `/aima/pnc/pnc_task_response`, `aimdk_msgs/msg/PncTaskResponse`.
- Mapping command: `/integrated_command`, `std_msgs/msg/String`, with `RELIABLE + TRANSIENT_LOCAL` QoS. Commands are `start_mapping` and `stop_mapping:<map_name>`.
- Stored map lookup: `/aimdk_5Fmsgs/srv/GetStoredMapByName`, which returns `map_id`, `map_info`, `map_path`, occupancy data, and topology.
- Sensor topics: chest-front lidar point cloud, lidar IMU, and `/slam/lidar_odom` remain documented interfaces.

The navigation bridge uses the documented PNC request interface. Web mapping publishes only the two SLAM mapping commands; it does not publish PNC or locomotion/velocity commands. A successful stop is reported only after `GetStoredMapByName` returns code 0 and a positive map ID, and the occupancy grid has valid dimensions, resolution, origin, and data. The bridge materializes it as `MapManagerModule/<map_id>/occupancy_map.png`, `grid_map_info.txt`, and import metadata for the existing map web service.

## Robot installation

The supplied archive contains SDK source, prebuilt AArch64 libraries/messages, examples, and documentation. It does not contain a complete AIMDK installation archive (`.7z`/`.tar.gz`) that can safely replace the robot's installed overlay. Do not install this SDK source archive over `/home/agi/aimdk` or label an existing R16 bundle as v1.1.0.

After the vendor-supported v1.1.0 overlay has been installed, set `ROBOT_AIMDK_SETUP` to its `install/setup.bash` before starting the bridge and console. There is intentionally no default overlay path; startup and diagnostics fail closed unless `PncTaskRequest` and `GetStoredMapByName` can both be imported.

## Remaining field acceptance

- Confirm the robot firmware has the optional SLAM component enabled and has an active `/integrated_command` subscriber and `GetStoredMapByName` server.
- Confirm the exact `stop_mapping` save latency and map-path behavior on the target X2.
- Code-level verification covers conversion from the AIMDK occupancy grid into the existing numeric map directory format, discovery and image serving by the map web app, and the existing pixel/world origin convention. Rotated map origins are rejected because the current point editor does not represent rotated map origins.
- On the target X2, verify that the installed v1.1.0 service returns the documented occupancy-grid fields and that the resulting map appears in the website after saving. Then validate a known location against the external remote-control position before creating production navigation points; hardware, firmware, service permissions, and real sensor data are not available in this development workspace.
- Build a new deployment bundle with the vendor-provided v1.1.0 installable overlay; the current V1.3.1 R16 full bundle is not that bundle.
