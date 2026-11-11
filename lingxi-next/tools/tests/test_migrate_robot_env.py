import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / 'deploy' / 'migrate_robot_env.py'


class RobotEnvironmentMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='lx-env-migration-')
        self.addCleanup(self.temporary.cleanup)
        self.config = Path(self.temporary.name) / 'robot.env'

    def migrate(self, content):
        self.config.write_text(content, encoding='utf-8')
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.config)],
                                capture_output=True, text=True, timeout=5)
        return result

    def test_old_internal_port_and_matching_url_move_to_3445(self):
        result = self.migrate('LINGXI_PORT=3000\nLINGXI_API_URL=https://127.0.0.1:3000\n')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({'changed': True, 'updatedKeys': ['LINGXI_PORT', 'LINGXI_API_URL']},
                         json.loads(result.stdout))
        self.assertEqual('LINGXI_PORT=3445\nLINGXI_API_URL=https://127.0.0.1:3445\n',
                         self.config.read_text())

    def test_legacy_api_url_without_port_setting_is_migrated(self):
        for legacy_port in ('3000', '3444'):
            with self.subTest(legacy_port=legacy_port):
                result = self.migrate(f'# preserved\nLINGXI_API_URL=https://127.0.0.1:{legacy_port}\n')
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual({'changed': True, 'updatedKeys': ['LINGXI_PORT', 'LINGXI_API_URL']},
                                 json.loads(result.stdout))
                self.assertEqual('# preserved\nLINGXI_API_URL=https://127.0.0.1:3445\nLINGXI_PORT=3445\n',
                                 self.config.read_text())

    def test_custom_port_and_non_legacy_configuration_are_left_untouched(self):
        content = 'LINGXI_PORT=3450\nLINGXI_API_URL=https://127.0.0.1:3450\n'
        result = self.migrate(content)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({'changed': False, 'updatedKeys': []}, json.loads(result.stdout))
        self.assertEqual(content, self.config.read_text())

    def test_no_legacy_url_does_not_rewrite_a_missing_port(self):
        content = '# operator-managed values\nLINGXI_API_URL=https://127.0.0.1:3450\n'
        result = self.migrate(content)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({'changed': False, 'updatedKeys': []}, json.loads(result.stdout))
        self.assertEqual(content, self.config.read_text())

    def test_duplicate_or_invalid_port_fails_without_modifying_file(self):
        for content in ('LINGXI_PORT=3000\nLINGXI_PORT=3445\n', 'LINGXI_PORT=0\n'):
            with self.subTest(content=content):
                result = self.migrate(content)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(content, self.config.read_text())
                self.assertNotIn(content.strip(), result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
