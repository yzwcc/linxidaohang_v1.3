#!/usr/bin/env bash
set -Eeuo pipefail
PACKAGE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-doctor}" in
  doctor) exec bash "${PACKAGE_DIR}/install.sh" --check ;;
  logs) exec sudo tail -n 100 -F /var/log/sparkrobot/lingxi-nav/java-agent.log ;;
  *) echo "用法：sudo bash ./control.sh doctor|logs" >&2; exit 2 ;;
esac
