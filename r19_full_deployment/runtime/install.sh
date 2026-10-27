#!/usr/bin/env bash
set -euo pipefail
umask 077

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS_FILE=""
REINSTALL=false
SETUP_SOC2=false
AIMDK_MODE="overlay"
AIMDK_SETUP=""
INSTALL_ROOT="${SPARKROBOT_INSTALL_ROOT:-/}"
TEST_MODE="${SPARKROBOT_INSTALL_TEST_MODE:-false}"
RUNTIME_GROUP="${SPARKROBOT_RUNTIME_GROUP:-sparkrobot-nav}"

usage() {
  cat <<'EOF'
用法：sudo ./install.sh --secrets ./secrets.env [选项]
  --secrets FILE   首次安装必填；升级且保留旧配置时可省略
  --aimdk-mode overlay|reuse
                   overlay=使用包内 AIMDK v1.1.0 aimdk_msgs；reuse=复用指定 setup.bash
  --aimdk-setup FILE  reuse 模式下机器人现有 AIMDK 的绝对 setup.bash 路径
  --setup-soc2     安装后交互配置 SOC2 语音运维密钥
  --reinstall      允许覆盖安装同版本，旧版自动备份
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --secrets) [[ $# -ge 2 ]] || { echo "--secrets 缺少参数" >&2; exit 2; }; SECRETS_FILE="$2"; shift 2 ;;
    --aimdk-mode) [[ $# -ge 2 ]] || { echo "--aimdk-mode 缺少参数" >&2; exit 2; }; AIMDK_MODE="$2"; shift 2 ;;
    --aimdk-setup) [[ $# -ge 2 ]] || { echo "--aimdk-setup 缺少参数" >&2; exit 2; }; AIMDK_SETUP="$2"; shift 2 ;;
    --setup-soc2) SETUP_SOC2=true; shift ;;
    --reinstall) REINSTALL=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "${AIMDK_MODE}" == overlay || "${AIMDK_MODE}" == reuse ]] || { echo "--aimdk-mode must be overlay or reuse" >&2; exit 2; }
