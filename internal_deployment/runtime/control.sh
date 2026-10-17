#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${SPARKROBOT_INSTALL_ROOT:-/}"
TEST_MODE="${SPARKROBOT_INSTALL_TEST_MODE:-false}"
INSTALL_ROOT="${INSTALL_ROOT%/}"; [[ -n "${INSTALL_ROOT}" ]] || INSTALL_ROOT="/"
root_path() { [[ "${INSTALL_ROOT}" == "/" ]] && printf '%s\n' "$1" || printf '%s%s\n' "${INSTALL_ROOT}" "$1"; }
systemctl_do() { [[ "${TEST_MODE}" == true ]] || systemctl "$@"; }

PRODUCT_BASE="$(root_path /opt/sparkrobot/lingxi-nav)"
CURRENT="${PRODUCT_BASE}/current"
STATE_DIR="$(root_path /var/lib/sparkrobot/lingxi-nav)"

require_root() { [[ "${EUID}" -eq 0 ]] || { echo "请使用 sudo 运行" >&2; exit 1; }; }
current_version() { [[ -L "${CURRENT}" ]] && basename "$(readlink -f "${CURRENT}")" || echo none; }

verify() {
  [[ -d "${CURRENT}" ]] || { echo "当前版本不存在" >&2; exit 1; }
  python3 "${CURRENT}/scripts/verify_payload.py" --root "${CURRENT}" --manifest "${CURRENT}/payload-manifest.json"
}

status() {
  echo "version=$(current_version)"
  if [[ "${TEST_MODE}" == true ]]; then
    echo "console=not-started"; echo "java_agent=not-started"; return
  fi
  echo "console=$(systemctl is-active lingxi-nav-console.service 2>/dev/null || true)"
  echo "java_agent=$(systemctl is-active lingxi-java-agent-robot.service 2>/dev/null || true)"
  runuser -u agi -- "${CURRENT}/scripts/navigation_control.sh" status || true
  "${CURRENT}/scripts/robot_voice_bridge_control.sh" status || true
  curl -fsS --max-time 3 http://127.0.0.1:3000/api/robots/default/status 2>/dev/null || true
  echo
}

doctor() {
  local failed=0
  echo "version=$(current_version)"
  [[ -L "${CURRENT}" && -d "${CURRENT}" ]] || { echo "[失败] 当前版本链接无效"; failed=1; }
  [[ -f "${CURRENT}/console/dist/public/index.html" ]] || { echo "[失败] 网页前端文件缺失，请重新安装当前归档"; failed=1; }
  [[ -r "$(root_path /etc/sparkrobot/lingxi-nav/console.env)" ]] || { echo "[失败] 网页配置缺失"; failed=1; }
  [[ -r "$(root_path /etc/sparkrobot/lingxi-nav/lingxi-nav.properties)" ]] || { echo "[失败] 灵心配置缺失"; failed=1; }
  [[ -r "$(root_path /etc/sparkrobot/lingxi-nav/runtime.env)" ]] || { echo "[失败] SOC2 地址配置缺失"; failed=1; }
  [[ -r "$(root_path /etc/sparkrobot/lingxi-nav/soc2-ed25519)" ]] || { echo "[提示] SOC2 运维密钥尚未配置"; failed=1; }
  [[ "${TEST_MODE}" == true ]] && return "${failed}"
  systemctl is-active --quiet lingxi-nav-console.service || { echo "[失败] 网页控制台未运行"; failed=1; }
  curl -fsS --max-time 3 http://127.0.0.1:3000/health >/dev/null || { echo "[失败] 网页健康检查失败"; failed=1; }
  ss -ltn | grep -q ':3000 ' || { echo "[失败] 3000 端口未监听"; failed=1; }
  for port in 8002 18080 18082 18083; do
    ss -ltn | grep -q ":${port} " || { echo "[失败] ${port} 端口未监听"; failed=1; }
  done
  systemctl is-active --quiet lingxi-java-agent-robot.service || { echo "[失败] Java 语音 Agent 未运行"; failed=1; }
  if [[ "${failed}" -eq 0 ]]; then
    echo "[通过] 网页、导航、地图、语音网关和 Java Agent 均已就绪"
  fi
  return "${failed}"
}

