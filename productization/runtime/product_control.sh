#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${SPARKROBOT_INSTALL_ROOT:-/}"
INSTALL_TEST_MODE="${SPARKROBOT_INSTALL_TEST_MODE:-false}"
RUNTIME_GROUP="${SPARKROBOT_RUNTIME_GROUP:-sparkrobot-nav}"
INSTALL_ROOT="${INSTALL_ROOT%/}"
[[ -n "${INSTALL_ROOT}" ]] || INSTALL_ROOT="/"
if [[ "${INSTALL_TEST_MODE}" == true && "${INSTALL_ROOT}" == "/" ]]; then
  echo "隔离安装测试模式禁止写入真实系统根目录" >&2
  exit 2
fi

root_path() {
  local path="$1"
  if [[ "${INSTALL_ROOT}" == "/" ]]; then
    printf '%s\n' "${path}"
  else
    printf '%s%s\n' "${INSTALL_ROOT}" "${path}"
  fi
}

systemctl_do() {
  [[ "${INSTALL_TEST_MODE}" == true ]] && return 0
  systemctl "$@"
}

PRODUCT_BASE="$(root_path /opt/sparkrobot/lingxi-nav)"
CURRENT_LINK="${PRODUCT_BASE}/current"
RELEASES_DIR="${PRODUCT_BASE}/releases"
META_DIR="$(root_path /etc/sparkrobot/lingxi-nav/releases)"
STATE_DIR="$(root_path /var/lib/sparkrobot/lingxi-nav)"
HISTORY_FILE="${STATE_DIR}/previous-version"

require_root() {
  [[ "${EUID}" -eq 0 ]] || { echo "该操作只能由厂商以 root 身份执行" >&2; exit 1; }
}

validate_version() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || { echo "版本号不安全：$1" >&2; exit 2; }
}

current_version() {
  [[ -L "${CURRENT_LINK}" ]] || return 1
  basename "$(readlink -f "${CURRENT_LINK}")"
}

verify_version() {
  local version="$1" release="${RELEASES_DIR}/${version}" meta="${META_DIR}/${version}"
  [[ -d "${release}" && -f "${meta}/manifest.json" && -f "${meta}/manifest.sig" ]] || {
    echo "版本 ${version} 不完整" >&2
    return 1
  }
  python3 "${release}/system/usr/lib/sparkrobot-license/verify_manifest.py" \
    --root "${INSTALL_ROOT}" --manifest "${meta}/manifest.json" --signature "${meta}/manifest.sig" \
    --public-key "$(root_path /etc/sparkrobot/lingxi-nav/vendor-public.pem)" --check-modes --quiet
}

install_system_files() {
  local release="$1"
  cp -a "${release}/system/." "${INSTALL_ROOT}/"
  chmod 0550 "$(root_path /usr/local/libexec/sparkrobot-license-verify)" \
    "$(root_path /usr/local/libexec/sparkrobot-release-verify)" \
    "$(root_path /usr/local/sbin/sparkrobot-lingxi-product)"
  chmod 0440 "$(root_path /etc/sudoers.d/sparkrobot-lingxi-nav)"
  chmod 0644 "$(root_path /etc/systemd/system/lingxi-nav-console.service)" \
    "$(root_path /etc/systemd/system/lingxi-java-agent-robot.service)"
  if [[ "${INSTALL_TEST_MODE}" != true ]]; then
    chown root:"${RUNTIME_GROUP}" \
      "$(root_path /usr/local/libexec/sparkrobot-license-verify)" \
      "$(root_path /usr/local/libexec/sparkrobot-release-verify)" \
      "$(root_path /usr/local/sbin/sparkrobot-lingxi-product)"
  fi
  systemctl_do daemon-reload
}

stop_runtime() {
  systemctl_do stop lingxi-nav-console.service lingxi-java-agent-robot.service 2>/dev/null || true
  if [[ "${INSTALL_TEST_MODE}" != true && -x "${CURRENT_LINK}/scripts/robot_local_navigation_v3.sh" ]]; then
    runuser -u agi -- "${CURRENT_LINK}/scripts/robot_local_navigation_v3.sh" stop 2>/dev/null || true
  fi
}

wait_for_console() {
  [[ "${INSTALL_TEST_MODE}" == true ]] && return 0
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 http://127.0.0.1:3000/health >/dev/null \
      && /usr/local/libexec/sparkrobot-license-verify --quiet \
      && /usr/local/libexec/sparkrobot-release-verify --quiet; then
      return 0
    fi
    sleep 1
  done
  return 1
}

