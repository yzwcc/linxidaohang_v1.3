#!/usr/bin/env bash
set -Eeuo pipefail

NAV_DIR="${NAV_DIR:-/opt/sparkrobot/lingxi-nav/current/navigation/robot_local_navigation_v3}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/lib/sparkrobot/lingxi-nav-pnc-arrival-fix/backups}"
NAV_CONTROL="${NAV_CONTROL:-/opt/sparkrobot/lingxi-nav/current/scripts/navigation_control.sh}"
LOG_FILE="${LOG_FILE:-/var/log/sparkrobot/lingxi-nav/lingxi_nav_bridge.log}"

require_root() {
  [[ "${EUID}" -eq 0 ]] || { echo "请使用 sudo 运行此命令" >&2; exit 1; }
}

source_ros_environment() {
  # ROS Humble setup.bash reads AMENT_TRACE_SETUP_FILES under nounset.
  # Disable nounset only while sourcing vendor environment scripts.
  set +u
  [[ -r /opt/ros/humble/setup.bash ]] && source /opt/ros/humble/setup.bash
  [[ -r /home/agi/aimdk/install/setup.bash ]] && source /home/agi/aimdk/install/setup.bash
  set -u
}

restart_navigation() {
  local candidates=("${NAV_CONTROL}" \
    "/opt/sparkrobot/lingxi-nav/current/scripts/navigation_control.sh" \
    "/opt/sparkrobot/lingxi-nav/current/scripts/robot_local_navigation_v3.sh")
  local control=""
  for candidate in "${candidates[@]}"; do
    if [[ -x "${candidate}" ]]; then control="${candidate}"; break; fi
  done
  [[ -n "${control}" ]] || { echo "找不到导航专用重启脚本" >&2; exit 1; }
  if command -v runuser >/dev/null 2>&1 && id agi >/dev/null 2>&1; then
    runuser -u agi -- "${control}" restart
  else
    "${control}" restart
  fi
}

status() {
  local missing=0
  source_ros_environment
  echo "导航目录：${NAV_DIR}"
  for file in navigation_client.py pnc_response.py; do
    if [[ -f "${NAV_DIR}/${file}" ]]; then echo "[正常] ${file}"; else echo "[缺失] ${file}"; missing=1; fi
  done
  local response="$(ros2 topic type /aima/pnc/pnc_task_response 2>/dev/null || true)"
  if [[ -n "${response}" ]]; then
    echo "[正常] PNC 回验话题类型：${response}"
  else
    echo "[提示] 尚未读到 /aima/pnc/pnc_task_response 类型，bridge 会继续使用里程计兜底"
  fi
  return "${missing}"
}

doctor() {
  require_root
  source_ros_environment
  status
  local tmp
  tmp="$(mktemp -d)"
  trap 'rm -rf "${tmp}"' RETURN
  PYTHONPYCACHEPREFIX="${tmp}/pycache" python3 -m py_compile "${NAV_DIR}/"*.py
  echo "Python 语法检查通过"
  echo "PNC 回验话题：$(ros2 topic type /aima/pnc/pnc_task_response 2>/dev/null || echo 未发现)"
  echo "说明：未发现话题不会阻止 bridge 启动，但必须在真实导航前检查 AIMDK/ROS2 环境。"
}

logs() {
  [[ -f "${LOG_FILE}" ]] || { echo "日志不存在：${LOG_FILE}" >&2; exit 1; }
  tail -n 200 "${LOG_FILE}"
}

rollback() {
  require_root
  local backup="${1:-}"
  [[ -d "${backup}" ]] || { echo "用法：sudo bash ./control.sh rollback ${BACKUP_ROOT}/时间戳" >&2; exit 2; }
  [[ -f "${backup}/navigation_client.py" ]] || { echo "备份缺少 navigation_client.py" >&2; exit 1; }
  install -m "$(stat -c '%a' "${NAV_DIR}/navigation_client.py")" "${backup}/navigation_client.py" "${NAV_DIR}/.navigation_client.py.rollback.$$"
  chown --reference="${NAV_DIR}/navigation_client.py" "${NAV_DIR}/.navigation_client.py.rollback.$$"
  mv -f "${NAV_DIR}/.navigation_client.py.rollback.$$" "${NAV_DIR}/navigation_client.py"
  if grep -q '^pnc_response_existed=true$' "${backup}/metadata.txt" 2>/dev/null; then
    install -m 0644 "${backup}/pnc_response.py" "${NAV_DIR}/.pnc_response.py.rollback.$$"
    mv -f "${NAV_DIR}/.pnc_response.py.rollback.$$" "${NAV_DIR}/pnc_response.py"
  else
    rm -f "${NAV_DIR}/pnc_response.py"
  fi
  echo "已回滚：${backup}"
  restart_navigation
}

case "${1:-status}" in
  status) status ;;
  doctor) doctor ;;
  restart) require_root; restart_navigation ;;
  logs) logs ;;
  rollback) rollback "${2:-}" ;;
  *) echo "用法：sudo bash ./control.sh status|doctor|restart|logs|rollback 备份目录" >&2; exit 2 ;;
esac
