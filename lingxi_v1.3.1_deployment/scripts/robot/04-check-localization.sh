#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -un)" == "agi" ]] || { echo "请使用 agi 账号执行本脚本" >&2; exit 1; }

source /opt/ros/humble/setup.bash
source /home/agi/aimdk/install/setup.bash

echo "检查定位话题发布者："
ros2 topic info /slam/lidar_odom
echo
echo "检查 10 秒内是否有持续定位数据："
timeout 10 ros2 topic hz /slam/lidar_odom

echo "[通过] /slam/lidar_odom 有持续数据，可以在现场看护下执行网页自动重定位。"
