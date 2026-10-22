#!/usr/bin/env bash
set -Eeuo pipefail
umask 0027

FIX_VERSION="1.0.3-20261023"
FIX_STATE_ROOT="${FIX_STATE_ROOT:-/var/lib/sparkrobot/lingxi-consecutive-nav-fix}"
BACKUP_ROOT="${BACKUP_ROOT:-${FIX_STATE_ROOT}/backups}"
RUNTIME_STATE_ROOT="${SPARKROBOT_STATE_ROOT:-/var/lib/sparkrobot/lingxi-nav}"
LOG_ROOT="${SPARKROBOT_LOG_ROOT:-/var/log/sparkrobot/lingxi-nav}"
RUNTIME_GROUP="${SPARKROBOT_RUNTIME_GROUP:-sparkrobot-nav}"
ROS_SETUP="${ROBOT_LOCAL_NAV_ROS_SETUP:-/opt/ros/humble/setup.bash}"
AIMDK_SETUP="${ROBOT_LOCAL_NAV_AIMDK_SETUP:-/home/agi/aimdk/install/setup.bash}"
DEFAULT_BRIDGE_PORT="${ROBOT_LOCAL_NAVIGATION_V3_BRIDGE_PORT:-18082}"

die() {
  echo "失败：$*" >&2
  return 1
}

require_root() {
  if [[ "${SPARKROBOT_FIX_TEST_MODE:-false}" == "true" ]]; then
    return 0
  fi
  [[ "${EUID}" -eq 0 ]] || die "请使用 run 账号执行 sudo bash ./install.sh，或 sudo sparkrobot-lingxi-consecutive-nav-fix ..."
}

resolve_nav_dir() {
  local candidate="${NAV_DIR:-}"
  if [[ -n "${candidate}" ]]; then
    [[ -d "${candidate}" ]] || die "NAV_DIR 不存在：${candidate}"
    readlink -f "${candidate}"
    return
  fi

  candidate="/opt/sparkrobot/lingxi-nav/current/navigation/robot_local_navigation_v3"
  if [[ -d "${candidate}" ]]; then
    readlink -f "${candidate}"
    return
  fi

  local -a releases=()
  if [[ -d /opt/sparkrobot/lingxi-nav/releases ]]; then
    while IFS= read -r path; do
      releases+=("${path}")
    done < <(
      find /opt/sparkrobot/lingxi-nav/releases -mindepth 3 -maxdepth 3 \
        -type d -path '*/navigation/robot_local_navigation_v3' -print 2>/dev/null | sort
    )
  fi
  if [[ "${#releases[@]}" -eq 1 ]]; then
    echo "提示：current 软链接缺失，唯一可用导航目录为 ${releases[0]}" >&2
    readlink -f "${releases[0]}"
    return
  fi
  die "无法唯一确定导航目录。请先修复 /opt/sparkrobot/lingxi-nav/current，或执行 NAV_DIR=实际目录 sudo -E bash ./install.sh"
}

bridge_pid_file() {
  printf '%s/pids/lingxi_nav_bridge.pid\n' "${RUNTIME_STATE_ROOT}"
}

bridge_log_file() {
  printf '%s/lingxi_nav_bridge.log\n' "${LOG_ROOT}"
}

is_bridge_process() {
  local pid="$1" command_line=""
  [[ -r "/proc/${pid}/cmdline" ]] || return 1
  command_line="$(tr '\0' ' ' < "/proc/${pid}/cmdline")"
  [[ "${command_line}" == *"robot_local_navigation_v3.lingxi_nav_bridge"* \
    || "${command_line}" == *"/lingxi_nav_bridge.py"* ]]
}

read_running_bridge_pid() {
  local pid_file pid=""
  pid_file="$(bridge_pid_file)"
  [[ -r "${pid_file}" ]] && pid="$(tr -cd '0-9' < "${pid_file}")"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    if is_bridge_process "${pid}"; then
      printf '%s\n' "${pid}"
      return 0
    fi
    echo "提示：忽略陈旧 PID 文件，pid=${pid} 不是导航 bridge，绝不会停止该进程" >&2
  fi
  return 1
}

