"""Commissioning defaults must keep physical backends disabled."""
from pathlib import Path
import unittest


class DeploymentDefaultsTests(unittest.TestCase):
    def test_default_console_does_not_select_physical_navigation_socket(self):
        path = Path(__file__).resolve().parents[2] / "deploy" / "robot.env.example"
        values = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
        self.assertEqual("0", values["LINGXI_INDEPENDENT_PRODUCT"])
        self.assertEqual("", values["LINGXI_NAVIGATION_SOCKET"])
        self.assertEqual("3000", values["LINGXI_PORT"])


if __name__ == "__main__":
    unittest.main()
