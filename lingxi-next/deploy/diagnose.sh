#!/usr/bin/env bash
set -euo pipefail
echo "Lingxi Next diagnostic (no credentials, transcript, map, or business JSON)"
uname -sm
for service in api aimdk actions agent independent-mapping independent-slam navigation; do
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
python3 - /etc/lingxi-next/robot.env <<'PY'
import pathlib
import stat
import sys

config = pathlib.Path(sys.argv[1])
values = []
if config.exists():
    for line in config.read_text().splitlines():
        if line.startswith("LINGXI_NAVIGATION_SOCKET="):
            values.append(line.partition("=")[2].strip())
if len(values) > 1:
    raise SystemExit("Invalid duplicate navigation bridge configuration")
value = values[0] if values else ""
if not value:
    print("navigation bridge disabled")
else:
    endpoint = pathlib.Path(value)
    if not endpoint.is_absolute() or str(endpoint) != value or len(value.encode("utf-8")) >= 100:
        raise SystemExit("Invalid navigation bridge socket path")
    try:
        metadata = endpoint.lstat()
    except FileNotFoundError:
        print("navigation bridge configured but not listening (socket absent)")
    else:
        if not stat.S_ISSOCK(metadata.st_mode):
            raise SystemExit("Invalid navigation bridge socket file type")
        print("navigation bridge present (not a liveness or readiness check)")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            print("navigation bridge socket permissions require review (expected 0600)")
PY
curl --silent --show-error --max-time 3 --cacert /etc/lingxi-next/tls/ca.crt "https://127.0.0.1:$PORT/health"
echo
echo "For local error review: journalctl -u lingxi-next-api -u lingxi-next-aimdk -u lingxi-next-actions -u lingxi-next-agent -u lingxi-next-independent-mapping -u lingxi-next-independent-slam -u lingxi-next-navigation --since '10 minutes ago'"