resolve_bridge_run_user() {
  local old_pid="${1:-}" nav_dir="${2:-}" candidate=""
  if [[ -n "${NAV_RUN_USER:-}" ]]; then
    candidate="${NAV_RUN_USER}"
  elif [[ "${SPARKROBOT_FIX_TEST_MODE:-false}" == "true" ]]; then
    candidate="$(id -un)"
  elif id agi >/dev/null 2>&1; then
    # The product console always invokes navigation services as agi. Do not
    # inherit root/run ownership from a bridge started by an earlier hotfix.
    candidate="agi"
  elif [[ -n "${old_pid}" ]]; then
    candidate="$(stat -c '%U' "/proc/${old_pid}")"
  elif [[ -n "${nav_dir}" && -e "${nav_dir}/lingxi_nav_bridge.py" ]]; then
    candidate="$(stat -c '%U' "${nav_dir}/lingxi_nav_bridge.py")"
  fi
  [[ -n "${candidate}" ]] || die "无法确定导航 bridge 运行用户"
  id "${candidate}" >/dev/null 2>&1 || die "导航运行用户不存在：${candidate}"
  if [[ "${SPARKROBOT_FIX_TEST_MODE:-false}" != "true" && "${candidate}" == "root" ]]; then
    die "拒绝以 root 运行导航 bridge；请确认机器人存在 agi 账号"
  fi
  printf '%s\n' "${candidate}"
}

resolve_runtime_group() {
  local run_user="$1"
  if [[ "${SPARKROBOT_FIX_TEST_MODE:-false}" != "true" ]] \
    && getent group "${RUNTIME_GROUP}" >/dev/null 2>&1; then
    printf '%s\n' "${RUNTIME_GROUP}"
    return 0
  fi
  id -gn "${run_user}"
}

repair_runtime_permissions() {
  require_root
  local run_user="${1:-}" runtime_group pid_dir ros_log_dir member
  if [[ -z "${run_user}" ]]; then
    run_user="$(resolve_bridge_run_user "" "$(resolve_nav_dir)")"
  fi
  runtime_group="$(resolve_runtime_group "${run_user}")"
  pid_dir="$(dirname "$(bridge_pid_file)")"
  ros_log_dir="${ROBOT_LOCAL_NAV_ROS_LOG_DIR:-${LOG_ROOT}/ros}"

  if [[ "${SPARKROBOT_FIX_TEST_MODE:-false}" != "true" && "${runtime_group}" == "${RUNTIME_GROUP}" ]]; then
    for member in agi run "${run_user}"; do
      if id "${member}" >/dev/null 2>&1 \
        && ! id -nG "${member}" | tr ' ' '\n' | grep -Fxq "${runtime_group}"; then
        usermod -a -G "${runtime_group}" "${member}"
      fi
    done
  fi

  if [[ "${SPARKROBOT_FIX_TEST_MODE:-false}" == "true" ]]; then
    install -d -m 0770 "${pid_dir}" "${LOG_ROOT}" "${ros_log_dir}"
    return 0
  fi

  install -d -o "${run_user}" -g "${runtime_group}" -m 0770 \
    "${pid_dir}" "${LOG_ROOT}" "${ros_log_dir}"
  find "${pid_dir}" -maxdepth 1 -type f -exec chown "${run_user}:${runtime_group}" {} + \
    -exec chmod 0660 {} + 2>/dev/null || true
  find "${LOG_ROOT}" -xdev -type d -exec chgrp "${runtime_group}" {} + \
    -exec chmod 0770 {} +
  find "${LOG_ROOT}" -xdev -type f -exec chgrp "${runtime_group}" {} + \
    -exec chmod 0660 {} +
  echo "运行目录权限已修复：用户=${run_user}，共享组=${runtime_group}"
}

