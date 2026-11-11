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
for command in systemctl openssl python3 tar sha256sum curl getent groupadd useradd flock; do command -v "$command" >/dev/null || { echo "Missing dependency: $command" >&2; exit 1; }; done
python3 -c 'import ipaddress,sys; ipaddress.ip_address(sys.argv[1])' "$ROBOT_IP"
cd "$PACKAGE"
sha256sum -c SHA256SUMS >/dev/null
VERSION="$(<VERSION)"
[[ "$VERSION" =~ ^[0-9A-Za-z._-]{1,80}$ ]] || { echo "Invalid version" >&2; exit 1; }
[[ -x runtime/node/bin/node && -x runtime/java/bin/java ]] || { echo "Bundled runtimes are missing" >&2; exit 1; }
runtime/node/bin/node --version
runtime/java/bin/java -version
source "$PACKAGE/deploy/transaction.sh"
deployment_lock
[[ ! -e "$ROOT/releases/$VERSION" ]] || { echo "Release already installed; choose a new build or use rollback.sh" >&2; exit 1; }
PREVIOUS="$(deployment_pointer current)" || { echo "Invalid current release" >&2; exit 1; }
deployment_pointer previous >/dev/null || { echo "Invalid previous release" >&2; exit 1; }
if ! getent group lingxi-next >/dev/null; then groupadd --system lingxi-next; fi
if ! id lingxi-next >/dev/null 2>&1; then useradd --system --gid lingxi-next --home-dir "$DATA" --shell /usr/sbin/nologin lingxi-next; fi
install -d -m 0755 "$ROOT" "$ROOT/releases"
install -d -m 0750 -o root -g lingxi-next "$CONFIG"
install -d -m 0750 -o root -g lingxi-next "$CONFIG/independent"
install -d -m 0700 "$ROOT/backups"
install -d -m 0700 -o lingxi-next -g lingxi-next "$DATA" "$DATA/runtime" "$DATA/maps" "$DATA/run" "$DATA/native-inputs"
if [[ -d "$PACKAGE/independent" ]]; then
  install -d -m 0750 -o root -g lingxi-next "$CONFIG/dds"
  python3 "$PACKAGE/deploy/provision_dds_profile.py" \
    --evidence "$PACKAGE/independent/source/docs/slam-humble-live-evidence.json"
fi
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
  # Some OpenSSL builds print a mismatch but exit successfully for -checkip.
  CHECK_IP="$(openssl x509 -in "$CONFIG/tls/robot.crt" -noout -checkip "$ROBOT_IP")"
  [[ "$CHECK_IP" == *"does match certificate"* && "$CHECK_IP" != *"does NOT match"* ]] || {
    echo "Existing certificate does not cover this robot IP; renew it before upgrading" >&2; exit 1;
  }
fi
install -d -m 0755 "$ROOT/releases/$VERSION"
cp -a "$PACKAGE/." "$ROOT/releases/$VERSION/"
chown -R root:root "$ROOT/releases/$VERSION"
chmod 0755 "$ROOT/releases/$VERSION"
if [[ -n "$LEGACY" ]]; then
  "$PACKAGE/runtime/node/bin/node" "$PACKAGE/tools/migrate-legacy.mjs" --source-dir "$LEGACY" --destination "$DATA"
fi
deployment_begin "pre-$VERSION"
deployment_stop
deployment_snapshot
if [[ -n "$LEGACY" ]]; then
  "$PACKAGE/runtime/node/bin/node" "$PACKAGE/tools/migrate-legacy.mjs" --source-dir "$LEGACY" --destination "$DATA" --apply
fi
chown -R lingxi-next:lingxi-next "$DATA"
python3 "$PACKAGE/deploy/migrate_robot_env.py" "$CONFIG/robot.env"
deployment_link current "$ROOT/releases/$VERSION"
install -m 0644 deploy/lingxi-next-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lingxi-next-api.service
if ! deployment_health; then
  echo "New API health check failed." >&2
  exit 1
fi
if ! deployment_restore_active_services; then
  echo "A previously active Lingxi Next service failed to restart." >&2
  exit 1
fi
if [[ -n "$PREVIOUS" ]]; then deployment_link previous "$PREVIOUS"; fi
deployment_commit
PORT="$DEPLOYMENT_PORT"
printf 'Installed commissioning build %s. Navigation console: https://%s:3444\n' "$VERSION" "$ROBOT_IP"
printf 'Local management API health: https://127.0.0.1:%s/health\n' "$PORT"
echo "Pairing code: /etc/lingxi-next/pairing-code.txt (root only). Import tls/ca.crt on each client."
echo "Configure the existing ROS/AIMDK setup and actual topics in robot.env, then enable lingxi-next-aimdk."
echo "Configure LinkSoul credentials in the console and LINKSOUL_AGENT_ID in robot.env, then enable lingxi-next-agent."
echo "For independent mapping/SLAM, read docs/INDEPENDENT_INSTALL_20261103.md; owners remain disabled until configured."
echo "No legacy service, vendor AIMDK installation, or map asset has been changed."
