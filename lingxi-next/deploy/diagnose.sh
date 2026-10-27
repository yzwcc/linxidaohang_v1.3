#!/usr/bin/env bash
set -euo pipefail
echo "Lingxi Next diagnostic (no credentials, transcript, map, or business JSON)"
uname -sm
for service in api aimdk agent; do
  systemctl show "lingxi-next-${service}.service" --property=ActiveState,SubState,Result,ExecMainStatus
done
if [[ -f /opt/lingxi-next/current/VERSION ]]; then cat /opt/lingxi-next/current/VERSION; fi
curl --silent --show-error --max-time 3 --cacert /etc/lingxi-next/tls/ca.crt https://127.0.0.1:3000/health
echo
echo "For local error review: journalctl -u lingxi-next-api -u lingxi-next-aimdk -u lingxi-next-agent --since '10 minutes ago'"
