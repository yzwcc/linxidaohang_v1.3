#!/usr/bin/env bash
set -euo pipefail

RUNTIME_ENV="${SPARKROBOT_RUNTIME_ENV:-/etc/sparkrobot/lingxi-nav/runtime.env}"
if [[ -r "${RUNTIME_ENV}" ]]; then
  set -a
  source "${RUNTIME_ENV}"
  set +a
fi

SOC2_HOST="${SOC2_HOST:-10.0.1.42}"
SOC2_USER="${SOC2_USER:-run}"
SSH_KEY="${SOC2_SSH_KEY:-/etc/sparkrobot/lingxi-nav/soc2-ed25519}"
PRODUCT_ROOT="${SPARKROBOT_PRODUCT_ROOT:-/opt/sparkrobot/lingxi-nav/current}"

[[ -r "${SSH_KEY}" ]] || { echo "缺少 SOC2 SSH 私钥，请先运行 setup_soc2_access.sh" >&2; exit 1; }
SSH=(/usr/bin/ssh -T -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new -i "${SSH_KEY}" "${SOC2_USER}@${SOC2_HOST}")

"${SSH[@]}" "sudo -n systemctl restart agibot_software.service"
for _ in $(seq 1 40); do
  if "${SSH[@]}" '
    systemctl is-active --quiet agibot_software.service &&
    pgrep -f "aima-agent-app" >/dev/null &&
    pgrep -f "aima-interaction-app" >/dev/null &&
    pgrep -f "aima-audiohal-app" >/dev/null
  '; then
    "${PRODUCT_ROOT}/scripts/robot_voice_bridge_control.sh" restart
    echo "机器人本机语音、唤醒、音频和灵心回调服务已启动"
    exit 0
  fi
  sleep 1
done
echo "SOC2 语音服务重启后 40 秒内未全部上线" >&2
exit 1
