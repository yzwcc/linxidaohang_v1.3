#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 sudo 运行此安装脚本" >&2
  exit 1
fi

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_ROOT="${TARGET_ROOT:-/opt/lingxi-nav-console}"
NAV_HOME="${ROBOT_NAV_HOME:-/home/agi/robot_local_navigation_v3}"
AIMDK_SETUP="${ROBOT_AIMDK_SETUP:-}"

if [[ "${TARGET_ROOT}" != /* ]]; then
  echo "TARGET_ROOT 必须是绝对路径" >&2
  exit 1
fi
if [[ "${NAV_HOME}" != /* ]]; then
  echo "ROBOT_NAV_HOME 必须是绝对路径" >&2
  exit 1
fi
if [[ "${TARGET_ROOT}" == "/" || "${NAV_HOME}" == "/" ]]; then
  echo "拒绝使用文件系统根目录作为安装位置" >&2
  exit 1
fi

for required in \
  "${PACKAGE_DIR}/payload/server/dist/index.js" \
  "${PACKAGE_DIR}/payload/server/public/index.html" \
  "${PACKAGE_DIR}/payload/server/node_modules/fastify/package.json" \
  "${PACKAGE_DIR}/payload/runtime/bin/node" \
  "${PACKAGE_DIR}/payload/navigation/robot_local_navigation_v3/mapping_control.py" \
  "${PACKAGE_DIR}/payload/navigation/scripts/robot_local_navigation_v3.sh" \
  "${PACKAGE_DIR}/payload/navigation/scripts/start_voice_nav_gateway.sh" \
  "${PACKAGE_DIR}/server.env.template"; do
  if [[ ! -e "${required}" ]]; then
    echo "增量包不完整，缺少：${required}" >&2
    exit 1
  fi
done

if [[ -z "${AIMDK_SETUP}" || ! -r "${AIMDK_SETUP}" ]]; then
  echo "必须设置 ROBOT_AIMDK_SETUP 为机器人上已安装的 AIMDK v1.1.0 install/setup.bash" >&2
  exit 1
fi
if ! bash -lc 'source "$1" && python3 -c "from aimdk_msgs.msg import PncTaskRequest; from aimdk_msgs.srv import GetStoredMapByName"' _ "${AIMDK_SETUP}"; then
  echo "当前 AIMDK overlay 缺少 v1.1.0 导航/建图接口，拒绝安装" >&2
  exit 1
fi
python3 "${PACKAGE_DIR}/verify_payload.py" "${PACKAGE_DIR}/payload" "${PACKAGE_DIR}/payload-manifest.json"
state_file="${ROBOT_LOCAL_NAV_MAPPING_STATE:-${NAV_HOME}/mapping-session.json}"
if [[ -f "${state_file}" ]] && python3 - "${state_file}" <<'PY'
import json, sys
from pathlib import Path
try:
    state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(0)
last = state.get("last_result") or {}
active = bool(state.get("active_map_name")) or last.get("status") in {"saving", "save_timeout"}
raise SystemExit(1 if active else 0)
PY
then
  echo "机器人仍有建图采集/保存会话；请先在网页停止并确认保存，再安装更新" >&2
  exit 1
fi

timestamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="${TARGET_ROOT}/backups/r18-aimdk-v1.1.0-${timestamp}"
install -d -m 0755 "${TARGET_ROOT}/server" "${NAV_HOME}" "${backup_dir}"

backup_path() {
  local path="$1"
  if [[ -e "${path}" || -L "${path}" ]]; then
    local relative="${path#/}"
    install -d -m 0755 "${backup_dir}/$(dirname "${relative}")"
    cp -a "${path}" "${backup_dir}/${relative}"
  fi
}

for path in \
  "${TARGET_ROOT}/server/dist" \
  "${TARGET_ROOT}/server/public" \
  "${TARGET_ROOT}/server/node_modules" \
  "${TARGET_ROOT}/server/package.json" \
  "${TARGET_ROOT}/server/package-lock.json" \
  "${TARGET_ROOT}/runtime" \
  "${NAV_HOME}/robot_local_navigation_v3" \
  "${NAV_HOME}/python_bridge" \
  "${NAV_HOME}/resource" \
  "${NAV_HOME}/scripts/robot_local_navigation_v3.sh" \
  "${NAV_HOME}/scripts/start_lingxi_nav_bridge.sh" \
  "${NAV_HOME}/scripts/start_voice_nav_gateway.sh" \
  "${NAV_HOME}/setup.py" \
  "${NAV_HOME}/package.xml" \
  "${NAV_HOME}/ros_dds_configuration.xml"; do
  backup_path "${path}"
done

install -d -m 0755 "${TARGET_ROOT}/server" "${TARGET_ROOT}/runtime" "${NAV_HOME}/scripts"
for name in dist public node_modules; do
  rm -rf -- "${TARGET_ROOT}/server/${name}"
  cp -a "${PACKAGE_DIR}/payload/server/${name}" "${TARGET_ROOT}/server/${name}"
done
cp -a "${PACKAGE_DIR}/payload/server/package.json" "${PACKAGE_DIR}/payload/server/package-lock.json" "${TARGET_ROOT}/server/"
rm -rf -- "${TARGET_ROOT}/runtime"
cp -a "${PACKAGE_DIR}/payload/runtime" "${TARGET_ROOT}/runtime"

for name in robot_local_navigation_v3 python_bridge resource; do
  rm -rf -- "${NAV_HOME}/${name}"
  cp -a "${PACKAGE_DIR}/payload/navigation/${name}" "${NAV_HOME}/${name}"
done
for name in robot_local_navigation_v3.sh start_lingxi_nav_bridge.sh start_voice_nav_gateway.sh; do
  install -m 0755 "${PACKAGE_DIR}/payload/navigation/scripts/${name}" "${NAV_HOME}/scripts/${name}"
done
for name in setup.py package.xml ros_dds_configuration.xml; do
  install -m 0644 "${PACKAGE_DIR}/payload/navigation/${name}" "${NAV_HOME}/${name}"
done

env_file="${TARGET_ROOT}/server/.env"
if [[ ! -f "${env_file}" ]]; then
  cp "${PACKAGE_DIR}/server.env.template" "${env_file}"
else
  backup_path "${env_file}"
fi
set_env() {
  local key="$1" value="$2"
  local temp_file="${env_file}.tmp"
  grep -v "^${key}=" "${env_file}" >"${temp_file}" || true
  printf '%s=%s\n' "${key}" "${value}" >>"${temp_file}"
  chmod --reference="${env_file}" "${temp_file}"
  mv "${temp_file}" "${env_file}"
}
set_env DEPLOYMENT_MODE robot
set_env HOST 0.0.0.0
set_env ROBOT_HOST 127.0.0.1
set_env ROBOT_BRIDGE_PORT 18082
set_env ROBOT_NAV_HOME "${NAV_HOME}"
set_env ROBOT_AIMDK_SETUP "${AIMDK_SETUP}"
set_env DEPLOYMENT_VERSION "$(sed -n 's/^VERSION = "\([^"]*\)"$/\1/p' "${PACKAGE_DIR}/build_package.py")"
chmod 0600 "${env_file}"

chown -R agi:agi "${NAV_HOME}/robot_local_navigation_v3" "${NAV_HOME}/python_bridge" "${NAV_HOME}/resource"
chown agi:agi "${NAV_HOME}/scripts/robot_local_navigation_v3.sh" "${NAV_HOME}/scripts/start_lingxi_nav_bridge.sh"
systemctl daemon-reload
systemctl restart lingxi-nav-console.service

healthy=false
expected_version="$(sed -n 's/^DEPLOYMENT_VERSION=//p' "${env_file}" | tail -n 1)"
for _ in $(seq 1 30); do
  health_payload="$(curl -fsS --max-time 2 http://127.0.0.1:3000/health 2>/dev/null || true)"
  running_version="$(printf '%s' "${health_payload}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version", ""))' 2>/dev/null || true)"
  if [[ -n "${expected_version}" && "${running_version}" == "${expected_version}" ]]; then
    healthy=true
    break
  fi
  sleep 1
done
if [[ "${healthy}" != true ]]; then
  systemctl status lingxi-nav-console.service --no-pager -l || true
  echo "控制台版本健康检查失败：期望 ${expected_version}，实际 ${running_version:-无响应}；更新前备份位于 ${backup_dir}" >&2
  exit 1
fi

echo "R18 增量更新完成。备份：${backup_dir}"
echo "手机/电脑连接机器人局域网后访问 http://机器人IP:3000/console/"
echo "在控制台启动导航服务后进入‘建图采集’；移动机器人请使用外置遥控器。"
