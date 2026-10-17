#!/usr/bin/env bash
set -euo pipefail
umask 077

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS_FILE=""
REINSTALL=false
SETUP_SOC2=false
INSTALL_ROOT="${SPARKROBOT_INSTALL_ROOT:-/}"
TEST_MODE="${SPARKROBOT_INSTALL_TEST_MODE:-false}"
RUNTIME_GROUP="${SPARKROBOT_RUNTIME_GROUP:-sparkrobot-nav}"

usage() {
  cat <<'EOF'
用法：sudo ./install.sh --secrets ./secrets.env [选项]
  --secrets FILE   首次安装必填；升级且保留旧配置时可省略
  --setup-soc2     安装后交互配置 SOC2 语音运维密钥
  --reinstall      允许覆盖安装同版本，旧版自动备份
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --secrets) SECRETS_FILE="$2"; shift 2 ;;
    --setup-soc2) SETUP_SOC2=true; shift ;;
    --reinstall) REINSTALL=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "${EUID}" -eq 0 ]] || { echo "请使用 sudo 运行安装" >&2; exit 1; }
[[ "${INSTALL_ROOT}" == /* ]] || { echo "SPARKROBOT_INSTALL_ROOT 必须是绝对路径" >&2; exit 2; }
INSTALL_ROOT="${INSTALL_ROOT%/}"; [[ -n "${INSTALL_ROOT}" ]] || INSTALL_ROOT="/"
[[ "${TEST_MODE}" != true || "${INSTALL_ROOT}" != "/" ]] || { echo "测试模式禁止写入真实根目录" >&2; exit 2; }

root_path() {
  [[ "${INSTALL_ROOT}" == "/" ]] && printf '%s\n' "$1" || printf '%s%s\n' "${INSTALL_ROOT}" "$1"
}
systemctl_do() { [[ "${TEST_MODE}" == true ]] || systemctl "$@"; }

PRODUCT_BASE="$(root_path /opt/sparkrobot/lingxi-nav)"
CONFIG_DIR="$(root_path /etc/sparkrobot/lingxi-nav)"
STATE_DIR="$(root_path /var/lib/sparkrobot/lingxi-nav)"
LOG_DIR="$(root_path /var/log/sparkrobot/lingxi-nav)"
BACKUP_DIR="${CONFIG_DIR}/backups"
RELEASE_BACKUP_DIR="${PRODUCT_BASE}/reinstall-backups"

for path in release-info.json payload-manifest.json payload scripts templates systemd; do
  [[ -e "${PACKAGE_DIR}/${path}" ]] || { echo "安装包不完整，缺少 ${path}" >&2; exit 1; }
done
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

if [[ "${TEST_MODE}" != true ]]; then
  for user in agi run; do getent passwd "${user}" >/dev/null || { echo "机器人缺少用户 ${user}" >&2; exit 1; }; done
  getent group "${RUNTIME_GROUP}" >/dev/null || groupadd --system "${RUNTIME_GROUP}"
  usermod -a -G "${RUNTIME_GROUP}" agi
  usermod -a -G "${RUNTIME_GROUP}" run
fi

install -d -m 0755 "$(root_path /opt/sparkrobot)" "$(root_path /etc/sparkrobot)" "$(root_path /var/lib/sparkrobot)" "$(root_path /var/log/sparkrobot)"
install -d -m 0755 "$(root_path /etc/systemd)" "$(root_path /etc/systemd/system)" \
  "$(root_path /etc/sudoers.d)" "$(root_path /usr)" "$(root_path /usr/local)" \
  "$(root_path /usr/local/sbin)"
install -d -m 0750 "${PRODUCT_BASE}" "${PRODUCT_BASE}/releases" "${CONFIG_DIR}"
install -d -m 0700 "${BACKUP_DIR}" "${RELEASE_BACKUP_DIR}"
install -d -m 0770 "${STATE_DIR}" "${STATE_DIR}/maps" "${STATE_DIR}/pids" "${LOG_DIR}"
if [[ "${TEST_MODE}" != true ]]; then
  chown root:"${RUNTIME_GROUP}" "${PRODUCT_BASE}" "${PRODUCT_BASE}/releases" "${CONFIG_DIR}"
  chown agi:"${RUNTIME_GROUP}" "${STATE_DIR}" "${STATE_DIR}/maps" "${STATE_DIR}/pids" "${LOG_DIR}"
fi

PREVIOUS=""; [[ -L "${PRODUCT_BASE}/current" ]] && PREVIOUS="$(readlink -f "${PRODUCT_BASE}/current")"
systemctl_do stop lingxi-nav-console.service lingxi-java-agent-robot.service 2>/dev/null || true
if [[ "${TEST_MODE}" != true && -n "${PREVIOUS}" && -x "${PREVIOUS}/scripts/navigation_control.sh" ]]; then
  runuser -u agi -- "${PREVIOUS}/scripts/navigation_control.sh" stop 2>/dev/null || true
  "${PREVIOUS}/scripts/robot_voice_bridge_control.sh" stop 2>/dev/null || true
fi

TARGET="${PRODUCT_BASE}/releases/${VERSION}"
if [[ -e "${TARGET}" && "${REINSTALL}" != true ]]; then
  echo "版本 ${VERSION} 已安装；修复安装请加 --reinstall" >&2; exit 1
fi
STAGING="${PRODUCT_BASE}/releases/.${VERSION}.installing.$$"
rm -rf -- "${STAGING}"; mkdir -p "${STAGING}"
cp -a "${PACKAGE_DIR}/payload/." "${STAGING}/"
cp -a "${PACKAGE_DIR}/scripts" "${PACKAGE_DIR}/templates" "${STAGING}/"
cp -a "${PACKAGE_DIR}/payload-manifest.json" "${PACKAGE_DIR}/release-info.json" "${STAGING}/"
find "${STAGING}" -type d -exec chmod 0750 {} +
find "${STAGING}" -type f -exec chmod 0640 {} +
find "${STAGING}/scripts" -type f -name '*.sh' -exec chmod 0750 {} +
find "${STAGING}/runtime/java/bin" -type f -exec chmod 0750 {} +
chmod 0750 "${STAGING}/runtime/node" "${STAGING}/runtime/java/lib/jspawnhelper"
[[ "${TEST_MODE}" == true ]] || chown -R root:"${RUNTIME_GROUP}" "${STAGING}"
if [[ -e "${TARGET}" ]]; then
  mv "${TARGET}" "${RELEASE_BACKUP_DIR}/${VERSION}-$(date -u +%Y%m%dT%H%M%SZ)"
fi
mv "${STAGING}" "${TARGET}"

# Keep the historical script name available for older local tooling and verify
# every script used by the web console before switching the current release.
ln -sfn "${TARGET}/scripts/java_agent_control.sh" "${TARGET}/scripts/lingxi_java_agent_control.sh"
for script in navigation_control.sh restart_robot_voice_stack.sh java_agent_control.sh lingxi_java_agent_control.sh; do
  [[ -x "${TARGET}/scripts/${script}" ]] || { echo "安装包缺少运行脚本：${script}" >&2; exit 1; }
done

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
for file in console.env lingxi-nav.properties runtime.env; do
  [[ -f "${CONFIG_DIR}/${file}" ]] && install -m 0600 "${CONFIG_DIR}/${file}" "${BACKUP_DIR}/${file}.${TIMESTAMP}"
done

if [[ -n "${SECRETS_FILE}" ]]; then
  python3 - "${SECRETS_FILE}" "${CONFIG_DIR}/lingxi-nav.properties" "${CONFIG_DIR}/runtime.env" <<'PY'
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

python3 - "${TARGET}/templates/unified_nav_config.json" "${STATE_DIR}/unified_nav_config.json" "${SECRETS_FILE}" "${INSTALL_ROOT}" <<'PY'
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

cat >"${CONFIG_DIR}/console.env" <<EOF
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
chmod 0600 "${CONFIG_DIR}/console.env"
if [[ "${TEST_MODE}" != true ]]; then
  chown root:"${RUNTIME_GROUP}" "${CONFIG_DIR}/lingxi-nav.properties" "${CONFIG_DIR}/runtime.env"
  chown agi:"${RUNTIME_GROUP}" "${STATE_DIR}/unified_nav_config.json"
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

install -m 0644 "${PACKAGE_DIR}/systemd/lingxi-nav-console.service" "$(root_path /etc/systemd/system/lingxi-nav-console.service)"
install -m 0644 "${PACKAGE_DIR}/systemd/lingxi-java-agent-robot.service" "$(root_path /etc/systemd/system/lingxi-java-agent-robot.service)"
install -m 0755 "${PACKAGE_DIR}/control.sh" "$(root_path /usr/local/sbin/sparkrobot-lingxi-internal)"
sudoers="$(root_path /etc/sudoers.d/sparkrobot-lingxi-internal)"
cat >"${sudoers}" <<'EOF'
run ALL=(root) NOPASSWD: /usr/bin/systemctl start lingxi-java-agent-robot.service, /usr/bin/systemctl stop lingxi-java-agent-robot.service, /usr/bin/systemctl restart lingxi-java-agent-robot.service, /usr/bin/systemctl is-active lingxi-java-agent-robot.service, /usr/bin/systemctl show * lingxi-java-agent-robot.service
agi ALL=(root) NOPASSWD: /usr/bin/systemctl restart agibot_software.service
EOF
chmod 0440 "${sudoers}"

rm -f "${PRODUCT_BASE}/current.new"
ln -s "${TARGET}" "${PRODUCT_BASE}/current.new"
mv -Tf "${PRODUCT_BASE}/current.new" "${PRODUCT_BASE}/current"
[[ -d "${PRODUCT_BASE}/current" ]] || { echo "当前版本链接创建失败：${PRODUCT_BASE}/current" >&2; exit 1; }
if [[ -n "${PREVIOUS}" && "${PREVIOUS}" != "${TARGET}" ]]; then
  basename "${PREVIOUS}" >"${STATE_DIR}/previous-version"; chmod 0600 "${STATE_DIR}/previous-version"
fi

if [[ "${TEST_MODE}" == true ]]; then
  python3 "${TARGET}/scripts/verify_payload.py" --root "${TARGET}" --manifest "${TARGET}/payload-manifest.json"
  echo "内部明文版 ${VERSION} 隔离安装成功"
  exit 0
fi

systemctl_do daemon-reload
systemctl_do enable lingxi-nav-console.service lingxi-java-agent-robot.service >/dev/null
systemctl_do restart lingxi-nav-console.service
if [[ "${SETUP_SOC2}" == true ]]; then
  "${TARGET}/scripts/setup_soc2_access.sh"
fi
for _ in $(seq 1 30); do
  curl -fsS --max-time 2 http://127.0.0.1:3000/health >/dev/null && {
    echo "内部明文版 ${VERSION} 安装成功：http://机器人IP:3000"
    echo "运维命令：sudo sparkrobot-lingxi-internal status"
    exit 0
  }
  sleep 1
done
echo "网页控制台未通过健康检查" >&2
journalctl -u lingxi-nav-console.service -n 100 --no-pager >&2 || true
exit 1
