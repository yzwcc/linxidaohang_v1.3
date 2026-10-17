#!/usr/bin/env bash
set -euo pipefail
umask 077

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_BASE=/opt/sparkrobot/lingxi-kb
CONFIG_DIR=/etc/sparkrobot/lingxi-kb
STATE_DIR=/var/lib/sparkrobot/lingxi-kb
LOG_DIR=/var/log/sparkrobot/lingxi-kb
SERVICE=sparkrobot-lingxi-kb.service
CONFIG_FILE=""
FROM_LINGXI=false
AGENT_CODE=""

usage() {
  cat <<'EOF'
用法：sudo ./install.sh [选项]
  --from-lingxi          只读复用现有 /etc/sparkrobot/lingxi-nav/lingxi-nav.properties
  --agent-code CODE      指定 Retriever Proxy 使用的智能体 Code
  --config FILE          使用独立配置文件（包含 RETRIEVER_* 字段）
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from-lingxi) FROM_LINGXI=true; shift ;;
    --agent-code) AGENT_CODE="${2:-}"; shift 2 ;;
    --config) CONFIG_FILE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "${EUID}" -eq 0 ]] || { echo "请使用 sudo 运行安装器" >&2; exit 1; }
[[ "$(uname -m)" == "aarch64" || "$(uname -m)" == "arm64" ]] || { echo "只能安装到 ARM64 机器人" >&2; exit 1; }
[[ "${FROM_LINGXI}" != true || -z "${CONFIG_FILE}" ]] || { echo "--from-lingxi 与 --config 二选一" >&2; exit 2; }
[[ "${FROM_LINGXI}" == true || -n "${CONFIG_FILE}" ]] || { echo "首次安装必须指定 --from-lingxi 或 --config" >&2; exit 2; }
[[ -f "${PACKAGE_DIR}/service/retriever_service.py" && -f "${PACKAGE_DIR}/systemd/${SERVICE}" ]] || { echo "安装包不完整" >&2; exit 1; }

install -d -m 0755 /opt/sparkrobot /etc/sparkrobot /var/lib/sparkrobot /var/log/sparkrobot
install -d -m 0750 "${INSTALL_BASE}" "${INSTALL_BASE}/releases" "${CONFIG_DIR}"
install -d -m 0770 -o run -g run "${STATE_DIR}" "${LOG_DIR}"

VERSION="$(awk -F= '/^VERSION=/{print $2}' "${PACKAGE_DIR}/release-info.txt")"
TARGET="${INSTALL_BASE}/releases/${VERSION}"
rm -rf -- "${TARGET}.installing"
install -d -m 0750 "${TARGET}.installing"
cp -a "${PACKAGE_DIR}/service" "${PACKAGE_DIR}/systemd" "${TARGET}.installing/"
cp -a "${PACKAGE_DIR}/release-info.txt" "${TARGET}.installing/"
chmod 0750 "${TARGET}.installing/service/kb-search" "${TARGET}.installing/service/retriever_service.py"
rm -rf -- "${TARGET}"
mv "${TARGET}.installing" "${TARGET}"
ln -sfn "${TARGET}" "${INSTALL_BASE}/current"

if [[ "${FROM_LINGXI}" == true ]]; then
  SOURCE=/etc/sparkrobot/lingxi-nav/lingxi-nav.properties
  [[ -f "${SOURCE}" ]] || { echo "找不到现有灵犀配置：${SOURCE}" >&2; exit 1; }
  python3 - "${SOURCE}" "${CONFIG_DIR}/retriever.env" "${AGENT_CODE}" <<'PY'
import os, re, sys
from pathlib import Path
source, output, agent = map(Path, sys.argv[1:])
values = {}
for raw in source.read_text(encoding="utf-8", errors="replace").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip()
code = str(agent)
if not code:
    log = Path("/var/log/sparkrobot/lingxi-nav/java-agent.log")
    if log.is_file():
        matches = re.findall(r"AI_AGENT_[A-Za-z0-9_-]+", log.read_text(encoding="utf-8", errors="replace"))
        code = matches[-1] if matches else ""
required = {
    "RETRIEVER_APP_ID": values.get("lingxi.appId", ""),
    "RETRIEVER_APP_KEY": values.get("lingxi.appKey", ""),
    "RETRIEVER_APP_SECRET": values.get("lingxi.appSecret", ""),
    "RETRIEVER_AGENT_CODE": code,
}
missing = [key for key, value in required.items() if not value or value.startswith("YOUR_")]
if missing:
    raise SystemExit("无法从现有灵犀配置得到：" + ", ".join(missing) + "；请使用 --agent-code 或 --config")
output_path = Path(output)
output_path.write_text("".join(f"{key}={value}\n" for key, value in required.items()) + "RETRIEVER_BASE_URL=https://open.agibot.com\nRETRIEVER_TIMEOUT_SEC=20\n", encoding="utf-8")
os.chmod(output_path, 0o600)
PY
else
  [[ -f "${CONFIG_FILE}" ]] || { echo "配置文件不存在：${CONFIG_FILE}" >&2; exit 1; }
  install -m 0600 "${CONFIG_FILE}" "${CONFIG_DIR}/retriever.env"
fi

chown -R run:run "${INSTALL_BASE}" "${CONFIG_DIR}" "${STATE_DIR}" "${LOG_DIR}"
install -m 0644 "${PACKAGE_DIR}/systemd/${SERVICE}" "/etc/systemd/system/${SERVICE}"
install -m 0755 "${TARGET}/service/kb-search" /usr/local/bin/kb-search
install -m 0755 "${PACKAGE_DIR}/control.sh" /usr/local/sbin/sparkrobot-lingxi-kb
systemctl daemon-reload
systemctl enable --now "${SERVICE}"
sleep 1
systemctl is-active --quiet "${SERVICE}" || { systemctl status "${SERVICE}" --no-pager; exit 1; }
curl -fsS http://127.0.0.1:18084/health
echo "知识库检索服务安装成功，监听 127.0.0.1:18084"
