#!/usr/bin/env bash
set -euo pipefail
umask 0007

PRODUCT_ROOT="${SPARKROBOT_PRODUCT_ROOT:-/opt/sparkrobot/lingxi-nav/current}"
ROS_SETUP="${ROBOT_LOCAL_NAV_ROS_SETUP:-/opt/ros/humble/setup.bash}"
AIMDK_SETUP="${ROBOT_LOCAL_NAV_AIMDK_SETUP:-${ROBOT_AIMDK_SETUP:-}}"
STATE_ROOT="${SPARKROBOT_STATE_ROOT:-/var/lib/sparkrobot/lingxi-nav}"
LOG_DIR="/var/log/sparkrobot/lingxi-nav"
ROS_LOG_DIR="${ROBOT_LOCAL_NAV_ROS_LOG_DIR:-${LOG_DIR}/ros}"
PID_FILE="${STATE_ROOT}/pids/robot_lingxi_bridge.pid"
LOG_FILE="${LOG_DIR}/robot_lingxi_bridge.log"

source_env() {
  set +u
  [[ -r "${ROS_SETUP}" ]] && source "${ROS_SETUP}"
  [[ -n "${AIMDK_SETUP}" && -r "${AIMDK_SETUP}" ]] || { echo "ROBOT_AIMDK_SETUP is missing or unreadable" >&2; return 1; }
  source "${AIMDK_SETUP}"
  set -u
  local ros_prefix="${ROS_SETUP%/setup.bash}"
  export PYTHONPATH="${PRODUCT_ROOT}/navigation:${ros_prefix}/local/lib/python3.10/dist-packages:${ros_prefix}/lib/python3.10/site-packages:/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages:${PYTHONPATH:-}"
  local aimdk_prefix="${AIMDK_SETUP%/setup.bash}"
  for prefix in "${ros_prefix}" /opt/ros/humble "${aimdk_prefix}"; do
    if [[ -d "${prefix}/lib" ]]; then
      export LD_LIBRARY_PATH="${prefix}/lib:${prefix}/lib/aarch64-linux-gnu:${LD_LIBRARY_PATH:-}"
    fi
  done
  export LINGXI_BRIDGE_HOST="${LINGXI_BRIDGE_HOST:-127.0.0.1}"
  export LINGXI_BRIDGE_PORT="${LINGXI_BRIDGE_PORT:-18080}"
  export ROS_LOG_DIR
}

python_preflight() {
  python3 - <<'PY'
import rclpy
import rclpy.action
from robot_local_navigation_v3 import robot_lingxi_bridge
print(f"语音 Python 环境正常：rclpy={rclpy.__file__} bridge={robot_lingxi_bridge.__file__}")
PY
}

stop_bridge() {
  [[ -f "${PID_FILE}" ]] || return 0
  pid="$(<"${PID_FILE}")"
  if kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}" 2>/dev/null || true
    for _ in $(seq 1 30); do kill -0 "${pid}" 2>/dev/null || break; sleep 0.1; done
    echo "机器人语音 bridge 已停止 (pid=${pid})"
  fi
  rm -f "${PID_FILE}"
}

start_bridge() {
  source_env
  mkdir -p "${STATE_ROOT}/pids" "${LOG_DIR}" "${ROS_LOG_DIR}"
  stop_bridge >/dev/null 2>&1 || true
  if ! python_preflight >"${LOG_DIR}/voice_preflight.log" 2>&1; then
    echo "语音 bridge Python/ROS 环境预检失败，未启动 18080 服务" >&2
    cat "${LOG_DIR}/voice_preflight.log" >&2
    printf 'ROS_SETUP=%s\nAIMDK_SETUP=%s\nPYTHONPATH=%s\nLD_LIBRARY_PATH=%s\n' \
      "${ROS_SETUP}" "${AIMDK_SETUP}" "${PYTHONPATH:-}" "${LD_LIBRARY_PATH:-}" >&2
    return 1
  fi
  nohup python3 -c 'from robot_local_navigation_v3.robot_lingxi_bridge import main; main()' \
    >"${LOG_FILE}" 2>&1 &
  echo $! >"${PID_FILE}"
  for _ in $(seq 1 40); do
    pid="$(<"${PID_FILE}")"
    kill -0 "${pid}" 2>/dev/null || { tail -n 80 "${LOG_FILE}" >&2 || true; exit 1; }
    if ss -ltn 2>/dev/null | grep -q ":${LINGXI_BRIDGE_PORT:-18080} "; then
      echo "机器人语音 bridge 已启动（127.0.0.1:${LINGXI_BRIDGE_PORT:-18080}）"
      return
    fi
    sleep 0.25
  done
  echo "语音 bridge 端口未就绪" >&2
  tail -n 80 "${LOG_FILE}" >&2 || true
  exit 1
}

status_bridge() {
  if [[ -f "${PID_FILE}" ]] && kill -0 "$(<"${PID_FILE}")" 2>/dev/null; then
    echo "robot_voice_bridge=running pid=$(<"${PID_FILE}")"
  else
    echo "robot_voice_bridge=stopped"
    return 1
  fi
}

case "${1:-start}" in
  start) start_bridge ;;
  restart) stop_bridge; start_bridge ;;
  stop) stop_bridge ;;
  status) status_bridge ;;
  *) echo "用法：$0 start|stop|restart|status" >&2; exit 2 ;;
esac
