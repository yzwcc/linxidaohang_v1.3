#!/usr/bin/env bash
set -euo pipefail
umask 077

OUTPUT="${1:-./secrets.env}"
[[ -t 0 ]] || { echo "配置向导需要在交互式终端运行" >&2; exit 1; }

read -r -p "灵心 APP ID: " LINGXI_APP_ID
read -r -p "灵心 AppKey: " LINGXI_APP_KEY
read -r -s -p "灵心 AppSecret: " LINGXI_APP_SECRET; echo
read -r -s -p "豆包 Ark API Key: " DOUBAO_API_KEY; echo
read -r -p "豆包模型 [doubao-seed-2-0-mini-260428]: " DOUBAO_MODEL
read -r -p "机器人显示名 [灵犀]: " ROBOT_DISPLAY_NAME
read -r -p "SOC2 地址 [10.0.1.42]: " SOC2_HOST

DOUBAO_MODEL="${DOUBAO_MODEL:-doubao-seed-2-0-mini-260428}"
ROBOT_DISPLAY_NAME="${ROBOT_DISPLAY_NAME:-灵犀}"
SOC2_HOST="${SOC2_HOST:-10.0.1.42}"
for name in LINGXI_APP_ID LINGXI_APP_KEY LINGXI_APP_SECRET DOUBAO_API_KEY; do
  [[ -n "${!name}" ]] || { echo "${name} 不能为空" >&2; exit 1; }
  [[ "${!name}" != *$'\n'* && "${!name}" != *$'\r'* ]] || { echo "${name} 包含非法换行" >&2; exit 1; }
done

cat >"${OUTPUT}" <<EOF
LINGXI_APP_ID=${LINGXI_APP_ID}
LINGXI_APP_KEY=${LINGXI_APP_KEY}
LINGXI_APP_SECRET=${LINGXI_APP_SECRET}
DOUBAO_API_KEY=${DOUBAO_API_KEY}
DOUBAO_MODEL=${DOUBAO_MODEL}
ROBOT_DISPLAY_NAME=${ROBOT_DISPLAY_NAME}
SOC2_HOST=${SOC2_HOST}
EOF
chmod 0600 "${OUTPUT}"
echo "配置已写入 ${OUTPUT}，请不要发送给客户或上传代码仓库。"
