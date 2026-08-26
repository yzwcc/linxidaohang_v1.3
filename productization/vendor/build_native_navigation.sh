#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEFAULT_SOURCE="${PROJECT_ROOT}/robot_local_navigation_v3/robot_local_navigation_v3-new-auto-return-interaction-end"
SOURCE_ROOT="${DEFAULT_SOURCE}"
ROBOT_BRIDGE_SOURCE="${PROJECT_ROOT}/deploy/robot/robot_lingxi_bridge.py"
OUTPUT_DIR="${PROJECT_ROOT}/productization/build/navigation-native"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ALLOW_NON_ARM64=false
SKIP_IMPORT_SMOKE=false
WHEELHOUSE=""

usage() {
  cat <<'EOF'
用法：build_native_navigation.sh [选项]
  --source DIR             导航工程根目录
  --robot-bridge FILE      机器人语音 ROS bridge 源文件
  --output DIR             原生模块输出目录
  --python PATH            目标机器人 Python（默认 python3）
  --wheelhouse DIR         厂商提供的离线 Python wheels 目录
  --allow-non-arm64        仅用于本机流程测试，不得作为正式制品
  --skip-import-smoke      仅排障时跳过导入验证
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE_ROOT="$2"; shift 2 ;;
    --robot-bridge) ROBOT_BRIDGE_SOURCE="$2"; shift 2 ;;
    --output) OUTPUT_DIR="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --wheelhouse) WHEELHOUSE="$2"; shift 2 ;;
    --allow-non-arm64) ALLOW_NON_ARM64=true; shift ;;
    --skip-import-smoke) SKIP_IMPORT_SMOKE=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
  esac
done

SOURCE_ROOT="$(cd "${SOURCE_ROOT}" && pwd)"
PACKAGE_SOURCE="${SOURCE_ROOT}/robot_local_navigation_v3"
[[ -d "${PACKAGE_SOURCE}" ]] || { echo "找不到导航包：${PACKAGE_SOURCE}" >&2; exit 1; }
[[ -f "${ROBOT_BRIDGE_SOURCE}" ]] || { echo "找不到机器人语音 bridge：${ROBOT_BRIDGE_SOURCE}" >&2; exit 1; }

ARCH="$(uname -m | tr '[:upper:]' '[:lower:]')"
if [[ "${ARCH}" != "aarch64" && "${ARCH}" != "arm64" && "${ALLOW_NON_ARM64}" != true ]]; then
  echo "正式导航制品只能在 ARM64 机器人或同 ABI 构建机上生成，当前架构：${ARCH}" >&2
  exit 1
fi

PYTHON_VERSION="$(${PYTHON_BIN} -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "${PYTHON_VERSION}" != "3.10" && "${ALLOW_NON_ARM64}" != true ]]; then
  echo "目标机器人使用 Python 3.10，当前解释器是 ${PYTHON_VERSION}，拒绝生成不兼容制品" >&2
  exit 1
fi

set +u
[[ -r /opt/ros/humble/setup.bash ]] && source /opt/ros/humble/setup.bash
[[ -r /agibot/software/common/local_setup.bash ]] && source /agibot/software/common/local_setup.bash
[[ -r /home/agi/aimdk/install/setup.bash ]] && source /home/agi/aimdk/install/setup.bash
set -u
[[ -r /agibot/software/entry/cfg/ros_dds_configuration.xml ]] \
  && export FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/entry/cfg/ros_dds_configuration.xml
[[ -r /agibot/software/nav/bin/librmw_fastrtps.so ]] \
  && export RMW_LIBRARY_PATH=/agibot/software/nav/bin/librmw_fastrtps.so

BUILD_ROOT="$(mktemp -d -t sparkrobot-native-build.XXXXXX)"
cleanup() { rm -rf -- "${BUILD_ROOT}"; }
trap cleanup EXIT

BUILD_PYTHON="${PYTHON_BIN}"
BUILD_PYTHONPATH=""
PIP_SOURCE_ARGS=()
if [[ -n "${WHEELHOUSE}" ]]; then
  WHEELHOUSE="$(cd "${WHEELHOUSE}" && pwd)"
  PIP_SOURCE_ARGS=(--no-index --find-links "${WHEELHOUSE}")
fi
if "${PYTHON_BIN}" -m venv --system-site-packages "${BUILD_ROOT}/venv" >/dev/null 2>&1; then
  BUILD_PYTHON="${BUILD_ROOT}/venv/bin/python"
  "${BUILD_PYTHON}" -m pip install --disable-pip-version-check --no-input "${PIP_SOURCE_ARGS[@]}" \
    'Cython>=3.0,<4' 'setuptools>=65,<70' wheel
else
  mkdir -p "${BUILD_ROOT}/deps"
  "${PYTHON_BIN}" -m pip install --disable-pip-version-check --no-input "${PIP_SOURCE_ARGS[@]}" \
    --target "${BUILD_ROOT}/deps" \
    'Cython>=3.0,<4' 'setuptools>=65,<70' wheel
  BUILD_PYTHONPATH="${BUILD_ROOT}/deps"