start_all() {
  require_root
  verify
  systemctl_do restart lingxi-nav-console.service
  [[ "${TEST_MODE}" == true ]] && return
  runuser -u agi -- "${CURRENT}/scripts/navigation_control.sh" restart
  systemctl restart lingxi-java-agent-robot.service
  if [[ -r /etc/sparkrobot/lingxi-nav/runtime.env ]]; then set -a; source /etc/sparkrobot/lingxi-nav/runtime.env; set +a; fi
  "${CURRENT}/scripts/restart_robot_voice_stack.sh"
  echo "全部服务已启动"
}

stop_all() {
  require_root
  systemctl_do stop lingxi-java-agent-robot.service 2>/dev/null || true
  if [[ "${TEST_MODE}" != true && -d "${CURRENT}" ]]; then
    runuser -u agi -- "${CURRENT}/scripts/navigation_control.sh" stop 2>/dev/null || true
    "${CURRENT}/scripts/robot_voice_bridge_control.sh" stop 2>/dev/null || true
  fi
  systemctl_do stop lingxi-nav-console.service 2>/dev/null || true
  echo "全部业务服务已停止"
}

rollback() {
  require_root
  version="${1:-}"
  [[ -n "${version}" ]] || { [[ -f "${STATE_DIR}/previous-version" ]] && version="$(<"${STATE_DIR}/previous-version")"; }
  [[ -n "${version}" && -d "${PRODUCT_BASE}/releases/${version}" ]] || { echo "没有可回滚版本" >&2; exit 1; }
  stop_all
  rm -f "${CURRENT}.new"; ln -s "${PRODUCT_BASE}/releases/${version}" "${CURRENT}.new"; mv -Tf "${CURRENT}.new" "${CURRENT}"
  systemctl_do daemon-reload
  systemctl_do restart lingxi-nav-console.service
  echo "已回滚到 ${version}"
}

logs() {
  case "${1:-all}" in
    console) journalctl -u lingxi-nav-console.service -n 200 --no-pager ;;
    voice) tail -n 200 "$(root_path /var/log/sparkrobot/lingxi-nav/java-agent.log)" ;;
    navigation) tail -n 200 "$(root_path /var/log/sparkrobot/lingxi-nav/lingxi_nav_bridge.log)" ;;
    all)
      journalctl -u lingxi-nav-console.service -n 80 --no-pager || true
      tail -n 80 "$(root_path /var/log/sparkrobot/lingxi-nav/java-agent.log)" 2>/dev/null || true
      tail -n 80 "$(root_path /var/log/sparkrobot/lingxi-nav/lingxi_nav_bridge.log)" 2>/dev/null || true
      ;;
    *) echo "用法：logs console|voice|navigation|all" >&2; exit 2 ;;
  esac
}

uninstall_runtime() {
  require_root
  [[ "${1:-}" == sparkrobot-lingxi-nav-internal ]] || { echo "请显式传入 sparkrobot-lingxi-nav-internal" >&2; exit 2; }
  stop_all
  systemctl_do disable lingxi-nav-console.service lingxi-java-agent-robot.service >/dev/null 2>&1 || true
  rm -f "$(root_path /etc/systemd/system/lingxi-nav-console.service)" "$(root_path /etc/systemd/system/lingxi-java-agent-robot.service)"
  rm -f "${CURRENT}"
  systemctl_do daemon-reload
  echo "服务已卸载，版本、配置、地图、点位、任务和日志已保留"
}

case "${1:-status}" in
  start) start_all ;;
  stop) stop_all ;;
  restart) stop_all; start_all ;;
  status) status ;;
  doctor) doctor ;;
  verify) verify ;;
  rollback) rollback "${2:-}" ;;
  logs) logs "${2:-all}" ;;
  uninstall) uninstall_runtime "${2:-}" ;;
  *) echo "用法：$0 start|stop|restart|status|doctor|verify|rollback [VERSION]|logs [TYPE]|uninstall sparkrobot-lingxi-nav-internal" >&2; exit 2 ;;
esac
