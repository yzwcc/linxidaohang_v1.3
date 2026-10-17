#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAV_DIR="${NAV_DIR:-/opt/sparkrobot/lingxi-nav/current/navigation/robot_local_navigation_v3}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/lib/sparkrobot/lingxi-nav-pnc-arrival-fix/backups}"
NAV_CONTROL="${NAV_CONTROL:-/opt/sparkrobot/lingxi-nav/current/scripts/navigation_control.sh}"
FILES=(navigation_client.py pnc_response.py)

usage() {
  cat <<'EOF'
用法：sudo bash ./install.sh

只安装 PNC 导航到达判定修复，不重新部署 AIMDK、语音、地图、灵心或 Java Agent。
可选环境变量：
  NAV_DIR       正式导航 Python 包目录
  NAV_CONTROL   导航专用重启脚本
  BACKUP_ROOT   备份目录
EOF
}

[[ "${1:-}" != "-h" && "${1:-}" != "--help" ]] || { usage; exit 0; }
[[ $# -eq 0 ]] || { echo "未知参数：$1" >&2; usage >&2; exit 2; }
[[ "${EUID}" -eq 0 ]] || { echo "请使用 sudo 运行：sudo bash ./install.sh" >&2; exit 1; }
[[ "$(uname -m)" == "aarch64" || "$(uname -m)" == "arm64" ]] || {
  echo "此增量包只允许安装到 ARM64 机器人" >&2
  exit 1
}
[[ -d "${NAV_DIR}" ]] || { echo "找不到导航目录：${NAV_DIR}" >&2; exit 1; }

for file in "${FILES[@]}"; do
  [[ -f "${PACKAGE_DIR}/payload/${file}" ]] || { echo "安装包缺少：payload/${file}" >&2; exit 1; }
done
[[ -f "${NAV_DIR}/navigation_client.py" ]] || {
  echo "导航目录缺少原文件：${NAV_DIR}/navigation_client.py" >&2
  echo "本包不是完整导航包，请先安装正式导航版本。" >&2
  exit 1
}

TMP_COMPILE="$(mktemp -d)"
trap 'rm -rf "${TMP_COMPILE}"' EXIT
PYTHONPYCACHEPREFIX="${TMP_COMPILE}/pycache" python3 -m py_compile "${PACKAGE_DIR}/payload/"*.py

install -d -m 0700 "${BACKUP_ROOT}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${BACKUP_ROOT}/${TIMESTAMP}"
install -d -m 0700 "${BACKUP_DIR}"
printf 'version=%s\n' "$(awk -F= '/^version=/{print $2}' "${PACKAGE_DIR}/release-info.txt")" > "${BACKUP_DIR}/metadata.txt"
printf 'nav_dir=%s\n' "${NAV_DIR}" >> "${BACKUP_DIR}/metadata.txt"
printf 'pnc_response_existed=%s\n' "$([[ -f "${NAV_DIR}/pnc_response.py" ]] && echo true || echo false)" >> "${BACKUP_DIR}/metadata.txt"
cp -a "${NAV_DIR}/navigation_client.py" "${BACKUP_DIR}/navigation_client.py"
if [[ -f "${NAV_DIR}/pnc_response.py" ]]; then
  cp -a "${NAV_DIR}/pnc_response.py" "${BACKUP_DIR}/pnc_response.py"
fi

for file in "${FILES[@]}"; do
  target="${NAV_DIR}/${file}"
  temp="${NAV_DIR}/.${file}.pnc-arrival-fix.$$"
  if [[ -f "${target}" ]]; then
    mode="$(stat -c '%a' "${target}")"
    install -m "${mode}" "${PACKAGE_DIR}/payload/${file}" "${temp}"
  else
    install -m 0644 "${PACKAGE_DIR}/payload/${file}" "${temp}"
  fi
  chown --reference="${NAV_DIR}/navigation_client.py" "${temp}"
  mv -f "${temp}" "${target}"
done

echo "PNC 到达判定补丁已安装，原文件备份于：${BACKUP_DIR}"

restart_navigation() {
  local candidates=("${NAV_CONTROL}" \
    "/opt/sparkrobot/lingxi-nav/current/scripts/navigation_control.sh" \
    "/opt/sparkrobot/lingxi-nav/current/scripts/robot_local_navigation_v3.sh")
  local control=""
  for candidate in "${candidates[@]}"; do
    if [[ -x "${candidate}" ]]; then
      control="${candidate}"
      break
    fi
  done
  if [[ -z "${control}" ]]; then
    echo "未找到导航专用重启脚本，未自动重启 bridge。" >&2
    echo "补丁文件已安装，请使用正式导航脚本执行 restart 后再验收。" >&2
    return 0
  fi
  if command -v runuser >/dev/null 2>&1 && id agi >/dev/null 2>&1; then
    runuser -u agi -- "${control}" restart
  else
    "${control}" restart
  fi
}

restart_navigation
echo "PNC 到达判定修复安装完成；未重启语音、地图、灵心、Java Agent 或 AIMDK。"
