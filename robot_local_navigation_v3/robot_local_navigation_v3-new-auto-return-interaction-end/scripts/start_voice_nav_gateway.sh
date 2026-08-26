#!/usr/bin/env bash
set -euo pipefail

APP_HOME="${ROBOT_LOCAL_NAVIGATION_V3_HOME:-/home/agi/robot_local_navigation_v3}"
PID_FILE="${APP_HOME}/pids/voice_nav_gateway.pid"
LOG_FILE="${APP_HOME}/logs/voice_nav_gateway.log"
ACTION="${1:-start}"

mkdir -p "${APP_HOME}/pids" "${APP_HOME}/logs"

stop_gateway() {
  if [[ -f "$PID_FILE" ]]; then
    pid="$(<"$PID_FILE")"
    kill "$pid" >/dev/null 2>&1 || true
    rm -f "$PID_FILE"
  fi
}

case "$ACTION" in
  start)
    stop_gateway
    cd "$APP_HOME"
    export PYTHONPATH="${APP_HOME}:${PYTHONPATH:-}"
    nohup python3 -m robot_local_navigation_v3.voice_nav_gateway \
      --host 127.0.0.1 --port 18083 --voice-port 18080 --nav-port 18082 \
      >"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
    sleep 0.5
    if ! kill -0 "$(<"$PID_FILE")" >/dev/null 2>&1; then
      echo "语音导航兼容网关启动失败，请查看日志：${LOG_FILE}" >&2
      tail -n 20 "$LOG_FILE" >&2 || true
      rm -f "$PID_FILE"
      exit 1
    fi
    echo "语音导航兼容网关已启动 (pid=$(<"$PID_FILE"), port=18083)"
    ;;
  stop)
    stop_gateway
    echo "语音导航兼容网关已停止"
    ;;
  status)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(<"$PID_FILE")" >/dev/null 2>&1; then
      echo "voice_nav_gateway: running (pid=$(<"$PID_FILE"))"
    else
      echo "voice_nav_gateway: stopped"
      exit 1
    fi
    ;;
  *)
    echo "用法: $(basename "$0") start|stop|status" >&2
    exit 2
    ;;
esac
