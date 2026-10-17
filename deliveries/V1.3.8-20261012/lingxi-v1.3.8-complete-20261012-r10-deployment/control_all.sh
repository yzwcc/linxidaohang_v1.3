#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="1.3.8-complete-20261012-r10"
NAV_VERSION="1.0.19-20260921"
CURRENT="/opt/sparkrobot/lingxi-nav/current"
RUNTIME_GROUP="${SPARKROBOT_RUNTIME_GROUP:-sparkrobot-nav}"
STATE_ROOT="/var/lib/sparkrobot/lingxi-nav"
LOG_ROOT="/var/log/sparkrobot/lingxi-nav"

require_root() {
  [[ "${EUID}" -eq 0 ]] || { echo "请使用 sudo 运行此命令" >&2; exit 1; }
}

repair_permissions() {
  require_root
  getent group "${RUNTIME_GROUP}" >/dev/null || groupadd --system "${RUNTIME_GROUP}"
  for user in agi run; do
    id "${user}" >/dev/null 2>&1 || { echo "缺少机器人账号：${user}" >&2; exit 1; }
    usermod -a -G "${RUNTIME_GROUP}" "${user}"
  done
  python3 "${CURRENT}/scripts/repair_java_access.py"
  runuser -u run -- python3 "${CURRENT}/scripts/check_java_access.py"
  install -d -m 0770 -o agi -g "${RUNTIME_GROUP}" "${STATE_ROOT}" "${STATE_ROOT}/maps"
  install -d -m 0770 -o run -g "${RUNTIME_GROUP}" \
    "${STATE_ROOT}/pids" "${STATE_ROOT}/ssh" "${LOG_ROOT}" "${LOG_ROOT}/ros"
  find "${STATE_ROOT}/maps" -type d -exec chown agi:"${RUNTIME_GROUP}" {} + -exec chmod 0770 {} +
  find "${STATE_ROOT}/maps" -type f -exec chown agi:"${RUNTIME_GROUP}" {} + -exec chmod 0660 {} +
  find "${STATE_ROOT}/pids" "${STATE_ROOT}/ssh" "${LOG_ROOT}" -type d \
    -exec chown run:"${RUNTIME_GROUP}" {} + -exec chmod 0770 {} +
  find "${STATE_ROOT}/pids" "${STATE_ROOT}/ssh" "${LOG_ROOT}" -type f \
    -exec chown run:"${RUNTIME_GROUP}" {} + -exec chmod 0660 {} +
  touch "${LOG_ROOT}/java-agent.log"
  chown run:"${RUNTIME_GROUP}" "${LOG_ROOT}/java-agent.log"
  chmod 0660 "${LOG_ROOT}/java-agent.log"
  install -d -m 0700 -o run -g run /home/run/.ssh
  touch /home/run/.ssh/known_hosts
  chown run:run /home/run/.ssh/known_hosts
  chmod 0600 /home/run/.ssh/known_hosts
  touch "${STATE_ROOT}/ssh/soc2-known-hosts"
  chown run:"${RUNTIME_GROUP}" "${STATE_ROOT}/ssh/soc2-known-hosts"
  chmod 0660 "${STATE_ROOT}/ssh/soc2-known-hosts"
  echo "共享运行目录权限已修复"
}

verify() {
  require_root
  [[ "$(readlink -f "${CURRENT}" 2>/dev/null || true)" == "/opt/sparkrobot/lingxi-nav/releases/${NAV_VERSION}" ]] \
    || { echo "当前版本不是 ${NAV_VERSION}" >&2; exit 1; }
  sparkrobot-lingxi-internal verify
  runuser -u run -- python3 "${CURRENT}/scripts/check_java_access.py"
  python3 - <<'PY'
import zipfile
jar = "/opt/sparkrobot/lingxi-nav/current/java/robot-local-navigation-java-all.jar"
with zipfile.ZipFile(jar) as archive:
    assert "com/agibot/aiem/nav/RetrieverClient.class" in archive.namelist()
print("V1.3.8 Java Agent 与知识库接入检查通过")
PY
  echo "完整产品版本：${VERSION}"
}