activate_version() {
  require_root
  local version="$1" target="${RELEASES_DIR}/${1}" previous="" previous_target=""
  validate_version "${version}"
  verify_version "${version}"
  previous="$(current_version 2>/dev/null || true)"
  stop_runtime
  install_system_files "${target}"
  rm -f "${CURRENT_LINK}.new"
  ln -s "${target}" "${CURRENT_LINK}.new"
  mv -Tf "${CURRENT_LINK}.new" "${CURRENT_LINK}"
  python3 "${target}/system/usr/lib/sparkrobot-license/verify_stable.py" \
    --root "${INSTALL_ROOT}" --list "${target}/stable-files.json" --quiet
  systemctl_do enable lingxi-nav-console.service lingxi-java-agent-robot.service >/dev/null
  systemctl_do restart lingxi-nav-console.service
  if wait_for_console; then
    if [[ -n "${previous}" && "${previous}" != "${version}" ]]; then
      printf '%s\n' "${previous}" >"${HISTORY_FILE}"
      chmod 0600 "${HISTORY_FILE}"
    fi
    echo "已激活灵犀导航产品版本 ${version}"
    return 0
  fi

  echo "版本 ${version} 激活后未通过健康检查" >&2
  if [[ -n "${previous}" && "${previous}" != "${version}" && -d "${RELEASES_DIR}/${previous}" ]]; then
    previous_target="${RELEASES_DIR}/${previous}"
    install_system_files "${previous_target}"
    rm -f "${CURRENT_LINK}.new"
    ln -s "${previous_target}" "${CURRENT_LINK}.new"
    mv -Tf "${CURRENT_LINK}.new" "${CURRENT_LINK}"
    systemctl_do restart lingxi-nav-console.service 2>/dev/null || true
    echo "已自动恢复到版本 ${previous}" >&2
  fi
  return 1
}

rollback() {
  require_root
  local version="${1:-}"
  if [[ -z "${version}" && -f "${HISTORY_FILE}" ]]; then
    version="$(<"${HISTORY_FILE}")"
  fi
  [[ -n "${version}" ]] || { echo "没有可回滚版本" >&2; exit 1; }
  activate_version "${version}"
}

status() {
  local current="$(current_version 2>/dev/null || true)"
  printf 'current_version=%s\n' "${current:-none}"
  if [[ "${INSTALL_TEST_MODE}" == true ]]; then
    printf 'license=isolated-test\nrelease=valid\nconsole=not-started\nvoice=not-started\n'
  else
    printf 'license=%s\n' "$(sudo -n /usr/local/libexec/sparkrobot-license-verify --quiet 2>/dev/null && echo valid || echo invalid)"
    printf 'release=%s\n' "$(sudo -n /usr/local/libexec/sparkrobot-release-verify --quiet 2>/dev/null && echo valid || echo invalid)"
    printf 'console=%s\n' "$(systemctl is-active lingxi-nav-console.service 2>/dev/null || true)"
    printf 'voice=%s\n' "$(systemctl is-active lingxi-java-agent-robot.service 2>/dev/null || true)"
  fi
  if [[ -x "${CURRENT_LINK}/scripts/robot_local_navigation_v3.sh" ]]; then
    runuser -u agi -- "${CURRENT_LINK}/scripts/robot_local_navigation_v3.sh" status || true
  fi
}

uninstall_runtime() {
  require_root
  [[ "${1:-}" == "sparkrobot-lingxi-nav" ]] || {
    echo "拒绝卸载。请显式传入产品 ID：sparkrobot-lingxi-nav" >&2
    exit 2
  }
  stop_runtime
  systemctl_do disable lingxi-nav-console.service lingxi-java-agent-robot.service >/dev/null 2>&1 || true
  rm -f "$(root_path /etc/systemd/system/lingxi-nav-console.service)" "$(root_path /etc/systemd/system/lingxi-java-agent-robot.service)"
  rm -f "${CURRENT_LINK}"
  systemctl_do daemon-reload
  echo "产品服务已卸载；许可证、版本备份、地图、点位、任务和日志均已保留"
}

case "${1:-status}" in
  activate) [[ $# -eq 2 ]] || { echo "用法：$0 activate VERSION" >&2; exit 2; }; activate_version "$2" ;;
  rollback) rollback "${2:-}" ;;
  verify)
    if [[ "${INSTALL_TEST_MODE}" == true ]]; then
      version="$(current_version)"
      verify_version "${version}"
      python3 "${RELEASES_DIR}/${version}/system/usr/lib/sparkrobot-license/verify_stable.py" \
        --root "${INSTALL_ROOT}" --list "${RELEASES_DIR}/${version}/stable-files.json"
    else
      /usr/local/libexec/sparkrobot-license-verify --quiet
      /usr/local/libexec/sparkrobot-release-verify
    fi
    ;;
  status) status ;;
  uninstall) uninstall_runtime "${2:-}" ;;
  *) echo "用法：$0 status|verify|activate VERSION|rollback [VERSION]|uninstall sparkrobot-lingxi-nav" >&2; exit 2 ;;
esac
