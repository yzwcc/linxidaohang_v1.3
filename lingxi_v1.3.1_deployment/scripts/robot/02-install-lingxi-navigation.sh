#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -un)" == "run" ]] || { echo "请使用 run 账号执行本脚本" >&2; exit 1; }

ARCHIVE=/home/run/sparkrobot-lingxi-nav-internal-1.0.8-20261023-r16.tar.gz
CHECKSUM_FILE=${ARCHIVE}.sha256
PACKAGE_DIR=/home/run/sparkrobot-lingxi-nav-internal-1.0.8-20261023-r16
SECRETS=${PACKAGE_DIR}/secrets.env

[[ -f "${ARCHIVE}" ]] || { echo "缺少 ${ARCHIVE}" >&2; exit 1; }
[[ -f "${CHECKSUM_FILE}" ]] || { echo "缺少 ${CHECKSUM_FILE}" >&2; exit 1; }
ACTUAL="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
EXPECTED="$(awk 'NF >= 1 {print tolower($1); exit}' "${CHECKSUM_FILE}")"
[[ "${ACTUAL}" == "${EXPECTED}" ]] || { echo "导航包 SHA-256 校验失败" >&2; exit 1; }

rm -rf -- "${PACKAGE_DIR}"
cd /home/run
tar -xzf "${ARCHIVE}"
cd "${PACKAGE_DIR}"
trap 'rm -f -- "${SECRETS}"' EXIT

./configure.sh "${SECRETS}"
chmod 600 "${SECRETS}"

INSTALL_ARGS=(--secrets "${SECRETS}" --setup-soc2)
if [[ -d /opt/sparkrobot/lingxi-nav/releases/1.0.8-20261023-r16 ]]; then
  INSTALL_ARGS+=(--reinstall)
fi
sudo ./install.sh "${INSTALL_ARGS[@]}"
rm -f -- "${SECRETS}"
trap - EXIT

readlink -f /opt/sparkrobot/lingxi-nav/current
sudo systemctl is-active lingxi-nav-console.service
curl -fsS http://127.0.0.1:3000/health

ROBOT_IP="$(hostname -I | awk '{print $1}')"
echo "[通过] 导航产品已安装，临时密钥已删除"
echo "请用同一局域网手机打开：http://${ROBOT_IP}:3000/console/"
echo "必须在网页点击‘一键开启全部服务’，然后执行 /home/run/03-verify-after-web-start.sh"