process_arg_value() {
  local pid="$1" option="$2" fallback="$3" previous=""
  local arg
  if [[ -r "/proc/${pid}/cmdline" ]]; then
    while IFS= read -r -d '' arg; do
      if [[ "${previous}" == "${option}" ]]; then
        printf '%s\n' "${arg}"
        return 0
      fi
      previous="${arg}"
    done < "/proc/${pid}/cmdline"
  fi
  printf '%s\n' "${fallback}"
}

process_optional_args() {
  local pid="$1" previous="" arg
  [[ -r "/proc/${pid}/cmdline" ]] || return 0
  while IFS= read -r -d '' arg; do
    if [[ "${previous}" == "--nav-mode" || "${previous}" == "--active-map-name" ]]; then
      printf '%s\0%s\0' "${previous}" "${arg}"
      previous=""
      continue
    fi
    case "${arg}" in
      --nav-mode|--active-map-name) previous="${arg}" ;;
      *) previous="" ;;
    esac
  done < "/proc/${pid}/cmdline"
}

source_ros_environment() {
  local nav_dir="$1" nav_root ros_prefix nounset_was_on=false
  nav_root="$(dirname "${nav_dir}")"
  ros_prefix="${ROS_SETUP%/setup.bash}"
  [[ "$-" == *u* ]] && nounset_was_on=true
  set +u
  [[ -r "${ROS_SETUP}" ]] && source "${ROS_SETUP}"
  [[ -r "${AIMDK_SETUP}" ]] && source "${AIMDK_SETUP}"
  [[ "${nounset_was_on}" == true ]] && set -u
  [[ -r /agibot/software/entry/cfg/ros_dds_configuration.xml ]] \
    && export FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/entry/cfg/ros_dds_configuration.xml
  [[ -r /agibot/software/nav/bin/librmw_fastrtps.so ]] \
    && export RMW_LIBRARY_PATH=/agibot/software/nav/bin/librmw_fastrtps.so
  export ROBOT_LOCAL_NAVIGATION_V3_HOME="${RUNTIME_STATE_ROOT}"
  export NAV_ODOM_TOPIC="${NAV_ODOM_TOPIC:-/slam/lidar_odom}"
  export ROS_LOG_DIR="${ROBOT_LOCAL_NAV_ROS_LOG_DIR:-${LOG_ROOT}/ros}"
  export PYTHONPATH="${nav_root}:${ros_prefix}/local/lib/python3.10/dist-packages:${ros_prefix}/lib/python3.10/site-packages:/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages:${PYTHONPATH:-}"
  local prefix
  for prefix in "${ros_prefix}" /opt/ros/humble /home/agi/aimdk/install; do
    if [[ -d "${prefix}/lib" ]]; then
      export LD_LIBRARY_PATH="${prefix}/lib:${prefix}/lib/aarch64-linux-gnu:${LD_LIBRARY_PATH:-}"
    fi
  done
}

bridge_import_preflight() {
  local nav_dir="$1"
  source_ros_environment "${nav_dir}"
  PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
from robot_local_navigation_v3 import lingxi_nav_bridge, navigation_client
from robot_local_navigation_v3.motion_option_policy import motion_requires_explicit_stop

assert motion_requires_explicit_stop(6001)
assert not motion_requires_explicit_stop(1002)
print(f"导航 bridge 启动前导入检查通过：{lingxi_nav_bridge.__file__}")
print(f"导航客户端启动前导入检查通过：{navigation_client.__file__}")
PY
}

stop_bridge_process() {
  local pid="$1" attempt
  kill "${pid}" 2>/dev/null || true
  for attempt in $(seq 1 50); do
    kill -0 "${pid}" 2>/dev/null || return 0
    sleep 0.1
  done
  kill -TERM "${pid}" 2>/dev/null || true
  sleep 0.5
  kill -0 "${pid}" 2>/dev/null && die "旧导航 bridge 无法停止，pid=${pid}"
}

