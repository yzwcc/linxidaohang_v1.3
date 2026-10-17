#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 sudo bash ./control.sh 运行" >&2
  exit 1
fi

NAV_ROOT="${LINGXI_NAV_ROOT:-/opt/sparkrobot/lingxi-nav/current}"
MOTION_TARGET="${NAV_ROOT}/navigation/robot_local_navigation_v3/sdk_motion_controller.py"
BACKUP_ROOT="/var/lib/sparkrobot/lingxi-v1.3.8-motion-stand-fix/backups"

start_services() {
  if command -v sparkrobot-lingxi-internal >/dev/null 2>&1; then
    sparkrobot-lingxi-internal start
  else
    systemctl start lingxi-java-agent-robot.service
  fi
}

stop_services() {
  if command -v sparkrobot-lingxi-internal >/dev/null 2>&1; then
    sparkrobot-lingxi-internal stop || true
  else
    systemctl stop lingxi-java-agent-robot.service 2>/dev/null || true
  fi
}

latest_backup() {
  find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr | sed -n '1s/^[^ ]* //p'
}

status() {
  echo "motion_controller=${MOTION_TARGET}"
  if grep -q "STABILITY_REJECTION_MARKERS" "${MOTION_TARGET}" 2>/dev/null; then
    echo "stable_rejection_recovery=enabled"
  else
    echo "stable_rejection_recovery=missing"
  fi
  if systemctl list-unit-files lingxi-java-agent-robot.service >/dev/null 2>&1; then
    echo "java_agent=$(systemctl is-active lingxi-java-agent-robot.service || true)"
  fi
  journalctl -u lingxi-java-agent-robot.service -n 80 --no-pager 2>/dev/null \
    | grep -E 'SDK motion rejected|unstable stand|STAND_DEFAULT|动作服务拒绝|动作请求已接受' \
    | tail -n 20 || true
}

rollback() {
  local backup="${1:-$(latest_backup)}"
  [[ -n "${backup}" && -f "${backup}/targets.tsv" ]] || {
    echo "找不到增量包事务备份：${backup:-<none>}" >&2
    exit 1
  }
  stop_services
  while IFS=$'\t' read -r source target; do
    [[ -n "${source}" && -n "${target}" ]] || continue
    [[ -f "${source}" ]] || { echo "备份文件缺失：${source}" >&2; exit 1; }
    install -d "$(dirname -- "${target}")"
    cp -a "${source}" "${target}"
  done < "${backup}/targets.tsv"
  start_services
  echo "已回滚：${backup}"
}

case "${1:-status}" in
  status) status ;;
  rollback) rollback "${2:-}" ;;
  logs)
    journalctl -u lingxi-java-agent-robot.service -f -o short-precise \
      | grep --line-buffered -E 'motion=1003|motion=|STAND_DEFAULT|stable|站立|拒绝|accepted|rejected'
    ;;
  *)
    echo "用法：sudo bash ./control.sh status|logs|rollback [备份目录]" >&2
    exit 2
    ;;
esac
