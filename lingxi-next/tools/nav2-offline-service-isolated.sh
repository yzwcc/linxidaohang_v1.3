#!/bin/bash
set -euo pipefail
mode=run
if [[ ${1:-} == --check ]]; then mode=check; shift; fi
if [[ ${1:-} == --verify-live-terrain-output ]]; then mode=verify-live-terrain-output; shift; fi
if [[ ${1:-} == --verify-localization-output ]]; then mode=verify-localization-output; shift; fi
if [[ ${1:-} == --verify-physical-evidence ]]; then mode=verify-physical-evidence; shift; fi
if [[ ${1:-} == --verify-physical-action ]]; then mode=verify-physical-action; shift; fi
if [[ ${1:-} == --verify-physical-velocity ]]; then mode=verify-physical-velocity; shift; fi
if [[ ${1:-} == --verify-nav2-producer ]]; then mode=verify-nav2-producer; shift; fi
if [[ ${EUID} != 0 || $# != 3 ]]; then
  echo 'usage: sudo -n bash tools/nav2-offline-service-isolated.sh [--check|--verify-live-terrain-output|--verify-localization-output|--verify-physical-evidence|--verify-physical-action|--verify-physical-velocity|--verify-nav2-producer] ROOTFS CASE NEW_OUTPUT' >&2
  exit 2
fi
for tool in realpath unshare mount ip chroot; do command -v "$tool" >/dev/null; done
root=$(realpath -e -- "$1")
project=$(realpath -e -- "$(dirname -- "${BASH_SOURCE[0]}")/..")
case_name=$2 output=$3
if [[ $root == / || $root == "$project" || $project == "$root"/* || $root == "$project"/* ||
      ! -f $root/opt/ros/humble/setup.bash || ! -x $root/bin/bash ||
      ! $output =~ ^/[A-Za-z0-9_-]+$ || -e $root$output || -L $root$output ]]; then
  echo 'separate_humble_rootfs_and_new_output_required' >&2
  exit 2
fi
case $case_name in open|blocked|detour|appearing-wall) ;; *) exit 2 ;; esac
# Keep stdin intact for the framed protocol. No network, vendor sockets or hardware devices.
exec unshare --mount --net --ipc --pid --fork --kill-child=KILL /bin/bash -c '
  set -euo pipefail
  root=$1 project=$2 case_name=$3 output=$4 mode=$5
  mount --make-rprivate /
  ip link set lo up
  mkdir -p "$root/dev" "$root/proc" "$root/sys" "$root/work" "$root/tmp"
  mount -t tmpfs tmpfs "$root/dev"
  for name in null zero random urandom; do
    touch "$root/dev/$name"
    mount --bind "/dev/$name" "$root/dev/$name"
  done
  mkdir -p "$root/dev/shm"
  mount -t tmpfs tmpfs "$root/dev/shm"
  mount -t tmpfs tmpfs "$root/tmp"
  mount -t proc proc "$root/proc"
  mount -t sysfs sysfs "$root/sys"
  mount --bind "$project" "$root/work"
  mount -o remount,bind,ro "$root/work"
  exec chroot "$root" /usr/bin/env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 /bin/bash -c '\''
    source /opt/ros/humble/setup.bash
    export ROS_LOCALHOST_ONLY=1 ROS_DOMAIN_ID=174 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
    export PYTHONDONTWRITEBYTECODE=1 RCUTILS_LOGGING_USE_STDOUT=0
    if [[ $3 == verify-nav2-producer ]]; then
      export LINGXI_VERIFY_NAV2_PRODUCER_ROS=1 LINGXI_NAV2_TEST_OUTPUT="$2"
      exec python3 -B -m unittest discover -s /work/tools/tests -p test_nav2_producer_humble.py -v
    fi
    if [[ $3 == verify-live-terrain-output ]]; then
      export LINGXI_VERIFY_LIVE_TERRAIN_ROS=1
      exec python3 -B -m unittest discover -s /work/tools/tests -p test_live_terrain_ros_humble.py
    fi
    if [[ $3 == verify-localization-output ]]; then
      export LINGXI_VERIFY_LOCALIZATION_OUTPUT_ROS=1
      exec python3 -B -m unittest discover -s /work/tools/tests -p test_localization_output_humble.py -v
    fi
    if [[ $3 == verify-physical-evidence ]]; then
      export LINGXI_VERIFY_PHYSICAL_EVIDENCE_ROS=1
      exec python3 -B -m unittest discover -s /work/tools/tests -p test_physical_evidence_humble.py -v
    fi
    if [[ $3 == verify-physical-action ]]; then
      export LINGXI_VERIFY_PHYSICAL_ACTION_ROS=1
      exec python3 -B -m unittest discover -s /work/tools/tests -p test_physical_action_humble.py -v
    fi
    if [[ $3 == verify-physical-velocity ]]; then
      export LINGXI_VERIFY_PHYSICAL_VELOCITY_ROS=1
      exec python3 -B -m unittest discover -s /work/tools/tests -p test_physical_velocity_humble.py -v
    fi
    if [[ $3 == check ]]; then
      python3 -c "import rclpy, launch_ros; from ament_index_python.packages import get_package_share_directory as share; [share(p) for p in (\"nav2_planner\", \"nav2_controller\", \"nav2_bt_navigator\", \"nav2_velocity_smoother\", \"nav2_lifecycle_manager\", \"nav2_navfn_planner\", \"nav2_regulated_pure_pursuit_controller\")]" >&2
      echo lingxi_offline_nav2_preflight_ok_v1
      exit 0
    fi
    exec python3 /work/tools/offline_nav2_service.py --case "$1" --output "$2"
  '\'' bash "$case_name" "$output" "$mode"
' bash "$root" "$project" "$case_name" "$output" "$mode"
