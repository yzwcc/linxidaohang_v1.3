#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PACKAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
NAV_DIR="${LINKCRAFT_NAV_DIR:-/opt/sparkrobot/lingxi-nav/current/navigation/robot_local_navigation_v3}"
CONSOLE_DIR="${LINKCRAFT_CONSOLE_DIR:-/opt/sparkrobot/lingxi-nav/current/console/dist}"
BACKUP_ROOT="${LINKCRAFT_BACKUP_ROOT:-/var/lib/sparkrobot/lingxi-linkcraft/backups}"
NO_RESTART=false
FILES=(
  lingxi_nav_bridge.py
  sdk_motion_controller.py
  linkcraft_resources.py
  tour_step_actions.py
  tour_interaction_policy.py
)

usage() {
  cat <<'EOF'
用法：sudo ./install.sh [--no-restart]

只替换导航讲解任务的灵创相关 Python 文件，并备份旧文件。
  --no-restart  替换后不重启导航 bridge，适合先做静态检查
环境变量：
  LINKCRAFT_NAV_DIR     导航 Python 包目录
  LINKCRAFT_BACKUP_ROOT 备份根目录
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-restart) NO_RESTART=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "${EUID}" -eq 0 ]] || { echo "请使用 sudo 运行安装脚本" >&2; exit 1; }
[[ "$(uname -m)" == "aarch64" || "$(uname -m)" == "arm64" ]] || {
  echo "此附加包只允许安装到 ARM64 机器人" >&2
  exit 1
}
[[ -d "${NAV_DIR}" ]] || { echo "找不到导航 Python 包目录：${NAV_DIR}" >&2; exit 1; }
[[ -d "${CONSOLE_DIR}" ]] || { echo "找不到网页控制台目录：${CONSOLE_DIR}" >&2; exit 1; }

for file in "${FILES[@]}"; do
  [[ -f "${PACKAGE_DIR}/payload/robot_local_navigation_v3/${file}" ]] || {
    echo "安装包缺少文件：${file}" >&2
    exit 1
  }
  [[ -f "${NAV_DIR}/${file}" ]] || {
    echo "机器人当前导航目录缺少原文件：${NAV_DIR}/${file}" >&2
    echo "请确认导航主包已按正式流程安装；本附加包不会安装导航主包。" >&2
    exit 1
  }
done
[[ -f "${PACKAGE_DIR}/payload/console/dist/linkcraftResources.js" ]] || {
  echo "安装包缺少网页后端文件：linkcraftResources.js" >&2
  exit 1
}
[[ -f "${CONSOLE_DIR}/linkcraftResources.js" ]] || {
  echo "网页控制台缺少原文件：${CONSOLE_DIR}/linkcraftResources.js" >&2
  echo "请确认当前安装的是支持灵创任务编排的导航网页版本。" >&2
  exit 1
}

for dependency in config_store.py map_point_store.py motion_option_policy.py navigation_client.py navigation_events.py point_catalog.py tts_speaker.py; do
  [[ -f "${NAV_DIR}/${dependency}" ]] || {
    echo "导航依赖文件缺少：${NAV_DIR}/${dependency}" >&2
    exit 1
  }
done

python3 -m py_compile "${PACKAGE_DIR}/payload/robot_local_navigation_v3/"*.py
install -d -m 0700 "${BACKUP_ROOT}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${BACKUP_ROOT}/${TIMESTAMP}"
install -d -m 0700 "${BACKUP_DIR}"
printf 'version=%s\n' "$(awk -F= '/^VERSION=/{print $2}' "${PACKAGE_DIR}/release-info.txt")" > "${BACKUP_DIR}/metadata.txt"
printf 'nav_dir=%s\n' "${NAV_DIR}" >> "${BACKUP_DIR}/metadata.txt"

for file in "${FILES[@]}"; do
  cp -a "${NAV_DIR}/${file}" "${BACKUP_DIR}/${file}"
done
install -d -m 0700 "${BACKUP_DIR}/console"
cp -a "${CONSOLE_DIR}/linkcraftResources.js" "${BACKUP_DIR}/console/linkcraftResources.js"

for file in "${FILES[@]}"; do
  source_file="${PACKAGE_DIR}/payload/robot_local_navigation_v3/${file}"
  temp_file="${NAV_DIR}/.${file}.linkcraft.$$"
  install -m "$(stat -c '%a' "${NAV_DIR}/${file}")" "${source_file}" "${temp_file}"
  chown --reference="${NAV_DIR}/${file}" "${temp_file}"
  mv -f "${temp_file}" "${NAV_DIR}/${file}"
done
console_source="${PACKAGE_DIR}/payload/console/dist/linkcraftResources.js"
console_temp="${CONSOLE_DIR}/.linkcraftResources.js.linkcraft.$$"
install -m "$(stat -c '%a' "${CONSOLE_DIR}/linkcraftResources.js")" "${console_source}" "${console_temp}"
chown --reference="${CONSOLE_DIR}/linkcraftResources.js" "${console_temp}"
mv -f "${console_temp}" "${CONSOLE_DIR}/linkcraftResources.js"

echo "灵创文件已更新，旧文件备份于：${BACKUP_DIR}"
if [[ "${NO_RESTART}" == true ]]; then
  echo "已跳过导航 bridge 重启。"
  exit 0
fi

restart_navigation() {
  local control="${LINKCRAFT_NAV_CONTROL:-/opt/sparkrobot/lingxi-nav/current/scripts/navigation_control.sh}"
  if [[ -x "${control}" ]]; then
    if command -v runuser >/dev/null 2>&1 && id agi >/dev/null 2>&1; then
      runuser -u agi -- "${control}" restart
    elif command -v sudo >/dev/null 2>&1 && id agi >/dev/null 2>&1; then
      sudo -u agi "${control}" restart
    else
      "${control}" restart
    fi
    return
  fi
  if command -v sparkrobot-lingxi-internal >/dev/null 2>&1; then
    echo "未找到 navigation_control.sh，将兼容调用内部控制器 restart；该方式可能同时重启语音服务。" >&2
    sparkrobot-lingxi-internal restart
    return
  fi
  echo "文件已替换，但找不到导航重启脚本：${control}" >&2
  echo "可先执行 --no-restart，然后补齐导航主包脚本，再手动重启导航 bridge。" >&2
  return 1
}

restart_navigation
if systemctl cat lingxi-nav-console.service >/dev/null 2>&1; then
  systemctl restart lingxi-nav-console.service
  echo "网页控制台已重启，资源列表修复已加载。"
else
  echo "未找到 lingxi-nav-console.service，请手动重启网页控制台后刷新页面。" >&2
fi
echo "灵创附加包安装完成；未重新部署导航主包、AIMDK 或语音配置。"
