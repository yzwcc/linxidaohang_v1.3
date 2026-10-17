#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="1.3.8-complete-20261012-r10"
NAV_VERSION="1.0.19-20260921"
NAV_NAME="sparkrobot-lingxi-nav-internal-${NAV_VERSION}.tar.gz"
NAV_PACKAGE_NAME="sparkrobot-lingxi-nav-internal-${NAV_VERSION}"
KB_VERSION="1.0.2-20260902"
KB_NAME="sparkrobot-lingxi-kb-${KB_VERSION}.zip"
KB_PACKAGE_NAME="sparkrobot-lingxi-kb-${KB_VERSION}"
AIMDK_NAME="aimdk-aarch64-1bde262f-artifacts.7z"
WORK_ROOT="${WORK_ROOT:-/home/run/lingxi-v1.3.8-complete-20261012-r10-work}"
EXPECTED_RELEASE="/opt/sparkrobot/lingxi-nav/releases/${NAV_VERSION}"

die() { echo "失败：$*" >&2; exit 1; }
wait_http() {
  local url="$1" timeout_seconds="${2:-30}" elapsed=0
  while (( elapsed < timeout_seconds )); do
    if curl -fsS --max-time 2 "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  return 1
}
need_run() { [[ "$(id -un)" == "run" ]] || die "请使用 run 用户执行此阶段"; }
need_arm64() {
  case "$(uname -m)" in aarch64|arm64) ;; *) die "只能安装到 ARM64 机器人" ;; esac
}

