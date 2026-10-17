#!/usr/bin/env bash
set -Eeuo pipefail
umask 0027

SCRIPT_PATH="${BASH_SOURCE[0]}"
SCRIPT_PATH="$(readlink -f "${SCRIPT_PATH}" 2>/dev/null || printf '%s' "${SCRIPT_PATH}")"
PACKAGE_DIR="$(cd "$(dirname "${SCRIPT_PATH}")" && pwd)"
NAV_DIR="${BLOCKED_REMINDER_NAV_DIR:-/opt/sparkrobot/lingxi-nav/current/navigation/robot_local_navigation_v3}"
BACKUP_ROOT="${BLOCKED_REMINDER_BACKUP_ROOT:-/var/lib/sparkrobot/lingxi-blocked-reminder/backups}"
CONTROL_LINK="${BLOCKED_REMINDER_CONTROL_LINK:-/usr/local/sbin/sparkrobot-lingxi-blocked-reminder}"
NAV_CONTROL="${BLOCKED_REMINDER_NAV_CONTROL:-/opt/sparkrobot/lingxi-nav/current/scripts/navigation_control.sh}"
NO_RESTART=false
FILES=(blocked_navigation.py navigation_events.py navigation_client.py lingxi_nav_bridge.py)

die() { echo "失败：$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
用法：sudo bash ./install.sh [--no-restart]

新增导航受阻礼貌提醒：行进中连续无有效位移满 5 秒后播报“请大家让让我”，之后每隔 5 秒最多一次。
原有“当前路线暂时无法到达/导航失败”提示保留，不修改 AIMDK、PNC、地图、任务或语音配置。
安装后调用机器人现有 navigation_control.sh；该脚本可能按原有逻辑同时重启地图和兼容语音网关。
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-restart) NO_RESTART=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数：$1" ;;
  esac
done

[[ "${EUID}" -eq 0 ]] || die "请使用 sudo 运行"
[[ "$(uname -m)" == "aarch64" || "$(uname -m)" == "arm64" ]] || die "此包只支持 ARM64 机器人"
[[ -d "${NAV_DIR}" ]] || die "导航目录不存在：${NAV_DIR}"
[[ -x "${NAV_CONTROL}" ]] || die "找不到导航重启脚本：${NAV_CONTROL}"

for file in "${FILES[@]}"; do
  [[ -f "${PACKAGE_DIR}/payload/robot_local_navigation_v3/${file}" ]] || die "安装包缺少 ${file}"
done
for file in navigation_events.py navigation_client.py lingxi_nav_bridge.py; do
  [[ -f "${NAV_DIR}/${file}" ]] || die "当前导航目录缺少 ${file}"
done

TMP_COMPILE="$(mktemp -d)"
trap 'rm -rf "${TMP_COMPILE}"' EXIT
PYTHONPYCACHEPREFIX="${TMP_COMPILE}/pycache" python3 -m py_compile \
  "${PACKAGE_DIR}/payload/robot_local_navigation_v3/blocked_navigation.py" \
  "${PACKAGE_DIR}/payload/robot_local_navigation_v3/navigation_events.py" \
  "${PACKAGE_DIR}/payload/robot_local_navigation_v3/navigation_client.py" \
  "${PACKAGE_DIR}/payload/robot_local_navigation_v3/lingxi_nav_bridge.py"

install -d -m 0700 "${BACKUP_ROOT}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$(mktemp -d "${BACKUP_ROOT}/${TIMESTAMP}.XXXXXX")"
printf 'version=1.0.0-20260929\ninstalled_at=%s\nnav_dir=%s\n' \
  "${TIMESTAMP}" "${NAV_DIR}" > "${BACKUP_DIR}/metadata.txt"

for file in "${FILES[@]}"; do
  if [[ -f "${NAV_DIR}/${file}" ]]; then
    printf 'true\n' > "${BACKUP_DIR}/existed-${file}"
    cp -a "${NAV_DIR}/${file}" "${BACKUP_DIR}/${file}"
  else
    printf 'false\n' > "${BACKUP_DIR}/existed-${file}"
  fi
done

restore_files() {
  local file existed temp
  for file in "${FILES[@]}"; do
    existed="false"
    [[ -r "${BACKUP_DIR}/existed-${file}" ]] && existed="$(<"${BACKUP_DIR}/existed-${file}")"
    if [[ "${existed}" == "true" ]]; then
      temp="${NAV_DIR}/.${file}.restore.$$"
      install -m "$(stat -c '%a' "${NAV_DIR}/${file}")" "${BACKUP_DIR}/${file}" "${temp}"
      chown --reference="${NAV_DIR}/${file}" "${temp}"
      mv -f "${temp}" "${NAV_DIR}/${file}"
    else
      rm -f "${NAV_DIR}/${file}"
    fi
  done
}

restart_bridge() {
  if command -v runuser >/dev/null 2>&1 && id agi >/dev/null 2>&1; then
    runuser -u agi -- "${NAV_CONTROL}" restart
  else
    "${NAV_CONTROL}" restart
  fi
}

rollback_on_error() {
  local code=$?
  trap - ERR
  set +e
  echo "安装失败，正在恢复原导航文件：${BACKUP_DIR}" >&2
  restore_files
  if [[ "${NO_RESTART}" != true ]]; then
    restart_bridge >&2 || echo "原文件已恢复，但 bridge 未能自动重启" >&2
  fi
  exit "${code}"
}
trap rollback_on_error ERR

STAGING_DIR="$(mktemp -d "${NAV_DIR}/.blocked-reminder-stage.XXXXXX")"
trap 'rm -rf "${STAGING_DIR}" "${TMP_COMPILE}"' EXIT
for file in "${FILES[@]}"; do
  mode="0644"
  [[ -e "${NAV_DIR}/${file}" ]] && mode="$(stat -c '%a' "${NAV_DIR}/${file}")"
  install -m "${mode}" "${PACKAGE_DIR}/payload/robot_local_navigation_v3/${file}" "${STAGING_DIR}/${file}"
  chown --reference="${NAV_DIR}/lingxi_nav_bridge.py" "${STAGING_DIR}/${file}"
done
for file in "${FILES[@]}"; do
  mv -f "${STAGING_DIR}/${file}" "${NAV_DIR}/${file}"
done
install -m 0755 "${PACKAGE_DIR}/control.sh" "${CONTROL_LINK}"

if [[ "${NO_RESTART}" != true ]]; then
  restart_bridge
else
  echo "已按 --no-restart 跳过 bridge 重启"
fi

printf '%s\n' "${BACKUP_DIR}" > "${BACKUP_ROOT}/last-backup"
chmod 0600 "${BACKUP_ROOT}/last-backup"
trap - ERR
echo "导航受阻礼貌提醒已安装：首次 5 秒，之后每隔 5 秒播报“请大家让让我”"
echo "原文件备份：${BACKUP_DIR}"
echo "验收：sudo ${CONTROL_LINK} doctor"
echo "回滚：sudo ${CONTROL_LINK} rollback ${BACKUP_DIR}"
