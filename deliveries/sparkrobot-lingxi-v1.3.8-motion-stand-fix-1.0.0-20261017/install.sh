#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 sudo bash ./install.sh 运行" >&2
  exit 1
fi

PACKAGE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
NAV_ROOT="${LINGXI_NAV_ROOT:-/opt/sparkrobot/lingxi-nav/current}"
MOTION_TARGET="${NAV_ROOT}/navigation/robot_local_navigation_v3/sdk_motion_controller.py"
BRIDGE_TARGET="${NAV_ROOT}/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py"
MOTION_PAYLOAD="${PACKAGE_DIR}/payload/sdk_motion_controller.py"
SKILL_TOOL="${PACKAGE_DIR}/payload/disable_legacy_skills.py"
BACKUP_ROOT="/var/lib/sparkrobot/lingxi-v1.3.8-motion-stand-fix/backups"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${BACKUP_ROOT}/${STAMP}"
APPLIED=0

die() {
  echo "失败：$*" >&2
  exit 1
}

start_services() {
  if command -v sparkrobot-lingxi-internal >/dev/null 2>&1; then
    sparkrobot-lingxi-internal start || true
  else
    systemctl start lingxi-java-agent-robot.service 2>/dev/null || true
  fi
}

restore_on_error() {
  local rc=$?
  if [[ "${APPLIED}" -eq 0 && -f "${BACKUP_DIR}/targets.tsv" ]]; then
    echo "安装失败，正在恢复事务备份：${BACKUP_DIR}" >&2
    while IFS=$'\t' read -r source target; do
      [[ -n "${source}" && -n "${target}" ]] || continue
      [[ -f "${source}" ]] || continue
      install -d "$(dirname -- "${target}")"
      cp -a "${source}" "${target}" || true
    done < "${BACKUP_DIR}/targets.tsv"
    start_services
  fi
  exit "${rc}"
}
trap restore_on_error ERR

[[ -f "${MOTION_PAYLOAD}" ]] || die "增量包缺少动作控制器 payload"
[[ -f "${SKILL_TOOL}" ]] || die "增量包缺少技能配置修复脚本"
[[ -f "${MOTION_TARGET}" ]] || die "未找到 V1.3.8 动作控制器：${MOTION_TARGET}"
[[ -f "${BRIDGE_TARGET}" ]] || die "未找到 V1.3.8 导航 bridge：${BRIDGE_TARGET}"

grep -q "class SdkMotionController" "${MOTION_TARGET}" || die "当前文件不是 V1.3.8 动作控制器"
grep -q "play_preset_motion_and_wait" "${MOTION_TARGET}" || die "当前动作控制器不支持语音预设动作回执"
grep -q "MC_ACTION_SERVICE" "${MOTION_TARGET}" || die "当前动作控制器缺少 STAND_DEFAULT 恢复能力，请先升级 V1.3.8 主包"
grep -q "play_preset_motion_and_wait" "${BRIDGE_TARGET}" || die "当前导航 bridge 不支持 V1.3.8 语音预设动作回执"

install -d -m 0700 "${BACKUP_DIR}"
cp -a "${MOTION_TARGET}" "${BACKUP_DIR}/motion_controller.py"
printf '%s\t%s\n' "${BACKUP_DIR}/motion_controller.py" "${MOTION_TARGET}" > "${BACKUP_DIR}/targets.tsv"

mapfile -t SKILL_CONFIGS < <(
  printf '%s\n' \
    /agibot/software/agent/bin/cfg/skills.json \
    /agibot/software/agent_cfg/skills.json \
  | while read -r path; do [[ -f "${path}" ]] && printf '%s\n' "${path}"; done
)

for index in "${!SKILL_CONFIGS[@]}"; do
  config="${SKILL_CONFIGS[$index]}"
  backup="${BACKUP_DIR}/skills-${index}.json"
  cp -a "${config}" "${backup}"
  printf '%s\t%s\n' "${backup}" "${config}" >> "${BACKUP_DIR}/targets.tsv"
done

if command -v sparkrobot-lingxi-internal >/dev/null 2>&1; then
  sparkrobot-lingxi-internal stop || true
else
  systemctl stop lingxi-java-agent-robot.service 2>/dev/null || true
fi

cp -a "${MOTION_PAYLOAD}" "${MOTION_TARGET}"
chown --reference="${BACKUP_DIR}/motion_controller.py" "${MOTION_TARGET}"
chmod --reference="${BACKUP_DIR}/motion_controller.py" "${MOTION_TARGET}"

for config in "${SKILL_CONFIGS[@]}"; do
  python3 "${SKILL_TOOL}" "${config}"
done

python3 -m py_compile "${MOTION_TARGET}"
start_services
sleep 2

if systemctl list-unit-files lingxi-java-agent-robot.service >/dev/null 2>&1; then
  systemctl is-active --quiet lingxi-java-agent-robot.service || die "lingxi-java-agent-robot.service 未恢复为 active"
fi

APPLIED=1
trap - ERR
echo "V1.3.8 预设动作稳定站立修复安装成功"
echo "备份目录：${BACKUP_DIR}"
echo "修复内容：稳定站立拒绝回执触发 STAND_DEFAULT 后重试；禁用旧重复技能映射"
echo "回滚：sudo bash ${PACKAGE_DIR}/control.sh rollback ${BACKUP_DIR}"
