#!/usr/bin/env bash
set -euo pipefail

PRODUCT_ROOT="${SPARKROBOT_PRODUCT_ROOT:-/opt/sparkrobot/lingxi-nav/current}"
STATE_ROOT="${SPARKROBOT_STATE_ROOT:-/var/lib/sparkrobot/lingxi-nav}"
LOG_DIR="/var/log/sparkrobot/lingxi-nav"
PID_FILE="${STATE_ROOT}/pids/robot_lingxi_bridge.pid"
LOG_FILE="${LOG_DIR}/robot_lingxi_bridge.log"

sudo -n /usr/local/libexec/sparkrobot-license-verify --quiet --require-feature voice
sudo -n /usr/local/libexec/sparkrobot-release-verify --quiet

mkdir -p "${STATE_ROOT}/pids" "${LOG_DIR}"
set +u
[[ -r /opt/ros/humble/setup.bash ]] && source /opt/ros/humble/setup.bash
[[ -r /home/agi/aimdk/install/setup.bash ]] && source /home/agi/aimdk/install/setup.bash
set -u

export PYTHONPATH="${PRODUCT_ROOT}/navigation:${PYTHONPATH:-}"
export LINGXI_BRIDGE_HOST="${LINGXI_BRIDGE_HOST:-127.0.0.1}"
export LINGXI_BRIDGE_PORT="${LINGXI_BRIDGE_PORT:-18080}"

if [[ -f "${PID_FILE}" ]]; then
  old_pid="$(<"${PID_FILE}")"
  if kill -0 "${old_pid}" 2>/dev/null; then
    kill "${old_pid}" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "${old_pid}" 2>/dev/null || break
      sleep 0.1
    done
  fi
  rm -f "${PID_FILE}"
fi

nohup python3 -c 'from robot_local_navigation_v3.robot_lingxi_bridge import main; main()' \
  >"${LOG_FILE}" 2>&1 &
echo $! >"${PID_FILE}"

for _ in $(seq 1 40); do
  pid="$(<"${PID_FILE}")"
  kill -0 "${pid}" 2>/dev/null || { tail -n 80 "${LOG_FILE}" >&2 || true; exit 1; }
  if ss -ltn 2>/dev/null | grep -q ":${LINGXI_BRIDGE_PORT} "; then
    echo "机器人语音 ROS bridge 已启动（127.0.0.1:${LINGXI_BRIDGE_PORT}）"
    exit 0
  fi
  sleep 0.25
done

echo "机器人语音 ROS bridge 进程已启动，但端口 ${LINGXI_BRIDGE_PORT} 未就绪" >&2
tail -n 80 "${LOG_FILE}" >&2 || true
exit 1
