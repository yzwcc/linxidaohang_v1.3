#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 sudo 运行此安装脚本" >&2
  exit 1
fi

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE_DIR="${PACKAGE_DIR}/navigation-update"
TARGET_DIR="${ROBOT_NAV_HOME:-/home/agi/robot_local_navigation_v3}/robot_local_navigation_v3"

if [[ ! -d "${TARGET_DIR}" ]]; then
  echo "未找到基础导航目录 ${TARGET_DIR}，跳过导航增量更新。请先按部署文档安装基础导航。"
  exit 0
fi

required=(
  lingxi_nav_bridge.py
  map_point_store.py
  sdk_motion_controller.py
  tour_step_actions.py
)
for file in "${required[@]}"; do
  if [[ ! -f "${SOURCE_DIR}/${file}" ]]; then
    echo "安装包不完整，缺少导航更新文件：${file}" >&2
    exit 1
  fi
done

backup_dir="${ROBOT_NAV_HOME:-/home/agi/robot_local_navigation_v3}/backups/v1.2.0-$(date +%Y%m%d-%H%M%S)"
install -d -m 0755 "${backup_dir}"
for file in "${required[@]}"; do
  [[ -f "${TARGET_DIR}/${file}" ]] && cp -a "${TARGET_DIR}/${file}" "${backup_dir}/"
  install -o agi -g agi -m 0644 "${SOURCE_DIR}/${file}" "${TARGET_DIR}/${file}"
done

echo "导航动作与表情模块已更新，原文件备份在：${backup_dir}"
