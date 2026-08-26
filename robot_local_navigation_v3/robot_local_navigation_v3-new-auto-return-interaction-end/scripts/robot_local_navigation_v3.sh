#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

APP_HOME="${ROBOT_LOCAL_NAVIGATION_V3_HOME:-${REPO_DIR}}"
CONFIG_JSON="${ROBOT_LOCAL_NAVIGATION_V3_CONFIG_JSON:-${APP_HOME}/unified_nav_config.json}"
ACTIVE_MAP_NAME="${ROBOT_LOCAL_NAVIGATION_V3_ACTIVE_MAP_NAME:-}"
NAV_MODE="${ROBOT_LOCAL_NAVIGATION_V3_NAV_MODE:-}"
DEFAULT_MAP_ROOT="${ROBOT_LOCAL_NAV_MAP_ROOT:-/agibot/data/var/MapManagerModule/}"
BRIDGE_HOST="${ROBOT_LOCAL_NAVIGATION_V3_BRIDGE_HOST:-0.0.0.0}"
BRIDGE_PORT="${ROBOT_LOCAL_NAVIGATION_V3_BRIDGE_PORT:-18082}"
WEB_HOST="${ROBOT_LOCAL_NAVIGATION_V3_WEB_HOST:-0.0.0.0}"
WEB_PORT="${ROBOT_LOCAL_NAVIGATION_V3_WEB_PORT:-8002}"
NAV_ODOM_TOPIC="${NAV_ODOM_TOPIC:-/slam/lidar_odom}"
ROS_DISTRO_SETUP="${ROBOT_LOCAL_NAV_ROS_SETUP:-/opt/ros/humble/setup.bash}"
AIMDK_SETUP="${ROBOT_LOCAL_NAV_AIMDK_SETUP:-/home/agi/aimdk/install/setup.bash}"

PID_DIR="${APP_HOME}/pids"
LOG_DIR="${APP_HOME}/logs"
BRIDGE_PID_FILE="${PID_DIR}/lingxi_nav_bridge.pid"
WEB_PID_FILE="${PID_DIR}/map_label_server_v3.pid"
BRIDGE_LOG_FILE="${LOG_DIR}/lingxi_nav_bridge.log"
WEB_LOG_FILE="${LOG_DIR}/map_label_server_v3.log"

mkdir -p "${APP_HOME}" "${PID_DIR}" "${LOG_DIR}"

usage() {
  cat <<EOF
用法:
  $(basename "$0") start-bridge
  $(basename "$0") start-web
  $(basename "$0") start-web-live
  $(basename "$0") up
  $(basename "$0") up-live
  $(basename "$0") stop
  $(basename "$0") status
  $(basename "$0") logs [bridge|web]

默认:
  v3目录: ${REPO_DIR}
  配置: ${CONFIG_JSON}
  bridge: http://${BRIDGE_HOST}:${BRIDGE_PORT}
  web: http://${WEB_HOST}:${WEB_PORT}
  地图根目录: ${DEFAULT_MAP_ROOT}
EOF
}

source_env() {
  export ROBOT_LOCAL_NAVIGATION_V3_HOME="${APP_HOME}"
  export NAV_ODOM_TOPIC="${NAV_ODOM_TOPIC}"
  if [[ -z "${FASTRTPS_DEFAULT_PROFILES_FILE:-}" ]]; then
    if [[ -r "${APP_HOME}/ros_dds_configuration.xml" ]]; then
      export FASTRTPS_DEFAULT_PROFILES_FILE="${APP_HOME}/ros_dds_configuration.xml"
    elif [[ -r "/agibot/software/entry/cfg/ros_dds_configuration.xml" ]]; then
      export FASTRTPS_DEFAULT_PROFILES_FILE="/agibot/software/entry/cfg/ros_dds_configuration.xml"
    fi
  fi
  if [[ -z "${RMW_LIBRARY_PATH:-}" && -r "/agibot/software/nav/bin/librmw_fastrtps.so" ]]; then
    export RMW_LIBRARY_PATH="/agibot/software/nav/bin/librmw_fastrtps.so"
  fi
  local vendor_protocol_py="${APP_HOME}/vendor/share/protocol/py"
  local vendor_dist_packages="${APP_HOME}/vendor/local/lib/python3.10/dist-packages"
  if [[ -d "${vendor_protocol_py}" ]]; then
    export PYTHONPATH="${vendor_protocol_py}:${PYTHONPATH:-}"
  fi
  if [[ -d "${vendor_dist_packages}" ]]; then
    export PYTHONPATH="${vendor_dist_packages}:${PYTHONPATH:-}"
  fi
  export PYTHONPATH="${REPO_DIR}:${PYTHONPATH:-}"
  set +u
  if [[ -f "${ROS_DISTRO_SETUP}" ]]; then
    # shellcheck disable=SC1090
    source "${ROS_DISTRO_SETUP}"
  fi
  if [[ -f "${AIMDK_SETUP}" ]]; then
    # shellcheck disable=SC1090
    source "${AIMDK_SETUP}"
  fi
  set -u
}

