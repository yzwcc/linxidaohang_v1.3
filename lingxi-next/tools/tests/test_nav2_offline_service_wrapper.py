"""The isolated Nav2 wrapper must never let one mode override another."""
import shutil
import subprocess
import sys
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "nav2-offline-service-isolated.sh"


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("bash"),
                     "wrapper argument integration requires Linux bash")
class Nav2OfflineServiceWrapperTests(unittest.TestCase):
    def test_check_cannot_be_overridden_by_lifecycle_diagnostic(self):
        result = subprocess.run(["bash", str(SCRIPT), "--check", "--diagnose-lifecycle"],
                                capture_output=True, text=True, timeout=5, check=False)
        self.assertEqual(2, result.returncode)
        self.assertIn("exactly one wrapper mode may be selected", result.stderr)
        self.assertNotIn("lingxi_offline_nav2_preflight_ok_v1", result.stdout)


if __name__ == "__main__":
    unittest.main()
