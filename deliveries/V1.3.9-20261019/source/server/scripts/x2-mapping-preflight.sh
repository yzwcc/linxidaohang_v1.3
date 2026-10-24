#!/usr/bin/env bash
set -uo pipefail

MAP_ROOT="${ROBOT_MAP_ROOT:-/agibot/data/var/MapManagerModule}"
ROS_SETUP="${ROBOT_ROS_SETUP:-/opt/ros/humble/setup.bash}"
AIMDK_SETUP="${ROBOT_AIMDK_SETUP:-/home/agi/aimdk/install/setup.bash}"

if [[ -r "$ROS_SETUP" ]]; then
  # shellcheck disable=SC1090
  source "$ROS_SETUP"
fi
if [[ -r /agibot/software/common/local_setup.bash ]]; then
  # shellcheck disable=SC1091
  source /agibot/software/common/local_setup.bash
fi
if [[ -r "$AIMDK_SETUP" ]]; then
  # shellcheck disable=SC1090
  source "$AIMDK_SETUP"
fi
if [[ -r /agibot/software/entry/cfg/ros_dds_configuration.xml ]]; then
  export FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/entry/cfg/ros_dds_configuration.xml
fi
if [[ -r /agibot/software/nav/bin/librmw_fastrtps.so ]]; then
  export RMW_LIBRARY_PATH=/agibot/software/nav/bin/librmw_fastrtps.so
fi

section() { printf '\n===== %s =====\n' "$1"; }
bounded() { timeout "${1}s" "${@:2}" 2>&1 || true; }

section system
printf 'date_utc='; date -u +%FT%TZ
printf 'hostname='; hostname
printf 'kernel='; uname -srmo
printf 'ros_distro=%s\n' "${ROS_DISTRO:-unset}"
printf 'agibot_software='; bounded 3 systemctl is-active agibot_software.service
printf 'sensor_process='; if pgrep -af 'aima-hal_sensor-app-main' | head -1; then :; else printf 'not-found\n'; fi
printf 'slam_process='; if pgrep -af './slam --cfg_file_path' | head -1; then :; else printf 'not-found\n'; fi

section target_topics
for spec in \
  '/aima/hal/sensor/lidar_chest_front/lidar_pointcloud|sensor_msgs/msg/PointCloud2' \
  '/aima/hal/sensor/lidar_chest_front/imu|sensor_msgs/msg/Imu' \
  '/slam/lidar_odom|nav_msgs/msg/Odometry'; do
  topic="${spec%%|*}"
  type="${spec#*|}"
  printf '\n--- %s [%s] ---\n' "$topic" "$type"
  bounded 4 ros2 topic info "$topic" -v
  printf 'single_frame='
  if timeout 4 ros2 topic echo --once --qos-reliability best_effort "$topic" "$type" >/dev/null 2>&1; then
    printf 'received\n'
  else
    printf 'not-received-or-timeout\n'
  fi
done

section mapping_graph
bounded 5 bash -lc 'ros2 topic list -t | grep -Ei "map|slam|lidar|imu|pointcloud|octomap" | head -100'
printf '\n-- services --\n'
bounded 5 bash -lc 'ros2 service list -t | grep -Ei "map|slam|lidar|imu|octomap" | head -100'

section packages
for package in py_examples spark_fast_lio octomap_server nav2_map_server; do
  printf '%s=' "$package"
  bounded 3 ros2 pkg prefix "$package" | head -1
done
printf 'open3d='; bounded 4 python3 -c 'import open3d; print(open3d.__version__)'
printf 'map_saver_executable='; bounded 4 ros2 pkg executables nav2_map_server | grep -E '(^| )map_saver_cli$' | head -1

section map_manager
printf 'map_root=%s\n' "$MAP_ROOT"
if [[ -d "$MAP_ROOT" ]]; then
  printf 'filesystem='; df -hP "$MAP_ROOT" | tail -1
  while IFS= read -r -d '' directory; do
    name="${directory##*/}"
    [[ "$name" =~ ^[0-9]+$ ]] || continue
    printf 'map_id=%s occupancy_png=%s grid_info=%s\n' \
      "$name" \
      "$([[ -s "$directory/occupancy_map.png" ]] && printf yes || printf no)" \
      "$([[ -s "$directory/grid_map_info.txt" ]] && printf yes || printf no)"
  done < <(find "$MAP_ROOT" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null | sort -z)
else
  printf 'map_root=not-found\n'
fi

section end
printf 'read_only=true\n'
printf 'note=No robot mode changes, process starts/stops, map exports, or filesystem writes were performed.\n'
