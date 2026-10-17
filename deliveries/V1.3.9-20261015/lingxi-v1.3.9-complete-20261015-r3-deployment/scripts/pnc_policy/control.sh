#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
if command -v readlink >/dev/null 2>&1; then
  SCRIPT_PATH="$(readlink -f "${SCRIPT_PATH}" 2>/dev/null || printf '%s' "${SCRIPT_PATH}")"
fi
SCRIPT_DIR="$(cd "$(dirname "${SCRIPT_PATH}")" && pwd)"
STATE_ROOT="${NARROW_CORRIDOR_STATE_ROOT:-/var/lib/sparkrobot/lingxi-v1.3.9-narrow-corridor}"
BACKUP_ROOT="${NARROW_CORRIDOR_BACKUP_ROOT:-${STATE_ROOT}/backups}"
CONFIG_ROOT="${PNC_CONFIG_ROOT:-/agibot/software/nav/config}"
VECTORFLUX_CONFIG="${PNC_VECTORFLUX_CONFIG:-${CONFIG_ROOT}/vectorflux/vectorflux_app_cfg.yaml}"
APP2_CONFIG="${PNC_APP2_CONFIG:-${CONFIG_ROOT}/vectorflux_common/a2/app2.yaml}"
MPPI_CONFIG="${PNC_MPPI_CONFIG:-${CONFIG_ROOT}/vectorflux_common/a2/mppi_controller.yaml}"
RESTART_SCRIPT="${PNC_RESTART_SCRIPT:-/agibot/software/drp/bin/stop_record.sh}"
POLICY="${NARROW_CORRIDOR_POLICY:-${SCRIPT_DIR}/policy.json}"
APPLY_POLICY="${SCRIPT_DIR}/apply_policy.py"

die() { echo "失败：$*" >&2; exit 1; }
require_root() { [[ "${EUID}" -eq 0 ]] || die "请使用 sudo 运行"; }

source_ros() {
  set +u
  [[ -r /opt/ros/humble/setup.bash ]] && source /opt/ros/humble/setup.bash
  [[ -r /home/agi/aimdk/install/setup.bash ]] && source /home/agi/aimdk/install/setup.bash
  set -u
}

verify_policy() {
  python3 "${APPLY_POLICY}" --policy "${POLICY}" --app2 "${APP2_CONFIG}" --mppi "${MPPI_CONFIG}" --verify >/dev/null
}

wait_for_pnc() {
  local i
  for i in $(seq 1 90); do
    if pgrep -f '[v]ectorflux --cfg_file_path=.*vectorflux_app_cfg.yaml' >/dev/null 2>&1; then
      sleep 2
      return 0
    fi
    sleep 1
  done
  return 1
}

has_global_costmap_topic() {
  local i
  for i in 1 2 3 4 5; do
    if ros2 topic list 2>/dev/null | grep -Eq '^/pnc/global_costmap(/|$)'; then
      return 0
    fi
    sleep 1
  done
  return 1
}

restart_pnc() {
  require_root
  [[ -x "${RESTART_SCRIPT}" ]] || die "找不到 PNC 重启脚本：${RESTART_SCRIPT}"
  echo "正在重启 PNC/AIMRT，使避障配置生效：${RESTART_SCRIPT}"
  "${RESTART_SCRIPT}"
  wait_for_pnc || die "PNC 进程未在 90 秒内恢复"
  echo "PNC 进程已恢复"
}

status() {
  echo "profile=balanced-narrow"
  echo "app2=${APP2_CONFIG}"
  echo "mppi=${MPPI_CONFIG}"
  verify_policy
  echo "policy=verified"
  if pgrep -f '[v]ectorflux --cfg_file_path=.*vectorflux_app_cfg.yaml' >/dev/null 2>&1; then
    echo "pnc=running"
  else
    echo "pnc=not_running"
  fi
  source_ros
  if command -v ros2 >/dev/null 2>&1 && has_global_costmap_topic; then
    echo "global_costmap=online"
  else
    echo "global_costmap=unknown"
  fi
}

doctor() {
  require_root
  [[ -f "${VECTORFLUX_CONFIG}" ]] || die "主配置不存在：${VECTORFLUX_CONFIG}"
  grep -Eq "main_config:[[:space:]]*[\"']?\.\./config/vectorflux_common/a2/app2\.yaml" "${VECTORFLUX_CONFIG}" ||
    die "主配置不是 A2 app2.yaml"
  python3 -m py_compile "${APPLY_POLICY}"
  verify_policy
  wait_for_pnc || die "PNC 进程未运行"
  source_ros
  command -v ros2 >/dev/null 2>&1 || die "ROS2 命令不可用"
  has_global_costmap_topic || die "未发现 /pnc/global_costmap（或 AIMDK 包装话题）"
  echo "窄通道平衡避障策略验收通过"
  echo "注意：该策略调整 PNC 膨胀和轨迹 clearance，未关闭 footprint 或碰撞检查"
}

rollback() {
  require_root
  local backup="${1:-}"
  [[ -d "${backup}" ]] || die "用法：sudo ${0} rollback ${BACKUP_ROOT}/时间戳"
  [[ -f "${backup}/app2.yaml" && -f "${backup}/mppi_controller.yaml" ]] || die "备份不完整：${backup}"
  cp -a "${backup}/app2.yaml" "${APP2_CONFIG}"
  cp -a "${backup}/mppi_controller.yaml" "${MPPI_CONFIG}"
  restart_pnc
  echo "已回滚 PNC 避障策略：${backup}"
}

backups() {
  find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort || true
}

logs() {
  local log_root="${PNC_LOG_ROOT:-/agibot/log/pnc/aimrt_pnc}"
  [[ -d "${log_root}" ]] || die "日志目录不存在：${log_root}"
  local latest
  latest="$(find "${log_root}" -type f -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2- || true)"
  [[ -n "${latest}" ]] || die "没有 PNC 日志"
  echo "日志：${latest}"
  tail -n 200 "${latest}"
}

case "${1:-status}" in
  status) status ;;
  doctor) doctor ;;
  restart) restart_pnc ;;
  rollback) rollback "${2:-}" ;;
  backups) backups ;;
  logs) logs ;;
  *) die "用法：${0} status|doctor|restart|rollback BACKUP_DIR|backups|logs" ;;
esac
