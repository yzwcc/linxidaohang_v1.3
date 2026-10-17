#!/usr/bin/env bash
set -Eeuo pipefail

NAV_DIR="${BLOCKED_REMINDER_NAV_DIR:-/opt/sparkrobot/lingxi-nav/current/navigation/robot_local_navigation_v3}"
BACKUP_ROOT="${BLOCKED_REMINDER_BACKUP_ROOT:-/var/lib/sparkrobot/lingxi-blocked-reminder/backups}"
NAV_CONTROL="${BLOCKED_REMINDER_NAV_CONTROL:-/opt/sparkrobot/lingxi-nav/current/scripts/navigation_control.sh}"
LOG_FILE="${BLOCKED_REMINDER_LOG_FILE:-/var/log/sparkrobot/lingxi-nav/lingxi_nav_bridge.log}"
FILES=(blocked_navigation.py navigation_events.py navigation_client.py lingxi_nav_bridge.py)

die() { echo "失败：$*" >&2; exit 1; }
require_root() { [[ "${EUID}" -eq 0 ]] || die "请使用 sudo 运行"; }

restart_bridge() {
  [[ -x "${NAV_CONTROL}" ]] || die "找不到导航重启脚本：${NAV_CONTROL}"
  if command -v runuser >/dev/null 2>&1 && id agi >/dev/null 2>&1; then
    runuser -u agi -- "${NAV_CONTROL}" restart
  else
    "${NAV_CONTROL}" restart
  fi
}

status() {
  echo "导航目录：${NAV_DIR}"
  for file in "${FILES[@]}"; do
    [[ -f "${NAV_DIR}/${file}" ]] && echo "[正常] ${file}" || echo "[缺失] ${file}"
  done
  echo "新增提醒：请大家让让我；间隔=5秒"
  if curl -fsS --max-time 2 http://127.0.0.1:18082/nav/status >/dev/null 2>&1; then
    echo "[正常] lingxi_nav_bridge HTTP 18082"
  else
    echo "[失败] lingxi_nav_bridge HTTP 18082"
  fi
}

doctor() {
  require_root
  status
  python3 -m py_compile "${NAV_DIR}/blocked_navigation.py" "${NAV_DIR}/navigation_events.py" \
    "${NAV_DIR}/navigation_client.py" "${NAV_DIR}/lingxi_nav_bridge.py"
  grep -q '请大家让让我' "${NAV_DIR}/lingxi_nav_bridge.py" || die "提醒文本未安装"
  grep -q 'NavigationEventType.BLOCKED' "${NAV_DIR}/lingxi_nav_bridge.py" || die "受阻事件未接入"
  curl -fsS --max-time 3 http://127.0.0.1:18082/nav/status >/dev/null || die "导航 bridge 健康检查失败"
  echo "导航受阻礼貌提醒验收通过；原有导航失败提示保留"
}

rollback() {
  require_root
  local backup="${1:-}"
  [[ -n "${backup}" && -d "${backup}" ]] || die "用法：sudo $0 rollback ${BACKUP_ROOT}/时间戳目录"
  local file temp
  for file in "${FILES[@]}"; do
    [[ -f "${backup}/${file}" ]] || die "备份不完整，缺少 ${file}"
    temp="${NAV_DIR}/.${file}.rollback.$$"
    install -m "$(stat -c '%a' "${NAV_DIR}/${file}")" "${backup}/${file}" "${temp}"
    chown --reference="${NAV_DIR}/${file}" "${temp}"
    mv -f "${temp}" "${NAV_DIR}/${file}"
  done
  restart_bridge
  echo "已回滚导航受阻礼貌提醒：${backup}"
}

case "${1:-status}" in
  status) status ;;
  doctor) doctor ;;
  restart) require_root; restart_bridge ;;
  rollback) rollback "${2:-}" ;;
  logs) [[ -r "${LOG_FILE}" ]] || die "日志不存在：${LOG_FILE}"; tail -n "${2:-200}" "${LOG_FILE}" ;;
  *) die "用法：$0 status|doctor|restart|rollback 备份目录|logs [行数]" ;;
esac
