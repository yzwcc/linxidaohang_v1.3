#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
PACKAGE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "${EUID}" -eq 0 ]] || { echo "请使用 sudo bash ./install.sh" >&2; exit 1; }
cd -- "${PACKAGE_DIR}"
sha256sum -c -- SHA256SUMS >/dev/null
exec python3 "${PACKAGE_DIR}/install.py" "$@"