status() {
  echo "完整产品版本=${VERSION}"
  echo "导航主包版本=$(basename "$(readlink -f "${CURRENT}" 2>/dev/null || echo 未安装)")"
  echo "=== 系统服务 ==="
  for service in lingxi-nav-console.service lingxi-java-agent-robot.service sparkrobot-lingxi-kb.service; do
    printf '%s=' "${service}"
    systemctl is-active "${service}" 2>/dev/null || true
  done
  echo "=== 导航服务 ==="
  if [[ -x "${CURRENT}/scripts/navigation_control.sh" ]]; then
    runuser -u agi -- "${CURRENT}/scripts/navigation_control.sh" status 2>/dev/null || true
  else
    echo "导航控制脚本不存在"
  fi
  echo "=== 端口 ==="
  ss -ltn 2>/dev/null | grep -E ':(3000|8002|18080|18082|18083|18084) ' || true
}

start_all() {
  require_root
  repair_permissions
  systemctl enable --now sparkrobot-lingxi-kb.service >/dev/null
  sparkrobot-lingxi-internal start
  echo "全部服务已启动；本命令不会自动开始导航、重定位、讲解或动作。"
}

stop_all() {
  require_root
  sparkrobot-lingxi-internal stop
  systemctl stop sparkrobot-lingxi-kb.service 2>/dev/null || true
  echo "全部灵犀业务服务已停止"
}

doctor() {
  require_root
  local failed=0
  verify || failed=1
  sparkrobot-lingxi-internal doctor || failed=1
  sparkrobot-lingxi-kb doctor || failed=1
  curl -fsS --max-time 5 http://127.0.0.1:18082/task/commands >/dev/null \
    || { echo "讲解任务命令接口不可用" >&2; failed=1; }
  curl -fsS --max-time 5 http://127.0.0.1:18082/linkcraft/resources >/dev/null \
    || { echo "灵创资源接口不可用" >&2; failed=1; }
  [[ "${failed}" -eq 0 ]] || exit 1
  echo "完整套件健康检查通过；未发送导航、重定位、动作、舞蹈或语音指令。"
}

logs() {
  local mode="${1:-all}"
  case "${mode}" in
    all)
      journalctl -u lingxi-nav-console.service -u lingxi-java-agent-robot.service \
        -u sparkrobot-lingxi-kb.service -n 150 --no-pager || true
      for file in lingxi_nav_bridge.log map_label_server_v3.log voice_nav_gateway.log robot_lingxi_bridge.log java-agent.log; do
        echo "===== ${file} ====="
        tail -n 150 "${LOG_ROOT}/${file}" 2>/dev/null || true
      done
      ;;
    navigation) tail -n 600 "${LOG_ROOT}/lingxi_nav_bridge.log" ;;
    voice) tail -n 600 "${LOG_ROOT}/robot_lingxi_bridge.log" "${LOG_ROOT}/java-agent.log" ;;
    console) journalctl -u lingxi-nav-console.service -n 400 --no-pager ;;
    knowledge) journalctl -u sparkrobot-lingxi-kb.service -n 400 --no-pager ;;
    *) echo "用法：sudo sparkrobot-lingxi-complete logs all|navigation|voice|console|knowledge" >&2; exit 2 ;;
  esac
}

case "${1:-status}" in
  status) status ;;
  verify) verify ;;
  start) start_all ;;
  stop) stop_all ;;
  restart) stop_all; start_all ;;
  doctor) doctor ;;
  repair-permissions) repair_permissions ;;
  logs) logs "${2:-all}" ;;
  rollback)
    require_root
    sparkrobot-lingxi-internal rollback "${2:-}"
    ;;
  *)
    echo "用法：sudo sparkrobot-lingxi-complete status|verify|start|stop|restart|doctor|repair-permissions|logs [类型]|rollback [版本]" >&2
    exit 2
    ;;
esac
