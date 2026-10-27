#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  api)
    exec /opt/lingxi-next/current/runtime/node/bin/node /opt/lingxi-next/current/api/index.mjs
    ;;
  aimdk)
    [[ -f "${LINGXI_ROS_SETUP:-}" ]] || { echo "ROS setup is not configured" >&2; exit 1; }
    # Vendor setup files may reference variables which are initially unset.
    set +u
    source "$LINGXI_ROS_SETUP"
    if [[ -n "${LINGXI_AIMDK_SETUP:-}" ]]; then source "$LINGXI_AIMDK_SETUP"; fi
    set -u
    exec /usr/bin/python3 /opt/lingxi-next/current/bridge/aimdk_ros_bridge.py
    ;;
  agent)
    [[ -f "${LINGXI_CREDENTIALS_FILE:-}" && -n "${LINKSOUL_AGENT_ID:-}" ]] || { echo "Configure LinkSoul credentials and agent ID before enabling voice" >&2; exit 1; }
    exec /opt/lingxi-next/current/runtime/java/bin/java \
      -Dlogback.configurationFile=/opt/lingxi-next/current/agent/logback.xml \
      -cp '/opt/lingxi-next/current/agent/lingxi-agent.jar:/opt/lingxi-next/current/agent/lib/*' local.lingxi.AgentMain
    ;;
  *) echo "Usage: run.sh api|aimdk|agent" >&2; exit 2 ;;
esac
