#!/usr/bin/env bash
set -euo pipefail

SCRIPT="${SCRIPT:-/home/agi/daohang_reverse_tunnel.sh}"
LOG="${LOG:-/home/agi/daohang_reverse_tunnel.log}"

if pgrep -u "$(id -u)" -f "ssh -N -T .*47\\.82\\.118\\.118" >/dev/null 2>&1; then
  exit 0
fi

if pgrep -u "$(id -u)" -f "$SCRIPT" >/dev/null 2>&1; then
  exit 0
fi

mkdir -p "$(dirname "$LOG")"
date "+%F %T watchdog starting reverse tunnel" >> "$LOG"
nohup "$SCRIPT" >/dev/null 2>&1 &
