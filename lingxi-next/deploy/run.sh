#!/usr/bin/env bash
set -euo pipefail

CURRENT=/opt/lingxi-next/current
INDEPENDENT="$CURRENT/independent"

require_independent_product() {
  [[ "${LINGXI_INDEPENDENT_PRODUCT:-0}" == "1" ]] || {
    echo "Independent product flag is not enabled" >&2
    exit 78
  }
  [[ -d "$INDEPENDENT/source/robot-runtime/python" && -d "$INDEPENDENT/source/robot-slam" ]] || {
    echo "Independent runtime sources are missing from this release" >&2
    exit 78
  }
}

required_file() {
  local value="$1" label="$2"
  [[ -n "$value" && "$value" == /* && -f "$value" && ! -L "$value" ]] || {
    echo "$label is not configured as a direct file" >&2
    exit 78
  }
}

required_directory() {
  local value="$1" label="$2"
  [[ -n "$value" && "$value" == /* && -d "$value" && ! -L "$value" ]] || {
    echo "$label is not configured as a direct directory" >&2
    exit 78
  }
}

source_ros_runtime() {
  [[ -f "${LINGXI_ROS_SETUP:-}" ]] || { echo "ROS setup is not configured" >&2; exit 78; }
  set +u
  # Humble setup scripts may read variables before defining them.
  source "$LINGXI_ROS_SETUP"
  if [[ -n "${LINGXI_AIMDK_SETUP:-}" ]]; then source "$LINGXI_AIMDK_SETUP"; fi
  set -u
}

independent_node() {
  local node="$CURRENT/runtime/node/bin/node"
  [[ -x "$node" && ! -L "$node" ]] || { echo "Bundled ARM64 Node runtime is missing" >&2; exit 78; }
  printf '%s' "$node"
}

independent_api() {
  local profile="$1" api web
  api="$INDEPENDENT/assets/$profile/api/main.mjs"
  web="$INDEPENDENT/assets/$profile/web"
  [[ -f "$api" && ! -L "$api" && -d "$web" && ! -L "$web" ]] || {
    echo "Independent $profile assets are missing" >&2
    exit 78
  }
  printf '%s' "$api"
}

file_sha256() {
  local sum
  sum="$(sha256sum -- "$1")" || return
  printf '%s' "${sum%% *}"
}

required_pinned_configuration() {
  local file="$1" digest="$2" label="$3" actual
  required_file "$file" "$label"
  [[ "$digest" =~ ^[a-f0-9]{64}$ ]] || {
    echo "$label requires an explicit SHA256" >&2
    exit 78
  }
  actual="$(file_sha256 "$file")"
  [[ "$actual" == "$digest" ]] || {
    echo "$label SHA256 mismatch" >&2
    exit 78
  }
}

independent_python() {
  local python="${LINGXI_SLAM_PYTHON:-}"
  local checker="$INDEPENDENT/source/tools/check_installed_slam_python.py"
  [[ "$python" == /* && -f "$python" && -x "$python" && -f "$checker" && ! -L "$checker" ]] || {
    echo "Configure LINGXI_SLAM_PYTHON as the verified isolated runtime interpreter" >&2
    exit 78
  }
  if ! /usr/bin/timeout --signal=TERM --kill-after=5s 30s "$python" -s -B "$checker" --python "$python" >/dev/null; then
    echo "Independent Python preflight failed; no owner was started" >&2
    exit 78
  fi
  printf '%s' "$python"
}

verified_native_inputs() {
  local python="$1" config="$2" root="${LINGXI_NATIVE_INPUTS_ROOT:-}"
  [[ -n "$root${LINGXI_NATIVE_INPUTS_SHA256:-}" ]] || return 0
  required_directory "$root" LINGXI_NATIVE_INPUTS_ROOT
  required_pinned_configuration "$root/native-inputs-manifest.json" "${LINGXI_NATIVE_INPUTS_SHA256:-}" LINGXI_NATIVE_INPUTS
  [[ "$config" == "$root/mapping.json" ]] || {
    echo "Mapping/SLAM configuration must belong to the selected native inputs" >&2
    exit 78
  }
  if ! /usr/bin/timeout --signal=TERM --kill-after=5s 90s "$python" -s -B \
      "$INDEPENDENT/source/tools/prepare_installed_mapping_inputs.py" verify \
      --root "$root" --manifest-sha256 "$LINGXI_NATIVE_INPUTS_SHA256" >/dev/null; then
    echo "Native mapping inputs failed verification; no owner was started" >&2
    exit 78
  fi
}

run_independent_mapping() {
  require_independent_product
  source_ros_runtime
  required_file "${LINGXI_MAPPING_CONFIG:-}" LINGXI_MAPPING_CONFIG
  required_directory "${LINGXI_MAP_ROOT:-}" LINGXI_MAP_ROOT
  local node api output parent python
  export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
  python="$(independent_python)"
  verified_native_inputs "$python" "$LINGXI_MAPPING_CONFIG"
  node="$(independent_node)"
  api="$(independent_api mapping)"
  parent="${LINGXI_RUNTIME_DIR:-/var/lib/lingxi-next/runtime}"
  required_directory "$parent" LINGXI_RUNTIME_DIR
  output="$parent/mapping-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  # Retain owner diagnostics; maps live in the separate persistent catalog.
  export LINGXI_WEB_ROOT="$INDEPENDENT/assets/mapping/web"
  export LINGXI_PORT="${LINGXI_MAPPING_PORT:-3443}"
  "$python" -s -B "$INDEPENDENT/source/robot-runtime/python/managed_mapping.py" \
    --config "$LINGXI_MAPPING_CONFIG" --maps "$LINGXI_MAP_ROOT" --output "$output" \
    --node "$node" --api-bundle "$api" \
    --node-sha256 "$(file_sha256 "$node")" \
    --api-sha256 "$(file_sha256 "$api")" --persistent
}

run_independent_slam() {
  require_independent_product
  source_ros_runtime
  required_file "${LINGXI_SLAM_CONFIG:-}" LINGXI_SLAM_CONFIG
  required_directory "${LINGXI_MAP_ROOT:-}" LINGXI_MAP_ROOT
  local node api output parent python
  export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
  python="$(independent_python)"
  verified_native_inputs "$python" "$LINGXI_SLAM_CONFIG"
  local -a selection=()
  if [[ -n "${LINGXI_PRODUCT_CONFIG:-}${LINGXI_PRODUCT_CONFIG_SHA256:-}" ]]; then
    required_pinned_configuration "${LINGXI_PRODUCT_CONFIG:-}" "${LINGXI_PRODUCT_CONFIG_SHA256:-}" LINGXI_PRODUCT_CONFIG
    selection+=(--product-config "$LINGXI_PRODUCT_CONFIG" --product-config-sha256 "$LINGXI_PRODUCT_CONFIG_SHA256")
  fi
  if [[ -n "${LINGXI_ACQUISITION_SETTINGS:-}${LINGXI_ACQUISITION_SETTINGS_SHA256:-}" ]]; then
    required_pinned_configuration "${LINGXI_ACQUISITION_SETTINGS:-}" "${LINGXI_ACQUISITION_SETTINGS_SHA256:-}" LINGXI_ACQUISITION_SETTINGS
    selection+=(--acquisition-settings "$LINGXI_ACQUISITION_SETTINGS" --acquisition-settings-sha256 "$LINGXI_ACQUISITION_SETTINGS_SHA256")
  fi
  node="$(independent_node)"
  api="$(independent_api slam)"
  parent="${LINGXI_RUNTIME_DIR:-/var/lib/lingxi-next/runtime}"
  required_directory "$parent" LINGXI_RUNTIME_DIR
  output="$parent/slam-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  # The supervised owner controls child shutdown and preserves its run evidence.
  export LINGXI_WEB_ROOT="$INDEPENDENT/assets/slam/web"
  export LINGXI_PORT="${LINGXI_SLAM_PORT:-3444}"
  "$python" -s -B "$INDEPENDENT/source/robot-runtime/python/managed_slam.py" \
    --config "$LINGXI_SLAM_CONFIG" --maps "$LINGXI_MAP_ROOT" --output "$output" \
    --node "$node" --api-bundle "$api" \
    --node-sha256 "$(file_sha256 "$node")" \
    --api-sha256 "$(file_sha256 "$api")" "${selection[@]}"
}

case "${1:-}" in
  api)
    exec "$CURRENT/runtime/node/bin/node" "$CURRENT/api/index.mjs"
    ;;
  aimdk)
    [[ -f "${LINGXI_ROS_SETUP:-}" ]] || { echo "ROS setup is not configured" >&2; exit 1; }
    # Vendor setup files may reference variables which are initially unset.
    set +u
    source "$LINGXI_ROS_SETUP"
    if [[ -n "${LINGXI_AIMDK_SETUP:-}" ]]; then source "$LINGXI_AIMDK_SETUP"; fi
    set -u
    exec /usr/bin/python3 "$CURRENT/bridge/aimdk_ros_bridge.py" --mode full
    ;;
  actions)
    require_independent_product
    source_ros_runtime
    required_directory "${LINGXI_MAP_ROOT:-}" LINGXI_MAP_ROOT
    export PYTHONPATH="$INDEPENDENT/source/robot-slam${PYTHONPATH:+:$PYTHONPATH}"
    exec /usr/bin/python3 "$CURRENT/bridge/aimdk_ros_bridge.py" --mode actions-only
    ;;
  navigation)
    [[ -f "${LINGXI_ROS_SETUP:-}" ]] || { echo "ROS setup is not configured" >&2; exit 1; }
    require_independent_product
    set +u
    source "$LINGXI_ROS_SETUP"
    if [[ -n "${LINGXI_AIMDK_SETUP:-}" ]]; then source "$LINGXI_AIMDK_SETUP"; fi
    set -u
    exec /usr/bin/python3 "$INDEPENDENT/source/robot-navigation/bridge/physical_nav_bridge.py"
    ;;
  independent-mapping)
    run_independent_mapping
    ;;
  independent-slam)
    run_independent_slam
    ;;
  agent)
    if [[ -n "${LINGXI_PRODUCT_CONFIG:-}${LINGXI_PRODUCT_CONFIG_SHA256:-}" ]]; then
      require_independent_product
      required_pinned_configuration "${LINGXI_PRODUCT_CONFIG:-}" "${LINGXI_PRODUCT_CONFIG_SHA256:-}" LINGXI_PRODUCT_CONFIG
      # Java resolves the selected state/credentials, token and playback endpoint.
      # Never fall back to the unrelated core API environment in product mode.
    else
      required_file "${LINGXI_CREDENTIALS_FILE:-}" LINGXI_CREDENTIALS_FILE
    fi
    [[ -n "${LINKSOUL_AGENT_ID:-}" ]] || { echo "Configure LinkSoul agent ID before enabling voice" >&2; exit 78; }
    exec /opt/lingxi-next/current/runtime/java/bin/java \
      -Dlogback.configurationFile=/opt/lingxi-next/current/agent/logback.xml \
      -cp '/opt/lingxi-next/current/agent/lingxi-agent.jar:/opt/lingxi-next/current/agent/lib/*' local.lingxi.AgentMain
    ;;
  *) echo "Usage: run.sh api|aimdk|actions|navigation|independent-mapping|independent-slam|agent" >&2; exit 2 ;;
esac
