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
PRESERVED_JAVA_JAR="java/robot-local-navigation-java-all.jar"
PRESERVED_JAVA_JAR_SHA256=""
PRESERVED_JAVA_JAR_SIZE=""

declare -A BASELINE_SHA256=(
  [console/dist/app.js]="27f249eb03e35a1a9dfa22cb5f1daed259c045d3d3296b5abbd6a9fe1b777185"
  [console/dist/robotClient.js]="a4b41a685205f345b90d8a8fce288b3ad158d3d0b9d1304fe3f9ef218bd20e79"
  [console/dist/public/app.js]="55d7154d8877784edbc901a2cbb8d0a84a030eec4edfab3d831999eed45f9f9a"
  [console/dist/public/index.html]="a689217542a93e14d210ded880d08601e5463bf9825c6c73a108866ff73298d0"
  [console/dist/public/styles.css]="3dad5f4344538fbb14b3e54ef60bcdc5acf8eee7fa690a4548060597ff04938a"
)
declare -A PREVIOUS_HOTFIX_SHA256=(
  [console/dist/app.js]="89ab364e75169095653c5cd5852c4159cd878f13a0d0279e7f65a56033851b1c"
  [console/dist/robotClient.js]="d0b7e0f9101fe6d08b93f1fc7c71494a5c08c612250a6ea827c5bbc4acdc6ee0"
  [console/dist/public/app.js]="8213e2676893ba1c1a12ccb36e515a95a1a27f7e5705a703f5b89d05dc41b3f4"
  [console/dist/public/index.html]="6cd122aa5a0aaceb09e34be34eebea0f5952b5b3691f058bfaa8960ecc2b8fa6"
  [console/dist/public/styles.css]="8b70fb1f33b5b7d7110b8bf27f55a42d0122e6766c1260a6541296c05738094f"
)
declare -A R3_HOTFIX_SHA256=(
  [console/dist/app.js]="89ab364e75169095653c5cd5852c4159cd878f13a0d0279e7f65a56033851b1c"
  [console/dist/robotClient.js]="d0b7e0f9101fe6d08b93f1fc7c71494a5c08c612250a6ea827c5bbc4acdc6ee0"
  [console/dist/public/app.js]="c476dec96cc2b9ce5503231768b24a9c518a47951b7e284b1256bff826f5be1b"
  [console/dist/public/index.html]="5c69982f70226cab13bc0215da91eb52415408c713823935980e51984db71721"
  [console/dist/public/styles.css]="8b70fb1f33b5b7d7110b8bf27f55a42d0122e6766c1260a6541296c05738094f"
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
      stop)
        rm -f -- "${state}"
        ;;
      restart) install -d "$(dirname "${state}")"; touch "${state}" ;;
      is-active) [[ -f "${state}" ]] ;;
      *) return 0 ;;
    esac
  else
    systemctl "$@"
  fi
}

navigation_control_do() {
  local action="$1"
  if [[ "${TEST_MODE}" == true ]]; then
    local state="${HOTFIX_ROOT}/var/lib/sparkrobot/lingxi-nav/navigation-stack-active"
    case "${action}" in
      stop) rm -f -- "${state}" ;;
      start)
        if [[ "${LINGXI_HOTFIX_TEST_NAV_START_FAIL_ONCE:-false}" == true ]]; then
          local failed_once="${state}.start-failed-once"
          if [[ ! -f "${failed_once}" ]]; then
            install -d "$(dirname "${state}")"
            touch "${failed_once}"
            return 1
          fi
        fi
        [[ "${LINGXI_HOTFIX_TEST_NAV_START_FAIL:-false}" != true ]] || return 1
        install -d "$(dirname "${state}")"
        touch "${state}"
        ;;
      status)
        if [[ -f "${state}" ]]; then
          printf '%s\n' 'lingxi_nav_bridge=running' 'map_label_server_v3=running' 'voice_nav_gateway=running'
          return 0
        fi
        printf '%s\n' 'lingxi_nav_bridge=stopped' 'map_label_server_v3=stopped' 'voice_nav_gateway=stopped'
        return 1
        ;;
      *) return 1 ;;
    esac
  else
    runuser -u agi -- "${PRODUCT_ROOT}/scripts/navigation_control.sh" "${action}"
  fi
}

changed_paths() { printf '%s\n' "${!BASELINE_SHA256[@]}" | LC_ALL=C sort; }

capture_preserved_java_jar() {
  local jar="${PRODUCT_ROOT}/${PRESERVED_JAVA_JAR}"
  [[ -f "${jar}" ]] || die "Java Agent JAR missing; hotfix will not replace it"
  python3 - "${jar}" <<'PY' || die "Java Agent JAR is not a valid ZIP archive; hotfix will not replace it"
import sys
import zipfile
if not zipfile.is_zipfile(sys.argv[1]):
    raise SystemExit(1)
with zipfile.ZipFile(sys.argv[1]) as archive:
    if archive.testzip() is not None:
        raise SystemExit(1)
PY
  PRESERVED_JAVA_JAR_SHA256="$(sha256sum "${jar}" | awk '{print $1}')"
  PRESERVED_JAVA_JAR_SIZE="$(stat -c '%s' "${jar}")"
}

