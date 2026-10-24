#!/usr/bin/env bash
set -Eeuo pipefail
umask 0027

[[ "$(id -un)" == "agi" ]] || { echo "请使用 agi 账号执行本脚本" >&2; exit 1; }

ARCHIVE=/home/agi/aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip
EXPECTED=dc80fe5d2907881ed77beb230680867efac6bd28b458f83d2ff4e9479abe3615
VERSION=aimdk-aarch64-faf4c945-artifacts
OLD_ROOT=/home/agi/aimdk
BACKUP_ROOT=/home/agi/aimdk-backups
STAGE="$(mktemp -d /home/agi/.aimdk-r18-stage.XXXXXX)"
NEW_ROOT=${STAGE}/${VERSION}
PREVIOUS_ROOT=""
NEW_INSTALLED=0
SWAP_STARTED=0

die() { echo "失败：$*" >&2; exit 1; }

cleanup() {
  local code=$?
  trap - EXIT ERR
  set +e
  if (( code != 0 && SWAP_STARTED )); then
    if (( NEW_INSTALLED )) && [[ -e "${OLD_ROOT}" ]]; then
      FAILED_ROOT="${BACKUP_ROOT}/failed-r18-$(date -u +%Y%m%dT%H%M%SZ)-$$"
      if mkdir -p "${BACKUP_ROOT}" && mv -- "${OLD_ROOT}" "${FAILED_ROOT}"; then
        echo "未通过的新 AIMDK 已隔离：${FAILED_ROOT}" >&2
      else
        echo "警告：未能隔离失败的新 AIMDK：${OLD_ROOT}" >&2
      fi
    fi
    if [[ -n "${PREVIOUS_ROOT}" && -d "${PREVIOUS_ROOT}" ]]; then
      if mv -- "${PREVIOUS_ROOT}" "${OLD_ROOT}"; then
        echo "原 AIMDK 已恢复：${OLD_ROOT}" >&2
      else
        echo "警告：原 AIMDK 备份仍保留在：${PREVIOUS_ROOT}" >&2
      fi
    fi
  fi
  rm -rf -- "${STAGE}"
  exit "${code}"
}
trap cleanup EXIT

[[ -f "${ARCHIVE}" ]] || die "缺少 ${ARCHIVE}"
[[ "$(sha256sum "${ARCHIVE}" | awk '{print $1}')" == "${EXPECTED}" ]] \
  || die "AIMDK v1.1.0 SHA-256 校验失败"
command -v unzip >/dev/null || die "缺少 unzip"
command -v colcon >/dev/null || die "缺少 colcon"
[[ -r /opt/ros/humble/setup.bash ]] || die "缺少 ROS 2 Humble"

python3 - "${ARCHIVE}" "${STAGE}" <<'PY'
import pathlib
import shutil
import stat
import sys
import zipfile

archive = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2]).resolve()
expected_root = "aimdk-aarch64-faf4c945-artifacts"

def normalized(parts):
    result = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if len(result) <= 1:
                raise ValueError("path escapes SDK root")
            result.pop()
        else:
            result.append(part)
    if not result or result[0] != expected_root:
        raise ValueError("path is outside SDK root")
    return pathlib.PurePosixPath(*result)

