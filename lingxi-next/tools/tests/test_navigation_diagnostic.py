import hashlib
import json
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
        cls.program = Path(__file__).resolve().parents[2] / 'deploy' / 'diagnostic_config.py'

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='lx-nav-diagnostic-')
        self.root = Path(self.temporary.name)
        self.config = self.root / 'robot.env'
        self.addCleanup(self.temporary.cleanup)

    def diagnose(self, value=None, mode='bridge'):
        if value is not None:
            self.config.write_text(value, encoding='utf-8')
        return subprocess.run([sys.executable, str(self.program), mode, str(self.config)],
                              capture_output=True, text=True, timeout=5)

    def test_absent_config_cannot_report_disabled_or_healthy(self):
        result = self.diagnose()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')

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
        self.assertIn('diagnostic configuration invalid', result.stderr)

    def test_duplicate_setting_is_invalid(self):
        result = self.diagnose('LINGXI_NAVIGATION_SOCKET=\nLINGXI_NAVIGATION_SOCKET=\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('diagnostic configuration invalid', result.stderr)

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
        self.assertIn('diagnostic configuration invalid', result.stderr)

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

    def test_console_and_core_api_have_distinct_default_health_targets(self):
        result = self.diagnose('', mode='targets')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'independent-slam 3444\ncore-api 3445\n')

    def test_unrelated_project_port_is_rejected_without_a_probe_target(self):
        result = self.diagnose('LINGXI_PORT=3000\n', mode='targets')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')

    def test_explicit_internal_api_is_reported_separately(self):
        result = self.diagnose('LINGXI_PORT=3001\nLINGXI_SLAM_PORT=3444\n', mode='targets')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'independent-slam 3444\ncore-api 3001\n')

    def test_invalid_ports_and_collisions_produce_no_probe_targets(self):
        for setting in ('LINGXI_SLAM_PORT=3000\n', 'LINGXI_PORT=3444\n',
                        'LINGXI_PORT=3444\nLINGXI_SLAM_PORT=3456\n',
                        'LINGXI_PORT=3445\nLINGXI_SLAM_PORT=3445\n',
                        'LINGXI_SLAM_PORT=0\n', 'LINGXI_SLAM_PORT=65536\n',
                        'LINGXI_SLAM_PORT=\n', 'LINGXI_PORT=3001\nLINGXI_PORT=3002\n',
                        'LINGXI_SLAM_PORT=$(printenv SECRET)\n'):
            with self.subTest(setting=setting):
                result = self.diagnose(setting, mode='targets')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                self.assertNotIn('SECRET', result.stderr)

    def product_settings(self, product):
        selected = self.root / 'product.json'
        raw = product if isinstance(product, bytes) else json.dumps(product).encode()
        selected.write_bytes(raw)
        return ('LINGXI_PRODUCT_CONFIG=' + str(selected.resolve()) + '\n'
                'LINGXI_PRODUCT_CONFIG_SHA256=' + hashlib.sha256(raw).hexdigest() + '\n')

    @unittest.skipUnless(os.name == 'posix', 'Linux socket paths required')
    def test_product_socket_takes_precedence_over_core_environment(self):
        settings = self.product_settings({'schemaVersion': 1, 'navigationSocket': str(self.root / 'absent.sock')})
        result = self.diagnose(settings + 'LINGXI_NAVIGATION_SOCKET=wrong-core-socket\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('socket absent', result.stdout)

    def test_product_without_socket_does_not_fall_back_to_core_environment(self):
        settings = self.product_settings({'schemaVersion': 1})
        result = self.diagnose(settings + 'LINGXI_NAVIGATION_SOCKET=wrong-core-socket\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'navigation bridge disabled\n')

    def test_product_integrity_failure_does_not_expose_data_or_use_core_socket(self):
        settings = self.product_settings({'schemaVersion': 1})
        (self.root / 'product.json').write_text('synthetic-secret-no-output')
        result = self.diagnose(settings + 'LINGXI_NAVIGATION_SOCKET=wrong-core-socket\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertNotIn('synthetic-secret-no-output', result.stderr)
        self.assertNotIn(str(self.root), result.stderr)

    def test_invalid_product_configuration_is_rejected_with_sanitized_error(self):
        for product in (b'{"schemaVersion":1,"secret":"synthetic-secret"',
                        b'{"schemaVersion":1,"schemaVersion":1}',
                        {'schemaVersion': True}, {'schemaVersion': 2},
                        {'schemaVersion': 1, 'navigationSocket': None},
                        {'schemaVersion': 1, 'navigationSocket': ''}):
            with self.subTest(product=product):
                result = self.diagnose(self.product_settings(product))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                self.assertNotIn('synthetic-secret', result.stderr)

    def test_partial_product_selection_is_not_reported_as_disabled(self):
        for settings in ('LINGXI_PRODUCT_CONFIG=/private/product.json\n',
                         'LINGXI_PRODUCT_CONFIG_SHA256=' + 'a' * 64 + '\n'):
            result = self.diagnose(settings)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
