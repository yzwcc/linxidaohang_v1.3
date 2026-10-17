#!/usr/bin/env bash
set -euo pipefail
umask 077

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="${PACKAGE_DIR}/agent/robot-local-navigation-java-all-kb.jar"
TARGET=/opt/sparkrobot/lingxi-nav/current/java/robot-local-navigation-java-all.jar
BACKUP_DIR=/var/lib/sparkrobot/lingxi-kb/agent-backups
SERVICE=lingxi-java-agent-robot.service

usage() {
  cat <<'EOF'
用法：sudo ./install_voice_agent.sh
     sudo ./install_voice_agent.sh --rollback BACKUP_JAR

该脚本只更新灵犀 Java Agent，不修改灵犀配置、导航服务或直播项目。
EOF
}

[[ "${EUID}" -eq 0 ]] || { echo "请使用 sudo 运行" >&2; exit 1; }
[[ "$(uname -m)" == "aarch64" || "$(uname -m)" == "arm64" ]] || { echo "只能安装到 ARM64 机器人" >&2; exit 1; }

MODE=install
ROLLBACK=""
case "${1:-}" in
  "") ;;
  --rollback)
    MODE=rollback
    ROLLBACK="${2:-}"
    [[ -n "${ROLLBACK}" && -f "${ROLLBACK}" ]] || { echo "回滚 JAR 不存在：${ROLLBACK}" >&2; exit 1; }
    ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

[[ -f "${TARGET}" ]] || {
  echo "找不到现有灵犀 Java Agent：${TARGET}" >&2
  echo "请先完成灵犀导航/语音基础部署，再安装本知识库接入包。" >&2
  exit 1
}
if [[ "${MODE}" == install ]]; then
  [[ -f "${SOURCE}" ]] || { echo "安装包缺少语音接入 JAR：${SOURCE}" >&2; exit 1; }
  INPUT="${SOURCE}"
else
  INPUT="${ROLLBACK}"
fi

install -d -m 0750 -o run -g run "${BACKUP_DIR}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="${BACKUP_DIR}/robot-local-navigation-java-all.${STAMP}.jar"
install -o run -g run -m 0440 "${TARGET}" "${BACKUP}"
TMP="${TARGET}.installing.$$"
trap 'rm -f -- "${TMP}"' EXIT
install -o run -g run -m 0440 "${INPUT}" "${TMP}"
mv -f -- "${TMP}" "${TARGET}"
trap - EXIT

systemctl restart "${SERVICE}"
sleep 3
systemctl is-active --quiet "${SERVICE}" || {
  echo "语音 Agent 重启失败，原 JAR 备份在：${BACKUP}" >&2
  exit 1
}

if [[ "${MODE}" == install ]]; then
  echo "知识库语音接入已安装，旧 JAR 备份：${BACKUP}"
else
  echo "知识库语音接入已回滚，当前 JAR 的安装前备份：${BACKUP}"
fi
echo "语音 Agent 状态：$(systemctl is-active "${SERVICE}")"