if [[ "${AIMDK_MODE}" == reuse ]]; then
  [[ "${AIMDK_SETUP}" == /* && "${AIMDK_SETUP}" =~ ^[A-Za-z0-9_./-]+$ && -r "${AIMDK_SETUP}" ]] || { echo "reuse mode requires a readable absolute --aimdk-setup path without spaces or shell metacharacters" >&2; exit 2; }
  [[ "/${AIMDK_SETUP#/}/" != *"/../"* ]] || { echo "--aimdk-setup must not contain parent-directory traversal" >&2; exit 2; }
else
  [[ -z "${AIMDK_SETUP}" ]] || { echo "--aimdk-setup is only valid with --aimdk-mode reuse" >&2; exit 2; }
fi

[[ "${EUID}" -eq 0 ]] || { echo "请使用 sudo 运行安装" >&2; exit 1; }
[[ "${INSTALL_ROOT}" == /* ]] || { echo "SPARKROBOT_INSTALL_ROOT 必须是绝对路径" >&2; exit 2; }
INSTALL_ROOT="${INSTALL_ROOT%/}"; [[ -n "${INSTALL_ROOT}" ]] || INSTALL_ROOT="/"
[[ "${TEST_MODE}" != true || "${INSTALL_ROOT}" != "/" ]] || { echo "测试模式禁止写入真实根目录" >&2; exit 2; }

root_path() {
  [[ "${INSTALL_ROOT}" == "/" ]] && printf '%s\n' "$1" || printf '%s%s\n' "${INSTALL_ROOT}" "$1"
}
systemctl_do() { [[ "${TEST_MODE}" == true ]] || systemctl "$@"; }

MANAGED_FILES=(
  etc/sparkrobot/lingxi-nav/console.env
  etc/sparkrobot/lingxi-nav/lingxi-nav.properties
  etc/sparkrobot/lingxi-nav/runtime.env
  etc/sparkrobot/lingxi-nav/console-ed25519
  etc/sparkrobot/lingxi-nav/console-ed25519.pub
  etc/sparkrobot/lingxi-nav/soc2-ed25519
  etc/sparkrobot/lingxi-nav/soc2-ed25519.pub
  var/lib/sparkrobot/lingxi-nav/unified_nav_config.json
  var/lib/sparkrobot/lingxi-nav/previous-version
  etc/systemd/system/lingxi-nav-console.service
  etc/systemd/system/lingxi-java-agent-robot.service
  usr/local/sbin/sparkrobot-lingxi-internal
  etc/sudoers.d/sparkrobot-lingxi-internal
)
ROLLBACK_DIR=""
PREVIOUS_CONSOLE_ENABLED=""
PREVIOUS_JAVA_ENABLED=""
PREVIOUS_CONSOLE_ACTIVE=false
PREVIOUS_JAVA_ACTIVE=false

snapshot_managed_files() {
  local relative source_path home user
  ROLLBACK_DIR="${BACKUP_DIR}/install-${TIMESTAMP}-$$"
  install -d -m 0700 "${ROLLBACK_DIR}/files" "${ROLLBACK_DIR}/present"
  if [[ "${TEST_MODE}" != true ]]; then
    for user in agi run; do
      home="$(getent passwd "${user}" | cut -d: -f6)"
      [[ -n "${home}" ]] || continue
      relative="${home#/}/.ssh/authorized_keys"
      [[ " ${MANAGED_FILES[*]} " == *" ${relative} "* ]] || MANAGED_FILES+=("${relative}")
    done
  fi
  for relative in "${MANAGED_FILES[@]}"; do
    source_path="$(root_path "/${relative}")"
    if [[ -e "${source_path}" || -L "${source_path}" ]]; then
      install -d -m 0700 "${ROLLBACK_DIR}/files/$(dirname "${relative}")" "${ROLLBACK_DIR}/present/$(dirname "${relative}")"
      cp -a "${source_path}" "${ROLLBACK_DIR}/files/${relative}"
      : >"${ROLLBACK_DIR}/present/${relative}"
    fi
  done
  if [[ "${TEST_MODE}" != true ]]; then
    PREVIOUS_CONSOLE_ENABLED="$(systemctl is-enabled lingxi-nav-console.service 2>/dev/null || true)"
    PREVIOUS_JAVA_ENABLED="$(systemctl is-enabled lingxi-java-agent-robot.service 2>/dev/null || true)"
    systemctl is-active --quiet lingxi-nav-console.service && PREVIOUS_CONSOLE_ACTIVE=true || true
    systemctl is-active --quiet lingxi-java-agent-robot.service && PREVIOUS_JAVA_ACTIVE=true || true
  fi
}

restore_managed_files() {
  local relative destination temporary
  for relative in "${MANAGED_FILES[@]}"; do
    destination="$(root_path "/${relative}")"
    if [[ -f "${ROLLBACK_DIR}/present/${relative}" ]]; then
      install -d -m 0755 "$(dirname "${destination}")"
      temporary="${destination}.r19-rollback.$$"
      rm -f "${temporary}"
      cp -a "${ROLLBACK_DIR}/files/${relative}" "${temporary}"
      mv -f "${temporary}" "${destination}"
    else
      rm -f "${destination}"
    fi
  done
}

remove_console_key_from_authorized_keys() {
  local blob="$1" user home auth temporary
  [[ -n "${blob}" ]] || return 0
  for user in agi run; do
    home="$(getent passwd "${user}" | cut -d: -f6)"
    [[ -n "${home}" ]] || continue
    auth="${home}/.ssh/authorized_keys"
    [[ -f "${auth}" ]] || continue
    temporary="$(mktemp "${auth}.r19-rollback.XXXXXX")"
    awk -v blob="${blob}" 'index($0, blob) == 0' "${auth}" >"${temporary}"
    cat "${temporary}" >"${auth}"
    rm -f "${temporary}"
  done
}

restore_service_state() {
  local unit enabled was_active
  for unit in lingxi-nav-console.service lingxi-java-agent-robot.service; do
    if [[ "${unit}" == "lingxi-nav-console.service" ]]; then
      enabled="${PREVIOUS_CONSOLE_ENABLED}"
      was_active="${PREVIOUS_CONSOLE_ACTIVE}"
    else
      enabled="${PREVIOUS_JAVA_ENABLED}"
      was_active="${PREVIOUS_JAVA_ACTIVE}"
    fi
    case "${enabled}" in
      enabled|enabled-runtime) systemctl_do enable "${unit}" >/dev/null 2>&1 || true ;;
      masked|masked-runtime) systemctl_do mask "${unit}" >/dev/null 2>&1 || true ;;
      *) systemctl_do disable "${unit}" >/dev/null 2>&1 || true ;;
    esac
    if [[ "${was_active}" == true ]]; then
      systemctl_do restart "${unit}" >/dev/null 2>&1 || true
    else
      systemctl_do stop "${unit}" >/dev/null 2>&1 || true
    fi
  done
}

restore_previous_release() {
  local failed_target new_console_blob old_console_blob
  [[ "${SWITCH_STARTED}" == true && "${INSTALL_COMMITTED}" != true ]] || return 0
  if [[ "${TEST_MODE}" != true ]]; then
    systemctl_do stop lingxi-nav-console.service lingxi-java-agent-robot.service >/dev/null 2>&1 || true
  fi
  new_console_blob=""
  old_console_blob=""
  if [[ -f "${CONFIG_DIR}/console-ed25519.pub" ]]; then
    new_console_blob="$(awk '{print $2}' "${CONFIG_DIR}/console-ed25519.pub")"
  fi
  if [[ -f "${ROLLBACK_DIR}/files/etc/sparkrobot/lingxi-nav/console-ed25519.pub" ]]; then
    old_console_blob="$(awk '{print $2}' "${ROLLBACK_DIR}/files/etc/sparkrobot/lingxi-nav/console-ed25519.pub")"
  fi
  if [[ -n "${PREVIOUS}" && -d "${PREVIOUS}" ]]; then
    rm -f "${CURRENT_ROLLBACK}"
    ln -s "${PREVIOUS}" "${CURRENT_ROLLBACK}"
    mv -Tf "${CURRENT_ROLLBACK}" "${PRODUCT_BASE}/current"
  else
    rm -f "${PRODUCT_BASE}/current" "${CURRENT_ROLLBACK}"
  fi
  restore_managed_files
  if [[ "${new_console_blob}" != "${old_console_blob}" && "${TEST_MODE}" != true ]]; then
    remove_console_key_from_authorized_keys "${new_console_blob}"
  fi
  if [[ -n "${TARGET_BACKUP_PATH}" && -d "${TARGET_BACKUP_PATH}" ]]; then
    if [[ -e "${TARGET}" ]]; then
      failed_target="${RELEASE_BACKUP_DIR}/failed-${VERSION}-$(date -u +%Y%m%dT%H%M%SZ)"
      mv "${TARGET}" "${failed_target}"
    fi
    mv "${TARGET_BACKUP_PATH}" "${TARGET}"
  fi
  if [[ "${TEST_MODE}" != true ]]; then
    systemctl_do daemon-reload >/dev/null 2>&1 || true
    restore_service_state
    if [[ -n "${PREVIOUS}" && -x "${PREVIOUS}/scripts/navigation_control.sh" ]]; then
      runuser -u agi -- env ROBOT_LOCAL_NAV_AIMDK_SETUP="${PREVIOUS_AIMDK_SETUP}" "${PREVIOUS}/scripts/navigation_control.sh" restart >/dev/null 2>&1 || true
    fi
    if [[ -n "${PREVIOUS}" && -x "${PREVIOUS}/scripts/robot_voice_bridge_control.sh" ]]; then
      ROBOT_LOCAL_NAV_AIMDK_SETUP="${PREVIOUS_AIMDK_SETUP}" "${PREVIOUS}/scripts/robot_voice_bridge_control.sh" start >/dev/null 2>&1 || true
    fi
  fi
  SWITCH_STARTED=false
}

on_exit() {
  local status=$?
  trap - EXIT
  if [[ "${status}" -ne 0 && "${SNAPSHOT_READY}" == true && "${SWITCH_STARTED}" != true && "${INSTALL_COMMITTED}" != true ]]; then
    restore_managed_files || echo "pre-switch rollback failed; snapshot preserved at ${ROLLBACK_DIR}" >&2
  fi
  if [[ "${SWITCH_STARTED}" == true && "${INSTALL_COMMITTED}" != true ]]; then
    restore_previous_release || echo "警告：安装回滚期间出现错误；回滚快照保留在 ${ROLLBACK_DIR}" >&2
  fi
  if [[ "${status}" -ne 0 && -n "${STAGING}" && -d "${STAGING}" ]]; then
    rm -rf -- "${STAGING}"
  fi
  for temporary in \
    "${CURRENT_NEW}" "${CURRENT_ROLLBACK}" \
    "${SYSTEMD_CONSOLE_NEW}" "${SYSTEMD_JAVA_NEW}" "${CONTROL_NEW}" \
    "${CONSOLE_ENV_NEW}" "${NAV_CONFIG_NEW}" "${PROPERTIES_NEW}" "${RUNTIME_ENV_NEW}" \
    "${SUDOERS_TMP}" "${NAV_CONFIG_TMP}" "${CONSOLE_ENV_TMP}" "${PROPERTIES_TMP}" "${RUNTIME_ENV_TMP}"; do
    [[ -z "${temporary}" ]] || rm -f -- "${temporary}"
  done
  exit "${status}"
}

PRODUCT_BASE="$(root_path /opt/sparkrobot/lingxi-nav)"
CONFIG_DIR="$(root_path /etc/sparkrobot/lingxi-nav)"
STATE_DIR="$(root_path /var/lib/sparkrobot/lingxi-nav)"
LOG_DIR="$(root_path /var/log/sparkrobot/lingxi-nav)"
BACKUP_DIR="${CONFIG_DIR}/backups"
RELEASE_BACKUP_DIR="${PRODUCT_BASE}/reinstall-backups"
SYSTEMD_CONSOLE_PATH="$(root_path /etc/systemd/system/lingxi-nav-console.service)"
SYSTEMD_JAVA_PATH="$(root_path /etc/systemd/system/lingxi-java-agent-robot.service)"
CONTROL_PATH="$(root_path /usr/local/sbin/sparkrobot-lingxi-internal)"
SUDOERS_PATH="$(root_path /etc/sudoers.d/sparkrobot-lingxi-internal)"
CURRENT_NEW="${PRODUCT_BASE}/current.new.$$"
CURRENT_ROLLBACK="${PRODUCT_BASE}/current.rollback.$$"
SYSTEMD_CONSOLE_NEW="${SYSTEMD_CONSOLE_PATH}.new.$$"
SYSTEMD_JAVA_NEW="${SYSTEMD_JAVA_PATH}.new.$$"
CONTROL_NEW="${CONTROL_PATH}.new.$$"
CONSOLE_ENV_NEW="${CONFIG_DIR}/console.env.new.$$"
NAV_CONFIG_NEW="${STATE_DIR}/unified_nav_config.json.new.$$"
PROPERTIES_NEW="${CONFIG_DIR}/lingxi-nav.properties.new.$$"
RUNTIME_ENV_NEW="${CONFIG_DIR}/runtime.env.new.$$"
PREVIOUS=""
PREVIOUS_AIMDK_SETUP=""
TARGET=""
STAGING=""
TARGET_BACKUP_PATH=""
TIMESTAMP=""
SUDOERS_TMP=""
NAV_CONFIG_TMP=""
CONSOLE_ENV_TMP=""
PROPERTIES_TMP=""
RUNTIME_ENV_TMP=""
SWITCH_STARTED=false
SNAPSHOT_READY=false
INSTALL_COMMITTED=false
trap on_exit EXIT

for path in release-info.json payload-manifest.json payload scripts templates systemd docs SHA256SUMS; do
  [[ -e "${PACKAGE_DIR}/${path}" ]] || { echo "安装包不完整，缺少 ${path}" >&2; exit 1; }
done
(cd "${PACKAGE_DIR}" && sha256sum -c SHA256SUMS >/dev/null)
python3 "${PACKAGE_DIR}/scripts/verify_payload.py" --root "${PACKAGE_DIR}/payload" --manifest "${PACKAGE_DIR}/payload-manifest.json"
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["version"])' "${PACKAGE_DIR}/release-info.json")"
[[ "${VERSION}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || { echo "版本号不安全" >&2; exit 1; }
if [[ "$(uname -m)" != aarch64 && "$(uname -m)" != arm64 && "${SPARKROBOT_ALLOW_NON_ARM64:-false}" != true ]]; then
  echo "只能安装到 ARM64 机器人" >&2; exit 1
fi
[[ -z "${SECRETS_FILE}" || -f "${SECRETS_FILE}" ]] || { echo "配置文件不存在" >&2; exit 1; }
if [[ -z "${SECRETS_FILE}" && ! -f "${CONFIG_DIR}/lingxi-nav.properties" ]]; then
  echo "首次安装必须使用 --secrets" >&2; exit 1
fi
TARGET="${PRODUCT_BASE}/releases/${VERSION}"
if [[ -e "${TARGET}" && "${REINSTALL}" != true ]]; then
  echo "版本 ${VERSION} 已安装；修复安装请加 --reinstall" >&2; exit 1
fi

if [[ "${TEST_MODE}" != true ]]; then
  for user in agi run; do getent passwd "${user}" >/dev/null || { echo "机器人缺少用户 ${user}" >&2; exit 1; }; done
fi

if [[ "${AIMDK_MODE}" == overlay ]]; then
  PRECHECK_SETUP="${PACKAGE_DIR}/payload/aimdk-v1.1.0/setup.bash"
else
  PRECHECK_SETUP="${AIMDK_SETUP}"
fi
[[ -r "${PRECHECK_SETUP}" ]] || { echo "AIMDK setup 不可读：${PRECHECK_SETUP}" >&2; exit 1; }
if [[ "${TEST_MODE}" == true ]]; then
  /bin/bash -c 'set -e; source "$1"; export PYTHONPATH="$2${PYTHONPATH:+:${PYTHONPATH}}"; python3 -B -c '\''import rclpy; from aimdk_msgs.msg import PncTaskRequest; from aimdk_msgs.srv import ExecuteActionResource, GetStoredMapByName, PlayAudioFile, PlayTts, SetMcAction, SetMcPresetMotion; from sensor_msgs_py import point_cloud2; from tf2_ros import Buffer, TransformListener; from robot_local_navigation_v3 import lingxi_nav_bridge, map_label_server_v3, voice_nav_gateway, robot_lingxi_bridge'\''' _ "${PRECHECK_SETUP}" "${PACKAGE_DIR}/payload/navigation" || {
    echo "AIMDK/ROS 运行接口预检失败；尚未写入或停止任何服务" >&2; exit 1;
  }
else
  runuser -u agi -- /bin/bash -c 'set -e; source "$1"; export PYTHONPATH="$2${PYTHONPATH:+:${PYTHONPATH}}"; python3 -B -c '\''import rclpy; from aimdk_msgs.msg import PncTaskRequest; from aimdk_msgs.srv import ExecuteActionResource, GetStoredMapByName, PlayAudioFile, PlayTts, SetMcAction, SetMcPresetMotion; from sensor_msgs_py import point_cloud2; from tf2_ros import Buffer, TransformListener; from robot_local_navigation_v3 import lingxi_nav_bridge, map_label_server_v3, voice_nav_gateway, robot_lingxi_bridge'\''' _ "${PRECHECK_SETUP}" "${PACKAGE_DIR}/payload/navigation" || {
    echo "AIMDK/ROS v1.1.0 消息与实时建图接口预检失败；尚未写入或停止任何服务" >&2; exit 1;
  }
fi

if [[ -n "${SECRETS_FILE}" ]]; then
  python3 - "${SECRETS_FILE}" <<'PY'
import sys
from pathlib import Path

values = {}
for raw in Path(sys.argv[1]).read_text(encoding="utf-8-sig").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    key, separator, value = line.partition("=")
    if not separator or "\n" in value or "\r" in value:
        raise SystemExit("配置文件包含无效行")
    values[key.strip()] = value.strip()
required = ("LINGXI_APP_ID", "LINGXI_APP_KEY", "LINGXI_APP_SECRET", "DOUBAO_API_KEY")
missing = [key for key in required if not values.get(key)]
if missing:
    raise SystemExit("配置文件缺少：" + ", ".join(missing))
PY
fi

MAPPING_SESSION="$(root_path /var/lib/sparkrobot/lingxi-nav/mapping-session.json)"
if [[ -f "${MAPPING_SESSION}" ]] && python3 - "${MAPPING_SESSION}" <<'PY'
import json, sys
from pathlib import Path
try:
    session = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(0)
raise SystemExit(0 if session.get("active") or session.get("awaiting_save") else 1)
PY
then
  echo "检测到建图正在进行或有地图待保存；请先在网页完成停止/保存，再安装 R19" >&2
  exit 1
fi

PREVIOUS=""; [[ -L "${PRODUCT_BASE}/current" ]] && PREVIOUS="$(readlink -f "${PRODUCT_BASE}/current")"
PREVIOUS_AIMDK_SETUP=""
if [[ -n "${PREVIOUS}" && -r "${CONFIG_DIR}/console.env" ]]; then
  PREVIOUS_AIMDK_SETUP="$(sed -n 's/^ROBOT_AIMDK_SETUP=//p' "${CONFIG_DIR}/console.env" | tail -n 1)"
fi
if [[ "${AIMDK_MODE}" == overlay ]]; then
  ROBOT_AIMDK_SETUP="${PRODUCT_BASE}/releases/${VERSION}/aimdk-v1.1.0/setup.bash"
else
  ROBOT_AIMDK_SETUP="${AIMDK_SETUP}"
fi

STAGING="${PRODUCT_BASE}/releases/.${VERSION}.installing.$$"
if [[ -e "${STAGING}" ]]; then
  echo "安装暂存目录已存在，拒绝覆盖：${STAGING}" >&2; exit 1
fi
mkdir -p "${PRODUCT_BASE}/releases"
mkdir -p "${STAGING}"
cp -a "${PACKAGE_DIR}/payload/." "${STAGING}/"
cp -a "${PACKAGE_DIR}/scripts" "${PACKAGE_DIR}/templates" "${PACKAGE_DIR}/docs" "${STAGING}/"
cp -a "${PACKAGE_DIR}/payload-manifest.json" "${PACKAGE_DIR}/release-info.json" "${STAGING}/"
find "${STAGING}" -type d -exec chmod 0750 {} +
find "${STAGING}" -type f -exec chmod 0640 {} +
find "${STAGING}/scripts" -type f -name '*.sh' -exec chmod 0750 {} +
find "${STAGING}/runtime/java/bin" -type f -exec chmod 0750 {} +
chmod 0750 "${STAGING}/runtime/node" "${STAGING}/runtime/java/lib/jspawnhelper"
if [[ -d "${STAGING}/aimdk-v1.1.0" ]]; then
  find "${STAGING}/aimdk-v1.1.0" -type d -exec chmod 0750 {} +
  find "${STAGING}/aimdk-v1.1.0" -type f -exec chmod 0640 {} +
  chmod 0640 "${STAGING}/aimdk-v1.1.0/setup.bash"
fi
if [[ "${TEST_MODE}" != true ]]; then
  getent group "${RUNTIME_GROUP}" >/dev/null || groupadd --system "${RUNTIME_GROUP}"
fi
[[ "${TEST_MODE}" == true ]] || chown -R root:"${RUNTIME_GROUP}" "${STAGING}"
if [[ -e "${TARGET}" ]]; then
  if [[ "${PREVIOUS}" == "${TARGET}" ]]; then
    echo "当前运行版本已是 ${VERSION}；拒绝在原地覆盖活动版本。请先部署到不同 release version 或执行正式回滚" >&2; exit 1
  fi
fi
python3 "${PACKAGE_DIR}/scripts/verify_payload.py" --root "${STAGING}" --manifest "${STAGING}/payload-manifest.json"

if [[ "${TEST_MODE}" != true ]]; then
  usermod -a -G "${RUNTIME_GROUP}" agi
  usermod -a -G "${RUNTIME_GROUP}" run
fi

install -d -m 0755 "$(root_path /opt/sparkrobot)" "$(root_path /etc/sparkrobot)" "$(root_path /var/lib/sparkrobot)" "$(root_path /var/log/sparkrobot)"
install -d -m 0755 "$(root_path /etc/systemd)" "$(root_path /etc/systemd/system)" \
  "$(root_path /etc/sudoers.d)" "$(root_path /usr/local/sbin)"
install -d -m 0750 "${PRODUCT_BASE}" "${PRODUCT_BASE}/releases" "${CONFIG_DIR}"
install -d -m 0700 "${BACKUP_DIR}" "${RELEASE_BACKUP_DIR}"
install -d -m 0770 "${STATE_DIR}" "${STATE_DIR}/maps" "${STATE_DIR}/pids" "${LOG_DIR}"
if [[ "${TEST_MODE}" != true ]]; then
  chown root:"${RUNTIME_GROUP}" "${PRODUCT_BASE}" "${PRODUCT_BASE}/releases" "${CONFIG_DIR}"
  chown agi:"${RUNTIME_GROUP}" "${STATE_DIR}" "${STATE_DIR}/maps" "${STATE_DIR}/pids" "${LOG_DIR}"
fi

# The package carries a relative compatibility wrapper for the historical name.
# Never replace it with a link into this temporary staging path.
for script in navigation_control.sh restart_robot_voice_stack.sh java_agent_control.sh lingxi_java_agent_control.sh; do
  [[ -x "${STAGING}/scripts/${script}" ]] || { echo "安装包缺少运行脚本：${script}" >&2; exit 1; }
done

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"
snapshot_managed_files
SNAPSHOT_READY=true
for file in console.env lingxi-nav.properties runtime.env; do
  [[ -f "${CONFIG_DIR}/${file}" ]] && cp -a "${CONFIG_DIR}/${file}" "${BACKUP_DIR}/${file}.${TIMESTAMP}"
done
[[ ! -f "${STATE_DIR}/unified_nav_config.json" ]] || cp -a "${STATE_DIR}/unified_nav_config.json" "${BACKUP_DIR}/unified_nav_config.json.${TIMESTAMP}"

if [[ -n "${SECRETS_FILE}" ]]; then
  PROPERTIES_TMP="$(mktemp "${CONFIG_DIR}/.lingxi-nav.properties.XXXXXX")"
  RUNTIME_ENV_TMP="$(mktemp "${CONFIG_DIR}/.runtime.env.XXXXXX")"
  python3 - "${SECRETS_FILE}" "${PROPERTIES_TMP}" "${RUNTIME_ENV_TMP}" <<'PY'
import os
import sys
from pathlib import Path

values = {}
for raw in Path(sys.argv[1]).read_text(encoding="utf-8-sig").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    key, separator, value = line.partition("=")
    if not separator:
        raise SystemExit(f"配置文件包含无效行：{raw}")
    values[key.strip()] = value.strip()
required = ("LINGXI_APP_ID", "LINGXI_APP_KEY", "LINGXI_APP_SECRET", "DOUBAO_API_KEY")
missing = [key for key in required if not values.get(key)]
if missing:
    raise SystemExit("配置文件缺少：" + ", ".join(missing))
for key, value in values.items():
    if "\n" in value or "\r" in value:
        raise SystemExit(f"{key} 包含非法换行")
properties = f"""lingxi.url=wss://open.agibot.com/api/V1/open-portal/app/wss/agent-sdk
lingxi.appId={values['LINGXI_APP_ID']}
lingxi.appKey={values['LINGXI_APP_KEY']}
lingxi.appSecret={values['LINGXI_APP_SECRET']}
lingxi.callbacks=asr2llm,audio2tts,asr2tts,audio2llm
doubao.url=https://ark.cn-beijing.volces.com/api/v3/chat/completions
doubao.apiKey={values['DOUBAO_API_KEY']}
doubao.model={values.get('DOUBAO_MODEL', 'doubao-seed-2-0-mini-260428')}
robot.displayName={values.get('ROBOT_DISPLAY_NAME', '灵犀')}
nav.baseUrl=http://127.0.0.1:18083
nav.timeoutMs=5000
tts.mode=robot
tts.fixedDialogTest=false
tts.suppressLingxiText=true
tts.delayBeforeSpeakMs=800
qa.storePath=/var/lib/sparkrobot/lingxi-nav/qa-store.json
live.enabled=false
live.inputMode=websocket
live.host=127.0.0.1
live.port=18081
live.replyMode=ai
live.allowNavigation=false
live.allowEntertainment=true
live.maxQueueSize=20
live.minIntervalMs=3000
live.maxTextLength=80
"""
Path(sys.argv[2]).write_text(properties, encoding="utf-8", newline="\n")
Path(sys.argv[3]).write_text(
    f"SOC2_HOST={values.get('SOC2_HOST', '10.0.1.42')}\nSOC2_USER=run\n",
    encoding="utf-8", newline="\n"
)
os.chmod(sys.argv[2], 0o640)
os.chmod(sys.argv[3], 0o640)
PY
fi

NAV_CONFIG_TMP="$(mktemp "${STATE_DIR}/.unified_nav_config.json.XXXXXX")"
python3 - "${STAGING}/templates/unified_nav_config.json" "${NAV_CONFIG_TMP}" "${SECRETS_FILE}" "${INSTALL_ROOT}" <<'PY'
import json
import os
import sys
from pathlib import Path

template, output, secrets, install_root = map(Path, sys.argv[1:])
data = json.loads(template.read_text(encoding="utf-8"))

def rooted(path: str) -> Path:
    return Path(path) if str(install_root) == "/" else install_root / path.lstrip("/")

old_candidates = (
    output,
    rooted("/etc/sparkrobot/lingxi-nav/unified_nav_config.json"),
    rooted("/home/agi/robot_local_navigation_v3/unified_nav_config.json"),
    rooted("/agibot/data/home/agi/robot_local_navigation_v3/unified_nav_config.json"),
)
for candidate in old_candidates:
    if not candidate.is_file():
        continue
    try:
        previous = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    for key in ("navigation", "maps", "points", "exports"):
        if key in previous:
            data[key] = previous[key]
    if isinstance(previous.get("app"), dict):
        data["app"].update({key: value for key, value in previous["app"].items() if key != "doubao_api_key"})
    if previous.get("app", {}).get("doubao_api_key"):
        data["app"]["doubao_api_key"] = previous["app"]["doubao_api_key"]
    break
data.get("navigation", {}).pop("path_safety", None)
if secrets.is_file():
    values = {}
    for raw in secrets.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    data.setdefault("app", {})["doubao_api_key"] = values["DOUBAO_API_KEY"]
    data["app"]["doubao_model"] = values.get("DOUBAO_MODEL", "doubao-seed-2-0-mini-260428")
output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
os.chmod(output, 0o660)
PY

if ! find "${STATE_DIR}/maps" -maxdepth 1 -type f -name '*.json' -print -quit | grep -q .; then
  for old_maps in "$(root_path /home/agi/robot_local_navigation_v3/maps)" "$(root_path /agibot/data/home/agi/robot_local_navigation_v3/maps)"; do
    if [[ -d "${old_maps}" ]]; then
      cp -a "${old_maps}/." "${STATE_DIR}/maps/"
      break
    fi
  done
fi

TOKEN_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
CONSOLE_KEY="${CONFIG_DIR}/console-ed25519"
if [[ "${TEST_MODE}" == true ]]; then
  printf 'test-private-key\n' >"${CONSOLE_KEY}"
  printf 'ssh-ed25519 TEST sparkrobot-console\n' >"${CONSOLE_KEY}.pub"
else
  [[ -f "${CONSOLE_KEY}" ]] || ssh-keygen -q -t ed25519 -N '' -C sparkrobot-console -f "${CONSOLE_KEY}"
  for user in agi run; do
    home="$(getent passwd "${user}" | cut -d: -f6)"; group="$(id -gn "${user}")"
    install -d -m 0700 -o "${user}" -g "${group}" "${home}/.ssh"
    auth="${home}/.ssh/authorized_keys"; touch "${auth}"; chown "${user}:${group}" "${auth}"; chmod 0600 "${auth}"
    blob="$(awk '{print $2}' "${CONSOLE_KEY}.pub")"
    temporary="$(mktemp)"
    awk -v blob="${blob}" 'index($0, blob) == 0' "${auth}" >"${temporary}"
    printf 'from="127.0.0.1,::1",restrict %s\n' "$(<"${CONSOLE_KEY}.pub")" >>"${temporary}"
    install -m 0600 -o "${user}" -g "${group}" "${temporary}" "${auth}"
    rm -f "${temporary}"
  done
fi
chmod 0600 "${CONSOLE_KEY}"; chmod 0644 "${CONSOLE_KEY}.pub"

if [[ "${TEST_MODE}" == true && "${SPARKROBOT_INSTALL_TEST_FAIL_AFTER_KEY_PREP:-false}" == true ]]; then
  echo "injected pre-switch failure after managed key preparation" >&2
  exit 86
fi

CONSOLE_ENV_TMP="$(mktemp "${CONFIG_DIR}/.console.env.XXXXXX")"
cat >"${CONSOLE_ENV_TMP}" <<EOF
DEPLOYMENT_MODE=robot
DEPLOYMENT_VERSION=${VERSION}
HOST=0.0.0.0
PORT=3000
PUBLIC_BASE_URL=http://127.0.0.1:3000
ROBOT_HOST=127.0.0.1
ROBOT_CONNECTION_MODE=direct
ROBOT_BRIDGE_PORT=18082
ROBOT_WEB_PORT=8002
ROBOT_MAP_ROOT=/agibot/data/var/MapManagerModule/
ROBOT_AIMDK_SETUP=${ROBOT_AIMDK_SETUP}
ROBOT_SSH_USER=agi
ROBOT_SSH_PRIVATE_KEY_PATH=/etc/sparkrobot/lingxi-nav/console-ed25519
ROBOT_SYSTEM_SSH_USER=run
ROBOT_SYSTEM_SSH_PRIVATE_KEY_PATH=/etc/sparkrobot/lingxi-nav/console-ed25519
ROBOT_NAV_HOME=/opt/sparkrobot/lingxi-nav/current
JAVA_AGENT_EXECUTION_MODE=robot
ROBOT_JAVA_AGENT_HOME=/opt/sparkrobot/lingxi-nav/current/scripts
ROBOT_JAVA_AGENT_BRIDGE_PORT=18083
ROBOT_VOICE_RESTART_COMMAND=/opt/sparkrobot/lingxi-nav/current/scripts/restart_robot_voice_stack.sh
JAVA_AGENT_AUTO_START=false
DEV_AUTH_BYPASS=true
TOKEN_SECRET=${TOKEN_SECRET}
ROBOT_RESOURCE_USER=run
EOF
chmod 0600 "${CONSOLE_ENV_TMP}"
if [[ "${TEST_MODE}" != true ]]; then
  [[ -z "${PROPERTIES_TMP:-}" ]] || chown root:"${RUNTIME_GROUP}" "${PROPERTIES_TMP}"
  [[ -z "${RUNTIME_ENV_TMP:-}" ]] || chown root:"${RUNTIME_GROUP}" "${RUNTIME_ENV_TMP}"
  chown -R agi:"${RUNTIME_GROUP}" "${STATE_DIR}" "${LOG_DIR}"
fi
find "${STATE_DIR}" -type d -exec chmod 0770 {} +
find "${STATE_DIR}" -type f -exec chmod 0660 {} +
find "${LOG_DIR}" -type f -exec chmod 0660 {} +
touch "${LOG_DIR}/java-agent.log"
chmod 0640 "${LOG_DIR}/java-agent.log"
if [[ "${TEST_MODE}" != true ]]; then
  chown run:"${RUNTIME_GROUP}" "${LOG_DIR}/java-agent.log"
fi

SOC2_KEY="${CONFIG_DIR}/soc2-ed25519"
if [[ "${TEST_MODE}" != true && -f "${SOC2_KEY}" ]]; then
  chown run:run "${SOC2_KEY}"
  chmod 0600 "${SOC2_KEY}"
  if [[ -f "${SOC2_KEY}.pub" ]]; then
    chown run:"${RUNTIME_GROUP}" "${SOC2_KEY}.pub"
    chmod 0644 "${SOC2_KEY}.pub"
  fi
fi

install -m 0644 "${PACKAGE_DIR}/systemd/lingxi-nav-console.service" "${SYSTEMD_CONSOLE_NEW}"
install -m 0644 "${PACKAGE_DIR}/systemd/lingxi-java-agent-robot.service" "${SYSTEMD_JAVA_NEW}"
install -m 0755 "${PACKAGE_DIR}/control.sh" "${CONTROL_NEW}"
sudoers_tmp="$(mktemp "${SUDOERS_PATH}.XXXXXX")"
SUDOERS_TMP="${sudoers_tmp}"
cat >"${sudoers_tmp}" <<'EOF'
run ALL=(root) NOPASSWD: /usr/bin/systemctl start lingxi-java-agent-robot.service, /usr/bin/systemctl stop lingxi-java-agent-robot.service, /usr/bin/systemctl restart lingxi-java-agent-robot.service, /usr/bin/systemctl is-active lingxi-java-agent-robot.service, /usr/bin/systemctl show * lingxi-java-agent-robot.service
agi ALL=(root) NOPASSWD: /usr/bin/systemctl restart agibot_software.service
EOF
chmod 0440 "${sudoers_tmp}"
if [[ "${TEST_MODE}" != true ]]; then
  visudo -cf "${sudoers_tmp}" >/dev/null
fi

if [[ -f "${CONFIG_DIR}/lingxi-nav.properties" ]]; then install -m 0640 "${CONFIG_DIR}/lingxi-nav.properties" "${PROPERTIES_NEW}"; fi
if [[ -f "${CONFIG_DIR}/runtime.env" ]]; then install -m 0640 "${CONFIG_DIR}/runtime.env" "${RUNTIME_ENV_NEW}"; fi

install -m 0600 "${CONSOLE_ENV_TMP}" "${CONSOLE_ENV_NEW}"
install -m 0660 "${NAV_CONFIG_TMP}" "${NAV_CONFIG_NEW}"
[[ -z "${PROPERTIES_TMP:-}" ]] || install -m 0640 "${PROPERTIES_TMP}" "${PROPERTIES_NEW}"
[[ -z "${RUNTIME_ENV_TMP:-}" ]] || install -m 0640 "${RUNTIME_ENV_TMP}" "${RUNTIME_ENV_NEW}"
[[ ! -f "${PROPERTIES_NEW}" ]] || chmod 0640 "${PROPERTIES_NEW}"
[[ ! -f "${RUNTIME_ENV_NEW}" ]] || chmod 0640 "${RUNTIME_ENV_NEW}"
if [[ "${TEST_MODE}" != true ]]; then
  [[ ! -f "${PROPERTIES_NEW}" ]] || chown root:"${RUNTIME_GROUP}" "${PROPERTIES_NEW}"
  [[ ! -f "${RUNTIME_ENV_NEW}" ]] || chown root:"${RUNTIME_GROUP}" "${RUNTIME_ENV_NEW}"
fi

# Keep the running release intact until the new staged payload and all inputs
# have passed their checks. Stop robot services only at the final switch point.
SWITCH_STARTED=true
if [[ "${TEST_MODE}" != true && -n "${PREVIOUS}" ]]; then
  systemctl_do stop lingxi-nav-console.service lingxi-java-agent-robot.service 2>/dev/null || true
  if [[ -x "${PREVIOUS}/scripts/navigation_control.sh" ]]; then
    ROBOT_LOCAL_NAV_AIMDK_SETUP="${PREVIOUS_AIMDK_SETUP}" runuser -u agi -- "${PREVIOUS}/scripts/navigation_control.sh" stop 2>/dev/null || true
  fi
  if [[ -x "${PREVIOUS}/scripts/robot_voice_bridge_control.sh" ]]; then
    ROBOT_LOCAL_NAV_AIMDK_SETUP="${PREVIOUS_AIMDK_SETUP}" "${PREVIOUS}/scripts/robot_voice_bridge_control.sh" stop 2>/dev/null || true
  fi
fi

if [[ -e "${TARGET}" ]]; then
  TARGET_BACKUP_PATH="${RELEASE_BACKUP_DIR}/${VERSION}-${TIMESTAMP}"
  mv "${TARGET}" "${TARGET_BACKUP_PATH}"
fi
mv "${STAGING}" "${TARGET}"

rm -f "${CURRENT_NEW}"
ln -s "${TARGET}" "${CURRENT_NEW}"
mv -Tf "${CURRENT_NEW}" "${PRODUCT_BASE}/current"
mv -f "${CONSOLE_ENV_NEW}" "${CONFIG_DIR}/console.env"
mv -f "${NAV_CONFIG_NEW}" "${STATE_DIR}/unified_nav_config.json"
if [[ "${TEST_MODE}" != true ]]; then
  chown agi:"${RUNTIME_GROUP}" "${STATE_DIR}/unified_nav_config.json"
fi
[[ ! -f "${PROPERTIES_NEW}" ]] || mv -f "${PROPERTIES_NEW}" "${CONFIG_DIR}/lingxi-nav.properties"
[[ ! -f "${RUNTIME_ENV_NEW}" ]] || mv -f "${RUNTIME_ENV_NEW}" "${CONFIG_DIR}/runtime.env"
mv -f "${SYSTEMD_CONSOLE_NEW}" "${SYSTEMD_CONSOLE_PATH}"
mv -f "${SYSTEMD_JAVA_NEW}" "${SYSTEMD_JAVA_PATH}"
mv -f "${CONTROL_NEW}" "${CONTROL_PATH}"
mv -f "${sudoers_tmp}" "${SUDOERS_PATH}"
[[ -d "${PRODUCT_BASE}/current" ]] || { echo "当前版本链接创建失败：${PRODUCT_BASE}/current" >&2; exit 1; }
if [[ -n "${PREVIOUS}" && "${PREVIOUS}" != "${TARGET}" ]]; then
  basename "${PREVIOUS}" >"${STATE_DIR}/previous-version"; chmod 0600 "${STATE_DIR}/previous-version"
fi

if [[ "${TEST_MODE}" == true ]]; then
  python3 "${TARGET}/scripts/verify_payload.py" --root "${TARGET}" --manifest "${TARGET}/payload-manifest.json"
  INSTALL_COMMITTED=true
  echo "内部明文版 ${VERSION} 隔离安装成功"
  exit 0
fi

systemctl_do daemon-reload
systemctl_do enable lingxi-nav-console.service >/dev/null
systemctl_do restart lingxi-nav-console.service
if [[ -n "${PREVIOUS}" ]]; then systemctl_do enable lingxi-java-agent-robot.service >/dev/null; systemctl_do restart lingxi-java-agent-robot.service; fi
if [[ "${SETUP_SOC2}" == true ]]; then
  "${TARGET}/scripts/setup_soc2_access.sh"
fi
for _ in $(seq 1 30); do
  curl -fsS --max-time 2 http://127.0.0.1:3000/health >/dev/null && {
    INSTALL_COMMITTED=true
    echo "内部明文版 ${VERSION} 安装成功：http://机器人IP:3000"
    if [[ -z "${PREVIOUS}" ]]; then
      echo "网页安装完成；执行 sudo sparkrobot-lingxi-internal start 启动导航与语音服务"
    else
      echo "网页及 Java Agent 服务已升级；执行 sudo sparkrobot-lingxi-internal start 启动 R19 全部服务"
    fi
    echo "运维命令：sudo sparkrobot-lingxi-internal status"
    exit 0
  }
  sleep 1
done
echo "网页控制台未通过健康检查" >&2
journalctl -u lingxi-nav-console.service -n 100 --no-pager >&2 || true
echo "将通过安装事务回滚恢复先前版本及服务配置" >&2
exit 1