assert_work_root() {
  local resolved
  resolved="$(realpath -m -- "${WORK_ROOT}")" || die "无法解析 WORK_ROOT"
  [[ "${resolved}" == "${WORK_ROOT}" ]] || die "WORK_ROOT 不能包含符号链接或路径跳转：${WORK_ROOT}"
  [[ "${WORK_ROOT}" == /home/run/* ]] || die "WORK_ROOT 必须位于 /home/run 下：${WORK_ROOT}"
  [[ "${WORK_ROOT}" != "/home/run" && "${WORK_ROOT}" != "/home/run/" ]] \
    || die "拒绝把 WORK_ROOT 指向 /home/run 根目录"
}

verify_bundle() {
  [[ -f "${PACKAGE_DIR}/SHA256SUMS.txt" ]] || die "安装包缺少 SHA256SUMS.txt"
  (cd "${PACKAGE_DIR}" && LC_ALL=C sha256sum -c -- SHA256SUMS.txt >/dev/null) \
    || die "完整包 SHA-256 校验失败，请重新上传原始安装包"
}

safe_extract_zip() {
  local archive="$1" destination="$2"
  python3 - "$archive" "$destination" <<'PY'
import pathlib, sys, zipfile
archive = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2]).resolve()
with zipfile.ZipFile(archive) as source:
    for info in source.infolist():
        target = (destination / info.filename).resolve()
        if target != destination and destination not in target.parents:
            raise SystemExit(f"压缩包包含不安全路径：{info.filename}")
    source.extractall(destination)
PY
}

safe_extract_tar() {
  local archive="$1" destination="$2"
  python3 - "$archive" <<'PY'
import pathlib, sys, tarfile
archive = pathlib.Path(sys.argv[1])
with tarfile.open(archive, "r:gz") as source:
    for member in source.getmembers():
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"压缩包包含不安全路径：{member.name}")
        if member.issym() or member.islnk():
            link = pathlib.PurePosixPath(member.linkname)
            if link.is_absolute() or ".." in link.parts:
                raise SystemExit(f"压缩包包含不安全链接：{member.name}")
PY
  tar -xzf "${archive}" -C "${destination}" --no-same-owner --no-same-permissions
}

verify_aimdk_runtime() {
  sudo -u agi env -i \
    HOME=/home/agi USER=agi LOGNAME=agi SHELL=/bin/bash \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=C.UTF-8 bash --noprofile --norc -c '
set -e
set +u
source /opt/ros/humble/setup.bash
source /home/agi/aimdk/install/setup.bash
set -u
python3 -c "import rclpy; import aimdk_msgs.msg"
test -x /home/agi/aimdk/install/examples/lib/examples/navigation
' >/dev/null 2>&1 \
    || die "AIMDK 未完成替换或 aimdk_msgs 不可用；请使用 agi 执行：bash /home/agi/01-replace-aimdk.sh"
}

prepare() {
  need_run
  need_arm64
  assert_work_root
  verify_bundle
  for file in "${AIMDK_NAME}" "${NAV_NAME}" "${KB_NAME}"; do
    [[ -f "${PACKAGE_DIR}/components/${file}" ]] || die "安装包缺少组件：${file}"
  done
  sudo install -d -m 0755 /home/agi /home/run
  sudo install -o agi -g agi -m 0644 \
    "${PACKAGE_DIR}/components/${AIMDK_NAME}" "/home/agi/${AIMDK_NAME}"
  sudo install -o agi -g agi -m 0755 \
    "${PACKAGE_DIR}/scripts/01-replace-aimdk.sh" /home/agi/01-replace-aimdk.sh
  sudo install -o agi -g agi -m 0755 \
    "${PACKAGE_DIR}/scripts/04-check-localization.sh" /home/agi/04-check-localization.sh
  install -m 0755 "${PACKAGE_DIR}/scripts/00-install-environment.sh" /home/run/00-install-environment.sh
  install -m 0755 "${PACKAGE_DIR}/scripts/03-verify-after-web-start.sh" /home/run/03-verify-after-web-start.sh
  echo "[1/3] 安装系统依赖和 AIMDK 编译环境"
  /home/run/00-install-environment.sh
  echo "[通过] 准备完成"
  echo "下一步请登录 agi 用户执行：bash /home/agi/01-replace-aimdk.sh"
}

extract_components() {
  assert_work_root
  [[ "${WORK_ROOT}" == /home/run/* ]] || die "工作目录不安全"
  sudo rm -rf -- "${WORK_ROOT}"
  install -d -m 0700 "${WORK_ROOT}/navigation" "${WORK_ROOT}/knowledge-base"
  safe_extract_tar "${PACKAGE_DIR}/components/${NAV_NAME}" "${WORK_ROOT}/navigation"
  safe_extract_zip "${PACKAGE_DIR}/components/${KB_NAME}" "${WORK_ROOT}/knowledge-base"
  NAV_ROOT="${WORK_ROOT}/navigation/${NAV_PACKAGE_NAME}"
  KB_ROOT="${WORK_ROOT}/knowledge-base/${KB_PACKAGE_NAME}"
  [[ -d "${NAV_ROOT}" ]] || die "导航主包解压目录不存在：${NAV_ROOT}"
  [[ -d "${KB_ROOT}" ]] || die "知识库包解压目录不存在：${KB_ROOT}"
}

install_product() {
  need_run
  need_arm64
  assert_work_root
  verify_bundle
  local agent_code="" setup_soc2=true
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --agent-code) agent_code="${2:-}"; shift 2 ;;
      --skip-soc2-setup) setup_soc2=false; shift ;;
      -h|--help)
        echo "用法：bash ./install_all.sh install --agent-code AI_AGENT_xxx [--skip-soc2-setup]"
        return 0
        ;;
      *) die "未知参数：$1" ;;
    esac
  done
  [[ "${agent_code}" =~ ^AI_AGENT_[A-Za-z0-9_-]+$ ]] \
    || die "必须指定知识库智能体 Code，例如 --agent-code AI_AGENT_f2c382e0"
  verify_aimdk_runtime
  extract_components

  echo "[2/3] 配置并安装 V1.3.8 合并导航主包"
  local secrets="${NAV_ROOT}/secrets.env"
  trap 'rm -f -- "${secrets:-}"' EXIT
  "${NAV_ROOT}/configure.sh" "${secrets}"
  chmod 0600 "${secrets}"
  local install_args=(--secrets "${secrets}")
  [[ "${setup_soc2}" == true ]] && install_args+=(--setup-soc2)
  sudo test -d "${EXPECTED_RELEASE}" && install_args+=(--reinstall)
  sudo bash "${NAV_ROOT}/install.sh" "${install_args[@]}"
  rm -f -- "${secrets}"
  trap - EXIT

  local current_release
  current_release="$(sudo readlink -f /opt/sparkrobot/lingxi-nav/current)" \
    || die "无法读取 current 软链接，请检查安装结果和目录权限"
  [[ "${current_release}" == "${EXPECTED_RELEASE}" ]] \
    || die "current 软链接未指向 V1.3.8：${current_release}"
  sudo sparkrobot-lingxi-internal verify

  echo "[3/3] 安装知识库检索服务"
  sudo bash "${KB_ROOT}/install.sh" --from-lingxi --agent-code "${agent_code}"
  # V1.3.8 主包已经包含同一版知识库 Java 接入。禁止再用旧知识库 JAR 覆盖它。
  sudo install -m 0755 "${PACKAGE_DIR}/control_all.sh" /usr/local/sbin/sparkrobot-lingxi-complete
  sudo systemctl restart lingxi-nav-console.service
  sudo systemctl is-active --quiet lingxi-nav-console.service \
    || die "网页控制台未运行，请查看：sudo journalctl -u lingxi-nav-console.service -n 100 --no-pager"
  wait_http http://127.0.0.1:3000/health 30 \
    || die "网页控制台健康检查失败"
  wait_http http://127.0.0.1:18084/health 30 \
    || die "知识库服务健康检查失败"

  local robot_ip
  robot_ip="$(hostname -I | awk '{print $1}')"
  echo "[通过] 灵犀 V1.3.8 完整产品已安装"
  echo "请用同一局域网手机打开：http://${robot_ip}:3000/console/"
  echo "在网页点击“一键开启全部服务”，成功后执行：bash /home/run/03-verify-after-web-start.sh"
}

preflight() {
  need_run
  need_arm64
  verify_bundle
  verify_aimdk_runtime
  [[ "$(sudo readlink -f /opt/sparkrobot/lingxi-nav/current 2>/dev/null || true)" == "${EXPECTED_RELEASE}" ]] \
    || die "当前导航版本不是 ${NAV_VERSION}"
  sudo sparkrobot-lingxi-internal verify
  sudo systemctl is-active --quiet lingxi-nav-console.service || die "网页服务未运行"
  curl -fsS --max-time 5 http://127.0.0.1:3000/health >/dev/null || die "网页健康检查失败"
  curl -fsS --max-time 5 http://127.0.0.1:18084/health >/dev/null || die "知识库服务健康检查失败"
  echo "安装预检通过。此检查不会发送导航、重定位、动作、舞蹈或语音指令。"
}

doctor() {
  need_run
  need_arm64
  verify_aimdk_runtime
  sudo sparkrobot-lingxi-internal verify
  sudo sparkrobot-lingxi-internal doctor
  sudo sparkrobot-lingxi-kb doctor
  curl -fsS --max-time 5 http://127.0.0.1:18082/task/commands >/dev/null \
    || die "讲解任务命令接口不可用"
  curl -fsS --max-time 5 http://127.0.0.1:18082/linkcraft/resources >/dev/null \
    || die "灵创资源接口不可用"
  echo "完整套件验收通过。此检查不会让机器人移动、重定位或执行动作。"
}

case "${1:-}" in
  prepare) shift; [[ $# -eq 0 ]] || die "prepare 不接受参数"; prepare ;;
  install) shift; install_product "$@" ;;
  preflight) shift; [[ $# -eq 0 ]] || die "preflight 不接受参数"; preflight ;;
  doctor) shift; [[ $# -eq 0 ]] || die "doctor 不接受参数"; doctor ;;
  repair-permissions)
    shift; [[ $# -eq 0 ]] || die "repair-permissions 不接受参数"
    need_run; need_arm64; sudo sparkrobot-lingxi-complete repair-permissions
    ;;
  -h|--help)
    echo "用法：bash ./install_all.sh prepare|install --agent-code CODE|preflight|doctor|repair-permissions"
    ;;
  *) die "用法：bash ./install_all.sh prepare|install --agent-code CODE|preflight|doctor|repair-permissions" ;;
esac
