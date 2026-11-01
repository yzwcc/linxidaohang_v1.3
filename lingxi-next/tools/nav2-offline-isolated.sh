#!/bin/bash
# Run only against an explicitly separate offline ROS rootfs, never the host.
set -euo pipefail
if [[ ${EUID} != 0 || $# -lt 3 || $# -gt 4 ]]; then
  echo 'usage: sudo bash tools/nav2-offline-isolated.sh ROOTFS CASE NEW_OUTPUT [FROZEN_TERRAIN_JSON]' >&2
  exit 2
fi
root=$(realpath -e -- "$1")
project=$(realpath -e -- "$(dirname -- "${BASH_SOURCE[0]}")/..")
case_name=$2
output=$3
terrain=${4:-}
if [[ -n $terrain ]]; then
  terrain=$(realpath -e -- "$terrain")
  [[ -f $terrain && ! -L $terrain && $(stat -c %s -- "$terrain") -le 131072 ]] || exit 2
  case $2 in open|blocked|detour|unsupported-shortcut) ;; *) exit 2 ;; esac
fi
if [[ $root == / || $root == "$project" || $project == "$root"/* || $root == "$project"/* ||
      ! -f $root/opt/ros/humble/setup.bash || ! -x $root/bin/bash ||
      ! $output =~ ^/[A-Za-z0-9_-]+$ || -e $root$output ]]; then
  echo 'separate_humble_rootfs_and_new_top_level_output_required' >&2
  exit 2
fi
case $case_name in open|blocked|detour|appearing-wall|disappearing-obstacle|unsupported-shortcut|publication-probe) ;; *) exit 2 ;; esac
exec unshare --mount --net --ipc --fork /bin/bash -s -- "$root" "$project" "$case_name" "$output" "$terrain" <<'INNER'
set -euo pipefail
root=$1 project=$2 case_name=$3 output=$4 terrain=$5
mount --make-rprivate /
ip link set lo up
mkdir -p "$root/dev" "$root/dev/shm" "$root/proc" "$root/sys" "$root/work"
mount --bind /dev "$root/dev"
mount -t tmpfs tmpfs "$root/dev/shm"
mount -t proc proc "$root/proc"
mount -t sysfs sysfs "$root/sys"
mount --bind "$project" "$root/work"
mount -o remount,bind,ro "$root/work"
if [[ -n $terrain ]]; then
  touch "$root/frozen-terrain.json"
  mount --bind "$terrain" "$root/frozen-terrain.json"
  mount -o remount,bind,ro "$root/frozen-terrain.json"
fi
exec chroot "$root" /bin/bash -c '
  set -e
  source /opt/ros/humble/setup.bash
  export ROS_LOCALHOST_ONLY=1 ROS_DOMAIN_ID=173 RMW_IMPLEMENTATION=rmw_fastrtps_cpp PYTHONDONTWRITEBYTECODE=1
  if [[ $1 == publication-probe ]]; then
    mkdir -m 700 "$2"
    exec python3 /work/tools/probe_humble_publication.py "$2/report.json"
  fi
  args=()
  if [[ -n $3 ]]; then args=(--terrain-fixture /frozen-terrain.json); fi
  exec python3 /work/tools/run_nav2_offline.py --case "$1" --output "$2" "${args[@]}"
' bash "$case_name" "$output" "$terrain"
INNER
