#!/bin/bash
# Reuse hash-verified rootfs-compatible native builds; network is loopback only.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
if [[ ${EUID} != 0 || $# != 4 ]]; then
  echo 'usage: sudo bash tools/registered-terrain-ros-isolated.sh ROOTFS NATIVE_CACHE NEW_OUTPUT pause|stop|reseed|loss' >&2
  exit 2
fi
root=$(realpath -e -- "$1")
native=$(realpath -e -- "$2")
project=$(realpath -e -- "$(dirname -- "${BASH_SOURCE[0]}")/..")
output=$3 mode=$4
if [[ $root == / || $root == "$project" || $project == "$root"/* || $root == "$project"/* ||
      ! -f $root/opt/ros/humble/setup.bash || ! -f $native/ground/liblingxi_ground.so ||
      ! -f $native/octomap/liblingxi_octomap.so || ! $output =~ ^/[A-Za-z0-9_-]+$ || -e $root$output ]]; then
  echo 'separate_humble_rootfs_verified_native_cache_and_new_output_required' >&2
  exit 2
fi
case $mode in pause|stop|reseed|loss) ;; *) exit 2 ;; esac
exec unshare --mount --net --ipc --fork /bin/bash -s -- "$root" "$project" "$native" "$output" "$mode" <<'INNER'
set -euo pipefail
root=$1 project=$2 native=$3 output=$4 mode=$5
mount --make-rprivate /
ip link set lo up
mkdir -p "$root/dev" "$root/dev/shm" "$root/proc" "$root/sys" "$root/work" "$root/native"
mount --bind /dev "$root/dev"
mount -t tmpfs tmpfs "$root/dev/shm"
mount -t proc proc "$root/proc"
mount -t sysfs sysfs "$root/sys"
mount --bind "$project" "$root/work"
mount -o remount,bind,ro "$root/work"
mount --bind "$native" "$root/native"
mount -o remount,bind,ro "$root/native"
exec chroot "$root" /bin/bash -c '
  set -euo pipefail
  umask 077
  mkdir -m 700 "$1"
  export HOME="$1" PYTHONDONTWRITEBYTECODE=1
  set +u
  source /opt/ros/humble/setup.bash
  set -u
  export ROS_LOCALHOST_ONLY=1 ROS_DOMAIN_ID=173 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
  exec python3 /work/tools/probe_registered_terrain_ros.py --output "$1" --native /native --mode "$2"
' bash "$output" "$mode"
INNER