fi

build_python() {
  PYTHONPATH="${BUILD_PYTHONPATH}${BUILD_PYTHONPATH:+:}${PYTHONPATH:-}" "${BUILD_PYTHON}" "$@"
}

mkdir -p "${BUILD_ROOT}/source/robot_local_navigation_v3"
cp -a "${PACKAGE_SOURCE}/." "${BUILD_ROOT}/source/robot_local_navigation_v3/"
cp -a "${ROBOT_BRIDGE_SOURCE}" "${BUILD_ROOT}/source/robot_local_navigation_v3/robot_lingxi_bridge.py"
find "${BUILD_ROOT}/source" -type d -name __pycache__ -prune -exec rm -rf -- {} +
find "${BUILD_ROOT}/source/robot_local_navigation_v3" -maxdepth 1 -type f -name '*.pyc' -delete

cat >"${BUILD_ROOT}/setup.py" <<'PY'
from pathlib import Path

from Cython.Build import cythonize
from setuptools import Extension, setup

package_dir = Path(__file__).parent / "source" / "robot_local_navigation_v3"
extensions = [
    Extension(f"robot_local_navigation_v3.{source.stem}", [str(source)])
    for source in sorted(package_dir.glob("*.py"))
    if source.name != "__init__.py"
]
if not extensions:
    raise SystemExit("没有找到可编译的导航 Python 模块")

setup(
    name="sparkrobot-lingxi-nav-native",
    ext_modules=cythonize(
        extensions,
        compiler_directives={
            "language_level": "3",
            "binding": False,
            "embedsignature": False,
        },
        annotate=False,
        build_dir=str(Path(__file__).parent / "cython"),
        nthreads=1,
    ),
)
PY

OUTPUT_DIR="$(realpath -m "${OUTPUT_DIR}")"
if [[ "${OUTPUT_DIR}" == "/" || "${OUTPUT_DIR}" == "${SOURCE_ROOT}" || "${OUTPUT_DIR}" == "${PACKAGE_SOURCE}" ]]; then
  echo "拒绝使用不安全的输出目录：${OUTPUT_DIR}" >&2
  exit 1
fi
rm -rf -- "${OUTPUT_DIR}"
mkdir -p "${OUTPUT_DIR}"
(cd "${BUILD_ROOT}" && build_python setup.py build_ext --build-lib "${OUTPUT_DIR}")

PACKAGE_OUTPUT="${OUTPUT_DIR}/robot_local_navigation_v3"
[[ -d "${PACKAGE_OUTPUT}" ]] || { echo "原生导航包没有生成" >&2; exit 1; }
printf '%s\n' '"""SparkRobot Lingxi navigation native package."""' >"${PACKAGE_OUTPUT}/__init__.py"

while IFS= read -r -d '' binary; do
  strip --strip-unneeded "${binary}" 2>/dev/null || true
done < <(find "${PACKAGE_OUTPUT}" -maxdepth 1 -type f -name '*.so' -print0)

if find "${PACKAGE_OUTPUT}" -maxdepth 1 -type f -name '*.py' ! -name '__init__.py' | grep -q .; then
  echo "原生导航制品中仍包含业务 Python 源码" >&2
  exit 1
fi

SOURCE_COUNT="$(( $(find "${PACKAGE_SOURCE}" -maxdepth 1 -type f -name '*.py' ! -name '__init__.py' | wc -l) + 1 ))"
BINARY_COUNT="$(find "${PACKAGE_OUTPUT}" -maxdepth 1 -type f -name '*.so' | wc -l)"
if [[ "${SOURCE_COUNT}" -ne "${BINARY_COUNT}" ]]; then
  echo "原生模块数量不完整：源码 ${SOURCE_COUNT}，二进制 ${BINARY_COUNT}" >&2
  exit 1
fi

if [[ "${SKIP_IMPORT_SMOKE}" != true ]]; then
  MODULES="$({ find "${PACKAGE_SOURCE}" -maxdepth 1 -type f -name '*.py' ! -name '__init__.py' -printf '%f\n' | sed 's/\.py$//'; printf '%s\n' robot_lingxi_bridge; } | sort | paste -sd, -)"
  PYTHONPATH="${OUTPUT_DIR}:${PYTHONPATH:-}" "${PYTHON_BIN}" - "${MODULES}" <<'PY'
import importlib
import sys

for name in filter(None, sys.argv[1].split(",")):
    importlib.import_module(f"robot_local_navigation_v3.{name}")
print("原生导航模块导入验证通过")
PY
fi

printf '原生导航制品已生成：%s\n' "${OUTPUT_DIR}"
printf '架构=%s Python=%s 模块=%s\n' "${ARCH}" "${PYTHON_VERSION}" "${BINARY_COUNT}"
