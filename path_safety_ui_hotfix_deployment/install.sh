#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOTFIX_ROOT="${LINGXI_HOTFIX_ROOT:-}"
TEST_MODE="${LINGXI_HOTFIX_TEST_MODE:-false}"
PRODUCT_ROOT="${HOTFIX_ROOT}/opt/sparkrobot/lingxi-nav/current"
EXPECTED_RELEASE="${HOTFIX_ROOT}/opt/sparkrobot/lingxi-nav/releases/1.0.8-20261023-r16"
BACKUP_ROOT="${HOTFIX_ROOT}/var/lib/sparkrobot/lingxi-nav/path-safety-ui-hotfix-backups"
MANIFEST_TARGET="${PRODUCT_ROOT}/payload-manifest.json"
VERIFY_PAYLOAD="${PRODUCT_ROOT}/scripts/verify_payload.py"
CONSOLE_SERVICE="lingxi-nav-console.service"
BRIDGE_STATUS_URL="http://127.0.0.1:18082/nav/status"
RUNTIME_GROUP="sparkrobot-nav"

declare -A BASELINE_SHA256=(
  [console/dist/app.js]="27f249eb03e35a1a9dfa22cb5f1daed259c045d3d3296b5abbd6a9fe1b777185"
  [console/dist/robotClient.js]="a4b41a685205f345b90d8a8fce288b3ad158d3d0b9d1304fe3f9ef218bd20e79"
  [console/dist/public/app.js]="55d7154d8877784edbc901a2cbb8d0a84a030eec4edfab3d831999eed45f9f9a"
  [console/dist/public/index.html]="a689217542a93e14d210ded880d08601e5463bf9825c6c73a108866ff73298d0"
  [console/dist/public/styles.css]="3dad5f4344538fbb14b3e54ef60bcdc5acf8eee7fa690a4548060597ff04938a"
)

die() { echo "失败：$*" >&2; exit 1; }
need_root() {
  if [[ "${TEST_MODE}" == true ]]; then
    [[ -n "${HOTFIX_ROOT}" ]] || die "测试模式必须指定隔离根目录"
    return
  fi
  [[ "${EUID}" -eq 0 ]] || die "请使用 sudo 运行"
  [[ "$(uname -m)" == aarch64 || "$(uname -m)" == arm64 ]] || die "只支持 ARM64 机器人"
}
systemctl_do() {
  if [[ "${TEST_MODE}" == true ]]; then
    local state="${HOTFIX_ROOT}/var/lib/sparkrobot/lingxi-nav/console-active"
    case "$1" in
      stop) rm -f -- "${state}" ;;
      restart) install -d "$(dirname "${state}")"; touch "${state}" ;;
      is-active) [[ -f "${state}" ]] ;;
      *) return 0 ;;
    esac
  else
    systemctl "$@"
  fi
}

changed_paths() { printf '%s\n' "${!BASELINE_SHA256[@]}" | LC_ALL=C sort; }

verify_package() {
  [[ -f "${PACKAGE_DIR}/SHA256SUMS.txt" ]] || die "缺少热修完整性清单"
  (cd "${PACKAGE_DIR}" && sha256sum -c -- SHA256SUMS.txt >/dev/null) || die "热修包校验失败"
  [[ -f "${PACKAGE_DIR}/payload/SHA256SUMS.txt" ]] || die "缺少运行文件清单"
  (cd "${PACKAGE_DIR}/payload" && sha256sum -c -- SHA256SUMS.txt >/dev/null) || die "运行文件校验失败"
}

assert_navigation_idle() {
  local status
  if [[ "${TEST_MODE}" == true ]]; then
    status="${LINGXI_HOTFIX_TEST_NAV_STATUS:-}"
    if [[ -z "${status}" ]]; then
      status='{"navigation":{"status":"idle"},"task":{"active":false},"auto_return":{"state":"idle"}}'
    fi
  else
    status="$(curl -fsS --max-time 2 "${BRIDGE_STATUS_URL}")" || { echo "无法读取导航状态；为避免打断机器人，停止热修" >&2; return 1; }
  fi
  python3 -c 'import json,sys; p=json.loads(sys.argv[1]); nav=p.get("navigation",{}); task=p.get("task",{}); auto=p.get("auto_return",{}); ok=(nav.get("status")=="idle" and not task.get("active", True) and auto.get("state","unknown")=="idle"); print("navigation="+str(nav.get("status"))+" task_active="+str(task.get("active"))+" auto_return="+str(auto.get("state"))); sys.exit(0 if ok else 1)' "${status}" \
    || { echo "导航、讲解或自动返航未处于空闲状态；停止当前任务并等待返航流程结束后重试" >&2; return 1; }
}

