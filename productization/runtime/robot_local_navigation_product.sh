#!/usr/bin/env bash
set -euo pipefail

PRODUCT_ROOT="${SPARKROBOT_PRODUCT_ROOT:-/opt/sparkrobot/lingxi-nav/current}"
STATE_ROOT="${SPARKROBOT_STATE_ROOT:-/var/lib/sparkrobot/lingxi-nav}"
NAV_PACKAGE_ROOT="${PRODUCT_ROOT}/navigation"
CONFIG_JSON="${ROBOT_LOCAL_NAVIGATION_V3_CONFIG_JSON:-${STATE_ROOT}/unified_nav_config.json}"
DEFAULT_MAP_ROOT="${ROBOT_LOCAL_NAV_MAP_ROOT:-/agibot/data/var/MapManagerModule/}"
BRIDGE_PORT="${ROBOT_LOCAL_NAVIGATION_V3_BRIDGE_PORT:-18082}"
WEB_PORT="${ROBOT_LOCAL_NAVIGATION_V3_WEB_PORT:-8002}"
VOICE_GATEWAY_PORT="${ROBOT_LOCAL_NAVIGATION_V3_VOICE_GATEWAY_PORT:-18083}"
VOICE_PORT="${ROBOT_LOCAL_NAVIGATION_V3_VOICE_PORT:-18080}"
PID_DIR="${STATE_ROOT}/pids"
LOG_DIR="/var/log/sparkrobot/lingxi-nav"

license_check() {
  sudo -n /usr/local/libexec/sparkrobot-license-verify --quiet \
    --require-feature navigation
  sudo -n /usr/local/libexec/sparkrobot-release-verify --quiet
}

source_env() {
  export ROBOT_LOCAL_NAVIGATION_V3_HOME="${STATE_ROOT}"
  export PYTHONPATH="${NAV_PACKAGE_ROOT}:${PYTHONPATH:-}"
  export NAV_ODOM_TOPIC="${NAV_ODOM_TOPIC:-/slam/lidar_odom}"
  [[ -r /agibot/software/entry/cfg/ros_dds_configuration.xml ]] \
    && export FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/entry/cfg/ros_dds_configuration.xml
  [[ -r /agibot/software/nav/bin/librmw_fastrtps.so ]] \
    && export RMW_LIBRARY_PATH=/agibot/software/nav/bin/librmw_fastrtps.so
  set +u
  [[ -r /opt/ros/humble/setup.bash ]] && source /opt/ros/humble/setup.bash
  [[ -r /home/agi/aimdk/install/setup.bash ]] && source /home/agi/aimdk/install/setup.bash
  set -u
}

stop_pid() {
  local file="$1" name="$2"
  if [[ -f "$file" ]]; then
    local pid
    pid="$(<"$file")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.1
      done
      kill -TERM "$pid" 2>/dev/null || true
      echo "已停止 ${name} (pid=${pid})"
    fi
    rm -f "$file"
  fi
}

wait_pid() {
  local file="$1" name="$2" log="$3"
  local pid="$(<"$file")"
  for _ in $(seq 1 30); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "${name} 启动失败，最近日志：" >&2
      tail -n 60 "$log" >&2 || true
      return 1
    fi
    sleep 0.1
  done
}

start_all() {
  license_check
  source_env
  mkdir -p "$PID_DIR" "$LOG_DIR"
  stop_all >/dev/null 2>&1 || true
  nohup python3 -c 'from robot_local_navigation_v3.lingxi_nav_bridge import main; main()' \
    --config-json "$CONFIG_JSON" --host 0.0.0.0 --port "$BRIDGE_PORT" \
    >"${LOG_DIR}/lingxi_nav_bridge.log" 2>&1 &
  echo $! >"${PID_DIR}/lingxi_nav_bridge.pid"
  wait_pid "${PID_DIR}/lingxi_nav_bridge.pid" lingxi_nav_bridge "${LOG_DIR}/lingxi_nav_bridge.log"
  nohup python3 -c 'from robot_local_navigation_v3.map_label_server_v3 import main; main()' \
    --host 0.0.0.0 --port "$WEB_PORT" --config-json "$CONFIG_JSON" \
    --app-home "$STATE_ROOT" --default-map-root "$DEFAULT_MAP_ROOT" \
    --bridge-host 127.0.0.1 --bridge-port "$BRIDGE_PORT" \
    >"${LOG_DIR}/map_label_server_v3.log" 2>&1 &
  echo $! >"${PID_DIR}/map_label_server_v3.pid"
  wait_pid "${PID_DIR}/map_label_server_v3.pid" map_label_server_v3 "${LOG_DIR}/map_label_server_v3.log"
  nohup python3 -c 'from robot_local_navigation_v3.voice_nav_gateway import main; main()' \
    --host 127.0.0.1 --port "$VOICE_GATEWAY_PORT" \
    --voice-port "$VOICE_PORT" --nav-port "$BRIDGE_PORT" \
    >"${LOG_DIR}/voice_nav_gateway.log" 2>&1 &
  echo $! >"${PID_DIR}/voice_nav_gateway.pid"
  wait_pid "${PID_DIR}/voice_nav_gateway.pid" voice_nav_gateway "${LOG_DIR}/voice_nav_gateway.log"
  echo "灵犀导航、地图和语音兼容网关已启动"
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
    local name="${item%%:*}" file="${item#*:}" pid=""
    [[ -f "$file" ]] && pid="$(<"$file")"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "${name}=running pid=${pid}"
    else
      echo "${name}=stopped"
      failed=1
    fi
  done
  return "$failed"
}

case "${1:-}" in
  up|start) start_all ;;
  stop) stop_all ;;
  restart) stop_all; start_all ;;
  status) status_all ;;
  *) echo "用法：$0 up|stop|restart|status" >&2; exit 2 ;;
esac