assert_preserved_java_jar_unchanged() {
  local jar="${PRODUCT_ROOT}/${PRESERVED_JAVA_JAR}" actual_hash actual_size
  [[ -f "${jar}" ]] || { echo "Java Agent JAR disappeared during hotfix" >&2; return 1; }
  actual_hash="$(sha256sum "${jar}" | awk '{print $1}')"
  actual_size="$(stat -c '%s' "${jar}")"
  [[ "${actual_hash}" == "${PRESERVED_JAVA_JAR_SHA256}" && "${actual_size}" == "${PRESERVED_JAVA_JAR_SIZE}" ]] \
    || { echo "Java Agent JAR changed during hotfix; refusing the changed file" >&2; return 1; }
}

verify_payload_except_preserved_java_jar() {
  python3 - "${PRODUCT_ROOT}" "${MANIFEST_TARGET}" "${PRESERVED_JAVA_JAR}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath

root = Path(sys.argv[1]).resolve()
manifest_path = Path(sys.argv[2])
preserved = sys.argv[3]
payload = json.loads(manifest_path.read_text(encoding="utf-8"))
found_preserved = False
failures = []
for entry in payload.get("files", []):
    relative = PurePosixPath(str(entry.get("path", "")))
    if relative.is_absolute() or ".." in relative.parts:
        failures.append(f"invalid path: {relative}")
        continue
    name = relative.as_posix()
    if name == preserved:
        found_preserved = True
        continue
    target = root.joinpath(*relative.parts)
    if not target.is_file():
        failures.append(f"missing: {name}")
        continue
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if target.stat().st_size != int(entry["size"]) or digest != entry["sha256"]:
        failures.append(f"modified: {name}")
if not found_preserved:
    failures.append(f"manifest entry missing: {preserved}")
if failures:
    raise SystemExit("\n".join(failures))
print("All payload files except the preserved Java Agent JAR match the existing manifest.")
PY
}

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
      [[ -f "${HOTFIX_ROOT}/var/lib/sparkrobot/lingxi-nav/navigation-stack-active" ]] \
        || { echo "test navigation stack is stopped" >&2; return 1; }
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
  local relative actual matches_original=1 matches_previous=1 matches_r3=1
  for relative in "${!BASELINE_SHA256[@]}"; do
    local target="${PRODUCT_ROOT}/${relative}"
    [[ -f "${target}" ]] || die "R16 基线文件缺失：${relative}"
    actual="$(sha256sum "${target}" | awk '{print $1}')"
    [[ "${actual}" == "${BASELINE_SHA256[${relative}]}" ]] || matches_original=0
    [[ "${actual}" == "${PREVIOUS_HOTFIX_SHA256[${relative}]}" ]] || matches_previous=0
    [[ "${actual}" == "${R3_HOTFIX_SHA256[${relative}]}" ]] || matches_r3=0
  done
  [[ "${matches_original}" -eq 1 || "${matches_previous}" -eq 1 || "${matches_r3}" -eq 1 ]] \
    || die "当前控制台既不是精确 R16、首版热修，也不是 R3 热修；拒绝覆盖混合、其他版本或未知修改"
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

wait_for_http() {
  local url="$1" attempts="${2:-30}"
  for _ in $(seq 1 "${attempts}"); do
    if curl -fsS --max-time 2 "${url}" >/dev/null; then return 0; fi
    sleep 0.5
  done
  return 1
}

ensure_navigation_stack_online() {
  if [[ "${TEST_MODE}" == true ]]; then
    if [[ -f "${HOTFIX_ROOT}/var/lib/sparkrobot/lingxi-nav/navigation-stack-active" ]]; then
      return 0
    fi
    local status
    status="$(navigation_control_do status 2>&1 || true)"
    [[ "${status}" == $'lingxi_nav_bridge=stopped\nmap_label_server_v3=stopped\nvoice_nav_gateway=stopped' ]] \
      || die "测试导航栈状态不完整；拒绝自动启动"
    navigation_control_do start || die "测试导航栈无法启动；尚未修改控制台文件"
    return 0
  fi

  if curl -fsS --max-time 2 "${BRIDGE_STATUS_URL}" >/dev/null; then
    return 0
  fi

  local status
  status="$(navigation_control_do status 2>&1 || true)"
  if [[ "${status}" != $'lingxi_nav_bridge=stopped\nmap_label_server_v3=stopped\nvoice_nav_gateway=stopped' ]]; then
    die "导航状态接口不可用，且导航栈并非全部停止；为避免中断服务，不自动重启。先恢复 3000/导航服务后重试。"
  fi

  echo "检测到 bridge、8002 地图服务和导航语音 gateway 均已停止；先恢复服务以确认导航空闲..."
  navigation_control_do start || die "R16 导航栈启动失败；控制台文件未修改，请检查导航启动日志"
  wait_for_http "${BRIDGE_STATUS_URL}" 30 \
    || die "导航栈已请求启动但仍无法读取 /nav/status；控制台文件未修改，请检查导航日志"
}

