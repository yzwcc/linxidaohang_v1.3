#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ROBOT_LOCAL_NAVIGATION_V3_HOME="${ROBOT_LOCAL_NAVIGATION_V3_HOME:-$REPO_DIR}"
export PYTHONPATH="$REPO_DIR:${PYTHONPATH:-}"
BRIDGE_HOST="${ROBOT_LOCAL_NAVIGATION_V3_BRIDGE_HOST:-0.0.0.0}"
BRIDGE_PORT="${ROBOT_LOCAL_NAVIGATION_V3_BRIDGE_PORT:-18082}"
ROS_SETUP="${ROBOT_LOCAL_NAV_ROS_SETUP:-/opt/ros/humble/setup.bash}"
AIMDK_SETUP="${ROBOT_AIMDK_SETUP:-}"

if [ -f "$ROS_SETUP" ]; then
  # shellcheck disable=SC1091
  source "$ROS_SETUP"
fi

if [ -n "$AIMDK_SETUP" ] && [ -r "$AIMDK_SETUP" ]; then
  # shellcheck disable=SC1091
  source "$AIMDK_SETUP"
else
  echo "Error: set ROBOT_AIMDK_SETUP to the AIMDK v1.1.0 install/setup.bash" >&2
  exit 1
fi

python3 -c 'from aimdk_msgs.msg import PncTaskRequest; from aimdk_msgs.srv import GetStoredMapByName' \
  || { echo "错误：当前 AIMDK 缺少 v1.1.0 导航/地图接口，请检查 ROBOT_AIMDK_SETUP" >&2; exit 1; }

exec python3 "$REPO_DIR/python_bridge/lingxi_nav_bridge.py" \
  --config-json "$ROBOT_LOCAL_NAVIGATION_V3_HOME/unified_nav_config.json" \
  --host "$BRIDGE_HOST" \
  --port "$BRIDGE_PORT" \
  "$@"
