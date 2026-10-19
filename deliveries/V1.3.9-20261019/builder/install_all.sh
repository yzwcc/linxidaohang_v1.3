#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="1.3.9-complete-20261019-r5"
NAV_VERSION="1.0.23-20261019"
NAV_NAME="sparkrobot-lingxi-nav-internal-${NAV_VERSION}"
KB_NAME="sparkrobot-lingxi-kb-1.0.2-20260902"
WORK_ROOT="${WORK_ROOT:-/home/run/lingxi-v139-20261019-work}"
EXPECTED_RELEASE="/opt/sparkrobot/lingxi-nav/releases/${NAV_VERSION}"
die() { echo "失败：$*" >&2; exit 1; }
need_run() { [[ "$(id -un)" == run ]] || die "请使用 run 账号执行"; }
need_arm() { case "$(uname -m)" in aarch64|arm64) ;; *) die "需要 ARM64 机器人";; esac; }
verify_bundle() { (cd "${PACKAGE_DIR}" && sha256sum -c SHA256SUMS.txt >/dev/null) || die "完整包校验失败"; }
aimdk_ready() {
  sudo -u agi env -i HOME=/home/agi USER=agi LOGNAME=agi SHELL=/bin/bash \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 \
    bash --noprofile --norc -c '
      set -e; set +u
      source /opt/ros/humble/setup.bash
      source /home/agi/aimdk/install/setup.bash
      python3 -c "import rclpy; import aimdk_msgs.msg"
      test -x /home/agi/aimdk/install/examples/lib/examples/navigation
    ' || die "AIMDK 不完整；请先执行 prepare 和 agi 账号的 01-replace-aimdk.sh"
}
wait_http() {
  local url="$1"
  for attempt in $(seq 1 30); do
    curl -fsS --max-time 2 "${url}" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}