restart_console_and_check() {
  systemctl_do restart "${CONSOLE_SERVICE}" || return 1
  systemctl_do is-active --quiet "${CONSOLE_SERVICE}" || return 1
  if [[ "${TEST_MODE}" != true ]]; then
    assert_preserved_java_jar_unchanged || return 1
    python3 "${VERIFY_PAYLOAD}" --root "${PRODUCT_ROOT}" --manifest "${MANIFEST_TARGET}" || return 1
    wait_for_http http://127.0.0.1:3000/health 30 || return 1
  fi
}

start_navigation_stack() {
  navigation_control_do start || return 1
  if [[ "${TEST_MODE}" != true ]]; then
    wait_for_http http://127.0.0.1:18082/nav/status 30 || return 1
    wait_for_http http://127.0.0.1:8002/api/settings 30 || return 1
    wait_for_http http://127.0.0.1:18083/nav/status 30 || return 1
    assert_navigation_idle || return 1
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
  update_manifest
  assert_preserved_java_jar_unchanged
}

install_hotfix() {
  need_root
  verify_package
  [[ -d "${EXPECTED_RELEASE}" && "$(readlink -f -- "${PRODUCT_ROOT}")" == "$(readlink -f -- "${EXPECTED_RELEASE}")" ]] \
    || die "当前链接不是 R16 发布目录，拒绝安装"
  assert_console_active || die "控制台未处于活动状态，未修改任何文件"
  verify_baseline
  [[ -f "${MANIFEST_TARGET}" && -f "${VERIFY_PAYLOAD}" ]] \
    || die "当前 R16 安装不完整，缺少 payload 清单或校验脚本"
  capture_preserved_java_jar
  verify_payload_except_preserved_java_jar \
    || die "除现场 Java Agent JAR 外，R16 payload 还有其他完整性差异；未修改任何控制台文件"
  ensure_navigation_stack_online
  assert_navigation_idle || die "导航未确认空闲，未修改任何文件"

  local stamp backup relative failed=0
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup="${BACKUP_ROOT}/${stamp}"
  [[ ! -e "${backup}" ]] || die "同秒热修备份已存在；稍后重试以避免覆盖既有回滚点"
  backup_files "${backup}"
  navigation_control_do stop || die "无法按 R16 服务脚本停止导航栈；控制台文件未修改，检查 navigation_control.sh 后重试"
  systemctl_do stop "${CONSOLE_SERVICE}"

  while IFS= read -r relative; do
    install_file "${PACKAGE_DIR}/payload/${relative}" "${PRODUCT_ROOT}/${relative}" 0640 || { failed=1; break; }
  done < <(changed_paths)
  if [[ "${failed}" -eq 0 ]]; then update_manifest || failed=1; fi
  if [[ "${failed}" -eq 0 ]]; then restart_console_and_check || failed=1; fi
  if [[ "${failed}" -eq 0 ]]; then start_navigation_stack || failed=1; fi

  if [[ "${failed}" -ne 0 ]]; then
    echo "验收失败，恢复原控制台文件和清单..." >&2
    systemctl_do stop "${CONSOLE_SERVICE}" 2>/dev/null || true
    navigation_control_do stop >/dev/null 2>&1 || true
    restore_backup "${backup}"
    local rollback_ok=1
    restart_console_and_check || rollback_ok=0
    start_navigation_stack || rollback_ok=0
    if [[ "${rollback_ok}" -eq 1 ]]; then
      die "热修验收失败，控制台已回滚且 R16 导航栈已恢复；备份：${backup}"
    fi
    die "热修验收失败，原控制台文件已回滚，但服务恢复未通过；请检查控制台和导航日志；备份：${backup}"
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
  capture_preserved_java_jar
  assert_navigation_idle || die "导航未确认空闲，未执行回滚"
  ensure_navigation_stack_online
  assert_navigation_idle || die "导航未确认空闲，未执行回滚"
  navigation_control_do stop || die "无法按 R16 服务脚本停止导航栈；未执行回滚"
  systemctl_do stop "${CONSOLE_SERVICE}"
  restore_backup "${resolved}"
  restart_console_and_check || die "已还原文件但 3000 控制台验收失败，请检查系统日志"
  start_navigation_stack || die "已还原控制台文件，但 R16 导航栈恢复验收失败"
  echo "R16 点位页热修已回滚：${resolved}"
}

case "${1:-install}" in
  install) install_hotfix ;;
  rollback) shift; rollback_hotfix "$@" ;;
  -h|--help) echo "用法：sudo ./install.sh [install | rollback BACKUP_DIR]" ;;
  *) die "未知操作：${1}" ;;
esac
