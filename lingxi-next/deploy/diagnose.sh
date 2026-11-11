#!/usr/bin/env bash
set -euo pipefail
echo "Lingxi Next diagnostic (no credentials, transcript, map, or business JSON)"
uname -sm
for service in api aimdk actions agent independent-mapping independent-slam navigation; do
  systemctl show "lingxi-next-${service}.service" --property=ActiveState,SubState,Result,ExecMainStatus
done
if [[ -f /opt/lingxi-next/current/VERSION ]]; then cat /opt/lingxi-next/current/VERSION; fi
CHECKER="$(dirname -- "${BASH_SOURCE[0]}")/diagnostic_config.py"
TARGETS="$(python3 "$CHECKER" targets /etc/lingxi-next/robot.env)"
STATUS=0
if ! python3 "$CHECKER" bridge /etc/lingxi-next/robot.env; then STATUS=1; fi
while read -r service port; do
  printf '%s HTTPS %s: ' "$service" "$port"
  if curl --fail --noproxy '*' --silent --show-error --max-time 3 \
      --cacert /etc/lingxi-next/tls/ca.crt "https://127.0.0.1:$port/health"; then
    echo
  else
    echo "health check failed"
    STATUS=1
  fi
done <<< "$TARGETS"
echo "For local error review: journalctl -u lingxi-next-api -u lingxi-next-aimdk -u lingxi-next-actions -u lingxi-next-agent -u lingxi-next-independent-mapping -u lingxi-next-independent-slam -u lingxi-next-navigation --since '10 minutes ago'"
exit "$STATUS"
