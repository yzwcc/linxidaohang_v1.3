#!/usr/bin/env bash
# Shared by install and rollback; only this product's services are touched.
SERVICES=(lingxi-next-agent.service lingxi-next-api.service lingxi-next-aimdk.service
  lingxi-next-actions.service
  lingxi-next-independent-mapping.service lingxi-next-independent-slam.service
  lingxi-next-navigation.service)
ACTIVE_SERVICES=()

deployment_lock() {
  [[ ! -L "$ROOT" && ! -L "$ROOT/releases" && ! -L "$ROOT/backups" ]] || {
    echo "Managed deployment directories must not be symlinks" >&2; exit 1;
  }
  install -d -m 0755 "$ROOT" "$ROOT/releases"
  install -d -m 0700 "$ROOT/backups"
  [[ ! -L "$ROOT/.deployment.lock" ]] || { echo "Invalid deployment lock" >&2; exit 1; }
  exec 9>"$ROOT/.deployment.lock"
  flock -n 9 || { echo "Another install or rollback is running" >&2; exit 1; }
}

deployment_pointer() {
  local name="$1" target
  if [[ -L "$ROOT/$name" ]]; then
    target="$(readlink -f "$ROOT/$name")" || return 1
    [[ "$(dirname -- "$target")" == "$ROOT/releases" && -f "$target/VERSION" ]] || return 1
    printf '%s' "$target"
  elif [[ -e "$ROOT/$name" ]]; then
    return 1
  fi
}

deployment_link() {
  local name="$1" target="$2" temporary="$ROOT/.$1-$$"
  [[ ! -e "$temporary" && ! -L "$temporary" ]] || return 1
  if [[ -n "$target" ]]; then
    ln -s "$target" "$temporary" || return 1
    mv -Tf "$temporary" "$ROOT/$name"
  elif [[ -L "$ROOT/$name" ]]; then
    unlink "$ROOT/$name"
  else
    [[ ! -e "$ROOT/$name" ]]
  fi
}

deployment_health() {
  local port
  port="$(python3 - "$CONFIG/robot.env" <<'PY'
import pathlib, re, sys
values = [line.partition('=')[2].strip() for line in pathlib.Path(sys.argv[1]).read_text().splitlines()
          if line.startswith('LINGXI_PORT=')]
if len(values) > 1 or (values and (not re.fullmatch('[0-9]{1,5}', values[0])
                                  or not 1 <= int(values[0]) <= 65535)):
    raise SystemExit('Invalid or duplicate LINGXI_PORT')
port = int(values[0]) if values else 3445
if port in (3000, 3444):
    raise SystemExit('LINGXI_PORT conflicts with the separate navigation console or another project')
print(port)
PY
  )" || return 1
  DEPLOYMENT_PORT="$port"
  curl --fail --noproxy '*' --silent --max-time 3 --retry 10 --retry-connrefused --retry-delay 1 \
    --cacert "$CONFIG/tls/ca.crt" "https://127.0.0.1:$port/health" >/dev/null
}

deployment_begin() {
  OLD_CURRENT="$(deployment_pointer current)" || { echo "Invalid current release" >&2; exit 1; }
  OLD_PREVIOUS="$(deployment_pointer previous)" || { echo "Invalid previous release" >&2; exit 1; }
  if [[ -n "$OLD_CURRENT" ]]; then (cd "$OLD_CURRENT" && sha256sum -c SHA256SUMS >/dev/null); fi
  OLD_API_ACTIVE=0
  OLD_API_ENABLED=0
  ACTIVE_SERVICES=()
  local service
  for service in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$service"; then ACTIVE_SERVICES+=("$service"); fi
  done
  if [[ -n "$OLD_CURRENT" ]] && systemctl is-active --quiet lingxi-next-api.service; then OLD_API_ACTIVE=1; fi
  if systemctl is-enabled --quiet lingxi-next-api.service; then OLD_API_ENABLED=1; fi
  BACKUP="$(mktemp -d "$ROOT/backups/$1-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
  install -d -m 0700 "$BACKUP/units"
  for service in "${SERVICES[@]}"; do
    [[ ! -L "/etc/systemd/system/$service" ]] || { echo "Unmanaged unit symlink" >&2; exit 1; }
    if [[ -e "/etc/systemd/system/$service" ]]; then
      cp -p "/etc/systemd/system/$service" "$BACKUP/units/$service"
    fi
  done
  DATA_SNAPSHOT_READY=0
  trap 'deployment_recover $?' EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
}

deployment_stop() {
  for service in "${SERVICES[@]}"; do
    if systemctl cat "$service" >/dev/null 2>&1; then systemctl stop "$service"; fi
  done
}

deployment_restore_active_services() {
  local service name was_active
  local -a restore_order=(api aimdk actions independent-mapping independent-slam navigation agent)
  for name in "${restore_order[@]}"; do
    service="lingxi-next-${name}.service"
    was_active=0
    for active in "${ACTIVE_SERVICES[@]}"; do
      if [[ "$active" == "$service" ]]; then was_active=1; break; fi
    done
    if [[ "$was_active" == 1 ]]; then
      systemctl start "$service"
      systemctl is-active --quiet "$service" || return 1
    fi
  done
}

deployment_snapshot() {
  tar -czf "$BACKUP/data-and-config.tar.gz" -C / etc/lingxi-next var/lib/lingxi-next
  DATA_SNAPSHOT_READY=1
}

deployment_recover() {
  local status="$1" failed=0 service
  trap - EXIT HUP INT TERM
  [[ "$status" != 0 ]] || return 0
  set +e
  echo "Deployment failed; restoring prior code, units and saved state. Backup: $BACKUP" >&2
  for service in "${SERVICES[@]}"; do
    if systemctl cat "$service" >/dev/null 2>&1; then systemctl stop "$service" || failed=1; fi
  done
  if [[ "$OLD_API_ENABLED" == 0 ]]; then systemctl disable lingxi-next-api.service || failed=1; fi
  deployment_link current "$OLD_CURRENT" || failed=1
  deployment_link previous "$OLD_PREVIOUS" || failed=1
  for service in "${SERVICES[@]}"; do
    if [[ -f "$BACKUP/units/$service" ]]; then
      cp -p "$BACKUP/units/$service" "/etc/systemd/system/$service" || failed=1
    elif [[ -f "/etc/systemd/system/$service" ]]; then
      unlink "/etc/systemd/system/$service" || failed=1
    fi
  done
  # Do not restore files under a process that could still be writing them.
  if [[ "$failed" == 0 && "$DATA_SNAPSHOT_READY" == 1 ]]; then
    tar -xzf "$BACKUP/data-and-config.tar.gz" -C / || failed=1
  fi
  systemctl daemon-reload || failed=1
  if [[ "$failed" == 0 && "$OLD_API_ACTIVE" == 1 ]]; then
    if ! systemctl start lingxi-next-api.service || ! deployment_health; then
      failed=1
      systemctl stop lingxi-next-api.service || true
    fi
  fi
  if [[ "$failed" == 0 ]]; then
    echo "Prior deployment restored. Navigation and voice remain stopped pending operator inspection." >&2
    printf 'recovered\n' > "$BACKUP/recovery-status"
  else
    echo "Automatic recovery incomplete; services were not automatically resumed. Inspect retained backup and services." >&2
    printf 'recovery-incomplete\n' > "$BACKUP/recovery-status"
  fi
  exit "$status"
}

deployment_commit() {
  printf 'committed\n' > "$BACKUP/recovery-status"
  trap - EXIT HUP INT TERM
}
