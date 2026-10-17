#!/usr/bin/env bash
set -Eeuo pipefail

NAV_DIR="${LINKCRAFT_NAV_DIR:-/opt/sparkrobot/lingxi-nav/current/navigation/robot_local_navigation_v3}"
CONSOLE_DIR="${LINKCRAFT_CONSOLE_DIR:-/opt/sparkrobot/lingxi-nav/current/console/dist}"
BACKUP_ROOT="${LINKCRAFT_BACKUP_ROOT:-/var/lib/sparkrobot/lingxi-linkcraft/backups}"
LOG_FILE="${LINKCRAFT_LOG_FILE:-/var/log/sparkrobot/lingxi-nav/lingxi_nav_bridge.log}"
RESOURCE_CONFIG="${LINKCRAFT_RESOURCE_CONFIG:-/agibot/nfs/soc0/var/robot_proxy/resources/resource_config.yaml}"
RESOURCE_USER="${LINKCRAFT_RESOURCE_USER:-run}"
FILES=(
  lingxi_nav_bridge.py
  sdk_motion_controller.py
  linkcraft_resources.py
  tour_step_actions.py
  tour_interaction_policy.py
)

require_root() {
  [[ "${EUID}" -eq 0 ]] || { echo "请使用 sudo 运行" >&2; exit 1; }
}

restart_navigation() {
  local control="${LINKCRAFT_NAV_CONTROL:-/opt/sparkrobot/lingxi-nav/current/scripts/navigation_control.sh}"
  if [[ -x "${control}" ]]; then
    if command -v runuser >/dev/null 2>&1 && id agi >/dev/null 2>&1; then
      runuser -u agi -- "${control}" restart
    else
      "${control}" restart
    fi
  elif command -v sparkrobot-lingxi-internal >/dev/null 2>&1; then
    echo "兼容调用内部控制器 restart；该方式可能同时重启语音服务。" >&2
    sparkrobot-lingxi-internal restart
  else
    echo "找不到导航重启脚本：${control}" >&2
    exit 1
  fi
}

status() {
  local missing=0
  echo "导航目录：${NAV_DIR}"
  for file in "${FILES[@]}"; do
    if [[ -f "${NAV_DIR}/${file}" ]]; then
      echo "[正常] ${file}"
    else
      echo "[缺失] ${file}"
      missing=1
    fi
  done
  if [[ -f "${CONSOLE_DIR}/linkcraftResources.js" ]]; then
    echo "[正常] console/dist/linkcraftResources.js"
  else
    echo "[缺失] console/dist/linkcraftResources.js"
    missing=1
  fi
  local control="${LINKCRAFT_NAV_CONTROL:-/opt/sparkrobot/lingxi-nav/current/scripts/navigation_control.sh}"
  if [[ -x "${control}" ]]; then
    "${control}" status || true
  fi
  return "${missing}"
}

resources() {
  [[ -f "${NAV_DIR}/linkcraft_resources.py" ]] || { echo "灵创资源扫描文件不存在" >&2; exit 1; }
  local output=""
  local user="${RESOURCE_USER}"
  if [[ "${EUID}" -eq 0 && "${RESOURCE_USER}" != "root" && "$(id -u "${RESOURCE_USER}" 2>/dev/null || true)" != "" ]]; then
    if ! output="$(runuser -u "${RESOURCE_USER}" -- python3 "${NAV_DIR}/linkcraft_resources.py" --config "${RESOURCE_CONFIG}" --json 2>&1)"; then
      echo "灵创资源扫描失败：用户 ${RESOURCE_USER} 无法读取 ${RESOURCE_CONFIG}" >&2
      echo "请确认资源挂载已就绪，或使用 LINKCRAFT_RESOURCE_USER 指定有权限的机器人用户。" >&2
      echo "${output}" >&2
      return 1
    fi
  else
    if ! output="$(python3 "${NAV_DIR}/linkcraft_resources.py" --config "${RESOURCE_CONFIG}" --json 2>&1)"; then
      echo "灵创资源扫描失败：无法读取 ${RESOURCE_CONFIG}" >&2
      echo "${output}" >&2
      return 1
    fi
  fi
  printf '%s\n' "${output}"
}

doctor() {
  require_root
  status
  python3 -m py_compile "${NAV_DIR}/"*.py
  echo "灵创附加文件静态检查通过"
  resources
}

rollback() {
  require_root
  local backup="${1:-}"
  [[ -n "${backup}" && -d "${backup}" ]] || {
    echo "用法：sudo $0 rollback ${BACKUP_ROOT}/时间戳目录" >&2
    exit 2
  }
  [[ -d "${NAV_DIR}" ]] || { echo "导航目录不存在：${NAV_DIR}" >&2; exit 1; }
  for file in "${FILES[@]}"; do
    [[ -f "${backup}/${file}" ]] || { echo "备份不完整，缺少 ${file}" >&2; exit 1; }
    [[ -f "${NAV_DIR}/${file}" ]] || { echo "当前目录缺少 ${file}" >&2; exit 1; }
  done
  [[ -f "${backup}/console/linkcraftResources.js" ]] || { echo "备份不完整，缺少 console/dist/linkcraftResources.js" >&2; exit 1; }
  [[ -f "${CONSOLE_DIR}/linkcraftResources.js" ]] || { echo "当前网页目录缺少 linkcraftResources.js" >&2; exit 1; }
  for file in "${FILES[@]}"; do
    temp_file="${NAV_DIR}/.${file}.rollback.$$"
    install -m "$(stat -c '%a' "${NAV_DIR}/${file}")" "${backup}/${file}" "${temp_file}"
    chown --reference="${NAV_DIR}/${file}" "${temp_file}"
    mv -f "${temp_file}" "${NAV_DIR}/${file}"
  done
  console_temp="${CONSOLE_DIR}/.linkcraftResources.js.rollback.$$"
  install -m "$(stat -c '%a' "${CONSOLE_DIR}/linkcraftResources.js")" "${backup}/console/linkcraftResources.js" "${console_temp}"
  chown --reference="${CONSOLE_DIR}/linkcraftResources.js" "${console_temp}"
  mv -f "${console_temp}" "${CONSOLE_DIR}/linkcraftResources.js"
  echo "已恢复备份：${backup}"
  restart_navigation
  if systemctl cat lingxi-nav-console.service >/dev/null 2>&1; then systemctl restart lingxi-nav-console.service; fi
  echo "已重启导航 bridge"
}

logs() {
  if [[ -f "${LOG_FILE}" ]]; then
    tail -n 200 "${LOG_FILE}"
  else
    echo "日志文件不存在：${LOG_FILE}" >&2
    exit 1
  fi
}

case "${1:-status}" in
  status) status ;;
  doctor) doctor ;;
  resources) resources ;;
  restart) require_root; restart_navigation ;;
  rollback) rollback "${2:-}" ;;
  logs) logs ;;
  *) echo "用法：$0 status|doctor|resources|restart|rollback 备份目录|logs" >&2; exit 2 ;;
esac
