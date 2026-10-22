#!/usr/bin/env bash
set -Eeuo pipefail
umask 0027

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=control.sh
source "${PACKAGE_DIR}/control.sh"

FILES=(
  lingxi_nav_bridge.py
  motion_option_policy.py
  sdk_motion_controller.py
  navigation_client.py
  pnc_response.py
  voice_preset_motion.py
  point_catalog.py
)
NO_RESTART=false
REQUESTED_NAV_DIR=""

usage() {
  cat <<'EOF'
用法：
  sudo bash ./install.sh
  sudo bash ./install.sh --no-restart
  sudo bash ./install.sh --nav-dir /实际/navigation/robot_local_navigation_v3

本包只更新连续导航和语音预置动作相关的 7 个 Python 文件，不重新部署 AIMDK、地图、灵心、
知识库或灵创资源。默认只重启 lingxi_nav_bridge。
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-restart) NO_RESTART=true; shift ;;
    --nav-dir) REQUESTED_NAV_DIR="${2:-}"; [[ -n "${REQUESTED_NAV_DIR}" ]] || die "--nav-dir 缺少目录"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数：$1" ;;
  esac
done

require_root
if [[ "${SPARKROBOT_FIX_TEST_MODE:-false}" != "true" ]]; then
  case "$(uname -m)" in
    aarch64|arm64) ;;
    *) die "此增量包只允许安装到 ARM64 机器人" ;;
  esac
fi

verify_package_manifest "${PACKAGE_DIR}/manifest.json" "${PACKAGE_DIR}"
if [[ -n "${REQUESTED_NAV_DIR}" ]]; then
  export NAV_DIR="${REQUESTED_NAV_DIR}"
fi
NAV_DIR="$(resolve_nav_dir)"
export NAV_DIR

for file in "${FILES[@]}"; do
  [[ -f "${PACKAGE_DIR}/payload/${file}" ]] || die "安装包缺少 payload/${file}"
done
for file in lingxi_nav_bridge.py motion_option_policy.py sdk_motion_controller.py navigation_client.py; do
  [[ -f "${NAV_DIR}/${file}" ]] || die "正式导航目录缺少 ${file}，请先安装完整导航版本"
done

grep -q '_begin_linkcraft_or_advance' "${NAV_DIR}/lingxi_nav_bridge.py" \
  || die "目标导航版本过旧或不兼容：缺少灵创讲解编排接口"
grep -q '_stop_arrival_motion' "${NAV_DIR}/lingxi_nav_bridge.py" \
  || die "目标导航版本不兼容：缺少到点动作控制接口"
grep -q 'class SdkMotionController' "${NAV_DIR}/sdk_motion_controller.py" \
  || die "目标导航版本不兼容：缺少 SdkMotionController"
grep -q 'class NavigationClient' "${NAV_DIR}/navigation_client.py" \
  || die "目标导航版本不兼容：缺少 NavigationClient"

python3 - "${PACKAGE_DIR}/payload" <<'PY'
import pathlib
import py_compile
import sys
import tempfile

root = pathlib.Path(sys.argv[1])
with tempfile.TemporaryDirectory(prefix="lingxi-nav-fix-payload-") as temp:
    for index, path in enumerate(sorted(root.glob("*.py"))):
        py_compile.compile(str(path), cfile=str(pathlib.Path(temp) / f"{index}.pyc"), doraise=True)
print("补丁 Python 语法检查通过")
PY

install -d -m 0700 "${BACKUP_ROOT}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$(mktemp -d "${BACKUP_ROOT}/${TIMESTAMP}.XXXXXX")"
printf '%s\n' "${NAV_DIR}" > "${BACKUP_DIR}/nav-dir.txt"
printf 'fix_version=%s\ninstalled_at=%s\n' "${FIX_VERSION}" "${TIMESTAMP}" > "${BACKUP_DIR}/metadata.txt"
for file in "${FILES[@]}"; do
  if [[ -f "${NAV_DIR}/${file}" ]]; then
    printf 'true\n' > "${BACKUP_DIR}/existed-${file}"
    cp -a "${NAV_DIR}/${file}" "${BACKUP_DIR}/${file}"
  else
    printf 'false\n' > "${BACKUP_DIR}/existed-${file}"
  fi
