#!/usr/bin/env bash
set -euo pipefail

SERVICE="lingxi-java-agent-robot.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${ROBOT_JAVA_AGENT_HOME:-${SCRIPT_DIR}}"
LOG_FILE="${HOME_DIR}/logs/java-agent-managed.log"

status() {
  local running=false
  local pid=0
  local started_record=""
  local auth_record=""
  local online_record=""
  local offline_record=""
  local started_line=0
  local auth_line=0
  local online_line=0
  local offline_line=0
  local lingxi_connected=false
  local robot_online=false
  local recent_log=""
  if sudo -n systemctl is-active --quiet "$SERVICE"; then
    running=true
    pid="$(sudo -n systemctl show -p MainPID --value "$SERVICE")"
  fi
  if [[ -f "$LOG_FILE" ]]; then
    recent_log="$(tail -c 2097152 "$LOG_FILE" 2>/dev/null || true)"
    started_record="$(printf '%s\n' "$recent_log" | grep -n 'Lingxi navigation agent started\.' | tail -n 1 || true)"
    auth_record="$(printf '%s\n' "$recent_log" | grep -n 'Lingxi auth state:' | tail -n 1 || true)"
    online_record="$(printf '%s\n' "$recent_log" | grep -n 'Robot online(' | tail -n 1 || true)"
    offline_record="$(printf '%s\n' "$recent_log" | grep -n 'Robot offline(' | tail -n 1 || true)"
    [[ -n "$started_record" ]] && started_line="${started_record%%:*}"
    [[ -n "$auth_record" ]] && auth_line="${auth_record%%:*}"
    [[ -n "$online_record" ]] && online_line="${online_record%%:*}"
    [[ -n "$offline_record" ]] && offline_line="${offline_record%%:*}"
    if [[ "$running" == true && "$auth_line" -gt "$started_line" && "$auth_record" == *'code=0'* ]]; then
      lingxi_connected=true
    fi
    # 只有最后一次机器人回调为 online，才表示当前实体机器人仍与 Agent 绑定。
    if [[ "$running" == true &&
          "$online_line" -gt "$started_line" &&
          "$online_line" -gt "$offline_line" ]]; then
      robot_online=true
    fi
  fi
  printf 'configured=%s\n' "$([[ -f "${HOME_DIR}/config/lingxi-nav.properties" ]] && echo true || echo false)"
  printf 'running=%s\n' "$running"
  printf 'pid=%s\n' "$pid"
  printf 'home=%s\n' "$HOME_DIR"
  printf 'nav_base_url=%s\n' "${NAV_BASE_URL:-http://127.0.0.1:18083}"
  printf 'log_file=%s\n' "$LOG_FILE"
  printf 'lingxi_connected=%s\n' "$lingxi_connected"
  printf 'robot_online=%s\n' "$robot_online"
  printf 'last_auth_state=%s\n' "${auth_record#*:}"
  printf 'last_started_line=%s\n' "${started_record#*:}"
  printf '%s\n' '---LOG---'
  printf '%s\n' "$recent_log" | grep -v 'voice_embedding' | tail -n 80 || true
}

case "${1:-status}" in
  start)
    sudo -n systemctl start "$SERVICE"
    ;;
  restart)
    sudo -n systemctl restart "$SERVICE"
    ;;
  stop)
    sudo -n systemctl stop "$SERVICE"
    ;;
  status)
    status
    exit 0
    ;;
  *)
    echo "用法：$0 start|restart|stop|status" >&2
    exit 2
    ;;
esac

sleep 2
status