with zipfile.ZipFile(archive) as bundle:
    infos = [item for item in bundle.infolist() if item.filename]
    roots = {pathlib.PurePosixPath(item.filename).parts[0] for item in infos}
    if roots != {expected_root}:
        raise SystemExit(f"AIMDK ZIP 根目录错误：{sorted(roots)}")

    entries = {}
    links = {}
    for item in infos:
        path = pathlib.PurePosixPath(item.filename)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"AIMDK ZIP 含不安全成员：{item.filename}")
        mode = item.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
            raise SystemExit(f"AIMDK ZIP 含不支持的文件类型：{item.filename}")
        if kind == stat.S_IFLNK:
            try:
                target = bundle.read(item).decode("utf-8", "strict")
            except UnicodeDecodeError as exc:
                raise SystemExit(f"AIMDK 符号链接目标不是 UTF-8：{item.filename}") from exc
            if not target or pathlib.PurePosixPath(target).is_absolute():
                raise SystemExit(f"AIMDK ZIP 含绝对或空符号链接：{item.filename}")
            try:
                normalized((*path.parent.parts, *pathlib.PurePosixPath(target).parts))
            except ValueError as exc:
                raise SystemExit(f"AIMDK ZIP 含越界符号链接：{item.filename} -> {target}") from exc
            links[item.filename] = target
        else:
            entries[item.filename] = item

    for name, item in entries.items():
        target = destination.joinpath(*pathlib.PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if item.is_dir():
            target.mkdir(exist_ok=True)
            continue
        with bundle.open(item) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
        mode = item.external_attr >> 16
        permissions = stat.S_IMODE(mode) or 0o644
        target.chmod(permissions & 0o777)

    for name, link_target in links.items():
        target = destination.joinpath(*pathlib.PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(link_target)

    checks = {
        f"{expected_root}/version": b"aimdk v1.1.0-gfaf4c94",
        f"{expected_root}/src/aimdk_msgs/interface/robot/task_engine/msg/PncTaskRequest.msg": b"reference_line",
    }
    for name, marker in checks.items():
        if name not in entries:
            raise SystemExit(f"AIMDK ZIP 接口文件不存在：{name}")
        content = destination.joinpath(*pathlib.PurePosixPath(name).parts).read_bytes()
        if marker not in content:
            raise SystemExit(f"AIMDK ZIP 接口检查失败：{name}")

print(f"AIMDK_ARCHIVE_CONTRACT_OK symlinks={len(links)}")
PY

[[ -d "${NEW_ROOT}/src" && -f "${NEW_ROOT}/version" ]] || die "AIMDK ZIP 解压目录结构错误"
grep -qx 'aimdk v1.1.0-gfaf4c94' "${NEW_ROOT}/version" || die "AIMDK 版本不匹配"

cd "${NEW_ROOT}"
set +u
source /opt/ros/humble/setup.bash
set -u
colcon build
set +u
source /opt/ros/humble/setup.bash
source "${NEW_ROOT}/install/setup.bash"
set -u
python3 - <<'PY'
from aimdk_msgs.msg import PncTaskRequest, PncTaskResponse

request = PncTaskRequest()
required = (
    "task_type", "task_request", "pnc_mode", "task_id", "map_id",
    "target_pose", "reference_line", "target_pose_radius", "max_forward_speed",
)
missing = [name for name in required if not hasattr(request, name)]
if missing:
    raise SystemExit(f"PncTaskRequest 缺少字段：{missing}")
if not hasattr(PncTaskResponse(), "task_status"):
    raise SystemExit("PncTaskResponse 缺少 task_status")
print("AIMDK_IMPORT_OK")
print("AIMDK_PNC_110_CONTRACT_OK")
PY
ros2 pkg list | grep -qx 'aimdk_msgs'
if [[ -e "${OLD_ROOT}" || -L "${OLD_ROOT}" ]]; then
  [[ -d "${OLD_ROOT}" && ! -L "${OLD_ROOT}" ]] \
    || die "旧 AIMDK 路径不是普通目录；拒绝自动替换"
  mkdir -p "${BACKUP_ROOT}"
  PREVIOUS_ROOT="${BACKUP_ROOT}/$(date -u +%Y%m%dT%H%M%SZ)-pre-r18-$$"
  [[ ! -e "${PREVIOUS_ROOT}" ]] || die "备份路径已存在：${PREVIOUS_ROOT}"
  SWAP_STARTED=1
  mv -- "${OLD_ROOT}" "${PREVIOUS_ROOT}"
else
  SWAP_STARTED=1
fi

NEW_INSTALLED=1
mv -- "${NEW_ROOT}" "${OLD_ROOT}"

env -i HOME=/home/agi USER=agi LOGNAME=agi SHELL=/bin/bash \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  LANG=C.UTF-8 bash --noprofile --norc -c '
set -e
source /opt/ros/humble/setup.bash
source /home/agi/aimdk/install/setup.bash
test "$(cat /home/agi/aimdk/version)" = "aimdk v1.1.0-gfaf4c94"
python3 - <<"PY"
from aimdk_msgs.msg import PncTaskRequest, PncTaskResponse
request = PncTaskRequest()
required = ("task_type", "task_request", "pnc_mode", "task_id", "map_id", "target_pose", "reference_line", "target_pose_radius", "max_forward_speed")
missing = [name for name in required if not hasattr(request, name)]
if missing or not hasattr(PncTaskResponse(), "task_status"):
    raise SystemExit(f"AIMDK PNC 消息契约检查失败：{missing}")
print("AIMDK_OK")
PY
ros2 pkg list | grep -qx aimdk_msgs
'

echo "[通过] AIMDK v1.1.0 已切换；旧 AIMDK 备份：${PREVIOUS_ROOT:-不存在}"