done
for file in installed-manifest.json release-info.txt; do
  if [[ -f "${FIX_STATE_ROOT}/${file}" ]]; then
    printf 'true\n' > "${BACKUP_DIR}/existed-state-${file}"
    cp -a "${FIX_STATE_ROOT}/${file}" "${BACKUP_DIR}/state-${file}"
  else
    printf 'false\n' > "${BACKUP_DIR}/existed-state-${file}"
  fi
done

STAGING_DIR="$(mktemp -d "${NAV_DIR}/.consecutive-nav-fix-stage.XXXXXX")"
CHANGES_APPLIED=false
cleanup() {
  rm -rf -- "${STAGING_DIR:-}" 2>/dev/null || true
  rm -f -- \
    "${FIX_STATE_ROOT}/.installed-manifest.json.$$" \
    "${FIX_STATE_ROOT}/.release-info.txt.$$" 2>/dev/null || true
}
rollback_on_error() {
  local code=$?
  trap - ERR
  set +e
  if [[ "${CHANGES_APPLIED}" == true ]]; then
    echo "安装失败，正在自动恢复原文件：${BACKUP_DIR}" >&2
    restore_backup_files "${BACKUP_DIR}" >&2
    restore_install_state_from_backup "${BACKUP_DIR}" >&2
    if [[ "${NO_RESTART}" != true ]]; then
      restart_bridge >&2 || echo "原文件已恢复，但旧 bridge 自动启动失败，请执行正式导航重启命令" >&2
    fi
  fi
  cleanup
  exit "${code}"
}
trap cleanup EXIT
trap rollback_on_error ERR

REFERENCE="${NAV_DIR}/lingxi_nav_bridge.py"
for file in "${FILES[@]}"; do
  target="${NAV_DIR}/${file}"
  mode="0644"
  [[ -e "${target}" ]] && mode="$(stat -c '%a' "${target}")"
  install -m "${mode}" "${PACKAGE_DIR}/payload/${file}" "${STAGING_DIR}/${file}"
  if [[ "${SPARKROBOT_FIX_TEST_MODE:-false}" != "true" ]]; then
    chown --reference="${REFERENCE}" "${STAGING_DIR}/${file}"
  fi
done

CHANGES_APPLIED=true
for file in "${FILES[@]}"; do
  mv -f "${STAGING_DIR}/${file}" "${NAV_DIR}/${file}"
done

# 1.0.0 could replace the product's sparkrobot-nav group with the old bridge
# process primary group. Repair the shared runtime directories even when the
# caller requested --no-restart so the website can start every service again.
repair_runtime_permissions

if [[ "${NO_RESTART}" != true ]]; then
  restart_bridge
else
  echo "已按 --no-restart 跳过重启；新代码会在下次重启导航 bridge 后生效"
fi

install -d -m 0755 "${FIX_STATE_ROOT}"
install -m 0644 "${PACKAGE_DIR}/manifest.json" "${FIX_STATE_ROOT}/.installed-manifest.json.$$"
install -m 0644 "${PACKAGE_DIR}/release-info.txt" "${FIX_STATE_ROOT}/.release-info.txt.$$"
mv -f "${FIX_STATE_ROOT}/.installed-manifest.json.$$" "${FIX_STATE_ROOT}/installed-manifest.json"
mv -f "${FIX_STATE_ROOT}/.release-info.txt.$$" "${FIX_STATE_ROOT}/release-info.txt"
if [[ "${SPARKROBOT_FIX_TEST_MODE:-false}" != "true" ]]; then
  install -m 0755 "${PACKAGE_DIR}/control.sh" /usr/local/sbin/sparkrobot-lingxi-consecutive-nav-fix
fi
CHANGES_APPLIED=false
trap - ERR

echo "连续导航卡住修复已安装：${FIX_VERSION}"
echo "原文件备份：${BACKUP_DIR}"
echo "本次未修改 AIMDK、语音配置、灵心、知识库、地图或灵创资源。"
echo "离线检查命令：sudo sparkrobot-lingxi-consecutive-nav-fix doctor"
