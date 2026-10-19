#!/usr/bin/env bash
set -euo pipefail
umask 077

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS_FILE="$(mktemp "${TMPDIR:-/tmp}/sparkrobot-lingxi-secrets.XXXXXX")"
trap 'rm -f -- "${SECRETS_FILE}"' EXIT

echo "开始灵犀导航内部版安装向导"
"${PACKAGE_DIR}/configure.sh" "${SECRETS_FILE}"
sudo "${PACKAGE_DIR}/install.sh" --secrets "${SECRETS_FILE}" --setup-soc2
sudo sparkrobot-lingxi-internal start
sudo sparkrobot-lingxi-internal doctor
echo "部署完成，请用同一局域网手机访问：http://机器人IP:3000"