restart_bridge() {
  require_root
  local nav_dir nav_root run_user runtime_group old_pid="" config_json host port
  local pid_file log_file ros_log_dir new_pid attempt
  local -a optional_args=()
  nav_dir="$(resolve_nav_dir)"
  nav_root="$(dirname "${nav_dir}")"
  if old_pid="$(read_running_bridge_pid)"; then
    :
  else
    old_pid=""
  fi
  run_user="$(resolve_bridge_run_user "${old_pid}" "${nav_dir}")"
  runtime_group="$(resolve_runtime_group "${run_user}")"
  pid_file="$(bridge_pid_file)"
  log_file="$(bridge_log_file)"
  ros_log_dir="${ROBOT_LOCAL_NAV_ROS_LOG_DIR:-${LOG_ROOT}/ros}"
  config_json="${ROBOT_LOCAL_NAVIGATION_V3_CONFIG_JSON:-${RUNTIME_STATE_ROOT}/unified_nav_config.json}"
  host="${ROBOT_LOCAL_NAVIGATION_V3_BRIDGE_HOST:-0.0.0.0}"
  port="${DEFAULT_BRIDGE_PORT}"

  repair_runtime_permissions "${run_user}"
  bridge_import_preflight "${nav_dir}"
  if [[ -n "${old_pid}" ]]; then
    config_json="$(process_arg_value "${old_pid}" --config-json "${config_json}")"
    host="$(process_arg_value "${old_pid}" --host "${host}")"
    port="$(process_arg_value "${old_pid}" --port "${port}")"
    while IFS= read -r -d '' arg; do
      optional_args+=("${arg}")
    done < <(process_optional_args "${old_pid}")
    stop_bridge_process "${old_pid}"
  fi

  [[ -r "${config_json}" ]] || die "导航配置不存在或不可读：${config_json}"
  [[ "${port}" =~ ^[0-9]+$ && "${port}" -ge 1 && "${port}" -le 65535 ]] \
    || die "导航 bridge 端口无效：${port}"
  command -v runuser >/dev/null 2>&1 || die "系统缺少 runuser，无法按原运行用户重启 bridge"
  touch "${log_file}"
  chown "${run_user}:${runtime_group}" "${log_file}"
  chmod 0660 "${log_file}"
  rm -f "${pid_file}"

  runuser -u "${run_user}" -- bash -s -- \
    "${nav_root}" "${RUNTIME_STATE_ROOT}" "${LOG_ROOT}" "${ros_log_dir}" \
    "${ROS_SETUP}" "${AIMDK_SETUP}" "${config_json}" "${host}" "${port}" \
    "${log_file}" "${pid_file}" "${optional_args[@]}" <<'BRIDGE_START'
set -eo pipefail
nav_root="$1"; state_root="$2"; log_root="$3"; ros_log_dir="$4"
ros_setup="$5"; aimdk_setup="$6"; config_json="$7"; host="$8"; port="$9"
shift 9
log_file="$1"; pid_file="$2"; shift 2
set +u
[[ -r "${ros_setup}" ]] && source "${ros_setup}"
[[ -r "${aimdk_setup}" ]] && source "${aimdk_setup}"
set -u
[[ -r /agibot/software/entry/cfg/ros_dds_configuration.xml ]] \
  && export FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/entry/cfg/ros_dds_configuration.xml
[[ -r /agibot/software/nav/bin/librmw_fastrtps.so ]] \
  && export RMW_LIBRARY_PATH=/agibot/software/nav/bin/librmw_fastrtps.so
ros_prefix="${ros_setup%/setup.bash}"
export ROBOT_LOCAL_NAVIGATION_V3_HOME="${state_root}"
export NAV_ODOM_TOPIC="${NAV_ODOM_TOPIC:-/slam/lidar_odom}"
export ROS_LOG_DIR="${ros_log_dir}"
export PYTHONPATH="${nav_root}:${ros_prefix}/local/lib/python3.10/dist-packages:${ros_prefix}/lib/python3.10/site-packages:/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages:${PYTHONPATH:-}"
for prefix in "${ros_prefix}" /opt/ros/humble /home/agi/aimdk/install; do
  if [[ -d "${prefix}/lib" ]]; then
    export LD_LIBRARY_PATH="${prefix}/lib:${prefix}/lib/aarch64-linux-gnu:${LD_LIBRARY_PATH:-}"
  fi
done
nohup python3 -m robot_local_navigation_v3.lingxi_nav_bridge \
  --config-json "${config_json}" --host "${host}" --port "${port}" "$@" \
  >>"${log_file}" 2>&1 &
printf '%s\n' "$!" > "${pid_file}"
BRIDGE_START

  new_pid="$(tr -cd '0-9' < "${pid_file}" 2>/dev/null || true)"
  [[ -n "${new_pid}" ]] || die "导航 bridge 未写入 PID 文件"
  for attempt in $(seq 1 40); do
    if ! kill -0 "${new_pid}" 2>/dev/null; then
      tail -n 80 "${log_file}" >&2 || true
      die "导航 bridge 启动后立即退出"
    fi
    if command -v curl >/dev/null 2>&1 \
      && curl -fsS --max-time 1 "http://127.0.0.1:${port}/nav/status" >/dev/null 2>&1; then
      echo "导航 bridge 已恢复：pid=${new_pid}，端口=${port}"
      return 0
    fi
    sleep 0.25
  done
  kill -0 "${new_pid}" 2>/dev/null || die "导航 bridge 启动失败"
  echo "导航 bridge 进程已启动：pid=${new_pid}；当前未完成 HTTP 回验，请执行 doctor 查看日志"
}

