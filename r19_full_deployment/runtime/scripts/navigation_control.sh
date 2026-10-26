#!/usr/bin/env bash
set -euo pipefail
umask 0007

PRODUCT_ROOT="${SPARKROBOT_PRODUCT_ROOT:-/opt/sparkrobot/lingxi-nav/current}"
STATE_ROOT="${SPARKROBOT_STATE_ROOT:-/var/lib/sparkrobot/lingxi-nav}"
NAV_ROOT="${PRODUCT_ROOT}/navigation"
CONFIG_JSON="${ROBOT_LOCAL_NAVIGATION_V3_CONFIG_JSON:-${STATE_ROOT}/unified_nav_config.json}"
MAP_ROOT="${ROBOT_LOCAL_NAV_MAP_ROOT:-/agibot/data/var/MapManagerModule/}"
BRIDGE_PORT="${ROBOT_LOCAL_NAVIGATION_V3_BRIDGE_PORT:-18082}"
WEB_PORT="${ROBOT_LOCAL_NAVIGATION_V3_WEB_PORT:-8002}"
VOICE_GATEWAY_PORT="${ROBOT_LOCAL_NAVIGATION_V3_VOICE_GATEWAY_PORT:-18083}"
VOICE_PORT="${ROBOT_LOCAL_NAVIGATION_V3_VOICE_PORT:-18080}"
ROS_SETUP="${ROBOT_LOCAL_NAV_ROS_SETUP:-/opt/ros/humble/setup.bash}"
AIMDK_SETUP="${ROBOT_LOCAL_NAV_AIMDK_SETUP:-${ROBOT_AIMDK_SETUP:-}}"
PID_DIR="${STATE_ROOT}/pids"
LOG_DIR="/var/log/sparkrobot/lingxi-nav"
ROS_LOG_DIR="${ROBOT_LOCAL_NAV_ROS_LOG_DIR:-${LOG_DIR}/ros}"

source_env() {
  export ROBOT_LOCAL_NAVIGATION_V3_HOME="${STATE_ROOT}"
  export NAV_ODOM_TOPIC="${NAV_ODOM_TOPIC:-/slam/lidar_odom}"
  export ROS_LOG_DIR
  set +u
  [[ -r "${ROS_SETUP}" ]] && source "${ROS_SETUP}"
  [[ -n "${AIMDK_SETUP}" && -r "${AIMDK_SETUP}" ]] || { echo "ROBOT_AIMDK_SETUP is missing or unreadable" >&2; return 1; }
  source "${AIMDK_SETUP}"
  set -u
  [[ -r /agibot/software/entry/cfg/ros_dds_configuration.xml ]] \
    && export FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/entry/cfg/ros_dds_configuration.xml
  [[ -r /agibot/software/nav/bin/librmw_fastrtps.so ]] \
    && export RMW_LIBRARY_PATH=/agibot/software/nav/bin/librmw_fastrtps.so
  local ros_prefix="${ROS_SETUP%/setup.bash}"
  export PYTHONPATH="${NAV_ROOT}:${ros_prefix}/local/lib/python3.10/dist-packages:${ros_prefix}/lib/python3.10/site-packages:/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages:${PYTHONPATH:-}"
  local aimdk_prefix="${AIMDK_SETUP%/setup.bash}"
  for prefix in "${ros_prefix}" /opt/ros/humble "${aimdk_prefix}"; do
    if [[ -d "${prefix}/lib" ]]; then
      export LD_LIBRARY_PATH="${prefix}/lib:${prefix}/lib/aarch64-linux-gnu:${LD_LIBRARY_PATH:-}"
    fi
  done
}

python_preflight() {
  python3 - <<'PY'
import rclpy
import rclpy.action
from robot_local_navigation_v3 import lingxi_nav_bridge
print(f"导航 Python 环境正常：rclpy={rclpy.__file__} bridge={lingxi_nav_bridge.__file__}")
PY
}

stop_pid() {
  local file="$1" name="$2" pid=""
  [[ -f "${file}" ]] || return 0
  pid="$(<"${file}")"
  if kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}" 2>/dev/null || true
    for _ in $(seq 1 30); do
      kill -0 "${pid}" 2>/dev/null || break
      sleep 0.1
    done
    kill -TERM "${pid}" 2>/dev/null || true
    echo "已停止 ${name} (pid=${pid})"
  fi
  rm -f "${file}"
}

