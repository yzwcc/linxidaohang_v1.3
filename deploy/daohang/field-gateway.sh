#!/usr/bin/env bash
set -euo pipefail

SERVER_BASE_URL="${SERVER_BASE_URL:-https://daohang.sparkrobot.top}"
CLOUD_SSH_HOST="${CLOUD_SSH_HOST:-47.82.118.118}"
CLOUD_SSH_USER="${CLOUD_SSH_USER:-robot-tunnel}"
SSH_KEY="${SSH_KEY:-/home/agi/.ssh/daohang_server_ed25519}"
LOG="${LOG:-/home/agi/daohang_reverse_tunnel.log}"
TARGET_BRIDGE_PORT="${TARGET_BRIDGE_PORT:-18080}"
ALLOW_REMOTE_TARGET="${ALLOW_REMOTE_TARGET:-false}"

current_target=""
ssh_pid=""
last_skipped_target=""

read_target() {
  python3 - "$SERVER_BASE_URL" <<'PY'
import json
import sys
import urllib.request

base = sys.argv[1].rstrip("/")
with urllib.request.urlopen(base + "/api/robots/default/config", timeout=8) as resp:
    payload = json.load(resp)
print((payload.get("robot_host") or "").strip())
PY
}

target_belongs_to_this_robot() {
  local target="$1"
  if [ "$ALLOW_REMOTE_TARGET" = "true" ]; then
    return 0
  fi
  if [ "$target" = "$(hostname)" ]; then
    return 0
  fi
  local address
  for address in $(hostname -I 2>/dev/null); do
    if [ "$target" = "${address%%/*}" ]; then
      return 0
    fi
  done
  return 1
}

stop_tunnel() {
  if [ -n "${ssh_pid:-}" ] && kill -0 "$ssh_pid" >/dev/null 2>&1; then
    kill "$ssh_pid" >/dev/null 2>&1 || true
    wait "$ssh_pid" >/dev/null 2>&1 || true
  fi
  ssh_pid=""
}

start_tunnel() {
  local selected_target="$1"
  local forward_target="$selected_target"
  if [ "$ALLOW_REMOTE_TARGET" != "true" ]; then
    forward_target="127.0.0.1"
  fi
  date "+%F %T connecting cloud tunnel for website target ${selected_target} via ${forward_target}" >> "$LOG"
  /usr/bin/ssh -N -T \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=5 \
    -o ServerAliveCountMax=2 \
    -o TCPKeepAlive=yes \
    -o ConnectTimeout=10 \
    -o ConnectionAttempts=1 \
    -o StrictHostKeyChecking=accept-new \
    -i "$SSH_KEY" \
    -R 127.0.0.1:18080:"${forward_target}":"${TARGET_BRIDGE_PORT}" \
    -R 127.0.0.1:18002:"${forward_target}":8002 \
    -R 127.0.0.1:10022:"${forward_target}":22 \
    "${CLOUD_SSH_USER}@${CLOUD_SSH_HOST}" >> "$LOG" 2>&1 &
  ssh_pid=$!
}

while true; do
  if ! target="$(read_target 2>>"$LOG")"; then
    date "+%F %T failed to read website target; keeping ${current_target:-none}" >> "$LOG"
    sleep 10
    continue
  fi
  if [ -z "$target" ]; then
    date "+%F %T website target is empty; keeping ${current_target:-none}" >> "$LOG"
    sleep 10
    continue
  fi
  if ! target_belongs_to_this_robot "$target"; then
    stop_tunnel
    current_target=""
    if [ "$target" != "$last_skipped_target" ]; then
      date "+%F %T website selected ${target}; this robot does not own that address and will not claim the cloud ports" >> "$LOG"
      last_skipped_target="$target"
    fi
    sleep 10
    continue
  fi
  last_skipped_target=""
  if [ "$target" != "$current_target" ] || [ -z "${ssh_pid:-}" ] || ! kill -0 "$ssh_pid" >/dev/null 2>&1; then
    stop_tunnel
    current_target="$target"
    start_tunnel "$current_target"
  fi
  sleep 10
done
