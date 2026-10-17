#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -un)" == "agi" ]] || { echo "请使用 agi 账号执行本脚本" >&2; exit 1; }

set +u
source /opt/ros/humble/setup.bash
source /home/agi/aimdk/install/setup.bash
set -u

echo "检查定位话题发布者："
INFO="$(timeout 10 ros2 topic info /slam/lidar_odom)"
printf '%s\n' "${INFO}"
PUBLISHERS="$(printf '%s\n' "${INFO}" | awk '/Publisher count/ {print $3; exit}')"
[[ "${PUBLISHERS:-0}" -gt 0 ]] || {
  echo "[失败] /slam/lidar_odom 没有发布者，请先启动机器人底层定位服务" >&2
  exit 1
}
echo
echo "检查 10 秒内是否收到定位数据："
timeout 10 ros2 topic echo --once /slam/lidar_odom nav_msgs/msg/Odometry >/dev/null

echo "[通过] /slam/lidar_odom 有实时数据，可以在现场看护下执行网页自动重定位。"
