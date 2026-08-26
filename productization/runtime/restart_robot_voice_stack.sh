#!/usr/bin/env bash
set -euo pipefail

SOC2_HOST="${SOC2_HOST:-10.0.1.42}"
SOC2_USER="${SOC2_USER:-run}"
SSH_KEY="${SOC2_SSH_KEY:-/etc/sparkrobot/lingxi-nav/soc2-ed25519}"
PRODUCT_ROOT="${SPARKROBOT_PRODUCT_ROOT:-/opt/sparkrobot/lingxi-nav/current}"

[[ -r "${SSH_KEY}" ]] || { echo "缺少 SOC2 运维私钥，请由厂商重新执行设备部署" >&2; exit 1; }

SSH=(
  /usr/bin/ssh -T -o BatchMode=yes -o ConnectTimeout=5
  -o StrictHostKeyChecking=accept-new -i "${SSH_KEY}"
  "${SOC2_USER}@${SOC2_HOST}"
)

"${SSH[@]}" "sudo -n systemctl restart agibot_software.service"

for _ in $(seq 1 30); do
  if "${SSH[@]}" '
    systemctl is-active --quiet agibot_software.service &&
    pgrep -f "aima-agent-app" >/dev/null &&
    pgrep -f "aima-interaction-app" >/dev/null &&
    pgrep -f "aima-audiohal-app" >/dev/null
  '; then
    "${PRODUCT_ROOT}/scripts/start_robot_voice_bridge.sh"
    echo "机器人本机语音、唤醒、音频和灵犀回调服务已启动"
    exit 0
  fi
  sleep 1
done

echo "SOC2 语音服务重启后未在 30 秒内全部上线" >&2
exit 1
