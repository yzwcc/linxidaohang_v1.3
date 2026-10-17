#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_DIR="${SCRIPT_DIR}"
SECRETS_FILE=""
SOC2_PRIVATE_KEY=""
REINSTALL=false
INSTALL_ROOT="${SPARKROBOT_INSTALL_ROOT:-/}"
INSTALL_TEST_MODE="${SPARKROBOT_INSTALL_TEST_MODE:-false}"
RUNTIME_GROUP="${SPARKROBOT_RUNTIME_GROUP:-sparkrobot-nav}"

[[ "${INSTALL_ROOT}" == /* ]] || { echo "SPARKROBOT_INSTALL_ROOT 必须是绝对路径" >&2; exit 2; }
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
CONFIG_DIR="$(root_path /etc/sparkrobot/lingxi-nav)"
STATE_DIR="$(root_path /var/lib/sparkrobot/lingxi-nav)"
LOG_DIR="$(root_path /var/log/sparkrobot/lingxi-nav)"
CONFIG_BACKUP_DIR="${CONFIG_DIR}/backups"
RELEASE_BACKUP_DIR="${PRODUCT_BASE}/reinstall-backups"

[[ "${RUNTIME_GROUP}" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || {
  echo "运行组名称不安全：${RUNTIME_GROUP}" >&2
  exit 2
}

prepare_runtime_layout() {
  install -d -m 0755 "$(root_path /opt/sparkrobot)" "$(root_path /etc/sparkrobot)" \
    "$(root_path /var/lib/sparkrobot)" "$(root_path /var/log/sparkrobot)"
  if [[ "${INSTALL_TEST_MODE}" == true ]]; then
    install -d -m 0750 "${PRODUCT_BASE}" "${PRODUCT_BASE}/releases" \
      "${CONFIG_DIR}" "${CONFIG_DIR}/releases"
    install -d -m 0700 "${CONFIG_BACKUP_DIR}" "${RELEASE_BACKUP_DIR}"
    install -d -m 0770 "${STATE_DIR}" "${STATE_DIR}/maps" "${STATE_DIR}/pids" "${LOG_DIR}"
    return
  fi

  for user in agi run; do
    getent passwd "${user}" >/dev/null || { echo "机器人缺少用户 ${user}" >&2; exit 1; }
  done
  getent group "${RUNTIME_GROUP}" >/dev/null || groupadd --system "${RUNTIME_GROUP}"
  usermod -a -G "${RUNTIME_GROUP}" agi
  usermod -a -G "${RUNTIME_GROUP}" run
  install -d -m 0750 -o root -g "${RUNTIME_GROUP}" \
    "${PRODUCT_BASE}" "${PRODUCT_BASE}/releases" "${CONFIG_DIR}" "${CONFIG_DIR}/releases"
  install -d -m 0700 -o root -g root "${CONFIG_BACKUP_DIR}" "${RELEASE_BACKUP_DIR}"
  install -d -m 0770 -o agi -g "${RUNTIME_GROUP}" \
    "${STATE_DIR}" "${STATE_DIR}/maps" "${STATE_DIR}/pids" "${LOG_DIR}"
}

migrate_legacy_backups() {
  local legacy="${STATE_DIR}/backups" destination
  [[ -d "${legacy}" ]] || return 0
  if find "${legacy}" -mindepth 1 -print -quit | grep -q .; then
    destination="${CONFIG_BACKUP_DIR}/legacy-state-backups-$(date -u +%Y%m%dT%H%M%SZ)"
    install -d -m 0700 "${destination}"
    cp -a "${legacy}/." "${destination}/"
    rm -rf -- "${legacy}"
    [[ "${INSTALL_TEST_MODE}" == true ]] || chown -R root:root "${destination}"
    find "${destination}" -type d -exec chmod 0700 {} +
    find "${destination}" -type f -exec chmod 0600 {} +
  else
    rmdir "${legacy}"
  fi
}

usage() {
  cat <<'EOF'
用法：install_product.sh --secrets FILE [选项]
  --release-dir DIR       已解包的设备绑定发布目录
  --secrets FILE          厂商临时密钥文件；升级且保留原配置时可省略
  --soc2-private-key FILE 已由厂商授权到 SOC2 的专用私钥
  --reinstall             允许重新安装相同版本
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-dir) RELEASE_DIR="$2"; shift 2 ;;
    --secrets) SECRETS_FILE="$2"; shift 2 ;;
    --soc2-private-key) SOC2_PRIVATE_KEY="$2"; shift 2 ;;
    --reinstall) REINSTALL=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "${EUID}" -eq 0 ]] || { echo "安装必须由厂商以 root 身份执行" >&2; exit 1; }
RELEASE_DIR="$(cd "${RELEASE_DIR}" && pwd)"
for file in manifest.json manifest.sig vendor-public.pem license/license.json license/license.sig; do
  [[ -f "${RELEASE_DIR}/${file}" ]] || { echo "发布包缺少 ${file}" >&2; exit 1; }
done

ARCH="$(uname -m | tr '[:upper:]' '[:lower:]')"
if [[ "${ARCH}" != "aarch64" && "${ARCH}" != "arm64" && "${SPARKROBOT_ALLOW_NON_ARM64:-false}" != true ]]; then
  echo "目标不是 ARM64 机器人，拒绝安装：${ARCH}" >&2
  exit 1
fi
command -v python3 >/dev/null
command -v openssl >/dev/null
command -v ssh-keygen >/dev/null

MANIFEST_JSON="${RELEASE_DIR}/manifest.json"
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["version"])' "${MANIFEST_JSON}")"
[[ "${VERSION}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || { echo "发布版本号不安全" >&2; exit 1; }
VERSION_ROOT_REL="opt/sparkrobot/lingxi-nav/releases/${VERSION}"
VERIFY_DIR="${RELEASE_DIR}/rootfs/${VERSION_ROOT_REL}/system/usr/lib/sparkrobot-license"

if [[ -f "${CONFIG_DIR}/vendor-public.pem" ]] && ! cmp -s "${CONFIG_DIR}/vendor-public.pem" "${RELEASE_DIR}/vendor-public.pem"; then
  echo "发布包签名公钥与机器人已信任的厂商公钥不一致" >&2
  exit 1
fi

python3 "${VERIFY_DIR}/verify_manifest.py" \
  --root "${RELEASE_DIR}/rootfs" --manifest "${MANIFEST_JSON}" \
  --signature "${RELEASE_DIR}/manifest.sig" --public-key "${RELEASE_DIR}/vendor-public.pem" \
  --apply-modes --quiet
if [[ "${INSTALL_TEST_MODE}" == true ]]; then
  openssl dgst -sha256 -verify "${RELEASE_DIR}/vendor-public.pem" \
    -signature "${RELEASE_DIR}/license/license.sig" "${RELEASE_DIR}/license/license.json" >/dev/null
  python3 - "${RELEASE_DIR}/license/license.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
required = {"console", "navigation", "voice", "tour", "map-editor"}
if data.get("format") != "sparkrobot-license-v1" or data.get("product_id") != "sparkrobot-lingxi-nav":
    raise SystemExit("隔离测试许可证格式无效")
if not required.issubset(set(data.get("features", []))):
    raise SystemExit("隔离测试许可证功能授权不完整")
PY
else
  python3 "${VERIFY_DIR}/verify_license.py" \
    --license "${RELEASE_DIR}/license/license.json" --signature "${RELEASE_DIR}/license/license.sig" \
    --public-key "${RELEASE_DIR}/vendor-public.pem" --require-feature console \
    --require-feature navigation --require-feature voice --require-feature tour --require-feature map-editor --quiet
fi

python3 - "${MANIFEST_JSON}" "${RELEASE_DIR}/license/license.json" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
license_data = json.load(open(sys.argv[2], encoding="utf-8"))
for field in ("license_id", "device_fingerprint"):
    if manifest.get(field) != license_data.get(field):
        raise SystemExit(f"发布包与许可证的 {field} 不一致")
PY

if [[ -z "${SECRETS_FILE}" && ! -f "${CONFIG_DIR}/lingxi-nav.properties" ]]; then
  echo "首次安装必须通过 --secrets 提供独立的灵犀导航密钥文件" >&2
  exit 1
fi
[[ -z "${SECRETS_FILE}" || -f "${SECRETS_FILE}" ]] || { echo "密钥文件不存在" >&2; exit 1; }
[[ -z "${SOC2_PRIVATE_KEY}" || -f "${SOC2_PRIVATE_KEY}" ]] || { echo "SOC2 私钥不存在" >&2; exit 1; }

prepare_runtime_layout
migrate_legacy_backups
TARGET="${PRODUCT_BASE}/releases/${VERSION}"
if [[ -e "${TARGET}" && "${REINSTALL}" != true ]]; then
  echo "版本 ${VERSION} 已安装；如需修复安装请显式使用 --reinstall" >&2
  exit 1
fi

PREVIOUS=""
[[ -L "${PRODUCT_BASE}/current" ]] && PREVIOUS="$(readlink -f "${PRODUCT_BASE}/current")"
systemctl_do stop lingxi-nav-console.service lingxi-java-agent-robot.service 2>/dev/null || true
if [[ "${INSTALL_TEST_MODE}" != true && -n "${PREVIOUS}" && -x "${PREVIOUS}/scripts/robot_local_navigation_v3.sh" ]]; then
  runuser -u agi -- "${PREVIOUS}/scripts/robot_local_navigation_v3.sh" stop 2>/dev/null || true
fi

STAGING="${PRODUCT_BASE}/releases/.${VERSION}.installing.$$"
rm -rf -- "${STAGING}"
mkdir -p "${STAGING}"
cp -a "${RELEASE_DIR}/rootfs/${VERSION_ROOT_REL}/." "${STAGING}/"
[[ "${INSTALL_TEST_MODE}" == true ]] || chown -R root:"${RUNTIME_GROUP}" "${STAGING}"
find "${STAGING}" -type d -exec chmod 0750 {} +
if [[ -e "${TARGET}" ]]; then
  BACKUP="${RELEASE_BACKUP_DIR}/${VERSION}-reinstall-$(date -u +%Y%m%dT%H%M%SZ)"
  mv "${TARGET}" "${BACKUP}"
fi
mv "${STAGING}" "${TARGET}"

mkdir -p "${CONFIG_DIR}/releases/${VERSION}"
chmod 0750 "${CONFIG_DIR}/releases/${VERSION}"
install -m 0440 "${RELEASE_DIR}/manifest.json" "${CONFIG_DIR}/releases/${VERSION}/manifest.json"
install -m 0440 "${RELEASE_DIR}/manifest.sig" "${CONFIG_DIR}/releases/${VERSION}/manifest.sig"
install -m 0440 "${RELEASE_DIR}/vendor-public.pem" "${CONFIG_DIR}/vendor-public.pem"
install -m 0444 "${RELEASE_DIR}/license/license.json" "${CONFIG_DIR}/license.json"
install -m 0444 "${RELEASE_DIR}/license/license.sig" "${CONFIG_DIR}/license.sig"
if [[ "${INSTALL_TEST_MODE}" != true ]]; then
  chown -R root:"${RUNTIME_GROUP}" "${CONFIG_DIR}/releases" \
    "${CONFIG_DIR}/vendor-public.pem" "${CONFIG_DIR}/license.json" "${CONFIG_DIR}/license.sig"
fi

TOKEN_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
CONSOLE_KEY="${CONFIG_DIR}/console-ed25519"
if [[ ! -f "${CONSOLE_KEY}" ]]; then
  ssh-keygen -q -t ed25519 -N '' -C sparkrobot-console -f "${CONSOLE_KEY}"
fi
chmod 0600 "${CONSOLE_KEY}"
chmod 0644 "${CONSOLE_KEY}.pub"

authorize_console_key() {
  local user="$1" home group auth line key_blob temporary
  home="$(getent passwd "${user}" | cut -d: -f6)"
  group="$(id -gn "${user}")"
  [[ -n "${home}" && -d "${home}" ]] || { echo "机器人缺少用户 ${user}" >&2; exit 1; }
  install -d -m 0700 -o "${user}" -g "${group}" "${home}/.ssh"
  auth="${home}/.ssh/authorized_keys"
  touch "${auth}"
  chown "${user}:${group}" "${auth}"
  chmod 0600 "${auth}"
  key_blob="$(awk '{print $2}' "${CONSOLE_KEY}.pub")"
  [[ -n "${key_blob}" ]] || { echo "控制台 SSH 公钥格式无效" >&2; exit 1; }
  line="from=\"127.0.0.1,::1\",restrict $(<"${CONSOLE_KEY}.pub")"
  temporary="$(mktemp "${auth}.sparkrobot.XXXXXX")"
  awk -v key_blob="${key_blob}" 'index($0, key_blob) == 0' "${auth}" >"${temporary}"
  printf '%s\n' "${line}" >>"${temporary}"
  install -m 0600 -o "${user}" -g "${group}" "${temporary}" "${auth}"
  rm -f -- "${temporary}"
}
if [[ "${INSTALL_TEST_MODE}" != true ]]; then
  authorize_console_key agi
  authorize_console_key run
fi

if [[ -n "${SOC2_PRIVATE_KEY}" && "${INSTALL_TEST_MODE}" != true ]]; then
  install -m 0600 -o root -g root "${SOC2_PRIVATE_KEY}" "${CONFIG_DIR}/soc2-ed25519"
fi

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
for file in console.env lingxi-nav.properties unified_nav_config.json; do
  if [[ -f "${CONFIG_DIR}/${file}" ]]; then
    install -m 0600 "${CONFIG_DIR}/${file}" "${CONFIG_BACKUP_DIR}/${file}.${TIMESTAMP}"
  fi
done

if [[ -n "${SECRETS_FILE}" ]]; then
  python3 - "${SECRETS_FILE}" "${CONFIG_DIR}/lingxi-nav.properties" <<'PY'
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
        raise SystemExit(f"密钥文件包含无效行：{raw}")
    values[key.strip()] = value.strip()
required = ["LINGXI_APP_ID", "LINGXI_APP_KEY", "LINGXI_APP_SECRET", "DOUBAO_API_KEY"]
missing = [key for key in required if not values.get(key)]
if missing:
    raise SystemExit("密钥文件缺少：" + ", ".join(missing))
for key in required:
    if any(char in values[key] for char in "\r\n"):
        raise SystemExit(f"{key} 包含非法换行")
text = f"""lingxi.url=wss://open.agibot.com/api/V1/open-portal/app/wss/agent-sdk
lingxi.appId={values['LINGXI_APP_ID']}
lingxi.appKey={values['LINGXI_APP_KEY']}
lingxi.callbacks=asr2llm,audio2tts
lingxi.appSecret={values['LINGXI_APP_SECRET']}
doubao.url=https://ark.cn-beijing.volces.com/api/v3/chat/completions
doubao.apiKey={values['DOUBAO_API_KEY']}
doubao.model={values.get('DOUBAO_MODEL', 'doubao-seed-2-0-mini-260428')}
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
Path(sys.argv[2]).write_text(text, encoding="utf-8")
os.chmod(sys.argv[2], 0o640)
PY
  [[ "${INSTALL_TEST_MODE}" == true ]] || chown root:"${RUNTIME_GROUP}" "${CONFIG_DIR}/lingxi-nav.properties"
fi

python3 - "${TARGET}/templates/unified_nav_config.template.json" "${STATE_DIR}/unified_nav_config.json" "${SECRETS_FILE}" "${INSTALL_ROOT}" <<'PY'
import json
import os
import sys
from pathlib import Path

template_path, output_path, secrets_path, install_root = map(Path, sys.argv[1:])
output = json.loads(template_path.read_text(encoding="utf-8"))
existing = Path(output_path)

def rooted(path: str) -> Path:
    return Path(path) if str(install_root) == "/" else install_root / path.lstrip("/")

old_candidates = [
    existing,
    rooted("/etc/sparkrobot/lingxi-nav/unified_nav_config.json"),
    rooted("/home/agi/robot_local_navigation_v3/unified_nav_config.json"),
    rooted("/agibot/data/home/agi/robot_local_navigation_v3/unified_nav_config.json"),
]
for candidate in old_candidates:
    if candidate.is_file():
        try:
            previous = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for key in ("navigation", "maps", "points", "exports"):
            if key in previous:
                output[key] = previous[key]
        if isinstance(previous.get("app"), dict):
            output["app"].update({key: value for key, value in previous["app"].items() if key != "doubao_api_key"})
        break
if secrets_path.is_file():
    values = {}
    for raw in secrets_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    output["app"]["doubao_api_key"] = values.get("DOUBAO_API_KEY", "")
    output["app"]["doubao_model"] = values.get("DOUBAO_MODEL", output["app"].get("doubao_model"))
elif existing.is_file():
    previous = json.loads(existing.read_text(encoding="utf-8"))
    output["app"]["doubao_api_key"] = previous.get("app", {}).get("doubao_api_key", "")
existing.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
os.chmod(existing, 0o660)
PY
[[ "${INSTALL_TEST_MODE}" == true ]] || chown agi:"${RUNTIME_GROUP}" "${STATE_DIR}/unified_nav_config.json"
rm -f "${CONFIG_DIR}/unified_nav_config.json"
ln -s "${STATE_DIR}/unified_nav_config.json" "${CONFIG_DIR}/unified_nav_config.json"

if ! find "${STATE_DIR}/maps" -maxdepth 1 -type f -name '*.json' -print -quit | grep -q .; then
  for old_maps in "$(root_path /home/agi/robot_local_navigation_v3/maps)" "$(root_path /agibot/data/home/agi/robot_local_navigation_v3/maps)"; do
    if [[ -d "${old_maps}" ]]; then
      cp -a "${old_maps}/." "${STATE_DIR}/maps/"
      break
    fi
  done
fi
[[ "${INSTALL_TEST_MODE}" == true ]] || chown -R agi:"${RUNTIME_GROUP}" "${STATE_DIR}" "${LOG_DIR}"
find "${STATE_DIR}" -type d -exec chmod 0770 {} +
find "${STATE_DIR}" -type f -exec chmod 0660 {} +
chmod 0770 "${LOG_DIR}"
find "${LOG_DIR}" -type f -exec chmod 0660 {} +

cat >"${CONFIG_DIR}/console.env" <<EOF
DEPLOYMENT_MODE=robot
HOST=0.0.0.0
PORT=3000
PUBLIC_BASE_URL=http://127.0.0.1:3000
ROBOT_HOST=127.0.0.1
ROBOT_CONNECTION_MODE=direct
ROBOT_BRIDGE_PORT=18082
ROBOT_WEB_PORT=8002
ROBOT_MAP_ROOT=/agibot/data/var/MapManagerModule/
ROBOT_SSH_USER=agi
ROBOT_SSH_PRIVATE_KEY_PATH=${CONSOLE_KEY}
ROBOT_SYSTEM_SSH_USER=run
ROBOT_SYSTEM_SSH_PRIVATE_KEY_PATH=${CONSOLE_KEY}
ROBOT_NAV_HOME=${PRODUCT_BASE}/current
JAVA_AGENT_EXECUTION_MODE=robot
ROBOT_JAVA_AGENT_HOME=${PRODUCT_BASE}/current/scripts
ROBOT_JAVA_AGENT_BRIDGE_PORT=18083
ROBOT_VOICE_RESTART_COMMAND=${PRODUCT_BASE}/current/scripts/restart_robot_voice_stack.sh
JAVA_AGENT_AUTO_START=false
DEV_AUTH_BYPASS=true
TOKEN_SECRET=${TOKEN_SECRET}
ROBOT_RESOURCE_USER=run
EOF
chmod 0600 "${CONFIG_DIR}/console.env"

cp -a "${TARGET}/system/." "${INSTALL_ROOT}/"
chmod 0550 "$(root_path /usr/local/libexec/sparkrobot-license-verify)" "$(root_path /usr/local/libexec/sparkrobot-release-verify)" "$(root_path /usr/local/sbin/sparkrobot-lingxi-product)"
chmod 0440 "$(root_path /etc/sudoers.d/sparkrobot-lingxi-nav)"
chmod 0644 "$(root_path /etc/systemd/system/lingxi-nav-console.service)" "$(root_path /etc/systemd/system/lingxi-java-agent-robot.service)"
if [[ "${INSTALL_TEST_MODE}" != true ]]; then
  chown root:"${RUNTIME_GROUP}" \
    "$(root_path /usr/local/libexec/sparkrobot-license-verify)" \
    "$(root_path /usr/local/libexec/sparkrobot-release-verify)" \
    "$(root_path /usr/local/sbin/sparkrobot-lingxi-product)"
fi
rm -f "${PRODUCT_BASE}/current.new"
ln -s "${TARGET}" "${PRODUCT_BASE}/current.new"
mv -Tf "${PRODUCT_BASE}/current.new" "${PRODUCT_BASE}/current"
if [[ -n "${PREVIOUS}" && "${PREVIOUS}" != "${TARGET}" ]]; then
  basename "${PREVIOUS}" >"${STATE_DIR}/previous-version"
  chmod 0600 "${STATE_DIR}/previous-version"
fi

systemctl_do daemon-reload
systemctl_do enable lingxi-nav-console.service lingxi-java-agent-robot.service >/dev/null
systemctl_do restart lingxi-nav-console.service

if [[ "${INSTALL_TEST_MODE}" == true ]]; then
  python3 "${VERIFY_DIR}/verify_manifest.py" \
    --root "${INSTALL_ROOT}" --manifest "${CONFIG_DIR}/releases/${VERSION}/manifest.json" \
    --signature "${CONFIG_DIR}/releases/${VERSION}/manifest.sig" \
    --public-key "${CONFIG_DIR}/vendor-public.pem" --check-modes --quiet
  python3 "${VERIFY_DIR}/verify_stable.py" \
    --root "${INSTALL_ROOT}" --list "${TARGET}/stable-files.json" --quiet
  echo "灵犀导航产品 ${VERSION} 隔离安装验证成功"
  exit 0
fi

for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:3000/health >/dev/null; then
    /usr/local/libexec/sparkrobot-license-verify --quiet
    /usr/local/libexec/sparkrobot-release-verify --quiet
    echo "灵犀导航产品 ${VERSION} 安装成功，网页地址：http://机器人IP:3000"
    [[ -f "${CONFIG_DIR}/soc2-ed25519" ]] || echo "提示：尚未配置 SOC2 专用私钥，一键语音启动前需由厂商完成 SOC2 授权"
    exit 0
  fi
  sleep 1
done

echo "控制台安装后未通过健康检查" >&2
journalctl -u lingxi-nav-console.service -n 100 --no-pager >&2 || true
if [[ -n "${PREVIOUS}" && -d "${PREVIOUS}" ]]; then
  ln -sfn "${PREVIOUS}" "${PRODUCT_BASE}/current"
  cp -a "${PREVIOUS}/system/." "${INSTALL_ROOT}/"
  systemctl_do daemon-reload
  systemctl_do restart lingxi-nav-console.service 2>/dev/null || true
  echo "已自动恢复到版本 $(basename "${PREVIOUS}")" >&2
fi
exit 1