assert_console_active() {
  systemctl_do is-active --quiet "${CONSOLE_SERVICE}" \
    || { echo "控制台服务当前未运行；拒绝改变其原有服务状态" >&2; return 1; }
}

verify_baseline() {
  for relative in "${!BASELINE_SHA256[@]}"; do
    local target="${PRODUCT_ROOT}/${relative}"
    [[ -f "${target}" ]] || die "R16 基线文件缺失：${relative}"
    local actual
    actual="$(sha256sum "${target}" | awk '{print $1}')"
    [[ "${actual}" == "${BASELINE_SHA256[${relative}]}" ]] \
      || die "当前文件不是精确 R16 基线：${relative}（SHA-256=${actual}）。拒绝覆盖其他版本/热修"
  done
}

verify_backup() {
  local backup="$1" relative
  [[ -f "${backup}/payload-manifest.json" ]] || die "热修备份缺少 payload manifest"
  while IFS= read -r relative; do
    [[ -f "${backup}/${relative}" ]] || die "热修备份缺少文件：${relative}"
  done < <(changed_paths)
}

install_file() {
  local source="$1" target="$2" mode="$3"
  local temporary="${target}.path-safety-stage.$$"
  if [[ "${TEST_MODE}" == true ]]; then
    install -m "${mode}" "${source}" "${temporary}"
  else
    install -o root -g "${RUNTIME_GROUP}" -m "${mode}" "${source}" "${temporary}"
  fi
  mv -f -- "${temporary}" "${target}"
}

update_manifest() {
  python3 "${PACKAGE_DIR}/update_payload_manifest.py" --root "${PRODUCT_ROOT}" --manifest "${MANIFEST_TARGET}"
  if [[ "${TEST_MODE}" != true ]]; then
    chown root:"${RUNTIME_GROUP}" "${MANIFEST_TARGET}"
    chmod 0640 "${MANIFEST_TARGET}"
  fi
}

restart_and_check() {
  systemctl_do restart "${CONSOLE_SERVICE}" || return 1
  systemctl_do is-active --quiet "${CONSOLE_SERVICE}" || return 1
  if [[ "${TEST_MODE}" != true ]]; then
    python3 "${VERIFY_PAYLOAD}" --root "${PRODUCT_ROOT}" --manifest "${MANIFEST_TARGET}" || return 1
    curl -fsS --max-time 10 http://127.0.0.1:3000/health >/dev/null || return 1
    curl -fsS --max-time 5 http://127.0.0.1:8002/api/settings >/dev/null || return 1
  fi
}

backup_files() {
  local backup="$1" relative
  install -d -m 0700 "${backup}"
  while IFS= read -r relative; do
    mkdir -p "${backup}/$(dirname "${relative}")"
    cp -a "${PRODUCT_ROOT}/${relative}" "${backup}/${relative}"
  done < <(changed_paths)
  cp -a "${MANIFEST_TARGET}" "${backup}/payload-manifest.json"
  chmod 0700 "${backup}"
}

restore_backup() {
  local backup="$1" relative
  [[ -f "${backup}/payload-manifest.json" ]] || die "热修备份缺少 payload manifest"
  while IFS= read -r relative; do
    [[ -f "${backup}/${relative}" ]] || die "热修备份缺少文件：${relative}"
    install_file "${backup}/${relative}" "${PRODUCT_ROOT}/${relative}" 0640
  done < <(changed_paths)
  if [[ "${TEST_MODE}" == true ]]; then
    install -m 0644 "${backup}/payload-manifest.json" "${MANIFEST_TARGET}.rollback.$$"
  else
    install -o root -g "${RUNTIME_GROUP}" -m 0640 "${backup}/payload-manifest.json" "${MANIFEST_TARGET}.rollback.$$"
  fi
  mv -f -- "${MANIFEST_TARGET}.rollback.$$" "${MANIFEST_TARGET}"
}

