#!/usr/bin/env bash
set -Eeuo pipefail

CURRENT="/opt/sparkrobot/lingxi-nav/current"
NAV_CONTROL="${CURRENT}/scripts/navigation_control.sh"
FIX_CONTROL="/usr/local/sbin/sparkrobot-lingxi-consecutive-nav-fix"
LOG_ROOT="/var/log/sparkrobot/lingxi-nav"

require_root() {
  [[ "${EUID}" -eq 0 ]] || { echo "请使用 sudo 运行此命令" >&2; exit 1; }
}

status() {
  echo "=== 系统服务 ==="
  for service in lingxi-nav-console.service lingxi-java-agent-robot.service sparkrobot-lingxi-kb.service; do
    printf '%s=' "${service}"
    systemctl is-active "${service}" 2>/dev/null || true
  done
  echo "=== 导航进程 ==="
  if [[ -x "${NAV_CONTROL}" ]]; then
    runuser -u agi -- "${NAV_CONTROL}" status 2>/dev/null || true
  else
    echo "导航控制脚本不存在：${NAV_CONTROL}"
  fi
  echo "=== 端口 ==="
  ss -ltn 2>/dev/null | grep -E ':(3000|8002|18080|18082|18083|18084) ' || true
  echo "=== 运行目录权限 ==="
  stat -c '%A %a %U:%G %n' "${LOG_ROOT}" "${LOG_ROOT}/ros" \
    "/var/lib/sparkrobot/lingxi-nav/pids" 2>/dev/null || true
}

doctor() {
  require_root
  local failed=0
  systemctl is-active --quiet lingxi-nav-console.service || { echo "网页服务未运行" >&2; failed=1; }
  curl -fsS --max-time 5 http://127.0.0.1:3000/health >/dev/null || { echo "网页健康检查失败" >&2; failed=1; }
  curl -fsS --max-time 5 http://127.0.0.1:18084/health >/dev/null || { echo "知识库服务健康检查失败" >&2; failed=1; }
  [[ -x "${FIX_CONTROL}" ]] \
    && "${FIX_CONTROL}" doctor || { echo "连续导航修复检查失败或命令不存在" >&2; failed=1; }
  command -v sparkrobot-lingxi-linkcraft >/dev/null 2>&1 \
    && sparkrobot-lingxi-linkcraft status || { echo "灵创检查失败或命令不存在" >&2; failed=1; }
  [[ "${failed}" -eq 0 ]] || exit 1
  echo "完整套件健康检查通过；本命令不会发送导航、动作、重定位或语音指令"
}

repair_permissions() {
  require_root
  [[ -x "${FIX_CONTROL}" ]] || { echo "连续导航修复运维命令不存在，请重新安装完整包" >&2; exit 1; }
  "${FIX_CONTROL}" repair-permissions
}

restart_navigation() {
  require_root
  repair_permissions
  [[ -x "${NAV_CONTROL}" ]] || { echo "导航控制脚本不存在：${NAV_CONTROL}" >&2; exit 1; }
  runuser -u agi -- "${NAV_CONTROL}" restart
}

logs() {
  local mode="${1:-all}"
  case "${mode}" in
    all)
      journalctl -u lingxi-nav-console.service -u lingxi-java-agent-robot.service -n 120 --no-pager || true
      for file in lingxi_nav_bridge.log map_label_server_v3.log voice_nav_gateway.log robot_lingxi_bridge.log java-agent.log; do
        echo "===== ${file} ====="
        tail -n 120 "${LOG_ROOT}/${file}" 2>/dev/null || true
      done
      ;;
    navigation) tail -n 500 "${LOG_ROOT}/lingxi_nav_bridge.log" ;;
    voice) tail -n 500 "${LOG_ROOT}/robot_lingxi_bridge.log" "${LOG_ROOT}/java-agent.log" ;;
    console) journalctl -u lingxi-nav-console.service -n 300 --no-pager ;;
    *) echo "用法：sudo sparkrobot-lingxi-complete logs all|navigation|voice|console" >&2; exit 2 ;;
  esac
}

case "${1:-status}" in
  status) status ;;
  doctor) doctor ;;
  repair-permissions) repair_permissions ;;
  restart-navigation) restart_navigation ;;
  logs) logs "${2:-all}" ;;
  *) echo "用法：sudo sparkrobot-lingxi-complete status|doctor|repair-permissions|restart-navigation|logs [all|navigation|voice|console]" >&2; exit 2 ;;
esac
