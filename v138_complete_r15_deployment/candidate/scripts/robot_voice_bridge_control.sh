#!/usr/bin/env bash
set -euo pipefail
umask 0007

PRODUCT_ROOT="${SPARKROBOT_PRODUCT_ROOT:-/opt/sparkrobot/lingxi-nav/current}"
ROS_SETUP="${ROBOT_LOCAL_NAV_ROS_SETUP:-/opt/ros/humble/setup.bash}"
AIMDK_SETUP="${ROBOT_LOCAL_NAV_AIMDK_SETUP:-/home/agi/aimdk/install/setup.bash}"
STATE_ROOT="${SPARKROBOT_STATE_ROOT:-/var/lib/sparkrobot/lingxi-nav}"
LOG_DIR="${SPARKROBOT_LOG_DIR:-/var/log/sparkrobot/lingxi-nav}"
ROS_LOG_DIR="${ROBOT_LOCAL_NAV_ROS_LOG_DIR:-${LOG_DIR}/ros}"
PID_FILE="${STATE_ROOT}/pids/robot_lingxi_bridge.pid"
LOG_FILE="${LOG_DIR}/robot_lingxi_bridge.log"
RUNTIME_USER="${SPARKROBOT_RUNTIME_USER:-run}"
RUNTIME_GROUP="${SPARKROBOT_RUNTIME_GROUP:-sparkrobot-nav}"

prepare_runtime_paths() {
  [[ "${EUID}" -eq 0 ]] || return 0
  install -d -m 0770 -o "${RUNTIME_USER}" -g "${RUNTIME_GROUP}" \
    "${STATE_ROOT}/pids" "${LOG_DIR}" "${ROS_LOG_DIR}"
  touch "${LOG_FILE}" "${LOG_DIR}/voice_preflight.log"
  chown "${RUNTIME_USER}:${RUNTIME_GROUP}" \
    "${LOG_FILE}" "${LOG_DIR}/voice_preflight.log"
  chmod 0660 "${LOG_FILE}" "${LOG_DIR}/voice_preflight.log"
}

source_env() {
  set +u
  [[ -r "${ROS_SETUP}" ]] && source "${ROS_SETUP}"
  [[ -r "${AIMDK_SETUP}" ]] && source "${AIMDK_SETUP}"
  set -u
  local ros_prefix="${ROS_SETUP%/setup.bash}"
  export PYTHONPATH="${PRODUCT_ROOT}/navigation:${ros_prefix}/local/lib/python3.10/dist-packages:${ros_prefix}/lib/python3.10/site-packages:/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages:${PYTHONPATH:-}"
  for prefix in "${ros_prefix}" /opt/ros/humble /home/agi/aimdk/install; do
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

pid_matches_bridge() {
  local pid="$1" command=""
  [[ "${pid}" =~ ^[1-9][0-9]*$ && -r "/proc/${pid}/cmdline" ]] || return 1
  command="$(tr '\0' ' ' <"/proc/${pid}/cmdline")"
  [[ "${command}" == *robot_local_navigation_v3.robot_lingxi_bridge* ]]
}

bridge_ready() {
  [[ -r "${PID_FILE}" ]] || return 1
  local pid="$(<"${PID_FILE}")"
  pid_matches_bridge "${pid}" && kill -0 "${pid}" 2>/dev/null || return 1
  curl -fsS --max-time 2 "http://127.0.0.1:${LINGXI_BRIDGE_PORT:-18080}/health" >/dev/null 2>&1
}

stop_bridge() {
  [[ -f "${PID_FILE}" ]] || return 0
  local pid="$(<"${PID_FILE}")"
  if pid_matches_bridge "${pid}" && kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}" 2>/dev/null || true
    for _ in $(seq 1 30); do kill -0 "${pid}" 2>/dev/null || break; sleep 0.1; done
    if pid_matches_bridge "${pid}" && kill -0 "${pid}" 2>/dev/null; then
      echo "机器人语音 bridge 尚未退出，保留 PID 以便后续检查 (pid=${pid})" >&2
      return 1
    fi
    echo "机器人语音 bridge 已停止 (pid=${pid})"
  fi
  rm -f "${PID_FILE}"
}

start_bridge() {
  if bridge_ready; then
    echo "机器人语音 bridge 已在线，保留当前语音会话"
    return 0
  fi
  source_env
  mkdir -p "${STATE_ROOT}/pids" "${LOG_DIR}" "${ROS_LOG_DIR}"
  stop_bridge
  if ss -ltn 2>/dev/null | grep -q ":${LINGXI_BRIDGE_PORT:-18080} "; then
    echo "语音 bridge 端口已被其他进程占用，请先检查端口归属" >&2
    return 1
  fi
  if ! python_preflight >"${LOG_DIR}/voice_preflight.log" 2>&1; then
    echo "语音 bridge Python/ROS 环境预检失败，未启动 18080 服务" >&2
    cat "${LOG_DIR}/voice_preflight.log" >&2
    printf 'ROS_SETUP=%s\nAIMDK_SETUP=%s\nPYTHONPATH=%s\nLD_LIBRARY_PATH=%s\n' \
      "${ROS_SETUP}" "${AIMDK_SETUP}" "${PYTHONPATH:-}" "${LD_LIBRARY_PATH:-}" >&2
    return 1
  fi
  nohup python3 -c 'from robot_local_navigation_v3.robot_lingxi_bridge import main; main()' \
    >"${LOG_FILE}" 2>&1 9>&- &
  echo $! >"${PID_FILE}"
  for _ in $(seq 1 40); do
    pid="$(<"${PID_FILE}")"
    kill -0 "${pid}" 2>/dev/null || { tail -n 80 "${LOG_FILE}" >&2 || true; exit 1; }
    if bridge_ready; then
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
  if bridge_ready; then
    echo "robot_voice_bridge=running pid=$(<"${PID_FILE}")"
  else
    echo "robot_voice_bridge=stopped"
    return 1
  fi
}

ACTION="${1:-start}"
if [[ "${EUID}" -eq 0 ]]; then
  prepare_runtime_paths
  exec runuser -u "${RUNTIME_USER}" -- "$0" "${ACTION}"
fi

if [[ "${ACTION}" != status ]]; then
  mkdir -p "${STATE_ROOT}/pids"
  exec 9>"${STATE_ROOT}/pids/robot-voice-control.lock"
  flock -w 30 9 || { echo "语音服务操作仍在执行，请稍后重试" >&2; exit 1; }
fi

case "${ACTION}" in
  start) start_bridge ;;
  restart) stop_bridge; start_bridge ;;
  stop) stop_bridge ;;
  status) status_bridge ;;
  *) echo "用法：$0 start|stop|restart|status" >&2; exit 2 ;;
esac
