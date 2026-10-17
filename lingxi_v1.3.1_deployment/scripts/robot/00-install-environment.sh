#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -un)" == "run" ]] || { echo "请使用 run 账号执行本脚本" >&2; exit 1; }
[[ "$(uname -m)" == "aarch64" || "$(uname -m)" == "arm64" ]] || {
  echo "只支持 ARM64 机器人" >&2
  exit 1
}
[[ -r /opt/ros/humble/setup.bash ]] || { echo "缺少 ROS 2 Humble" >&2; exit 1; }
[[ -d /agibot/data/var/MapManagerModule ]] || { echo "缺少机器人地图目录" >&2; exit 1; }

echo "安装 AIMDK 编译与部署环境..."
sudo apt-get update
sudo apt-get install -y \
  p7zip-full \
  build-essential \
  cmake \
  pkg-config \
  python3-pip \
  python3-colcon-common-extensions \
  python3-rosdep \
  libopencv-dev \
  libcurl4-openssl-dev \
  libncurses-dev \
  ros-humble-cv-bridge \
  ros-humble-grid-map-msgs \
  ros-humble-visualization-msgs

command -v 7z >/dev/null
command -v colcon >/dev/null
command -v cmake >/dev/null
source /opt/ros/humble/setup.bash
ros2 --help >/dev/null
echo "[通过] 环境安装完成"