install_hotfix() {
  need_root
  verify_package
  [[ -d "${EXPECTED_RELEASE}" && "$(readlink -f -- "${PRODUCT_ROOT}")" == "$(readlink -f -- "${EXPECTED_RELEASE}")" ]] \
    || die "当前链接不是 R16 发布目录，拒绝安装"
  assert_console_active || die "控制台未处于活动状态，未修改任何文件"
  assert_navigation_idle || die "导航未确认空闲，未修改任何文件"
  verify_baseline
  [[ -f "${MANIFEST_TARGET}" && -f "${VERIFY_PAYLOAD}" ]] \
    || die "当前 R16 安装不完整，缺少 payload 清单或校验脚本"

  local stamp backup relative failed=0
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup="${BACKUP_ROOT}/${stamp}"
  [[ ! -e "${backup}" ]] || die "同秒热修备份已存在；稍后重试以避免覆盖既有回滚点"
  backup_files "${backup}"
  systemctl_do stop "${CONSOLE_SERVICE}"

  while IFS= read -r relative; do
    install_file "${PACKAGE_DIR}/payload/${relative}" "${PRODUCT_ROOT}/${relative}" 0640 || { failed=1; break; }
  done < <(changed_paths)
  if [[ "${failed}" -eq 0 ]]; then update_manifest || failed=1; fi
  if [[ "${failed}" -eq 0 ]]; then assert_navigation_idle || failed=1; fi
  if [[ "${failed}" -eq 0 ]]; then
    restart_and_check || failed=1
  fi

  if [[ "${failed}" -ne 0 ]]; then
    echo "验收失败，恢复原控制台文件和清单..." >&2
    systemctl_do stop "${CONSOLE_SERVICE}" 2>/dev/null || true
    restore_backup "${backup}"
    systemctl_do restart "${CONSOLE_SERVICE}" || true
    die "热修未通过安装验收，已回滚；备份：${backup}"
  fi

  echo "R16 点位页全局路径安全设置已安装。"
  echo "备份：${backup}"
  echo "点位编辑页的 task_type 下方可填写机器人实测外接半径和额外障碍净空。"
  echo "配置保持全局 path_safety；未实测填写的半径仍会导致安全拒绝。"
}

rollback_hotfix() {
  need_root
  local backup="${1:-}" resolved root
  [[ -d "${EXPECTED_RELEASE}" && "$(readlink -f -- "${PRODUCT_ROOT}")" == "$(readlink -f -- "${EXPECTED_RELEASE}")" ]] \
    || die "当前链接不是 R16 发布目录，拒绝回滚"
  [[ -n "${backup}" ]] || die "用法：sudo ./install.sh rollback BACKUP_DIR"
  [[ -d "${backup}" && -d "${BACKUP_ROOT}" ]] || die "热修备份目录不存在"
  resolved="$(readlink -f -- "${backup}")"
  root="$(readlink -f -- "${BACKUP_ROOT}")"
  [[ "$(dirname "${resolved}")" == "${root}" ]] || die "回滚目录必须是热修备份目录的直接子目录"
  [[ "$(basename "${resolved}")" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || die "无效的备份目录名称"
  verify_backup "${resolved}"
  assert_console_active || die "控制台未处于活动状态，未执行回滚"
  assert_navigation_idle || die "导航未确认空闲，未执行回滚"
  systemctl_do stop "${CONSOLE_SERVICE}"
  restore_backup "${resolved}"
  restart_and_check || die "已还原文件但验收失败，请检查系统日志"
  echo "R16 点位页热修已回滚：${resolved}"
}

case "${1:-install}" in
  install) install_hotfix ;;
  rollback) shift; rollback_hotfix "$@" ;;
  -h|--help) echo "用法：sudo ./install.sh [install | rollback BACKUP_DIR]" ;;
  *) die "未知操作：${1}" ;;
esac
