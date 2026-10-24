#!/usr/bin/env bash
set -Eeuo pipefail

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMP_ROOT="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_ROOT}"' EXIT

ROOT="${TEMP_ROOT}/robot"
PRODUCT="${ROOT}/opt/sparkrobot/lingxi-nav/releases/1.0.8-20261023-r16"
mkdir -p "${PRODUCT}/console/dist/public" "${PRODUCT}/scripts" "${ROOT}/var/lib/sparkrobot/lingxi-nav"
python3 - "${PRODUCT}" "${PACKAGE_DIR}" <<'PY'
import hashlib
import json
import pathlib
import sys

product = pathlib.Path(sys.argv[1])
package = pathlib.Path(sys.argv[2])
paths = [line.strip() for line in (package / "payload/SHA256SUMS.txt").read_text().splitlines() if line.strip()]
changed = []
for line in paths:
    _, relative = line.split("  ", 1)
    target = product / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((package / "test-fixtures" / relative).read_bytes())
    changed.append(relative)

# Existing unchanged files exercise manifest preservation.
keep = product / "console/dist/public/untouched.txt"
keep.write_text("preserve\n")
entries = []
for relative in changed + ["console/dist/public/untouched.txt"]:
    target = product / relative
    entries.append({
        "path": relative,
        "size": target.stat().st_size,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    })
(product / "payload-manifest.json").write_text(json.dumps({"format":"test", "files":entries}) + "\n")
(product / "scripts/verify_payload.py").write_text("raise SystemExit(0)\n")
PY
cat > "${PRODUCT}/scripts/navigation_control.sh" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "${PRODUCT}/scripts/navigation_control.sh"
ln -s "${PRODUCT}" "${ROOT}/opt/sparkrobot/lingxi-nav/current"
touch "${ROOT}/var/lib/sparkrobot/lingxi-nav/console-active"

LINGXI_HOTFIX_ROOT="${ROOT}" LINGXI_HOTFIX_TEST_MODE=true bash "${PACKAGE_DIR}/install.sh" install
[[ -f "${ROOT}/var/lib/sparkrobot/lingxi-nav/console-active" ]]
[[ -f "${ROOT}/var/lib/sparkrobot/lingxi-nav/navigation-stack-active" ]]
python3 "${PRODUCT}/scripts/verify_payload.py" --root "${PRODUCT}" --manifest "${PRODUCT}/payload-manifest.json"
grep -q 'id="robotRadius"' "${PRODUCT}/console/dist/public/index.html"
grep -q 'id="extraClearance"' "${PRODUCT}/console/dist/public/index.html"
grep -q 'id="robotRadius" form="pathSafetyForm"' "${PRODUCT}/console/dist/public/index.html"
grep -q 'id="extraClearance" form="pathSafetyForm"' "${PRODUCT}/console/dist/public/index.html"
grep -q 'id="savePathSafetyBtn" form="pathSafetyForm" type="submit"' "${PRODUCT}/console/dist/public/index.html"
grep -q '^preserve$' "${PRODUCT}/console/dist/public/untouched.txt"
python3 - "${PRODUCT}" <<'PY'
import hashlib
import json
import pathlib
import sys

