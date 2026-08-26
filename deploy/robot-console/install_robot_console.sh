#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 sudo 运行此安装脚本" >&2
  exit 1
fi

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET_ROOT="${TARGET_ROOT:-/opt/lingxi-nav-console}"
SERVER_SOURCE="${PACKAGE_DIR}/server"
RUNTIME_SOURCE="${PACKAGE_DIR}/runtime"

for required in \
  "${SERVER_SOURCE}/dist/index.js" \
  "${SERVER_SOURCE}/package.json" \
  "${SERVER_SOURCE}/node_modules" \
  "${RUNTIME_SOURCE}/bin/node"; do
  if [[ ! -e "${required}" ]]; then
    echo "安装包不完整，缺少：${required}" >&2
    exit 1
  fi
done

install -d -m 0755 "${TARGET_ROOT}" "${TARGET_ROOT}/server"
rm -rf "${TARGET_ROOT}/server/dist" "${TARGET_ROOT}/server/public" "${TARGET_ROOT}/server/node_modules" "${TARGET_ROOT}/runtime"
cp -a "${SERVER_SOURCE}/dist" "${SERVER_SOURCE}/public" "${SERVER_SOURCE}/node_modules" "${TARGET_ROOT}/server/"
cp -a "${SERVER_SOURCE}/package.json" "${SERVER_SOURCE}/package-lock.json" "${TARGET_ROOT}/server/"
cp -a "${RUNTIME_SOURCE}" "${TARGET_ROOT}/runtime"

if [[ -f "${PACKAGE_DIR}/deploy/robot-console/install_navigation_update.sh" ]]; then
  bash "${PACKAGE_DIR}/deploy/robot-console/install_navigation_update.sh"
fi

if [[ -f "${TARGET_ROOT}/server/.env" ]]; then
  cp -a "${TARGET_ROOT}/server/.env" "${TARGET_ROOT}/server/.env.backup.$(date +%Y%m%d-%H%M%S)"
fi
cp "${PACKAGE_DIR}/deploy/robot-console/robot-console.env" "${TARGET_ROOT}/server/.env"
token="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
sed -i "s/^TOKEN_SECRET=.*/TOKEN_SECRET=${token}/" "${TARGET_ROOT}/server/.env"
chmod 0600 "${TARGET_ROOT}/server/.env"

install -m 0644 "${PACKAGE_DIR}/deploy/robot-console/lingxi-nav-console-local.service" /etc/systemd/system/lingxi-nav-console.service

# The local product no longer needs or claims shared cloud tunnel ports.
systemctl disable --now daohang-reverse-tunnel.service >/dev/null 2>&1 || true
systemctl daemon-reload
systemctl enable --now lingxi-nav-console.service

for _ in $(seq 1 20); do
  if curl -fsS --max-time 2 http://127.0.0.1:3000/health >/dev/null; then
    break
  fi
  sleep 1
done

if ! curl -fsS --max-time 3 http://127.0.0.1:3000/health >/dev/null; then
  systemctl status lingxi-nav-console.service --no-pager -l || true
  echo "机器人本机控制台启动失败" >&2
  exit 1
fi

echo "机器人本机控制台安装完成，并已设置为开机自启。"
echo "请让手机或电脑连接机器人所在网络，然后访问："
for address in $(hostname -I); do
  case "${address}" in
    *:*) ;;
    *) echo "  http://${address}:3000/" ;;
  esac
done
