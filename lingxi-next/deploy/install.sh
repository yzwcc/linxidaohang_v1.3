#!/usr/bin/env bash
set -euo pipefail
umask 077
PACKAGE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROOT=/opt/lingxi-next
CONFIG=/etc/lingxi-next
DATA=/var/lib/lingxi-next
ROBOT_IP=""
LEGACY=""
COMMISSIONING=0
while (($#)); do
  case "$1" in
    --robot-ip) ROBOT_IP="${2:?IP missing}"; shift 2 ;;
    --legacy-dir) LEGACY="${2:?Legacy directory missing}"; shift 2 ;;
    --commissioning) COMMISSIONING=1; shift ;;
    *) echo "Usage: sudo bash deploy/install.sh --commissioning --robot-ip IP [--legacy-dir DIR]" >&2; exit 2 ;;
  esac
done
[[ $EUID == 0 && $COMMISSIONING == 1 ]] || { echo "Run as root with --commissioning; read docs/ACCEPTANCE.md first" >&2; exit 1; }
[[ "$(uname -m)" == aarch64 || "$(uname -m)" == arm64 ]] || { echo "This package requires Linux ARM64" >&2; exit 1; }
for command in systemctl openssl python3 tar sha256sum curl; do command -v "$command" >/dev/null || { echo "Missing dependency: $command" >&2; exit 1; }; done
python3 -c 'import ipaddress,sys; ipaddress.ip_address(sys.argv[1])' "$ROBOT_IP"
cd "$PACKAGE"
sha256sum -c SHA256SUMS >/dev/null
VERSION="$(<VERSION)"
[[ "$VERSION" =~ ^[0-9A-Za-z._-]{1,80}$ ]] || { echo "Invalid version" >&2; exit 1; }
[[ -x runtime/node/bin/node && -x runtime/java/bin/java ]] || { echo "Bundled runtimes are missing" >&2; exit 1; }
runtime/node/bin/node --version
runtime/java/bin/java -version
[[ ! -e "$ROOT/releases/$VERSION" ]] || { echo "Release already installed; choose a new build or use rollback.sh" >&2; exit 1; }
[[ ! -e "$ROOT/current" || -L "$ROOT/current" ]] || { echo "Current release is not a managed symlink" >&2; exit 1; }
PREVIOUS=""
if [[ -L "$ROOT/current" ]]; then
  PREVIOUS="$(readlink -f "$ROOT/current")"
  [[ "$PREVIOUS" == "$ROOT/releases/"* && -f "$PREVIOUS/VERSION" ]] || { echo "Invalid previous release" >&2; exit 1; }
fi
if ! id lingxi-next >/dev/null 2>&1; then useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin lingxi-next; fi
install -d -m 0755 "$ROOT" "$ROOT/releases"
install -d -m 0750 -o root -g lingxi-next "$CONFIG"
install -d -m 0700 "$ROOT/backups"
install -d -m 0700 -o lingxi-next -g lingxi-next "$DATA"
BACKUP="$ROOT/backups/pre-${VERSION}-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$BACKUP"
if [[ ! -e "$CONFIG/robot.env" ]]; then
  install -m 0600 deploy/robot.env.example "$CONFIG/robot.env"
  python3 - "$CONFIG" <<'PY'
import hashlib, pathlib, secrets, sys
root = pathlib.Path(sys.argv[1])
pairing = secrets.token_urlsafe(18)
(root / "pairing-code.txt").write_text(pairing + "\n")
with (root / "robot.env").open("a") as output:
    output.write("\nLINGXI_PAIRING_CODE_SHA256=" + hashlib.sha256(pairing.encode()).hexdigest() + "\n")
    output.write("LINGXI_AGENT_INTERNAL_TOKEN=" + secrets.token_urlsafe(32) + "\n")
    output.write("LINGXI_AIMDK_BRIDGE_TOKEN=" + secrets.token_urlsafe(32) + "\n")
