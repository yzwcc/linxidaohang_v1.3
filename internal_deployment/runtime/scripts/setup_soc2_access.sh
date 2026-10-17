#!/usr/bin/env bash
set -euo pipefail

[[ "${EUID}" -eq 0 ]] || { echo "请使用 sudo 运行" >&2; exit 1; }
CONFIG_DIR="/etc/sparkrobot/lingxi-nav"
RUNTIME_ENV="${SPARKROBOT_RUNTIME_ENV:-${CONFIG_DIR}/runtime.env}"
if [[ -r "${RUNTIME_ENV}" ]]; then
  set -a
  source "${RUNTIME_ENV}"
  set +a
fi
KEY="${CONFIG_DIR}/soc2-ed25519"
SOC2_HOST="${SOC2_HOST:-10.0.1.42}"
SOC2_USER="${SOC2_USER:-run}"
GROUP="${SPARKROBOT_RUNTIME_GROUP:-sparkrobot-nav}"

command -v ssh-copy-id >/dev/null || { echo "缺少 ssh-copy-id" >&2; exit 1; }
install -d -m 0750 -o root -g "${GROUP}" "${CONFIG_DIR}"
ROOT_HOME="$(getent passwd root | cut -d: -f6)"
[[ -n "${ROOT_HOME}" ]] || ROOT_HOME=/root
install -d -m 0700 -o root -g root "${ROOT_HOME}/.ssh"
if [[ ! -f "${KEY}" ]]; then
  ssh-keygen -q -t ed25519 -N '' -C sparkrobot-lingxi-soc2 -f "${KEY}"
fi
chown run:run "${KEY}"
chown run:"${GROUP}" "${KEY}.pub"
chmod 0600 "${KEY}"
chmod 0644 "${KEY}.pub"

echo "第 1 次输入 ${SOC2_USER}@${SOC2_HOST} 登录密码，用于安装 SSH 公钥。"
ssh-copy-id -i "${KEY}.pub" -o StrictHostKeyChecking=accept-new "${SOC2_USER}@${SOC2_HOST}"

temporary="$(mktemp)"
trap 'rm -f -- "${temporary}"' EXIT
cat >"${temporary}" <<'EOF'
run ALL=(root) NOPASSWD: /usr/bin/systemctl restart agibot_software.service, /usr/bin/systemctl is-active agibot_software.service
EOF
scp -q -i "${KEY}" "${temporary}" "${SOC2_USER}@${SOC2_HOST}:/tmp/sparkrobot-lingxi-voice.sudoers"
echo "第 2 次输入 SOC2 sudo 密码，用于安装受限的语音重启权限。"
ssh -t -i "${KEY}" "${SOC2_USER}@${SOC2_HOST}" \
  "sudo install -m 0440 /tmp/sparkrobot-lingxi-voice.sudoers /etc/sudoers.d/sparkrobot-lingxi-voice && sudo visudo -cf /etc/sudoers.d/sparkrobot-lingxi-voice && rm -f /tmp/sparkrobot-lingxi-voice.sudoers"
ssh -T -i "${KEY}" "${SOC2_USER}@${SOC2_HOST}" "sudo -n systemctl is-active agibot_software.service >/dev/null"
echo "SOC2 语音运维通道已配置"
