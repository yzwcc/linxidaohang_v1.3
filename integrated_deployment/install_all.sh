#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="v1.3.1-complete-deployment-20261023-r12"
WORK_ROOT="${WORK_ROOT:-/home/run/${VERSION}}"
BASE_ROOT="${WORK_ROOT}/base/lingxi-v1.3.1-full-deployment-20260901"
BASE_ARCHIVE="${PACKAGE_DIR}/components/lingxi-v1.3.1-full-deployment-20260901.zip"
AIMDK_NAME="aimdk-aarch64-1bde262f-artifacts.7z"
NAV_NAME="sparkrobot-lingxi-nav-internal-1.0.7-20260901.tar.gz"
LINKCRAFT_NAME="sparkrobot-lingxi-linkcraft-addon-1.0.3-20260902.tar.gz"
FIX_NAME="sparkrobot-lingxi-consecutive-nav-fix-1.0.3-20261023.tar.gz"
KB_NAME="sparkrobot-lingxi-kb-1.0.7-20261023.zip"

die() { echo "失败：$*" >&2; exit 1; }
need_run() { [[ "$(id -un)" == "run" ]] || die "请使用 run 用户执行此阶段"; }
need_arm64() { [[ "$(uname -m)" == "aarch64" || "$(uname -m)" == "arm64" ]] || die "只能安装到 ARM64 机器人"; }
component() { echo "${PACKAGE_DIR}/components/$1"; }

