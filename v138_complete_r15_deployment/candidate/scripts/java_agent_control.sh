#!/usr/bin/env bash
set -euo pipefail

SERVICE="lingxi-java-agent-robot.service"
PRODUCT_ROOT="${SPARKROBOT_PRODUCT_ROOT:-/opt/sparkrobot/lingxi-nav/current}"
LOG_FILE="/var/log/sparkrobot/lingxi-nav/java-agent.log"

status() {
  local running=false pid=0 recent="" started=""
  if sudo -n systemctl is-active --quiet "${SERVICE}"; then
    running=true
    pid="$(sudo -n systemctl show -p MainPID --value "${SERVICE}")"
  fi
  [[ -f "${LOG_FILE}" ]] && recent="$(tail -c 2097152 "${LOG_FILE}" 2>/dev/null || true)"
  started="$(printf '%s\n' "${recent}" | grep -n 'Lingxi navigation agent started\.' | tail -n 1 || true)"
  printf 'configured=%s\nrunning=%s\npid=%s\nhome=%s\n' \
    "$([[ -f /etc/sparkrobot/lingxi-nav/lingxi-nav.properties ]] && echo true || echo false)" \
    "${running}" "${pid}" "${PRODUCT_ROOT}/java"
  printf 'nav_base_url=http://127.0.0.1:18083\nlog_file=%s\n' "${LOG_FILE}"
  python3 "${PRODUCT_ROOT}/scripts/agent_connection_status.py" \
    --file "${LINGXI_AGENT_STATE_FILE:-/var/lib/sparkrobot/lingxi-nav/pids/java-agent-state.json}" \
    --pid "${pid}" --running "${running}"
  printf 'last_started_line=%s\n' "${started#*:}"
  printf '%s\n' '---LOG---'
  printf '%s\n' "${recent}" | grep -v 'voice_embedding' | tail -n 80 || true
}

case "${1:-status}" in
  start|restart|stop)
    sudo -n systemctl "$1" "${SERVICE}"
    sleep 2
    status
    ;;
  status) status ;;
  *) echo "用法：$0 start|restart|stop|status" >&2; exit 2 ;;
esac
