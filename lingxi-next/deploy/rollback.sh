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
BACKUP="$(mktemp -d "$ROOT/backups/pre-rollback-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
tar -czf "$BACKUP/data-and-config.tar.gz" -C / etc/lingxi-next var/lib/lingxi-next
ln -sfn "$TARGET" "$ROOT/current"
ln -sfn "$CURRENT" "$ROOT/previous"
install -m 0644 "$TARGET"/deploy/lingxi-next-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl start lingxi-next-api.service
PORT="$(python3 - /etc/lingxi-next/robot.env <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
match = re.search(r"^LINGXI_PORT=(\d+)\s*$", text, re.M)
port = int(match.group(1)) if match else 3000
if not 1 <= port <= 65535:
    raise SystemExit("Invalid LINGXI_PORT")
print(port)
PY
)"
if ! curl --fail --silent --retry 10 --retry-connrefused --retry-delay 1 \
    --cacert /etc/lingxi-next/tls/ca.crt "https://127.0.0.1:$PORT/health" >/dev/null; then
  echo "Rollback API health check failed. Inspect services and retained backups before continuing." >&2
  exit 1
fi
echo "Code rollback complete. Data/configuration preserved; AIMDK and voice require operator restart after inspection."