assert_work_root() {
  [[ "${WORK_ROOT}" == /home/run/* ]] || die "WORK_ROOT 必须位于 /home/run 下：${WORK_ROOT}"
  [[ "${WORK_ROOT}" != "/home/run" && "${WORK_ROOT}" != "/home/run/" ]] \
    || die "拒绝把 WORK_ROOT 指向 /home/run 根目录"
}

verify_aimdk_runtime() {
  sudo -u agi env -i \
    HOME=/home/agi USER=agi LOGNAME=agi SHELL=/bin/bash \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=C.UTF-8 bash --noprofile --norc -c '
set -e
source /opt/ros/humble/setup.bash
test -r /home/agi/aimdk/install/setup.bash
source /home/agi/aimdk/install/setup.bash
python3 -c "import rclpy; import aimdk_msgs.msg"
' >/dev/null 2>&1 || die "AIMDK 尚未替换完成或 aimdk_msgs 不可用；请先使用 agi 执行：cd /home/agi && bash ./01-replace-aimdk.sh"
}

verify_bundle_strict() {
  [[ -f "${PACKAGE_DIR}/SHA256SUMS.txt" ]] || die "missing SHA256SUMS.txt"
  (cd "${PACKAGE_DIR}" && LC_ALL=C sha256sum -c -- SHA256SUMS.txt >/dev/null) \
    || die "complete bundle SHA-256 verification failed; upload the original bundle"
}

safe_extract_zip() {
  local archive="$1" destination="$2"
  python3 - "$archive" "$destination" <<'PY'
import pathlib, sys, zipfile
archive = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2]).resolve()
with zipfile.ZipFile(archive) as zf:
    for info in zf.infolist():
        target = (destination / info.filename).resolve()
        if target != destination and destination not in target.parents:
            raise SystemExit(f"压缩包包含不安全路径：{info.filename}")
    zf.extractall(destination)
PY
}

prepare() {
  need_run; need_arm64; assert_work_root; verify_bundle_strict
  [[ -f "${BASE_ARCHIVE}" ]] || die "缺少基础套件：${BASE_ARCHIVE}"
  for file in "${AIMDK_NAME}" "${NAV_NAME}"; do [[ -f "$(component "${file}")" ]] || die "完整包缺少组件：${file}"; done
  sudo rm -rf -- "${WORK_ROOT}/base"
  install -d -m 0700 "${WORK_ROOT}/base"
  safe_extract_zip "${BASE_ARCHIVE}" "${WORK_ROOT}/base"
  [[ -d "${BASE_ROOT}" ]] || die "基础套件解压目录不正确：${BASE_ROOT}"
  install -d /home/run
  sudo install -d -o agi -g agi /home/agi
  sudo install -o agi -g agi -m 0644 "$(component "${AIMDK_NAME}")" "/home/agi/${AIMDK_NAME}"
  sudo install -o agi -g agi -m 0755 "${BASE_ROOT}/scripts/robot/01-replace-aimdk.sh" /home/agi/01-replace-aimdk.sh
  sudo install -o agi -g agi -m 0755 "${BASE_ROOT}/scripts/robot/04-check-localization.sh" /home/agi/04-check-localization.sh
  install -m 0644 "$(component "${NAV_NAME}")" "/home/run/${NAV_NAME}"
  install -m 0755 "${BASE_ROOT}/scripts/robot/00-install-environment.sh" /home/run/00-install-environment.sh
  install -m 0755 "${BASE_ROOT}/scripts/robot/02-install-lingxi-navigation.sh" /home/run/02-install-lingxi-navigation.sh
  install -m 0755 "${BASE_ROOT}/scripts/robot/03-verify-after-web-start.sh" /home/run/03-verify-after-web-start.sh
  echo "[1/3] 开始安装系统与 AIMDK 编译环境"
  /home/run/00-install-environment.sh
  echo "[通过] 文件已准备。请切换 agi 用户执行：cd /home/agi && bash ./01-replace-aimdk.sh"
}

install_navigation() {
  need_run; need_arm64; assert_work_root; verify_bundle_strict
  verify_aimdk_runtime
  [[ -x /home/run/02-install-lingxi-navigation.sh ]] || die "请先执行 bash ./install_all.sh prepare"
  echo "[2/3] 安装导航主包，请按提示填写灵心和豆包配置"
  bash /home/run/02-install-lingxi-navigation.sh
  rm -f -- /home/run/sparkrobot-lingxi-nav-internal-1.0.7-20260901/secrets.env 2>/dev/null || true
}

extract_addons() {
  assert_work_root
  local staging="${WORK_ROOT}/addons"
  [[ "${staging}" == "${WORK_ROOT}/addons" ]] || die "附加包临时目录不安全：${staging}"
  sudo rm -rf -- "${staging}"
  install -d -m 0700 "${staging}"
  tar -xzf "$(component "${LINKCRAFT_NAME}")" -C "${staging}"
  tar -xzf "$(component "${FIX_NAME}")" -C "${staging}"
  safe_extract_zip "$(component "${KB_NAME}")" "${staging}"
  LINKCRAFT_ROOT="${staging}/sparkrobot-lingxi-linkcraft-addon-1.0.3-20260902"
  FIX_ROOT="${staging}/sparkrobot-lingxi-consecutive-nav-fix-1.0.3-20261023"
  KB_ROOT="${staging}/sparkrobot-lingxi-kb-1.0.7-20261023"
  for path in "${LINKCRAFT_ROOT}" "${FIX_ROOT}" "${KB_ROOT}"; do [[ -d "${path}" ]] || die "附加包解压失败：${path}"; done
}

install_runtime_controls() {
  sudo install -m 0755 "${PACKAGE_DIR}/control_all.sh" /usr/local/sbin/sparkrobot-lingxi-complete
  sudo install -m 0755 "${LINKCRAFT_ROOT}/control.sh" /usr/local/sbin/sparkrobot-lingxi-linkcraft
  sudo install -m 0755 "${FIX_ROOT}/control.sh" /usr/local/sbin/sparkrobot-lingxi-consecutive-nav-fix
  sudo rm -f /usr/local/sbin/sparkrobot-lingxi-pnc-arrival
  sudo ln -s /usr/local/sbin/sparkrobot-lingxi-consecutive-nav-fix /usr/local/sbin/sparkrobot-lingxi-pnc-arrival
}

install_addons() {
  need_run; need_arm64; assert_work_root; verify_bundle_strict
  local agent_code=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --agent-code) agent_code="${2:-}"; shift 2 ;;
      -h|--help) echo "用法：bash ./install_all.sh install-addons --agent-code AI_AGENT_xxx"; return 0 ;;
      *) die "未知参数：$1" ;;
    esac
  done
  [[ "${agent_code}" =~ ^AI_AGENT_[A-Za-z0-9_-]+$ ]] \
    || die "必须指定格式正确的知识库智能体 Code，例如 --agent-code AI_AGENT_f2c382e0"
  verify_aimdk_runtime
  sudo test -f /etc/sparkrobot/lingxi-nav/lingxi-nav.properties || die "导航配置不存在，请先完成 install-navigation"
  extract_addons
  echo "[3/3] 安装灵创动作、表情和舞蹈能力"
  sudo bash "${LINKCRAFT_ROOT}/install.sh" --no-restart
  echo "[3/3] 连续导航修复将在本阶段最后安装"
  # The final fix is installed after LinkCraft so its bridge files cannot be overwritten.
  echo "[3/3] 安装灵心知识库检索和语音接入"
  sudo bash "${KB_ROOT}/install.sh" --from-lingxi --agent-code "${agent_code}"
  sudo bash "${KB_ROOT}/install_voice_agent.sh"
  echo "[3/3] 最后安装连续导航修复并修复运行目录权限"
  sudo bash "${FIX_ROOT}/install.sh"
  install_runtime_controls
  echo "[3/3] 重启网页控制台并确认附加能力已加载"
  sudo systemctl restart lingxi-nav-console.service \
    || die "网页控制台重启失败，请执行 sudo journalctl -u lingxi-nav-console.service -n 100 --no-pager"
  sudo systemctl is-active --quiet lingxi-nav-console.service \
    || die "网页控制台重启后未运行"
  curl -fsS --max-time 10 http://127.0.0.1:3000/health >/dev/null \
    || die "网页控制台健康检查失败"
  echo "完整附加能力安装完成"
}

doctor() {
  need_run; need_arm64
  local failed=0
  [[ -x /home/run/03-verify-after-web-start.sh ]] && /home/run/03-verify-after-web-start.sh || failed=1
  if command -v sparkrobot-lingxi-linkcraft >/dev/null 2>&1; then
    sudo sparkrobot-lingxi-linkcraft status || failed=1
  else
    echo "灵创运维命令不存在" >&2
    failed=1
  fi
  if command -v sparkrobot-lingxi-consecutive-nav-fix >/dev/null 2>&1; then
    sudo sparkrobot-lingxi-consecutive-nav-fix doctor || failed=1
  else
    echo "连续导航修复运维命令不存在" >&2
    failed=1
  fi
  if command -v sparkrobot-lingxi-kb >/dev/null 2>&1; then
    sudo sparkrobot-lingxi-kb doctor || failed=1
  else
    echo "知识库运维命令不存在" >&2
    failed=1
  fi
  [[ "${failed}" -eq 0 ]] || die "验收未全部通过，请查看上面的中文错误和日志"
  echo "完整套件健康检查通过；本命令不会发送导航、动作、重定位或语音指令"
}

case "${1:-}" in
  prepare) shift; [[ $# -eq 0 ]] || die "prepare 不接受参数"; prepare ;;
  install-navigation) shift; [[ $# -eq 0 ]] || die "install-navigation 不接受参数"; install_navigation ;;
  install-addons) shift; install_addons "$@" ;;
  doctor) shift; [[ $# -eq 0 ]] || die "doctor 不接受参数"; doctor ;;
  repair-permissions)
    need_run; need_arm64
    sudo /usr/local/sbin/sparkrobot-lingxi-consecutive-nav-fix repair-permissions ;;
  -h|--help) echo "用法：bash ./install_all.sh prepare|install-navigation|install-addons --agent-code CODE|doctor|repair-permissions" ;;
  *) die "用法：bash ./install_all.sh prepare|install-navigation|install-addons --agent-code CODE|doctor|repair-permissions" ;;
esac
