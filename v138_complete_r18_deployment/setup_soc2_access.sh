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
KEY="${SOC2_SSH_KEY:-${CONFIG_DIR}/soc2-ed25519}"
KNOWN_HOSTS="${SOC2_KNOWN_HOSTS_FILE:-/var/lib/sparkrobot/lingxi-nav/ssh/soc2-known-hosts}"
SOC2_HOST="${SOC2_HOST:-10.0.1.42}"
SOC2_USER="${SOC2_USER:-run}"
GROUP="${SPARKROBOT_RUNTIME_GROUP:-sparkrobot-nav}"

command -v ssh-copy-id >/dev/null || { echo "缺少 ssh-copy-id" >&2; exit 1; }
[[ "${KEY}" == /* && "${KNOWN_HOSTS}" == /* ]] || { echo "SOC2 SSH 文件必须使用绝对路径" >&2; exit 1; }
[[ ! -L "${KEY}" && ! -L "${KNOWN_HOSTS}" ]] || { echo "SOC2 SSH 文件不能是软链接" >&2; exit 1; }
install -d -m 0750 -o root -g "${GROUP}" "${CONFIG_DIR}"
install -d -m 0750 -o root -g "${GROUP}" "$(dirname "${KEY}")"
install -d -m 0770 -o run -g "${GROUP}" "$(dirname "${KNOWN_HOSTS}")"
if [[ ! -f "${KEY}" ]]; then
  ssh-keygen -q -t ed25519 -N '' -C sparkrobot-lingxi-soc2 -f "${KEY}"
fi
[[ -f "${KEY}.pub" ]] || { echo "SOC2 SSH 公钥不存在：${KEY}.pub" >&2; exit 1; }
chown run:run "${KEY}"
chown run:"${GROUP}" "${KEY}.pub"
chmod 0600 "${KEY}"
chmod 0644 "${KEY}.pub"
touch "${KNOWN_HOSTS}"
chown run:"${GROUP}" "${KNOWN_HOSTS}"
chmod 0660 "${KNOWN_HOSTS}"

# Bootstrap and all runtime consumers must use the same host-key database.
SSH_OPTIONS=(-o StrictHostKeyChecking=accept-new -o "UserKnownHostsFile=${KNOWN_HOSTS}")
echo "第 1 次输入 ${SOC2_USER}@${SOC2_HOST} 登录密码，用于安装 SSH 公钥。"
ssh-copy-id -i "${KEY}.pub" "${SSH_OPTIONS[@]}" "${SOC2_USER}@${SOC2_HOST}"

temporary="$(mktemp)"
trap 'rm -f -- "${temporary}"' EXIT
cat >"${temporary}" <<'EOF'
run ALL=(root) NOPASSWD: /usr/bin/systemctl restart agibot_software.service, /usr/bin/systemctl is-active agibot_software.service
EOF
scp -q -i "${KEY}" "${SSH_OPTIONS[@]}" "${temporary}" "${SOC2_USER}@${SOC2_HOST}:/tmp/sparkrobot-lingxi-voice.sudoers"
echo "第 2 次输入 SOC2 sudo 密码，用于安装受限的语音重启权限。"
ssh -t -i "${KEY}" "${SSH_OPTIONS[@]}" "${SOC2_USER}@${SOC2_HOST}" \
  "sudo install -m 0440 /tmp/sparkrobot-lingxi-voice.sudoers /etc/sudoers.d/sparkrobot-lingxi-voice && sudo visudo -cf /etc/sudoers.d/sparkrobot-lingxi-voice && rm -f /tmp/sparkrobot-lingxi-voice.sudoers"
# Prove that the account used by one-click startup can use the enrolled key.
runuser -u run -- ssh -T -i "${KEY}" -o BatchMode=yes -o ConnectTimeout=5 \
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=${KNOWN_HOSTS}" \
  "${SOC2_USER}@${SOC2_HOST}" "sudo -n systemctl is-active agibot_software.service >/dev/null"
echo "SOC2 语音运维通道已配置"
