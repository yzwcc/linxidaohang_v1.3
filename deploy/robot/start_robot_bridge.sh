#!/usr/bin/env bash
set -eo pipefail

BRIDGE_DIR="/agibot/data/home/agi/lingxi_robot_bridge"
BRIDGE_FILE="$BRIDGE_DIR/robot_lingxi_bridge.py"
LOG_FILE="$BRIDGE_DIR/bridge.log"

cd "$BRIDGE_DIR"
source /opt/ros/humble/setup.bash
if [ -f /home/agi/aimdk/install/setup.bash ]; then
  source /home/agi/aimdk/install/setup.bash
fi

export AMENT_PREFIX_PATH="/home/agi/aimdk/install/aimdk_msgs:/home/agi/aimdk/install:/agibot/software/app_proxy/bin/test_proxy:/agibot/software/app_proxy/bin/local_app_proxy:/agibot/software/app_proxy/bin/app_proxy_cpp:/agibot/software/app_proxy/bin/aimdk_msgs:/opt/ros/humble:${AMENT_PREFIX_PATH:-}"
export PYTHONPATH="/home/agi/aimdk/install/aimdk_msgs/local/lib/python3.10/dist-packages:/home/agi/aimdk/src/aimdk_msgs/prebuilt_aarch64/local/lib/python3.10/dist-packages:/agibot/software/app_proxy/bin/test_proxy/lib/python3.10/site-packages:/agibot/software/app_proxy/bin/local_app_proxy/lib/python3.10/site-packages:/agibot/software/app_proxy/bin/aimdk_msgs/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages:/opt/ros/humble/local/lib/python3.10/dist-packages:${PYTHONPATH:-}"
export LD_LIBRARY_PATH="/home/agi/aimdk/install/aimdk_msgs/lib:/home/agi/aimdk/src/aimdk_msgs/prebuilt_aarch64/lib:/agibot/software/app_proxy/bin/app_proxy_cpp/lib:/agibot/software/app_proxy/bin/aimdk_msgs/lib:/opt/ros/humble/lib/aarch64-linux-gnu:/opt/ros/humble/lib:${LD_LIBRARY_PATH:-}"
export LINGXI_DANCE_RESOURCE_KEY="${LINGXI_DANCE_RESOURCE_KEY:-linkcraft_resource_onnx_01K88DMFAWQCKW5T71XX86F1X8}"
export LINGXI_DANCE_RESOURCE_VERSION="${LINGXI_DANCE_RESOURCE_VERSION:-0.0.1}"

if ss -ltnp 2>/dev/null | grep -q ':18080 '; then
  old_pid="$(ss -ltnp | awk '/:18080 / {print $NF}' | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | head -1)"
  if [ -n "$old_pid" ]; then
    echo "stopping existing bridge pid=$old_pid"
    kill "$old_pid" || true
    sleep 1
    kill -9 "$old_pid" 2>/dev/null || true
  fi
fi

for old_pid in $(pgrep -f "$BRIDGE_FILE" || true); do
  if [ "$old_pid" != "$$" ]; then
    echo "stopping stale bridge pid=$old_pid"
    kill "$old_pid" || true
    sleep 0.2
    kill -9 "$old_pid" 2>/dev/null || true
  fi
done
sleep 1

nohup python3 "$BRIDGE_FILE" > "$LOG_FILE" 2>&1 &
sleep 2
pgrep -af robot_lingxi_bridge.py || true
tail -80 "$LOG_FILE" || true
