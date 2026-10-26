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

if [[ -z "${ROBOT_AIMDK_SETUP:-}" ]]; then
  echo "部署前必须设置 ROBOT_AIMDK_SETUP 为 AIMDK v1.1.0 install/setup.bash，例如：" >&2
  echo "sudo ROBOT_AIMDK_SETUP=/opt/aimdk-v1.1.0/install/setup.bash bash $0" >&2
  exit 1
fi
if [[ ! -r "${ROBOT_AIMDK_SETUP}" ]]; then
  echo "AIMDK setup 不可读：${ROBOT_AIMDK_SETUP}" >&2
  exit 1
fi
if ! bash -lc 'source "$1" && python3 -c "from aimdk_msgs.msg import PncTaskRequest; from aimdk_msgs.srv import GetStoredMapByName"' _ "${ROBOT_AIMDK_SETUP}"; then
  echo "AIMDK overlay 未提供 v1.1.0 导航与建图接口，拒绝部署" >&2
  exit 1
fi

install -d -m 0755 "${TARGET_ROOT}" "${TARGET_ROOT}/server"
backup_dir="${TARGET_ROOT}/backups/aimdk-v1.1.0-$(date +%Y%m%d-%H%M%S)"
install -d -m 0700 "${backup_dir}"
for path in "${TARGET_ROOT}/server/dist" "${TARGET_ROOT}/server/public" "${TARGET_ROOT}/server/node_modules" "${TARGET_ROOT}/runtime"; do
  if [[ -e "${path}" ]]; then
    install -d -m 0755 "${backup_dir}/$(dirname "${path#${TARGET_ROOT}/}")"
    mv "${path}" "${backup_dir}/${path#${TARGET_ROOT}/}"
  fi
done
cp -a "${SERVER_SOURCE}/dist" "${SERVER_SOURCE}/public" "${SERVER_SOURCE}/node_modules" "${TARGET_ROOT}/server/"
cp -a "${SERVER_SOURCE}/package.json" "${SERVER_SOURCE}/package-lock.json" "${TARGET_ROOT}/server/"
cp -a "${RUNTIME_SOURCE}" "${TARGET_ROOT}/runtime"

if [[ -f "${PACKAGE_DIR}/deploy/robot-console/install_navigation_update.sh" ]]; then
  bash "${PACKAGE_DIR}/deploy/robot-console/install_navigation_update.sh"
fi

env_file="${TARGET_ROOT}/server/.env"
if [[ -f "${env_file}" ]]; then
  cp -a "${env_file}" "${backup_dir}/server.env"
else
  cp "${PACKAGE_DIR}/deploy/robot-console/robot-console.env" "${env_file}"
fi
if grep -q '^ROBOT_AIMDK_SETUP=' "${env_file}"; then
  sed -i "s|^ROBOT_AIMDK_SETUP=.*|ROBOT_AIMDK_SETUP=${ROBOT_AIMDK_SETUP}|" "${env_file}"
else
  printf '\nROBOT_AIMDK_SETUP=%s\n' "${ROBOT_AIMDK_SETUP}" >>"${env_file}"
fi
if grep -q '^ROBOT_BRIDGE_PORT=' "${env_file}"; then
  sed -i 's/^ROBOT_BRIDGE_PORT=.*/ROBOT_BRIDGE_PORT=18082/' "${env_file}"
else
  printf '\nROBOT_BRIDGE_PORT=18082\n' >>"${env_file}"
fi
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
