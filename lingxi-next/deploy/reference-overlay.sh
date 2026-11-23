# Sourced by run.sh after the robot's ROS Humble and AIMDK setup.
# These directories are prepared in a dedicated, administrator-owned runtime.
if [[ "${LINGXI_REFERENCE_RUNTIME_ROOT:-}" != /* ]]; then
  echo "LINGXI_REFERENCE_RUNTIME_ROOT must be an absolute path" >&2
  return 78
fi
_lingxi_runtime="$LINGXI_REFERENCE_RUNTIME_ROOT"
_lingxi_nav2="$_lingxi_runtime/ros/opt/ros/humble"
if [[ ! -d "$_lingxi_nav2/share/nav2_bringup" || ! -f "$_lingxi_runtime/overlay/local_setup.bash" \
      || ! -d "$_lingxi_runtime/grid" ]]; then
  echo "The isolated reference native runtime is incomplete" >&2
  return 78
fi
if [[ -n "${LINGXI_REFERENCE_AIMDK_SETUP:-}" ]]; then
  [[ "$LINGXI_REFERENCE_AIMDK_SETUP" == /* && -r "$LINGXI_REFERENCE_AIMDK_SETUP" ]] || return 78
  source "$LINGXI_REFERENCE_AIMDK_SETUP"
fi
export AMENT_PREFIX_PATH="$_lingxi_nav2${AMENT_PREFIX_PATH:+:$AMENT_PREFIX_PATH}"
export LD_LIBRARY_PATH="$_lingxi_nav2/lib:$_lingxi_nav2/lib/aarch64-linux-gnu:$_lingxi_runtime/ros/usr/lib:$_lingxi_runtime/ros/usr/lib/aarch64-linux-gnu:$_lingxi_runtime/ros/lib/aarch64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$_lingxi_runtime/grid:$_lingxi_nav2/lib/python3.10/site-packages:$_lingxi_nav2/local/lib/python3.10/dist-packages${PYTHONPATH:+:$PYTHONPATH}"
source "$_lingxi_runtime/overlay/local_setup.bash"
unset _lingxi_runtime _lingxi_nav2