verify_package_manifest() {
  local manifest="$1" root="$2"
  python3 - "${manifest}" "${root}" <<'PY'
import hashlib
import json
import pathlib
import sys

manifest_path = pathlib.Path(sys.argv[1]).resolve()
root = pathlib.Path(sys.argv[2]).resolve()
data = json.loads(manifest_path.read_text(encoding="utf-8"))
listed = set()
for item in data.get("files", []):
    relative = pathlib.PurePosixPath(str(item["path"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise SystemExit(f"清单含不安全路径：{relative}")
    path = root.joinpath(*relative.parts).resolve()
    if root not in path.parents:
        raise SystemExit(f"清单路径越界：{relative}")
    if not path.is_file():
        raise SystemExit(f"安装包缺少文件：{relative}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != item["sha256"]:
        raise SystemExit(f"文件校验失败：{relative}")
    listed.add(relative.as_posix())
actual = {
    path.relative_to(root).as_posix()
    for path in root.rglob("*")
    if path.is_file() and path != manifest_path
}
if actual != listed:
    raise SystemExit(f"安装包文件清单不一致：缺失={sorted(listed-actual)}，额外={sorted(actual-listed)}")
print(f"安装包完整性校验通过：{len(listed)} 个文件")
PY
}

verify_installed_hashes() {
  local nav_dir manifest
  nav_dir="$(resolve_nav_dir)"
  manifest="${FIX_STATE_ROOT}/installed-manifest.json"
  [[ -r "${manifest}" ]] || die "尚未找到已安装修复清单：${manifest}"
  python3 - "${manifest}" "${nav_dir}" <<'PY'
import hashlib
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
nav_dir = pathlib.Path(sys.argv[2])
checked = 0
for item in manifest.get("files", []):
    relative = pathlib.PurePosixPath(str(item["path"]))
    if not relative.parts or relative.parts[0] != "payload":
        continue
    target = nav_dir / relative.name
    if not target.is_file():
        raise SystemExit(f"[失败] 运行文件缺失：{target}")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != item["sha256"]:
        raise SystemExit(f"[失败] 运行文件已变化：{target.name}\n  当前={digest}\n  应为={item['sha256']}")
    print(f"[正常] {target.name} sha256={digest}")
    checked += 1
if checked != 7:
    raise SystemExit(f"[失败] 修复清单应包含 7 个 payload 文件，实际为 {checked}")
PY
}

python_syntax_check() {
  local nav_dir
  nav_dir="$(resolve_nav_dir)"
  python3 - "${nav_dir}" <<'PY'
import pathlib
import py_compile
import tempfile
import sys

root = pathlib.Path(sys.argv[1])
files = [
    "lingxi_nav_bridge.py",
    "motion_option_policy.py",
    "sdk_motion_controller.py",
    "navigation_client.py",
    "pnc_response.py",
    "voice_preset_motion.py",
    "point_catalog.py",
]
with tempfile.TemporaryDirectory(prefix="lingxi-nav-fix-compile-") as temp:
    for index, name in enumerate(files):
        py_compile.compile(str(root / name), cfile=str(pathlib.Path(temp) / f"{index}.pyc"), doraise=True)
print("Python 语法检查通过")
PY
}

restore_backup_files() {
  local backup="$1" nav_dir file existed temp
  [[ -d "${backup}" ]] || die "备份目录不存在：${backup}"
  [[ -r "${backup}/nav-dir.txt" ]] || die "备份缺少 nav-dir.txt"
  nav_dir="$(<"${backup}/nav-dir.txt")"
  [[ -d "${nav_dir}" ]] || die "备份记录的导航目录不存在：${nav_dir}"
  for file in lingxi_nav_bridge.py motion_option_policy.py sdk_motion_controller.py navigation_client.py pnc_response.py voice_preset_motion.py point_catalog.py; do
    existed="false"
    [[ -r "${backup}/existed-${file}" ]] && existed="$(<"${backup}/existed-${file}")"
    if [[ "${existed}" == "true" ]]; then
      [[ -f "${backup}/${file}" ]] || die "备份缺少 ${file}"
      temp="${nav_dir}/.${file}.rollback.$$"
      cp -a "${backup}/${file}" "${temp}"
      mv -f "${temp}" "${nav_dir}/${file}"
    else
      rm -f "${nav_dir}/${file}"
    fi
  done
  echo "已恢复备份文件：${backup}"
}

restore_install_state_from_backup() {
  local backup="$1" name existed temp
  install -d -m 0755 "${FIX_STATE_ROOT}"
  for name in installed-manifest.json release-info.txt; do
    existed="false"
    [[ -r "${backup}/existed-state-${name}" ]] \
      && existed="$(<"${backup}/existed-state-${name}")"
    if [[ "${existed}" == "true" ]]; then
      [[ -f "${backup}/state-${name}" ]] || die "备份缺少 state-${name}"
      temp="${FIX_STATE_ROOT}/.${name}.rollback.$$"
      cp -a "${backup}/state-${name}" "${temp}"
      mv -f "${temp}" "${FIX_STATE_ROOT}/${name}"
    else
      rm -f "${FIX_STATE_ROOT}/${name}"
    fi
  done
}

show_topic_info() {
  local topic="$1" label="$2" output=""
  output="$(timeout 6s ros2 topic info "${topic}" 2>/dev/null || true)"
  if [[ -n "${output}" ]]; then
    echo "${label}："
    echo "${output}"
  else
    echo "${label}：未发现"
  fi
}

show_mc_state() {
  local source_output="" common_topic="" state_output=""
  if timeout 5s ros2 service type /aimdk_5Fmsgs/srv/GetCurrentInputSource >/dev/null 2>&1; then
    source_output="$(timeout 8s ros2 service call \
      /aimdk_5Fmsgs/srv/GetCurrentInputSource \
      aimdk_msgs/srv/GetCurrentInputSource '{}' 2>&1 || true)"
    if [[ -n "${source_output}" ]]; then
      echo "MC 当前输入源："
      echo "${source_output}" | grep -E 'name:|priority:|timeout:|code:' | head -n 12 || echo "  服务有响应，但未解析出输入源字段"
    fi
  else
    echo "MC 当前输入源：查询服务未发现"
  fi

  common_topic="$(timeout 5s ros2 topic list -t 2>/dev/null \
    | awk -F' ' '/aimdk_msgs\/msg\/McCommonState/{print $1; exit}' || true)"
  if [[ -n "${common_topic}" ]]; then
    state_output="$(timeout 6s ros2 topic echo --once "${common_topic}" 2>&1 || true)"
    echo "MC 当前运动状态（${common_topic}）："
    echo "${state_output}" | grep -E 'input_source:|motion_status:|player_state:|control_area:|motion:|name:|value:' | head -n 30 || true
  else
    echo "MC 当前运动状态：未发现 McCommonState 话题"
  fi
}

status() {
  local nav_dir pid=""
  nav_dir="$(resolve_nav_dir)"
  echo "连续导航修复版本：${FIX_VERSION}"
  echo "导航目录：${nav_dir}"
  if pid="$(read_running_bridge_pid)"; then
    echo "[正常] lingxi_nav_bridge=running pid=${pid}"
  else
    echo "[失败] lingxi_nav_bridge=stopped"
  fi
  verify_installed_hashes
}

doctor() {
  require_root
  local nav_dir log_file
  nav_dir="$(resolve_nav_dir)"
  log_file="$(bridge_log_file)"
  status
  python_syntax_check
  source_ros_environment "${nav_dir}"
  PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
from robot_local_navigation_v3 import lingxi_nav_bridge, navigation_client
from robot_local_navigation_v3.motion_option_policy import motion_requires_explicit_stop
assert motion_requires_explicit_stop(6001)
assert not motion_requires_explicit_stop(1002)
print(f"导航模块导入通过：bridge={lingxi_nav_bridge.__file__}")
print(f"导航客户端导入通过：client={navigation_client.__file__}")
PY
  show_topic_info /aima/te/pnc_task_request "PNC 请求话题"
  show_topic_info /aima/pnc/pnc_task_response "PNC 回验话题"
  show_mc_state
  if [[ -r "${log_file}" ]]; then
    echo "最近的连续导航关键日志："
    tail -n 800 "${log_file}" \
      | grep -E 'Arrival motion wait completed naturally|Continuous arrival motion requires explicit stop|Tour task motion release wait started|PNC 回验在|PNC 回验话题没有发布者|Navigation task started|Navigation event: type=(completed|failed|timeout)' \
      | tail -n 30 || true
  else
    echo "提示：导航日志不存在：${log_file}"
  fi
  echo "健康检查结束：本命令没有发送导航、动作、重定位或语音指令。"
}

logs() {
  local lines="${1:-300}" log_file
  [[ "${lines}" =~ ^[0-9]+$ && "${lines}" -ge 1 && "${lines}" -le 20000 ]] \
    || die "日志行数必须是 1 到 20000 的整数"
  log_file="$(bridge_log_file)"
  [[ -r "${log_file}" ]] || die "导航日志不存在：${log_file}"
  tail -n "${lines}" "${log_file}"
}

list_backups() {
  if [[ ! -d "${BACKUP_ROOT}" ]]; then
    echo "暂无备份"
    return 0
  fi
  find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r
}

rollback() {
  require_root
  local backup="${1:-latest}"
  if [[ "${backup}" == "latest" ]]; then
    backup="$(find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null | sort -r | head -n 1)"
  elif [[ "${backup}" != /* ]]; then
    backup="${BACKUP_ROOT}/${backup}"
  fi
  [[ -n "${backup}" ]] || die "没有可回滚的备份"
  restore_backup_files "${backup}"
  restore_install_state_from_backup "${backup}"
  restart_bridge
  echo "回滚完成：${backup}"
}

main() {
  case "${1:-status}" in
    status) status ;;
    doctor) doctor ;;
    restart) require_root; restart_bridge ;;
    repair-permissions) require_root; repair_runtime_permissions ;;
    logs) shift; logs "${1:-300}" ;;
    backups) list_backups ;;
    rollback) rollback "${2:-latest}" ;;
    *)
      echo "用法：sudo sparkrobot-lingxi-consecutive-nav-fix status|doctor|restart|repair-permissions|logs [行数]|backups|rollback [latest|时间戳|完整路径]" >&2
      exit 2
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
