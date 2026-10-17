#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${1:?用法：audit_final_archive.sh RELEASE.tar.gz}"
TEMP_DIR="$(mktemp -d /tmp/sparkrobot-final-audit.XXXXXX)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT

tar -xzf "${ARCHIVE}" -C "${TEMP_DIR}"
mapfile -t ROOTS < <(find "${TEMP_DIR}" -mindepth 1 -maxdepth 1 -type d -print)
[[ "${#ROOTS[@]}" -eq 1 ]] || { echo "归档必须只有一个顶层目录" >&2; exit 1; }
ROOT="${ROOTS[0]}"
cd "${ROOT}"

sha256sum -c SHA256SUMS >/dev/null
python3 scripts/verify_payload.py --root payload --manifest payload-manifest.json >/dev/null
while IFS= read -r -d '' file; do
  bash -n "${file}"
done < <(find . -type f -name '*.sh' -print0)

test -x install.sh
test -x configure.sh
test -x control.sh
test -x quick_install.sh
test -x payload/runtime/node
test -x payload/runtime/java/bin/java
test -f source/java/src/main/java/com/agibot/aiem/nav/LingxiNavAgentApp.java
test -f source/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py
test -f source/server/src/index.ts
test -f docs/内部部署与命令手册.md

printf 'FINAL_LINUX_AUDIT_OK files=%s\n' "$(find . -type f | wc -l)"
