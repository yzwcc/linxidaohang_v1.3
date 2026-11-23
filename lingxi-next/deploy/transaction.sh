#!/usr/bin/env bash
# Shared by install and rollback; only this product's services are touched.
SERVICES=(lingxi-next-agent.service lingxi-next-api.service lingxi-next-aimdk.service
  lingxi-next-actions.service
  lingxi-next-independent-mapping.service lingxi-next-independent-slam.service
  lingxi-next-navigation.service lingxi-next-reference-navigation.service)
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
  local port response
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
  response="$(curl --fail --noproxy '*' --silent --max-time 3 --retry 10 --retry-connrefused --retry-delay 1 \
    --cacert "$CONFIG/tls/ca.crt" "https://127.0.0.1:$port/health")" || return 1
  if [[ "${1:-0}" == 1 ]]; then
    python3 -c 'import json,sys
try:
    health = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
sys.exit(0 if isinstance(health, dict) and health.get("ok") is True
         and health.get("referenceStaticReady") is True else 1)' <<< "$response" || {
      echo "HTTPS API did not confirm a decoded, motion-disabled reference snapshot" >&2
      return 1
    }
  fi
}

deployment_require_reference_profile() {
  local release="$1" checker
  checker="$release/deploy/reference_dds_profile.py"
  if [[ -z "$release" || ! -f "$checker" || -L "$checker" ]] ||
      ! /usr/bin/python3 -I -B "$checker" --check-env "$CONFIG/robot.env"; then
    echo "Reference release requires a dedicated UDPv4-only DDS profile. No reference service was started." >&2
    return 1
  fi
}

deployment_start_reference() {
  local attempt state
  deployment_require_reference_profile "$(deployment_pointer current)" || return 1
  systemctl start lingxi-next-reference-navigation.service || return 1
  for ((attempt=0; attempt<45; attempt++)); do
    state="$(curl --silent --fail --noproxy '*' --max-time 1 \
      --unix-socket /run/lingxi-next-reference/control.sock http://localhost/lingxi/status)" || state=''
    if python3 -c 'import json, sys
try:
    status = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
sys.exit(0 if (isinstance(status, dict) and "navigation" not in status and status.get("phase") == "idle"
               and status.get("calibration") == "matched" and status.get("mappingReady") is True
               and status.get("sensorsReady") is True and status.get("aimdkReady") is True
               and status.get("nav2Available") is True) else 1)' <<< "$state"; then
      systemctl is-active --quiet lingxi-next-reference-navigation.service && return 0
    fi
    sleep 1
  done
  echo "Read-only FAST-LIO service did not confirm idle, target calibration, sensors, AIMDK and Nav2 discovery" >&2
  return 1
}

deployment_begin() {
  OLD_CURRENT="$(deployment_pointer current)" || { echo "Invalid current release" >&2; exit 1; }
  OLD_PREVIOUS="$(deployment_pointer previous)" || { echo "Invalid previous release" >&2; exit 1; }
  if [[ -n "$OLD_CURRENT" ]]; then (cd "$OLD_CURRENT" && sha256sum -c SHA256SUMS >/dev/null); fi
  OLD_API_ACTIVE=0
  OLD_API_ENABLED=0
  OLD_REFERENCE_ENABLED=0
  ACTIVE_SERVICES=()
  local service
  for service in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$service"; then ACTIVE_SERVICES+=("$service"); fi
  done
  if [[ -n "$OLD_CURRENT" ]] && systemctl is-active --quiet lingxi-next-api.service; then OLD_API_ACTIVE=1; fi
  if systemctl is-enabled --quiet lingxi-next-api.service; then OLD_API_ENABLED=1; fi
  if systemctl is-enabled --quiet lingxi-next-reference-navigation.service; then OLD_REFERENCE_ENABLED=1; fi
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
  local -a restore_order=(reference-navigation api aimdk actions independent-mapping independent-slam navigation agent)
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
  local status="$1" failed=0 service reference_restore_blocked=0
  trap - EXIT HUP INT TERM
  [[ "$status" != 0 ]] || return 0
  set +e
  echo "Deployment failed; restoring prior code, units and saved state. Backup: $BACKUP" >&2
  for service in "${SERVICES[@]}"; do
    if systemctl cat "$service" >/dev/null 2>&1; then systemctl stop "$service" || failed=1; fi
  done
  if [[ "$OLD_API_ENABLED" == 0 ]]; then systemctl disable lingxi-next-api.service || failed=1; fi
  if [[ "$OLD_REFERENCE_ENABLED" == 0 ]] && systemctl is-enabled --quiet lingxi-next-reference-navigation.service; then
    systemctl disable lingxi-next-reference-navigation.service || failed=1
  fi
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
  if [[ -n "$OLD_CURRENT" && -f "$OLD_CURRENT/.reference-commissioning" ]] &&
      ! deployment_require_reference_profile "$OLD_CURRENT"; then
    reference_restore_blocked=1
    systemctl disable lingxi-next-reference-navigation.service || failed=1
  elif [[ "$OLD_REFERENCE_ENABLED" == 1 ]] && ! systemctl is-enabled --quiet lingxi-next-reference-navigation.service; then
    systemctl enable lingxi-next-reference-navigation.service || failed=1
  fi
  if [[ "$failed" == 0 && "$OLD_API_ACTIVE" == 1 && "$reference_restore_blocked" == 0 ]]; then
    if [[ -n "$OLD_CURRENT" && -f "$OLD_CURRENT/.reference-commissioning" ]] && ! deployment_start_reference; then
      failed=1
      systemctl stop lingxi-next-reference-navigation.service || true
    elif ! systemctl start lingxi-next-api.service || ! deployment_health; then
      failed=1
      systemctl stop lingxi-next-api.service || true
    fi
  fi
  if [[ "$failed" == 0 ]]; then
    if [[ "$reference_restore_blocked" == 1 ]]; then
      echo "Prior deployment files restored; the old reference service remains stopped and disabled." >&2
    else
      echo "Prior deployment restored. Navigation and voice remain stopped pending operator inspection." >&2
    fi
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