wait_pid() {
  local file="$1" name="$2" log="$3" pid="$(<"$1")"
  for _ in $(seq 1 30); do
    kill -0 "${pid}" 2>/dev/null || { tail -n 80 "${log}" >&2 || true; return 1; }
    sleep 0.1
  done
  echo "${name} 已启动 (pid=${pid})"
}

start_all() {
  source_env
  mkdir -p "${PID_DIR}" "${LOG_DIR}" "${ROS_LOG_DIR}"
  stop_all >/dev/null 2>&1 || true
  if ! python_preflight >"${LOG_DIR}/navigation_preflight.log" 2>&1; then
    echo "导航 Python/ROS 环境预检失败，未启动导航服务" >&2
    cat "${LOG_DIR}/navigation_preflight.log" >&2
    printf 'PYTHONPATH=%s\nLD_LIBRARY_PATH=%s\n' "${PYTHONPATH:-}" "${LD_LIBRARY_PATH:-}" >&2
    return 1
  fi
  nohup python3 -m robot_local_navigation_v3.lingxi_nav_bridge \
    --config-json "${CONFIG_JSON}" --host 0.0.0.0 --port "${BRIDGE_PORT}" \
    >"${LOG_DIR}/lingxi_nav_bridge.log" 2>&1 &
  echo $! >"${PID_DIR}/lingxi_nav_bridge.pid"
  wait_pid "${PID_DIR}/lingxi_nav_bridge.pid" lingxi_nav_bridge "${LOG_DIR}/lingxi_nav_bridge.log"

  nohup python3 -c 'from robot_local_navigation_v3.map_label_server_v3 import main; main()' \
    --host 0.0.0.0 --port "${WEB_PORT}" --config-json "${CONFIG_JSON}" \
    --app-home "${STATE_ROOT}" --default-map-root "${MAP_ROOT}" \
    --bridge-host 127.0.0.1 --bridge-port "${BRIDGE_PORT}" \
    >"${LOG_DIR}/map_label_server_v3.log" 2>&1 &
  echo $! >"${PID_DIR}/map_label_server_v3.pid"
  wait_pid "${PID_DIR}/map_label_server_v3.pid" map_label_server_v3 "${LOG_DIR}/map_label_server_v3.log"

  nohup python3 -c 'from robot_local_navigation_v3.voice_nav_gateway import main; main()' \
    --host 127.0.0.1 --port "${VOICE_GATEWAY_PORT}" \
    --voice-port "${VOICE_PORT}" --nav-port "${BRIDGE_PORT}" \
    >"${LOG_DIR}/voice_nav_gateway.log" 2>&1 &
  echo $! >"${PID_DIR}/voice_nav_gateway.pid"
  wait_pid "${PID_DIR}/voice_nav_gateway.pid" voice_nav_gateway "${LOG_DIR}/voice_nav_gateway.log"
  echo "导航、地图和语音兼容网关已启动"
}

stop_all() {
  stop_pid "${PID_DIR}/voice_nav_gateway.pid" voice_nav_gateway
  stop_pid "${PID_DIR}/lingxi_nav_bridge.pid" lingxi_nav_bridge
  stop_pid "${PID_DIR}/map_label_server_v3.pid" map_label_server_v3
}

status_all() {
  local failed=0
  for item in \
    "lingxi_nav_bridge:${PID_DIR}/lingxi_nav_bridge.pid" \
    "map_label_server_v3:${PID_DIR}/map_label_server_v3.pid" \
    "voice_nav_gateway:${PID_DIR}/voice_nav_gateway.pid"; do
    name="${item%%:*}"; file="${item#*:}"; pid=""
    [[ -f "${file}" ]] && pid="$(<"${file}")"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      echo "${name}=running pid=${pid}"
    else
      echo "${name}=stopped"
      failed=1
    fi
  done
  return "${failed}"
}

case "${1:-status}" in
  up|start) start_all ;;
  stop) stop_all ;;
  restart) stop_all; start_all ;;
  status) status_all ;;
  *) echo "用法：$0 start|stop|restart|status" >&2; exit 2 ;;
esac
