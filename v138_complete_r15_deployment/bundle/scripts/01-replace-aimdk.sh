#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -un)" == "agi" ]] || { echo "请使用 agi 账号执行本脚本" >&2; exit 1; }

ARCHIVE=/home/agi/aimdk-aarch64-1bde262f-artifacts.7z
EXPECTED=f13147a175bc3fb621fb118698aae0353c097adda38bba640f9a39c01433cc98
STAGE=/home/agi/aimdk-staging-v131
NEW_ROOT=${STAGE}/aimdk-aarch64-1bde262f-artifacts/aimdk-aarch64-1bde262f-artifacts
OLD_ROOT=/home/agi/aimdk

[[ -f "${ARCHIVE}" ]] || { echo "缺少 ${ARCHIVE}" >&2; exit 1; }
ACTUAL="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
[[ "${ACTUAL}" == "${EXPECTED}" ]] || { echo "AIMDK SHA-256 校验失败" >&2; exit 1; }
command -v 7z >/dev/null || { echo "缺少 7z，请先执行环境安装脚本" >&2; exit 1; }
command -v colcon >/dev/null || { echo "缺少 colcon，请先执行环境安装脚本" >&2; exit 1; }

rm -rf -- "${STAGE}"
mkdir -p "${STAGE}"
7z x "${ARCHIVE}" -o"${STAGE}"

test -d "${NEW_ROOT}/src"
test -f "${NEW_ROOT}/version"
echo "即将编译：$(<"${NEW_ROOT}/version")"

cd "${NEW_ROOT}"
set +u
source /opt/ros/humble/setup.bash
set -u
colcon build

set +u
source /opt/ros/humble/setup.bash
source "${NEW_ROOT}/install/setup.bash"
set -u
python3 -c "import rclpy; import aimdk_msgs.msg; print('AIMDK_IMPORT_OK')"
ros2 pkg list | grep '^aimdk_msgs$'
test -x "${NEW_ROOT}/install/examples/lib/examples/navigation"

test "$(realpath -m "${OLD_ROOT}")" = "/home/agi/aimdk"
test "$(realpath "${NEW_ROOT}")" = "${NEW_ROOT}"
test -r "${NEW_ROOT}/install/setup.bash"
test -x "${NEW_ROOT}/install/examples/lib/examples/navigation"

BACKUP=""
restore_previous() {
  local code=$?
  trap - ERR
  if [[ -n "${BACKUP}" && -d "${BACKUP}" ]]; then
    if [[ -e "${OLD_ROOT}" ]]; then
      mv -- "${OLD_ROOT}" "${STAGE}/failed-aimdk" || {
        echo "失败版本保留在 ${OLD_ROOT}，旧版备份在 ${BACKUP}，请检查后恢复" >&2
        exit "${code}"
      }
    fi
    mv -- "${BACKUP}" "${OLD_ROOT}" || {
      echo "自动恢复失败，旧 AIMDK 完整保留在 ${BACKUP}" >&2
      exit "${code}"
    }
    echo "AIMDK 安装失败，已恢复原目录：${OLD_ROOT}" >&2
  fi
  exit "${code}"
}

echo "新 AIMDK 已通过验证，开始保留旧目录并切换..."
cd /home/agi
if [[ -e "${OLD_ROOT}" ]]; then
  BACKUP="/home/agi/aimdk-backup-$(date +%Y%m%d-%H%M%S)-$$"
  mv -- "${OLD_ROOT}" "${BACKUP}"
fi
trap restore_previous ERR
mv -- "${NEW_ROOT}" "${OLD_ROOT}"

env -i HOME=/home/agi USER=agi LOGNAME=agi SHELL=/bin/bash \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  LANG=C.UTF-8 bash --noprofile --norc -c '
set -e
set +u
source /opt/ros/humble/setup.bash
source /home/agi/aimdk/install/setup.bash
set -u
cat /home/agi/aimdk/version
python3 -c "import rclpy; import aimdk_msgs.msg; print(\"AIMDK_OK\")"
ros2 pkg list | grep "^aimdk_msgs$"
'

trap - ERR
rm -rf -- "${STAGE}"
[[ -z "${BACKUP}" ]] || echo "旧 AIMDK 备份已保留：${BACKUP}"

echo "检查机器人当前 ROS 话题（未启动底层服务时只提示，不影响 AIMDK 安装结果）："
set +e
for topic in /slam/lidar_odom /aima/te/pnc_task_request /map; do
  if timeout 10 ros2 topic info "${topic}" >/dev/null 2>&1; then
    echo "[可见] ${topic}"
  else
    echo "[提示] ${topic} 当前不可见，待机器人底层服务启动后再检查"
  fi
done
set -e

echo "[通过] AIMDK 替换完成，原始 7z 保留在 ${ARCHIVE}"