product = pathlib.Path(sys.argv[1])
manifest = json.loads((product / "payload-manifest.json").read_text())
entries = {entry["path"]: entry for entry in manifest["files"]}
changed = (
    "console/dist/app.js",
    "console/dist/robotClient.js",
    "console/dist/public/app.js",
    "console/dist/public/index.html",
    "console/dist/public/styles.css",
)
for relative in changed:
    target = product / relative
    entry = entries[relative]
    assert entry["size"] == target.stat().st_size, relative
    assert entry["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest(), relative
assert "console/dist/public/untouched.txt" in entries
PY
python3 - "${PACKAGE_DIR}/install.sh" <<'PY'
import pathlib
import sys

installer = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
assert 'NAV_CONTROL' not in installer
assert 'run_agi' not in installer
assert 'ensure_navigation_stack_online' in installer
assert 'navigation_control_do stop' in installer
assert 'navigation_control_do start' in installer
assert 'http://127.0.0.1:18083/nav/status' in installer
assert 'assert_navigation_idle || die' in installer
assert 'assert_console_active || die' in installer
assert 'systemctl_do restart "${CONSOLE_SERVICE}"' in installer
PY

# Unknown mixed console content must be rejected before starting a stopped stack.
cp "${PRODUCT}/console/dist/app.js" "${TEMP_ROOT}/known-app.js"
printf '\nunknown modification\n' >>"${PRODUCT}/console/dist/app.js"
rm -f -- "${ROOT}/var/lib/sparkrobot/lingxi-nav/navigation-stack-active"
if LINGXI_HOTFIX_ROOT="${ROOT}" LINGXI_HOTFIX_TEST_MODE=true bash "${PACKAGE_DIR}/install.sh" install; then
  echo "未知控制台基线未被拒绝" >&2
  exit 1
fi
[[ ! -f "${ROOT}/var/lib/sparkrobot/lingxi-nav/navigation-stack-active" ]]
printf '\nunknown modification\n' >>"${TEMP_ROOT}/known-app.js"
cmp -s "${PRODUCT}/console/dist/app.js" "${TEMP_ROOT}/known-app.js"
cp "${PACKAGE_DIR}/payload/console/dist/app.js" "${PRODUCT}/console/dist/app.js"
touch "${ROOT}/var/lib/sparkrobot/lingxi-nav/navigation-stack-active"

BEFORE_REJECT="$(sha256sum "${PRODUCT}/console/dist/app.js" "${PRODUCT}/payload-manifest.json")"
if LINGXI_HOTFIX_ROOT="${ROOT}" LINGXI_HOTFIX_TEST_MODE=true \
  LINGXI_HOTFIX_TEST_NAV_STATUS='{"navigation":{"status":"active"},"task":{"active":false},"auto_return":{"state":"idle"}}' \
  bash "${PACKAGE_DIR}/install.sh" install; then
  echo "忙碌导航状态未被拒绝" >&2
  exit 1
fi
AFTER_REJECT="$(sha256sum "${PRODUCT}/console/dist/app.js" "${PRODUCT}/payload-manifest.json")"
[[ "${BEFORE_REJECT}" == "${AFTER_REJECT}" ]]

BACKUP="$(find "${ROOT}/var/lib/sparkrobot/lingxi-nav/path-safety-ui-hotfix-backups" -mindepth 1 -maxdepth 1 -type d -print -quit)"
LINGXI_HOTFIX_ROOT="${ROOT}" LINGXI_HOTFIX_TEST_MODE=true bash "${PACKAGE_DIR}/install.sh" rollback "${BACKUP}"
[[ -f "${ROOT}/var/lib/sparkrobot/lingxi-nav/console-active" ]]
[[ -f "${ROOT}/var/lib/sparkrobot/lingxi-nav/navigation-stack-active" ]]
python3 - "${PRODUCT}" "${BACKUP}" <<'PY'
import hashlib
import pathlib
import sys

product, backup = map(pathlib.Path, sys.argv[1:])
for relative in (
    "console/dist/app.js",
    "console/dist/robotClient.js",
    "console/dist/public/app.js",
    "console/dist/public/index.html",
    "console/dist/public/styles.css",
):
    assert hashlib.sha256((product / relative).read_bytes()).digest() == hashlib.sha256((backup / relative).read_bytes()).digest(), relative
assert (product / "console/dist/public/untouched.txt").read_text() == "preserve\n"
PY

# Simulate a failed navigation restart after payload replacement; installation must restore R3.
python3 - "${PRODUCT}" "${PACKAGE_DIR}" <<'PY'
import hashlib
import json
import pathlib
import sys

product, package = map(pathlib.Path, sys.argv[1:])
paths = [line.strip() for line in (package / "payload/SHA256SUMS.txt").read_text().splitlines() if line.strip()]
entries = []
for line in paths:
    _, relative = line.split("  ", 1)
    target = product / relative
    target.write_bytes((package / "test-fixtures-r3" / relative).read_bytes())
    entries.append({
        "path": relative,
        "size": target.stat().st_size,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    })
untouched = product / "console/dist/public/untouched.txt"
entries.append({
    "path": "console/dist/public/untouched.txt",
    "size": untouched.stat().st_size,
    "sha256": hashlib.sha256(untouched.read_bytes()).hexdigest(),
})
(product / "payload-manifest.json").write_text(json.dumps({"format":"test", "files":entries}) + "\n")
PY
sleep 1
if LINGXI_HOTFIX_ROOT="${ROOT}" LINGXI_HOTFIX_TEST_MODE=true \
  LINGXI_HOTFIX_TEST_NAV_START_FAIL_ONCE=true bash "${PACKAGE_DIR}/install.sh" install; then
  echo "导航栈启动验收失败时未触发回滚" >&2
  exit 1
fi
[[ -f "${ROOT}/var/lib/sparkrobot/lingxi-nav/console-active" ]]
[[ -f "${ROOT}/var/lib/sparkrobot/lingxi-nav/navigation-stack-active" ]]
python3 - "${PRODUCT}" "${PACKAGE_DIR}" <<'PY'
import hashlib
import pathlib
import sys

product, package = map(pathlib.Path, sys.argv[1:])
for line in (package / "payload/SHA256SUMS.txt").read_text().splitlines():
    if not line.strip():
        continue
    _, relative = line.split("  ", 1)
    actual = hashlib.sha256((product / relative).read_bytes()).digest()
    previous = hashlib.sha256((package / "test-fixtures-r3" / relative).read_bytes()).digest()
    assert actual == previous, relative
PY

# Upgrade and rollback from the known R3 hotfix.
sleep 1
LINGXI_HOTFIX_ROOT="${ROOT}" LINGXI_HOTFIX_TEST_MODE=true bash "${PACKAGE_DIR}/install.sh" install
grep -q 'form="pathSafetyForm"' "${PRODUCT}/console/dist/public/index.html"
BACKUP_R1="$(find "${ROOT}/var/lib/sparkrobot/lingxi-nav/path-safety-ui-hotfix-backups" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -n | tail -n 1 | cut -d' ' -f2-)"
LINGXI_HOTFIX_ROOT="${ROOT}" LINGXI_HOTFIX_TEST_MODE=true bash "${PACKAGE_DIR}/install.sh" rollback "${BACKUP_R1}"
[[ -f "${ROOT}/var/lib/sparkrobot/lingxi-nav/console-active" ]]
[[ -f "${ROOT}/var/lib/sparkrobot/lingxi-nav/navigation-stack-active" ]]
python3 - "${PRODUCT}" "${PACKAGE_DIR}" <<'PY'
import hashlib
import pathlib
import sys

product, package = map(pathlib.Path, sys.argv[1:])
for line in (package / "payload/SHA256SUMS.txt").read_text().splitlines():
    if not line.strip():
        continue
    _, relative = line.split("  ", 1)
    actual = hashlib.sha256((product / relative).read_bytes()).digest()
    previous = hashlib.sha256((package / "test-fixtures-r3" / relative).read_bytes()).digest()
    assert actual == previous, relative
PY
echo "ISOLATED_INSTALL_ROLLBACK_OK"
