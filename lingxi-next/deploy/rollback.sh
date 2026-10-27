#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo "Run as root" >&2; exit 1; }
ROOT=/opt/lingxi-next
[[ -L "$ROOT/previous" && -L "$ROOT/current" ]] || { echo "No managed rollback release" >&2; exit 1; }
TARGET="$(readlink -f "$ROOT/previous")"
CURRENT="$(readlink -f "$ROOT/current")"
[[ "$TARGET" == "$ROOT/releases/"* && -f "$TARGET/VERSION" && -f "$TARGET/SHA256SUMS" ]] || { echo "Invalid rollback target" >&2; exit 1; }
[[ "$CURRENT" == "$ROOT/releases/"* && -f "$CURRENT/VERSION" ]] || { echo "Invalid current release" >&2; exit 1; }
(cd "$TARGET" && sha256sum -c SHA256SUMS >/dev/null)
for service in lingxi-next-agent.service lingxi-next-api.service lingxi-next-aimdk.service; do
  if systemctl cat "$service" >/dev/null 2>&1; then systemctl stop "$service"; fi
done
umask 077
tar -czf "$ROOT/backups/pre-rollback-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" -C / etc/lingxi-next var/lib/lingxi-next
ln -sfn "$TARGET" "$ROOT/current"
ln -sfn "$CURRENT" "$ROOT/previous"
install -m 0644 "$TARGET"/deploy/lingxi-next-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl start lingxi-next-api.service
echo "Code rollback complete. Data/configuration preserved; AIMDK and voice require operator restart after inspection."
