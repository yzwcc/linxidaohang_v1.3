#!/usr/bin/env bash
set -euo pipefail

RELEASE_DIR="${1:-/home/run/lingxi_java_agent/releases/2026.07.21-v140}"
HOME_DIR="${LINGXI_JAVA_HOME:-/home/run/lingxi_java_agent}"
CONFIG_FILE="${HOME_DIR}/config/lingxi-nav.properties"
SERVICE_FILE="/etc/systemd/system/lingxi-java-agent-robot.service"
LOG_FILE="${HOME_DIR}/logs/java-agent-managed.log"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="${HOME_DIR}/backups/v140-${STAMP}"

require_file() {
  [[ -f "$1" ]] || { echo "缺少升级文件：$1" >&2; exit 1; }
}

require_file "${RELEASE_DIR}/robot-local-navigation-java-0.1.0-all.jar"
require_file "${RELEASE_DIR}/linksoul-agentsdk-1.4.0-SNAPSHOT.jar"
require_file "${RELEASE_DIR}/lingxi-java-agent-robot.service"
require_file "$CONFIG_FILE"

app_key="$(sed -n 's/^lingxi\.appKey=//p' "$CONFIG_FILE" | tail -n 1 | tr -d '\r' | xargs)"
if [[ -z "$app_key" || "$app_key" == YOUR_* ]]; then
  echo "尚未配置 v1.4.0 所需的 lingxi.appKey，已取消升级，当前服务未改动。" >&2
  exit 2
fi

mkdir -p "$BACKUP_DIR"
cp -a "${HOME_DIR}/target/robot-local-navigation-java-0.1.0-all.jar" "$BACKUP_DIR/"
[[ -f "${HOME_DIR}/linksoul-agentsdk-1.0.0-SNAPSHOT.jar" ]] && cp -a "${HOME_DIR}/linksoul-agentsdk-1.0.0-SNAPSHOT.jar" "$BACKUP_DIR/"
cp -a "$SERVICE_FILE" "$BACKUP_DIR/lingxi-java-agent-robot.service"

rollback() {
  echo "v1.4.0 启动失败，正在恢复升级前版本。" >&2
  cp -a "$BACKUP_DIR/robot-local-navigation-java-0.1.0-all.jar" "${HOME_DIR}/target/"
  cp -a "$BACKUP_DIR/lingxi-java-agent-robot.service" "$SERVICE_FILE"
  systemctl daemon-reload
  systemctl restart lingxi-java-agent-robot.service
}
trap rollback ERR

cp -a "${RELEASE_DIR}/robot-local-navigation-java-0.1.0-all.jar" "${HOME_DIR}/target/"
cp -a "${RELEASE_DIR}/linksoul-agentsdk-1.4.0-SNAPSHOT.jar" "${HOME_DIR}/"
cp -a "${RELEASE_DIR}/lingxi-java-agent-robot.service" "$SERVICE_FILE"
systemctl daemon-reload

start_line=0
[[ -f "$LOG_FILE" ]] && start_line="$(wc -l < "$LOG_FILE")"
systemctl restart lingxi-java-agent-robot.service

for _ in $(seq 1 30); do
  if ! systemctl is-active --quiet lingxi-java-agent-robot.service; then
    sleep 1
    continue
  fi
  if tail -n "+$((start_line + 1))" "$LOG_FILE" 2>/dev/null \
      | grep -Eq 'Lingxi auth state: (appId=[^,]+, )?code=0'; then
    trap - ERR
    echo "灵心 AgentSDK v1.4.0 已激活并通过鉴权。"
    exit 0
  fi
  sleep 1
done

echo "v1.4.0 服务已启动，但 30 秒内未通过鉴权。" >&2
false
