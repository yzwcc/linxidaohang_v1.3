#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DELIVERY="$(dirname "${HERE}")"
ARCHIVE="${DELIVERY}/lingxi-v1.3.9-complete-20261019-r5-deployment.tar.gz"
NAME="lingxi-v1.3.9-complete-20261019-r5-deployment"
NAV="sparkrobot-lingxi-nav-internal-1.0.23-20261019"
[[ "${EUID}" -eq 0 ]] || { echo "Run as Linux root for isolated installer tests" >&2; exit 1; }
TEMP_ROOT="$(mktemp -d /tmp/lingxi-r5-extracted.XXXXXX)"
[[ "${TEMP_ROOT}" == /tmp/lingxi-r5-extracted.* ]] || exit 1
trap 'rm -rf -- "${TEMP_ROOT}"' EXIT
tar -xzf "${ARCHIVE}" -C "${TEMP_ROOT}"
cd "${TEMP_ROOT}/${NAME}"
sha256sum -c SHA256SUMS.txt >"${TEMP_ROOT}/outer-checksums.log"
tar -xzf "components/${NAV}.tar.gz" -C "${TEMP_ROOT}"
cd "${TEMP_ROOT}/${NAV}"
sha256sum -c SHA256SUMS >"${TEMP_ROOT}/inner-checksums.log"
python3 scripts/verify_payload.py --root payload --manifest payload-manifest.json
while IFS= read -r -d '' script; do bash -n "${script}"; done < <(find "${TEMP_ROOT}" -type f -name '*.sh' -print0)
export PYTHONDONTWRITEBYTECODE=1
python3 - "${HERE}" "${TEMP_ROOT}/${NAV}" "${TEMP_ROOT}/${NAME}" "${DELIVERY}" <<'PY'
import ast
import hashlib
import json
from pathlib import Path
import sys
import unittest
builder, inner, outer, delivery = map(Path, sys.argv[1:])
for root in (inner / "scripts", inner / "payload/navigation", inner / "source/navigation", outer / "scripts"):
    for path in root.rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
sys.path.insert(0, str(builder))
import test_install_lifecycle
test_install_lifecycle.HERE = inner
suite = unittest.defaultTestLoader.loadTestsFromTestCase(test_install_lifecycle.InstallerLifecycleTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful() or result.skipped:
    raise SystemExit("Extracted installer lifecycle failed")
archive = delivery / "lingxi-v1.3.9-complete-20261019-r5-deployment.tar.gz"
h = hashlib.sha256()
with archive.open("rb") as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        h.update(block)
report = {
    "passed": True, "archive": archive.name, "sha256": h.hexdigest(),
    "linux": True, "outer_checksums": "passed", "inner_checksums": "passed",
    "payload_manifest": "passed", "all_shell_syntax": "passed",
    "python_ast": "passed", "extracted_installer_lifecycle_tests": result.testsRun,
    "failed": 0, "skipped": 0, "robot_services_started": False,
}
(delivery / "extracted-validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print("FINAL_EXTRACTED_VALIDATION_PASS", h.hexdigest())
PY
