"""Upgrades restore the Lingxi Next services that were running beforehand."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DeploymentServiceRestore(unittest.TestCase):
    def test_active_services_are_captured_before_upgrade_stops_them(self):
        transaction = (ROOT / "deploy" / "transaction.sh").read_text(encoding="utf-8")
        capture = 'for service in "${SERVICES[@]}"; do\n    if systemctl is-active --quiet "$service"; then ACTIVE_SERVICES+=("$service"); fi\n  done'
        self.assertIn(capture, transaction)
        self.assertLess(transaction.index(capture), transaction.index("deployment_stop()"))

    def test_installer_restores_active_services_after_core_health(self):
        installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
        health = installer.index("if ! deployment_health; then")
        restore = installer.index("if ! deployment_restore_active_services; then")
        commit = installer.index("deployment_commit", restore)
        self.assertLess(health, restore)
        self.assertLess(restore, commit)

    def test_restore_order_starts_apis_before_robot_owners_and_voice(self):
        transaction = (ROOT / "deploy" / "transaction.sh").read_text(encoding="utf-8")
        order = "local -a restore_order=(api aimdk actions independent-mapping independent-slam navigation agent)"
        self.assertIn(order, transaction)
        self.assertIn('systemctl is-active --quiet "$service" || return 1', transaction)


if __name__ == "__main__":
    unittest.main()
