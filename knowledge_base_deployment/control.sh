#!/usr/bin/env bash
set -euo pipefail
SERVICE=sparkrobot-lingxi-kb.service
CONFIG=/etc/sparkrobot/lingxi-kb/retriever.env
INSTALL_BASE=/opt/sparkrobot/lingxi-kb
case "${1:-status}" in
  start|stop|restart) systemctl "$1" "${SERVICE}" ;;
  status) systemctl status "${SERVICE}" --no-pager ;;
  doctor)
    systemctl is-active --quiet "${SERVICE}" || { echo "服务未运行"; exit 1; }
    curl -fsS http://127.0.0.1:18084/health
    test -s "${CONFIG}"
    stat -c '%U:%G %a %n' "${CONFIG}"
    ;;
  logs) journalctl -u "${SERVICE}" -n 150 --no-pager ;;
  uninstall)
    systemctl disable --now "${SERVICE}" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SERVICE}" /usr/local/sbin/sparkrobot-lingxi-kb /usr/local/bin/kb-search
    systemctl daemon-reload
    rm -rf -- "${INSTALL_BASE}"
    echo "知识库程序已卸载；配置 ${CONFIG}、状态和日志已保留"
    ;;
  *) echo "用法：$0 start|stop|restart|status|doctor|logs|uninstall" >&2; exit 2 ;;
esac
