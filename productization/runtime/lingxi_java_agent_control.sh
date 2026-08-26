#!/usr/bin/env bash
set -euo pipefail

SERVICE="lingxi-java-agent-robot.service"
PRODUCT_ROOT="${SPARKROBOT_PRODUCT_ROOT:-/opt/sparkrobot/lingxi-nav/current}"
LOG_FILE="/var/log/sparkrobot/lingxi-nav/java-agent.log"

status() {
  sudo -n /usr/local/libexec/sparkrobot-license-verify --quiet --require-feature voice
  sudo -n /usr/local/libexec/sparkrobot-release-verify --quiet
  local running=false pid=0 recent=""
  if sudo -n systemctl is-active --quiet "$SERVICE"; then
    running=true
    pid="$(sudo -n systemctl show -p MainPID --value "$SERVICE")"
  fi
  [[ -f "$LOG_FILE" ]] && recent="$(tail -c 2097152 "$LOG_FILE" 2>/dev/null || true)"
  local started auth online offline
  started="$(printf '%s\n' "$recent" | grep -n 'Lingxi navigation agent started\.' | tail -n 1 || true)"
  auth="$(printf '%s\n' "$recent" | grep -n 'Lingxi auth state:' | tail -n 1 || true)"
  online="$(printf '%s\n' "$recent" | grep -n 'Robot online(' | tail -n 1 || true)"
  offline="$(printf '%s\n' "$recent" | grep -n 'Robot offline(' | tail -n 1 || true)"
  local started_n=0 auth_n=0 online_n=0 offline_n=0
  [[ -n "$started" ]] && started_n="${started%%:*}"
  [[ -n "$auth" ]] && auth_n="${auth%%:*}"
  [[ -n "$online" ]] && online_n="${online%%:*}"
  [[ -n "$offline" ]] && offline_n="${offline%%:*}"
  printf 'configured=%s\n' "$([[ -f /etc/sparkrobot/lingxi-nav/lingxi-nav.properties ]] && echo true || echo false)"
  printf 'running=%s\npid=%s\nhome=%s\n' "$running" "$pid" "${PRODUCT_ROOT}/java"
  printf 'nav_base_url=http://127.0.0.1:18083\nlog_file=%s\n' "$LOG_FILE"
  printf 'lingxi_connected=%s\n' "$([[ "$running" == true && "$auth_n" -gt "$started_n" && "$auth" == *'code=0'* ]] && echo true || echo false)"
  printf 'robot_online=%s\n' "$([[ "$running" == true && "$online_n" -gt "$started_n" && "$online_n" -gt "$offline_n" ]] && echo true || echo false)"
  printf 'last_auth_state=%s\nlast_started_line=%s\n' "${auth#*:}" "${started#*:}"
  printf '%s\n' '---LOG---'
  printf '%s\n' "$recent" | grep -v 'voice_embedding' | tail -n 80 || true
}

case "${1:-status}" in
  start|restart|stop)
    sudo -n systemctl "$1" "$SERVICE"
    sleep 2
    status
    ;;
  status) status ;;
  *) echo "用法：$0 start|restart|stop|status" >&2; exit 2 ;;
esac