display_host_ips() {
  hostname -I 2>/dev/null | awk '{$1=$1; print}'
}

print_bridge_urls() {
  local host_ips
  host_ips="$(display_host_ips)"
  echo "导航桥接监听: http://${BRIDGE_HOST}:${BRIDGE_PORT}"
  echo "本机自测地址: http://127.0.0.1:${BRIDGE_PORT}"
  if [[ -n "${host_ips}" ]]; then
    echo "机器人网卡IP: ${host_ips}"
  fi
  echo "Java Agent 本机配置: nav.baseUrl=http://127.0.0.1:18083"
}

is_pid_running() {
  local pid="$1"
  [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1
}

stop_by_pid_file() {
  local pid_file="$1"
  local name="$2"
  if [[ ! -f "${pid_file}" ]]; then
    echo "${name}: stopped"
    return
  fi
  local pid
  pid="$(<"${pid_file}")"
  if is_pid_running "${pid}"; then
    kill "${pid}" >/dev/null 2>&1 || true
    sleep 0.5
    if is_pid_running "${pid}"; then
      kill -TERM "${pid}" >/dev/null 2>&1 || true
    fi
    echo "已停止 ${name} (pid=${pid})"
  else
    echo "${name}: pid file exists but process is gone"
  fi
  rm -f "${pid_file}"
}

start_bridge() {
  stop_by_pid_file "${BRIDGE_PID_FILE}" "lingxi_nav_bridge" >/dev/null 2>&1 || true
  source_env
  local args=(
    python3 -m robot_local_navigation_v3.lingxi_nav_bridge
    --config-json "${CONFIG_JSON}"
    --host "${BRIDGE_HOST}"
    --port "${BRIDGE_PORT}"
  )
  if [[ -n "${NAV_MODE}" ]]; then
    args+=(--nav-mode "${NAV_MODE}")
  fi
  if [[ -n "${ACTIVE_MAP_NAME}" ]]; then
    args+=(--active-map-name "${ACTIVE_MAP_NAME}")
  fi
  nohup "${args[@]}" >"${BRIDGE_LOG_FILE}" 2>&1 &
  echo $! > "${BRIDGE_PID_FILE}"
  echo "已启动 lingxi_nav_bridge (pid=$(<"${BRIDGE_PID_FILE}"))"
  print_bridge_urls
  echo "日志文件: ${BRIDGE_LOG_FILE}"
}

start_bridge_live() {
  stop_by_pid_file "${BRIDGE_PID_FILE}" "lingxi_nav_bridge" >/dev/null 2>&1 || true
  source_env
  local args=(
    python3 -m robot_local_navigation_v3.lingxi_nav_bridge
    --config-json "${CONFIG_JSON}"
    --host "${BRIDGE_HOST}"
    --port "${BRIDGE_PORT}"
  )
  if [[ -n "${NAV_MODE}" ]]; then
    args+=(--nav-mode "${NAV_MODE}")
  fi
  if [[ -n "${ACTIVE_MAP_NAME}" ]]; then
    args+=(--active-map-name "${ACTIVE_MAP_NAME}")
  fi
  echo "前台启动 lingxi_nav_bridge，日志会实时显示在当前终端。"
  print_bridge_urls
  echo "日志文件: ${BRIDGE_LOG_FILE}"
  "${args[@]}" 2>&1 | tee -a "${BRIDGE_LOG_FILE}"
}

start_web() {
  stop_by_pid_file "${WEB_PID_FILE}" "map_label_server_v3" >/dev/null 2>&1 || true
  source_env
  nohup python3 -m robot_local_navigation_v3.map_label_server_v3 \
    --host "${WEB_HOST}" \
    --port "${WEB_PORT}" \
    --config-json "${CONFIG_JSON}" \
    --app-home "${APP_HOME}" \
    --default-map-root "${DEFAULT_MAP_ROOT}" \
    --bridge-host "${BRIDGE_HOST}" \
    --bridge-port "${BRIDGE_PORT}" \
    >"${WEB_LOG_FILE}" 2>&1 &
  echo $! > "${WEB_PID_FILE}"
  echo "已启动 map_label_server_v3 (pid=$(<"${WEB_PID_FILE}"))"
  echo "地图标点前端: http://$(hostname -I 2>/dev/null | awk '{print $1}'):${WEB_PORT}"
  echo "日志文件: ${WEB_LOG_FILE}"
}

start_web_live() {
  stop_by_pid_file "${WEB_PID_FILE}" "map_label_server_v3" >/dev/null 2>&1 || true
  source_env
  echo "前台启动 map_label_server_v3。"
  echo "地图标点前端: http://$(hostname -I 2>/dev/null | awk '{print $1}'):${WEB_PORT}"
  echo "网页里点击“启动桥接”后，18080 bridge 日志会实时显示在当前终端。"
  echo "web日志文件: ${WEB_LOG_FILE}"
  python3 -m robot_local_navigation_v3.map_label_server_v3 \
    --host "${WEB_HOST}" \
    --port "${WEB_PORT}" \
    --config-json "${CONFIG_JSON}" \
    --app-home "${APP_HOME}" \
    --default-map-root "${DEFAULT_MAP_ROOT}" \
    --bridge-host "${BRIDGE_HOST}" \
    --bridge-port "${BRIDGE_PORT}" \
    2>&1 | tee -a "${WEB_LOG_FILE}"
}

status_one() {
  local pid_file="$1"
  local name="$2"
  if [[ -f "${pid_file}" ]]; then
    local pid
    pid="$(<"${pid_file}")"
    if is_pid_running "${pid}"; then
      echo "${name}: running (pid=${pid})"
      return
    fi
  fi
  echo "${name}: stopped"
}

show_logs() {
  local target="${1:-bridge}"
  case "${target}" in
    bridge) tail -F "${BRIDGE_LOG_FILE}" ;;
    web) tail -F "${WEB_LOG_FILE}" ;;
    *) echo "未知日志目标: ${target}" >&2; exit 1 ;;
  esac
}

