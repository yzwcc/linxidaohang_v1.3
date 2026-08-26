#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 sudo 运行此更新脚本" >&2
  exit 1
fi

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET_ROOT="${TARGET_ROOT:-/opt/lingxi-nav-console}"
timestamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="${TARGET_ROOT}/backups/v1.2.0-${timestamp}"

for required in \
  "${PACKAGE_DIR}/server/dist/index.js" \
  "${PACKAGE_DIR}/server/public/app.js" \
  "${PACKAGE_DIR}/navigation-update/lingxi_nav_bridge.py"; do
  if [[ ! -f "${required}" ]]; then
    echo "更新包不完整，缺少：${required}" >&2
    exit 1
  fi
done

install -d -m 0755 "${backup_dir}" "${TARGET_ROOT}/server"
[[ -d "${TARGET_ROOT}/server/dist" ]] && cp -a "${TARGET_ROOT}/server/dist" "${backup_dir}/"
[[ -d "${TARGET_ROOT}/server/public" ]] && cp -a "${TARGET_ROOT}/server/public" "${backup_dir}/"
rm -rf "${TARGET_ROOT}/server/dist" "${TARGET_ROOT}/server/public"
cp -a "${PACKAGE_DIR}/server/dist" "${PACKAGE_DIR}/server/public" "${TARGET_ROOT}/server/"
cp -a "${PACKAGE_DIR}/server/package.json" "${TARGET_ROOT}/server/"

bash "${PACKAGE_DIR}/deploy/robot-console/install_navigation_update.sh"
systemctl restart lingxi-nav-console.service

for _ in $(seq 1 20); do
  curl -fsS --max-time 2 http://127.0.0.1:3000/health >/dev/null && break
  sleep 1
done
curl -fsS --max-time 3 http://127.0.0.1:3000/health >/dev/null

echo "灵犀导航控制台 v1.2.0 更新完成。备份目录：${backup_dir}"
