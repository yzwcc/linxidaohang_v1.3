#!/bin/bash
# Separate host-compatible native builds and ROS execution, never robot IO.
set -euo pipefail
if [[ ${EUID} != 0 || $# != 5 ]]; then
  echo 'usage: sudo bash tools/terrain-ros-isolated.sh ROOTFS PATCHWORK_SOURCE EIGEN NEW_OUTPUT expiry|death|revoke' >&2
  exit 2
fi
root=$(realpath -e -- "$1")
source=$(realpath -e -- "$2")
eigen=$(realpath -e -- "$3")
project=$(realpath -e -- "$(dirname -- "${BASH_SOURCE[0]}")/..")
output=$4 mode=$5
if [[ $root == / || $root == "$project" || $project == "$root"/* || $root == "$project"/* ||
      ! -f $root/opt/ros/humble/setup.bash || ! -x $root/usr/bin/g++ ||
      ! -d $source/.git || ! -f $eigen/Eigen/Core ||
      ! $output =~ ^/[A-Za-z0-9_-]+$ || -e $root$output ]]; then
  echo 'separate_compiler_humble_rootfs_pinned_source_and_new_output_required' >&2
  exit 2
fi
case $mode in expiry|death|revoke) ;; *) exit 2 ;; esac
exec unshare --mount --net --ipc --fork /bin/bash -s -- "$root" "$project" "$source" "$eigen" "$output" "$mode" <<'INNER'
set -euo pipefail
root=$1 project=$2 source=$3 eigen=$4 output=$5 mode=$6
mount --make-rprivate /
ip link set lo up
mkdir -p "$root/dev" "$root/dev/shm" "$root/proc" "$root/sys" "$root/work" "$root/upstream" "$root/eigen"
mount --bind /dev "$root/dev"
mount -t tmpfs tmpfs "$root/dev/shm"
mount -t proc proc "$root/proc"
mount -t sysfs sysfs "$root/sys"
mount --bind "$project" "$root/work"
mount -o remount,bind,ro "$root/work"
mount --bind "$source" "$root/upstream"
mount -o remount,bind,ro "$root/upstream"
mount --bind "$eigen" "$root/eigen"
mount -o remount,bind,ro "$root/eigen"
exec chroot "$root" /bin/bash -c '
  set -euo pipefail
  umask 077
  export PYTHONDONTWRITEBYTECODE=1 GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/upstream
  mkdir -m 700 "$1"
  export HOME="$1"
  mkdir -p "$HOME/.cache/lingxi-next"
  python3 /work/tools/build_ground_offline.py --source /upstream --eigen /eigen --output "$HOME/.cache/lingxi-next/ground"
  python3 /work/tools/build_octomap_offline.py --sysroot / --output "$HOME/.cache/lingxi-next/octomap"
  set +u
  source /opt/ros/humble/setup.bash
  set -u
  export ROS_LOCALHOST_ONLY=1 ROS_DOMAIN_ID=173 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
  exec python3 /work/tools/probe_terrain_ros.py --output "$1" --mode "$2"
' bash "$output" "$mode"
INNER
