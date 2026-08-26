#!/usr/bin/env bash
set -euo pipefail

SOC2_HOST="${SOC2_HOST:-10.0.1.42}"
SOC2_USER="${SOC2_USER:-run}"
SSH_KEY="${SSH_KEY:-/home/agi/.ssh/daohang_soc2_ed25519}"

SSH=(
  /usr/bin/ssh
  -T
  -o BatchMode=yes
  -o ConnectTimeout=5
  -o StrictHostKeyChecking=accept-new
  -i "$SSH_KEY"
  "${SOC2_USER}@${SOC2_HOST}"
)

"${SSH[@]}" "sudo -n systemctl restart agibot_software.service"

for _ in $(seq 1 60); do
  if "${SSH[@]}" '
    systemctl is-active --quiet agibot_software.service &&
    pgrep -f "aima-agent-app" >/dev/null &&
    pgrep -f "aima-interaction-app" >/dev/null &&
    pgrep -f "aima-audiohal-app" >/dev/null
  '; then
    /agibot/data/home/agi/lingxi_robot_bridge/start_robot_bridge.sh
    echo "机器人本机语音、唤醒和音频服务已启动"
    exit 0
  fi
  sleep 1
done

echo "机器人本机语音服务重启后未在 60 秒内全部上线" >&2
exit 1
