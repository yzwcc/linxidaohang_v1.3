#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ROBOT_LOCAL_NAVIGATION_V3_HOME="${ROBOT_LOCAL_NAVIGATION_V3_HOME:-$REPO_DIR}"
export PYTHONPATH="$REPO_DIR:${PYTHONPATH:-}"
BRIDGE_HOST="${ROBOT_LOCAL_NAVIGATION_V3_BRIDGE_HOST:-0.0.0.0}"
BRIDGE_PORT="${ROBOT_LOCAL_NAVIGATION_V3_BRIDGE_PORT:-18082}"

if [ -f /opt/ros/humble/setup.bash ]; then
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
fi

if [ -f /home/agi/aimdk/install/setup.bash ]; then
  # shellcheck disable=SC1091
  source /home/agi/aimdk/install/setup.bash
fi

exec python3 "$REPO_DIR/python_bridge/lingxi_nav_bridge.py" \
  --config-json "$ROBOT_LOCAL_NAVIGATION_V3_HOME/unified_nav_config.json" \
  --host "$BRIDGE_HOST" \
  --port "$BRIDGE_PORT" \
  "$@"