prepare() {
  need_run; need_arm; verify_bundle
  sudo install -o agi -g agi -m 0644 "${PACKAGE_DIR}/components/aimdk-aarch64-1bde262f-artifacts.7z" /home/agi/
  sudo install -o agi -g agi -m 0755 "${PACKAGE_DIR}/scripts/01-replace-aimdk.sh" /home/agi/
  sudo install -o agi -g agi -m 0755 "${PACKAGE_DIR}/scripts/04-check-localization.sh" /home/agi/
  install -m 0755 "${PACKAGE_DIR}/scripts/00-install-environment.sh" /home/run/
  bash /home/run/00-install-environment.sh
  echo "环境准备完成。请使用 agi 执行 bash /home/agi/01-replace-aimdk.sh，再回到 run 执行 install。"
}
install_product() {
  need_run; need_arm; verify_bundle
  local agent_code="" keep_config=false setup_soc2=true skip_pnc=false secrets=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --agent-code) [[ $# -ge 2 ]] || die "--agent-code 缺值"; agent_code="$2"; shift 2;;
      --keep-config) keep_config=true; shift;;
      --skip-soc2-setup) setup_soc2=false; shift;;
      --skip-pnc-policy) skip_pnc=true; shift;;
      *) die "未知参数：$1";;
    esac
  done
  [[ "${agent_code}" =~ ^AI_AGENT_[A-Za-z0-9_-]+$ ]] || die "需要 --agent-code AI_AGENT_xxx"
  aimdk_ready
  local policy_args=()
  [[ "${skip_pnc}" == true ]] && policy_args+=(--skip-pnc-policy)
  sudo python3 "${PACKAGE_DIR}/scripts/release_config.py" preflight "${policy_args[@]}"
  if [[ "${keep_config}" == true ]]; then
    sudo test -s /etc/sparkrobot/lingxi-nav/lingxi-nav.properties || die "没有旧配置，不能 --keep-config"
    sudo test -s /etc/sparkrobot/lingxi-nav/runtime.env || die "旧 runtime.env 缺失"
  fi
  [[ "$(realpath -m "${WORK_ROOT}")" == "${WORK_ROOT}" && "${WORK_ROOT}" == /home/run/* && "${WORK_ROOT}" != /home/run/ ]] \
    || die "WORK_ROOT 必须是 /home/run 下的独立目录"
  # Preserve prior work directories, including interrupted installation evidence.
  [[ ! -e "${WORK_ROOT}" ]] || mv -- "${WORK_ROOT}" "${WORK_ROOT}.previous.$(date -u +%Y%m%dT%H%M%SZ).$$"
  install -d -m 0700 "${WORK_ROOT}/navigation" "${WORK_ROOT}/knowledge"
  tar -xzf "${PACKAGE_DIR}/components/${NAV_NAME}.tar.gz" -C "${WORK_ROOT}/navigation" --no-same-owner
  python3 - "${PACKAGE_DIR}/components/${KB_NAME}.zip" "${WORK_ROOT}/knowledge" <<'PY'
import pathlib, sys, zipfile
root = pathlib.Path(sys.argv[2]).resolve()
with zipfile.ZipFile(sys.argv[1]) as z:
    for n in z.namelist():
        p = (root / n).resolve()
        if root not in p.parents:
            raise SystemExit("知识库归档路径无效")
    z.extractall(root)
PY
  local nav_root="${WORK_ROOT}/navigation/${NAV_NAME}" kb_root="${WORK_ROOT}/knowledge/${KB_NAME}"
  local args=()
  if [[ "${keep_config}" == false ]]; then
    secrets="${nav_root}/secrets.env"
    trap 'rm -f -- "${secrets:-}"' EXIT
    bash "${nav_root}/configure.sh" "${secrets}"
    args+=(--secrets "${secrets}")
  fi
  [[ "${setup_soc2}" == true ]] && args+=(--setup-soc2)
  sudo test ! -d "${EXPECTED_RELEASE}" || args+=(--reinstall)
  sudo systemctl stop lingxi-java-agent-robot.service lingxi-nav-console.service sparkrobot-lingxi-kb.service 2>/dev/null || true
  if sudo test -x /opt/sparkrobot/lingxi-nav/current/scripts/navigation_control.sh; then
    sudo runuser -u agi -- /opt/sparkrobot/lingxi-nav/current/scripts/navigation_control.sh stop
    sudo /opt/sparkrobot/lingxi-nav/current/scripts/robot_voice_bridge_control.sh stop
  fi
  local backup
  backup="$(sudo python3 "${PACKAGE_DIR}/scripts/backup_state.py" backup)"
  echo "完整安装前备份：${backup}"
  trap 'code=$?; rm -f -- "${secrets:-}"; if [[ "${code}" != 0 ]]; then echo "安装未完成。备份：${backup}；按部署说明使用 restore 恢复。服务可能已停止。" >&2; fi' EXIT
  sudo bash "${nav_root}/install.sh" "${args[@]}"
  rm -f -- "${secrets:-}"
  sudo python3 "${PACKAGE_DIR}/scripts/release_config.py" apply "${policy_args[@]}"
  if [[ "${skip_pnc}" == false ]]; then
    sudo bash "${PACKAGE_DIR}/scripts/pnc_policy/control.sh" restart
  fi
  sudo bash "${kb_root}/install.sh" --from-lingxi --agent-code "${agent_code}"
  # Reinstalling the same KB release must reload its process and new credentials.
  sudo systemctl restart sparkrobot-lingxi-kb.service
  sudo install -d -m 0700 /var/lib/sparkrobot/lingxi-complete
  sudo python3 - "${VERSION}" "${NAV_VERSION}" "${skip_pnc}" "${backup}" <<'PY'
import json, sys
from pathlib import Path
Path("/var/lib/sparkrobot/lingxi-complete/installed.json").write_text(
    json.dumps(dict(version=sys.argv[1], navigation_version=sys.argv[2],
                    pnc_policy_skipped=sys.argv[3] == "true", backup=sys.argv[4]), indent=2) + "\n")
PY
  sudo install -m 0755 "${PACKAGE_DIR}/control_all.sh" /usr/local/sbin/sparkrobot-lingxi-complete
  install -m 0755 "${PACKAGE_DIR}/scripts/03-verify-after-web-start.sh" /home/run/
  sudo systemctl restart lingxi-nav-console.service
  sudo sparkrobot-lingxi-internal verify
  [[ "$(sudo readlink -e /opt/sparkrobot/lingxi-nav/current)" == "${EXPECTED_RELEASE}" ]] || die "current 版本错误"
  wait_http http://127.0.0.1:3000/health || die "网页健康检查失败"
  wait_http http://127.0.0.1:18084/health || die "知识库健康检查失败"
  trap - EXIT
  echo "完整包 ${VERSION} 安装完成，所有合并功能已入包。"
  echo "请打开 http://机器人实际IP:3000/console/，点击一键开启全部服务，再执行 sudo sparkrobot-lingxi-complete doctor。"
}
case "${1:---help}" in
  prepare) shift; prepare "$@";;
  install) shift; install_product "$@";;
  preflight)
    shift
    need_run; need_arm; verify_bundle; aimdk_ready
    [[ $# == 0 || ( $# == 1 && "$1" == --skip-pnc-policy ) ]] || die "未知 preflight 参数"
    sudo python3 "${PACKAGE_DIR}/scripts/release_config.py" preflight "$@"
    echo "包、AIMDK 与固件配置预检通过"
    ;;
  doctor) sudo bash "${PACKAGE_DIR}/control_all.sh" doctor;;
  --help|-h)
    echo "用法：bash ./install_all.sh prepare|preflight|doctor"
    echo "      bash ./install_all.sh install --agent-code AI_AGENT_xxx [--keep-config] [--skip-soc2-setup] [--skip-pnc-policy]"
    ;;
  *) die "请执行 bash ./install_all.sh --help";;
esac