PY
fi
if [[ ! -e "$CONFIG/tls/robot.crt" ]]; then
  install -d -m 0750 -o root -g lingxi-next "$CONFIG/tls"
  openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 3650 \
    -subj "/CN=Lingxi Robot Local CA" -keyout "$CONFIG/tls/ca.key" -out "$CONFIG/tls/ca.crt" 2>/dev/null
  openssl req -new -newkey rsa:3072 -nodes -subj "/CN=$ROBOT_IP" \
    -keyout "$CONFIG/tls/robot.key" -out "$CONFIG/tls/robot.csr" 2>/dev/null
  printf 'subjectAltName=IP:%s,IP:127.0.0.1,DNS:localhost\nextendedKeyUsage=serverAuth\n' "$ROBOT_IP" > "$CONFIG/tls/server.ext"
  openssl x509 -req -in "$CONFIG/tls/robot.csr" -CA "$CONFIG/tls/ca.crt" -CAkey "$CONFIG/tls/ca.key" \
    -CAcreateserial -out "$CONFIG/tls/robot.crt" -days 825 -sha256 -extfile "$CONFIG/tls/server.ext" 2>/dev/null
  chmod 0600 "$CONFIG/tls/ca.key"
  chown root:lingxi-next "$CONFIG/tls/robot.key" "$CONFIG/tls/robot.crt" "$CONFIG/tls/ca.crt"
  chmod 0640 "$CONFIG/tls/robot.key" "$CONFIG/tls/robot.crt" "$CONFIG/tls/ca.crt"
else
  openssl x509 -in "$CONFIG/tls/robot.crt" -noout -checkip "$ROBOT_IP"
fi
install -d -m 0755 "$ROOT/releases/$VERSION"
cp -a "$PACKAGE/." "$ROOT/releases/$VERSION/"
if [[ -n "$LEGACY" ]]; then
  "$PACKAGE/runtime/node/bin/node" "$PACKAGE/tools/migrate-legacy.mjs" --source-dir "$LEGACY" --destination "$DATA"
fi
for service in lingxi-next-agent.service lingxi-next-api.service lingxi-next-aimdk.service; do
  if systemctl cat "$service" >/dev/null 2>&1; then systemctl stop "$service"; fi
done
tar -czf "$BACKUP/data-and-config.tar.gz" -C / etc/lingxi-next var/lib/lingxi-next
if [[ -n "$LEGACY" ]]; then
  "$PACKAGE/runtime/node/bin/node" "$PACKAGE/tools/migrate-legacy.mjs" --source-dir "$LEGACY" --destination "$DATA" --apply
fi
chown -R lingxi-next:lingxi-next "$DATA"
if [[ -n "$PREVIOUS" ]]; then
  ln -sfn "$PREVIOUS" "$ROOT/previous"
fi
ln -sfn "$ROOT/releases/$VERSION" "$ROOT/current"
install -m 0644 deploy/lingxi-next-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lingxi-next-api.service
PORT="$(python3 - "$CONFIG/robot.env" <<'PY'
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
    --cacert "$CONFIG/tls/ca.crt" "https://127.0.0.1:$PORT/health" >/dev/null; then
  echo "New API health check failed. Restore with deploy/rollback.sh; backup: $BACKUP" >&2
  exit 1
fi
printf 'Installed commissioning build %s. Console: https://%s:%s\n' "$VERSION" "$ROBOT_IP" "$PORT"
echo "Pairing code: /etc/lingxi-next/pairing-code.txt (root only). Import tls/ca.crt on each client."
echo "Configure the existing ROS/AIMDK setup and actual topics in robot.env, then enable lingxi-next-aimdk."
echo "Configure LinkSoul credentials in the console and LINKSOUL_AGENT_ID in robot.env, then enable lingxi-next-agent."
echo "No legacy service, vendor AIMDK installation, or map asset has been changed."
