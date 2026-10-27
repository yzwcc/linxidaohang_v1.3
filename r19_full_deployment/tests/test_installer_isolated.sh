#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <r19-package-directory|release.tar.gz>" >&2
  exit 2
fi
PACKAGE_INPUT="$1"
[[ -d "${PACKAGE_INPUT}" || -f "${PACKAGE_INPUT}" ]] || { echo "package input does not exist: ${PACKAGE_INPUT}" >&2; exit 2; }
[[ "${EUID}" -eq 0 ]] || { echo "run this isolated installer test as root" >&2; exit 2; }
TEST_ROOT="$(mktemp -d /tmp/r19-installer-test.XXXXXX)"
trap 'rm -rf -- "${TEST_ROOT}"' EXIT

if [[ -d "${PACKAGE_INPUT}" ]]; then
  PACKAGE_DIR="$(cd "${PACKAGE_INPUT}" && pwd)"
else
  mkdir -p "${TEST_ROOT}/release"
  python3 - "${PACKAGE_INPUT}" "${TEST_ROOT}/release" <<'PY'
import sys
import tarfile
from pathlib import Path, PurePosixPath

archive_path, destination = map(Path, sys.argv[1:])
root = destination.resolve()
with tarfile.open(archive_path, "r:gz") as archive:
    members = archive.getmembers()
    if not members:
        raise SystemExit("R19 archive is empty")
    for member in members:
        relative = PurePosixPath(member.name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in member.name:
            raise SystemExit(f"unsafe R19 archive path: {member.name}")
        if not (member.isfile() or member.isdir()):
            raise SystemExit(f"unsupported R19 archive entry type: {member.name}")
        if not (root / Path(*relative.parts)).resolve().is_relative_to(root):
            raise SystemExit(f"R19 archive path escapes extraction root: {member.name}")
    roots = {PurePosixPath(member.name).parts[0] for member in members if PurePosixPath(member.name).parts}
    if len(roots) != 1:
        raise SystemExit("archive must contain one package root directory")
    archive.extractall(root, members=members)
PY
  mapfile -t package_roots < <(find "${TEST_ROOT}/release" -mindepth 1 -maxdepth 1 -type d -print)
  [[ "${#package_roots[@]}" -eq 1 ]] || { echo "archive must contain one package root directory" >&2; exit 2; }
  PACKAGE_DIR="${package_roots[0]}"
fi
for required in install.sh release-info.json payload-manifest.json payload scripts templates systemd docs SHA256SUMS; do
  [[ -e "${PACKAGE_DIR}/${required}" ]] || {
    echo "not an R19 deployment package; missing ${required}: ${PACKAGE_DIR}" >&2
    exit 2
  }
done

group_setup_line="$(grep -nF 'groupadd --system "${RUNTIME_GROUP}"' "${PACKAGE_DIR}/install.sh" | head -n 1 | cut -d: -f1)"
staging_chown_line="$(grep -nF 'chown -R root:"${RUNTIME_GROUP}" "${STAGING}"' "${PACKAGE_DIR}/install.sh" | head -n 1 | cut -d: -f1)"
if [[ -z "${group_setup_line}" || -z "${staging_chown_line}" ]] || (( group_setup_line >= staging_chown_line )); then
  echo "installer must create the runtime group before assigning it to the staged release" >&2
  exit 1
fi

cat >"${TEST_ROOT}/secrets.env" <<'EOF'
LINGXI_APP_ID=test
LINGXI_APP_KEY=test
LINGXI_APP_SECRET=test
DOUBAO_API_KEY=test
EOF
mkdir -p "${TEST_ROOT}/bin"
cat >"${TEST_ROOT}/bin/python3" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "-c" && "${2:-}" == *"import rclpy"* ]]; then
  [[ "${R19_TEST_IMPORT_FAIL:-0}" != 1 ]] || exit 99
  exit 0
fi
exec /usr/bin/python3 "$@"
EOF
chmod 0755 "${TEST_ROOT}/bin/python3"
export PATH="${TEST_ROOT}/bin:${PATH}"
export SPARKROBOT_ALLOW_NON_ARM64=true
export SPARKROBOT_INSTALL_TEST_MODE=true
export ROBOT_LOCAL_NAV_ROS_SETUP=/etc/os-release

run_install() {
  local root="$1"
  local import_fail="$2"
  shift 2
  env R19_TEST_IMPORT_FAIL="${import_fail}" SPARKROBOT_INSTALL_ROOT="${root}" "${PACKAGE_DIR}/install.sh" \
    --secrets "${TEST_ROOT}/secrets.env" "$@"
}

overlay_root="${TEST_ROOT}/overlay"
run_install "${overlay_root}" 0 --aimdk-mode overlay
overlay_current="${overlay_root}/opt/sparkrobot/lingxi-nav/current"
[[ -L "${overlay_current}" && -d "${overlay_current}" ]]
[[ -x "${overlay_current}/scripts/lingxi_java_agent_control.sh" ]]
[[ ! -L "${overlay_current}/scripts/lingxi_java_agent_control.sh" ]]
[[ -f "${overlay_current}/docs/新机器人验收清单.md" ]]

reuse_root="${TEST_ROOT}/reuse"
run_install "${reuse_root}" 0 --aimdk-mode reuse --aimdk-setup /etc/os-release
grep -q '^ROBOT_AIMDK_SETUP=/etc/os-release$' "${reuse_root}/etc/sparkrobot/lingxi-nav/console.env"

failed_root="${TEST_ROOT}/precheck-failure"
if run_install "${failed_root}" 1 --aimdk-mode reuse --aimdk-setup /etc/os-release; then
  echo "failed interface precheck was unexpectedly accepted" >&2
  exit 1
fi
[[ ! -e "${failed_root}" ]]

echo "PASS: overlay install, existing-AIMDK reuse, precheck failure leaves target untouched"
