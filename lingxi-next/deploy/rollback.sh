#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo "Run as root" >&2; exit 1; }
ROOT=/opt/lingxi-next
CONFIG=/etc/lingxi-next
source "$(dirname -- "${BASH_SOURCE[0]}")/transaction.sh"
deployment_lock
[[ -L "$ROOT/previous" && -L "$ROOT/current" ]] || { echo "No managed rollback release" >&2; exit 1; }
TARGET="$(deployment_pointer previous)"
CURRENT="$(deployment_pointer current)"
[[ "$TARGET" == "$ROOT/releases/"* && -f "$TARGET/VERSION" && -f "$TARGET/SHA256SUMS" ]] || { echo "Invalid rollback target" >&2; exit 1; }
[[ "$CURRENT" == "$ROOT/releases/"* && -f "$CURRENT/VERSION" ]] || { echo "Invalid current release" >&2; exit 1; }
(cd "$TARGET" && sha256sum -c SHA256SUMS >/dev/null)
umask 077
deployment_begin pre-rollback
deployment_stop
deployment_snapshot
deployment_link current "$TARGET"
install -m 0644 "$TARGET"/deploy/lingxi-next-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl start lingxi-next-api.service
if ! deployment_health; then
  echo "Rollback API health check failed. Inspect services and retained backups before continuing." >&2
  exit 1
fi
deployment_link previous "$CURRENT"
deployment_commit
echo "Code rollback complete. Data/configuration preserved; AIMDK and voice require operator restart after inspection."