case "${1:-}" in
  start-bridge)
    start_bridge
    ;;
  start-web)
    start_web
    ;;
  start-web-live)
    start_web_live
    ;;
  up)
    start_bridge
    start_web
    "${SCRIPT_DIR}/start_voice_nav_gateway.sh" start
    ;;
  up-live)
    stop_by_pid_file "${BRIDGE_PID_FILE}" "lingxi_nav_bridge" >/dev/null 2>&1 || true
    start_web_live
    ;;
  stop)
    "${SCRIPT_DIR}/start_voice_nav_gateway.sh" stop
    stop_by_pid_file "${BRIDGE_PID_FILE}" "lingxi_nav_bridge"
    stop_by_pid_file "${WEB_PID_FILE}" "map_label_server_v3"
    ;;
  status)
    status_one "${BRIDGE_PID_FILE}" "lingxi_nav_bridge"
    status_one "${WEB_PID_FILE}" "map_label_server_v3"
    "${SCRIPT_DIR}/start_voice_nav_gateway.sh" status || true
    echo "配置: ${CONFIG_JSON}"
    print_bridge_urls
    echo "地图标点前端: http://${WEB_HOST}:${WEB_PORT}"
    ;;
  logs)
    show_logs "${2:-bridge}"
    ;;
  -h|--help|help|"")
    usage
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac
