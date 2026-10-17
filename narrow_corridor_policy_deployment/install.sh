#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="1.0.0-20260929"
STATE_ROOT="${NARROW_CORRIDOR_STATE_ROOT:-/var/lib/sparkrobot/lingxi-v1.3.9-narrow-corridor}"
BACKUP_ROOT="${NARROW_CORRIDOR_BACKUP_ROOT:-${STATE_ROOT}/backups}"
CONFIG_ROOT="${PNC_CONFIG_ROOT:-/agibot/software/nav/config}"
VECTORFLUX_CONFIG="${PNC_VECTORFLUX_CONFIG:-${CONFIG_ROOT}/vectorflux/vectorflux_app_cfg.yaml}"
APP2_CONFIG="${PNC_APP2_CONFIG:-${CONFIG_ROOT}/vectorflux_common/a2/app2.yaml}"
MPPI_CONFIG="${PNC_MPPI_CONFIG:-${CONFIG_ROOT}/vectorflux_common/a2/mppi_controller.yaml}"
CONTROL_ROOT="${NARROW_CORRIDOR_CONTROL_ROOT:-/usr/local/libexec/sparkrobot-lingxi-v1.3.9-narrow-corridor}"
CONTROL_LINK="${NARROW_CORRIDOR_CONTROL_LINK:-/usr/local/sbin/sparkrobot-lingxi-v1.3.9-narrow-corridor}"
NO_RESTART=false

die() { echo "失败：$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
用法：sudo bash ./install.sh [--no-restart]

安装 V1.3.9 窄通道平衡避障策略。只调整 A2 PNC YAML 配置，不修改 AIMDK 二进制、地图、讲解任务或语音配置。
回滚：sudo sparkrobot-lingxi-v1.3.9-narrow-corridor rollback BACKUP_DIR
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-restart) NO_RESTART=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数：$1" ;;
  esac
done
[[ "${EUID}" -eq 0 ]] || die "请使用 sudo 运行"
[[ "$(uname -m)" == "aarch64" || "$(uname -m)" == "arm64" ]] || die "此包只支持 ARM64 机器人"
[[ -f "${PACKAGE_DIR}/policy.json" && -f "${PACKAGE_DIR}/apply_policy.py" ]] || die "安装包缺少策略文件"
[[ -f "${APP2_CONFIG}" ]] || die "找不到 A2 PNC 配置：${APP2_CONFIG}"
[[ -f "${MPPI_CONFIG}" ]] || die "找不到 MPPI 配置：${MPPI_CONFIG}"
[[ -f "${VECTORFLUX_CONFIG}" ]] || die "找不到 vectorflux 主配置：${VECTORFLUX_CONFIG}"
grep -Eq "main_config:[[:space:]]*[\"']?\.\./config/vectorflux_common/a2/app2\.yaml" "${VECTORFLUX_CONFIG}" ||
  die "当前 PNC 主配置不是 A2 app2.yaml，已拒绝修改"

TMP_COMPILE="$(mktemp -d)"
trap 'rm -rf "${TMP_COMPILE}"' EXIT
PYTHONPYCACHEPREFIX="${TMP_COMPILE}/pycache" python3 -m py_compile "${PACKAGE_DIR}/apply_policy.py"

install -d -m 0700 "${BACKUP_ROOT}" "${STATE_ROOT}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$(mktemp -d "${BACKUP_ROOT}/${TIMESTAMP}.XXXXXX")"
printf 'version=%s\ninstalled_at=%s\napp2=%s\nmppi=%s\nvectorflux=%s\n' \
  "${VERSION}" "${TIMESTAMP}" "${APP2_CONFIG}" "${MPPI_CONFIG}" "${VECTORFLUX_CONFIG}" \
  >"${BACKUP_DIR}/metadata.txt"
cp -a "${APP2_CONFIG}" "${BACKUP_DIR}/app2.yaml"
cp -a "${MPPI_CONFIG}" "${BACKUP_DIR}/mppi_controller.yaml"
sha256sum "${APP2_CONFIG}" "${MPPI_CONFIG}" >"${BACKUP_DIR}/before.sha256"

restore() {
  cp -a "${BACKUP_DIR}/app2.yaml" "${APP2_CONFIG}"
  cp -a "${BACKUP_DIR}/mppi_controller.yaml" "${MPPI_CONFIG}"
}
on_error() {
  local code="$?"
  trap - ERR
  set +e
  restore
  echo "安装失败，已恢复 PNC 原配置：${BACKUP_DIR}" >&2
  exit "${code}"
}
trap on_error ERR

python3 "${PACKAGE_DIR}/apply_policy.py" --policy "${PACKAGE_DIR}/policy.json" \
  --app2 "${APP2_CONFIG}" --mppi "${MPPI_CONFIG}" --apply
python3 "${PACKAGE_DIR}/apply_policy.py" --policy "${PACKAGE_DIR}/policy.json" \
  --app2 "${APP2_CONFIG}" --mppi "${MPPI_CONFIG}" --verify >/dev/null

install -d -m 0755 "${CONTROL_ROOT}"
install -m 0755 "${PACKAGE_DIR}/control.sh" "${CONTROL_ROOT}/control.sh"
install -m 0755 "${PACKAGE_DIR}/apply_policy.py" "${CONTROL_ROOT}/apply_policy.py"
install -m 0644 "${PACKAGE_DIR}/policy.json" "${CONTROL_ROOT}/policy.json"
ln -sfn "${CONTROL_ROOT}/control.sh" "${CONTROL_LINK}"

if [[ "${NO_RESTART}" == false ]]; then
  if ! "${CONTROL_ROOT}/control.sh" restart; then
    restore
    echo "PNC 重启/验收失败，已恢复原配置：${BACKUP_DIR}" >&2
    exit 1
  fi
fi

printf '%s\n' "${BACKUP_DIR}" >"${STATE_ROOT}/last-backup"
chmod 0600 "${STATE_ROOT}/last-backup"
trap - ERR
echo "V1.3.9 窄通道平衡避障策略安装成功：${VERSION}"
echo "备份目录：${BACKUP_DIR}"
echo "策略：动态/局部膨胀 0.50m，静态地图 0.65m，行人距离 0.60m"
echo "验收：sudo ${CONTROL_LINK} doctor"
echo "回滚：sudo ${CONTROL_LINK} rollback ${BACKUP_DIR}"
