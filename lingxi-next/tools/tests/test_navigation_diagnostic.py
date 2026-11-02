import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest


class NavigationDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (Path(__file__).resolve().parents[2] / 'deploy' / 'diagnose.sh').read_text(encoding='utf-8')
        marker = "python3 - /etc/lingxi-next/robot.env <<'PY'\n"
        cls.program = source.split(marker)[-1].split('\nPY\n', 1)[0]
        if 'navigation bridge disabled' not in cls.program:
            raise AssertionError('navigation diagnostic program not found')

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='lx-nav-diagnostic-')
        self.root = Path(self.temporary.name)
        self.config = self.root / 'robot.env'
        self.addCleanup(self.temporary.cleanup)

    def diagnose(self, value=None):
        if value is not None:
            self.config.write_text(value, encoding='utf-8')
        return subprocess.run([sys.executable, '-c', self.program, str(self.config)],
                              capture_output=True, text=True, timeout=5)

    def test_absent_config_is_disabled(self):
        result = self.diagnose()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), 'navigation bridge disabled')

    def test_commented_or_empty_setting_is_disabled(self):
        for config in ['# LINGXI_NAVIGATION_SOCKET=/private/navigation.sock\n',
                       'LINGXI_NAVIGATION_SOCKET=\n']:
            with self.subTest(config=config):
                result = self.diagnose(config)
                self.assertEqual(result.returncode, 0)
                self.assertIn('navigation bridge disabled', result.stdout)

    @unittest.skipUnless(os.name == 'posix', 'Linux path semantics required')
    def test_configured_socket_absent(self):
        result = self.diagnose(f'LINGXI_NAVIGATION_SOCKET={self.root}/absent.sock\n')
        self.assertEqual(result.returncode, 0)
        self.assertIn('configured but not listening (socket absent)', result.stdout)

    def test_relative_path_is_invalid(self):
        result = self.diagnose('LINGXI_NAVIGATION_SOCKET=relative.sock\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Invalid navigation bridge socket path', result.stderr)

    def test_duplicate_setting_is_invalid(self):
        result = self.diagnose('LINGXI_NAVIGATION_SOCKET=\nLINGXI_NAVIGATION_SOCKET=\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Invalid duplicate', result.stderr)

    def test_does_not_print_other_config_values(self):
        result = self.diagnose('UNRELATED_VALUE=synthetic-private-value\n')
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('synthetic-private-value', result.stdout + result.stderr)

    @unittest.skipUnless(os.name == 'posix', 'Linux socket metadata required')
    def test_regular_file_is_not_a_socket(self):
        endpoint = self.root / 'navigation.sock'
        endpoint.touch()
        result = self.diagnose(f'LINGXI_NAVIGATION_SOCKET={endpoint}\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Invalid navigation bridge socket file type', result.stderr)

    @unittest.skipUnless(os.name == 'posix', 'Linux socket metadata required')
    def test_present_socket_is_not_reported_as_ready(self):
        endpoint = self.root / 'navigation.sock'
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(endpoint))
            endpoint.chmod(0o600)
            result = self.diagnose(f'LINGXI_NAVIGATION_SOCKET={endpoint}\n')
            self.assertEqual(result.returncode, 0)
            self.assertIn('present (not a liveness or readiness check)', result.stdout)
            self.assertNotIn('permissions require review', result.stdout)
            endpoint.chmod(0o660)
            result = self.diagnose()
            self.assertIn('permissions require review', result.stdout)


if __name__ == '__main__':
    unittest.main()
