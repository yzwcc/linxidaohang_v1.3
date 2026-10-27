#!/usr/bin/env bash
set -euo pipefail
echo "Lingxi Next diagnostic (no credentials, transcript, map, or business JSON)"
uname -sm
for service in api aimdk agent; do
  systemctl show "lingxi-next-${service}.service" --property=ActiveState,SubState,Result,ExecMainStatus
done
if [[ -f /opt/lingxi-next/current/VERSION ]]; then cat /opt/lingxi-next/current/VERSION; fi
PORT="$(python3 - /etc/lingxi-next/robot.env <<'PY'
import pathlib
import re
import sys

config = pathlib.Path(sys.argv[1])
values = []
if config.exists():
    for line in config.read_text().splitlines():
        if line.startswith("LINGXI_PORT="):
            value = line.partition("=")[2].strip()
            if not re.fullmatch(r"[0-9]{1,5}", value):
                raise SystemExit("Invalid LINGXI_PORT")
            port = int(value)
            if not 1 <= port <= 65535:
                raise SystemExit("Invalid LINGXI_PORT")
            values.append(port)
if len(values) > 1:
    raise SystemExit("Duplicate LINGXI_PORT")
print(values[0] if values else 3000)
PY
)"
curl --silent --show-error --max-time 3 --cacert /etc/lingxi-next/tls/ca.crt "https://127.0.0.1:$PORT/health"
echo
echo "For local error review: journalctl -u lingxi-next-api -u lingxi-next-aimdk -u lingxi-next-agent --since '10 minutes ago'"
