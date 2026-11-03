"""Actual ordinary-user owner/child startup, diagnostics and independent cleanup."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-runtime/python'))
from mapping_owner import failure_code


class OwnerDiagnostics(unittest.TestCase):
    def test_only_allowlisted_codes_are_reported_without_raw_exception_messages(self):
        self.assertEqual('mapping_status_closed', failure_code(RuntimeError('mapping_status_closed')))
        for error in (RuntimeError('PRIVATE_MARKER'), ValueError('mapping_status_closed: PRIVATE_MARKER'),
                      RuntimeError(['mapping_status_closed']), KeyError('PRIVATE_MARKER')):
            self.assertEqual('mapping_unexpected_error', failure_code(error))
        self.assertEqual('mapping_process_io_failed', failure_code(OSError('PRIVATE_MARKER')))
        self.assertEqual('mapping_status_json_invalid', failure_code(json.JSONDecodeError('PRIVATE_MARKER', '', 0)))

    def test_live_identity_is_independent_of_scheduler_state_and_distinguishes_pid_reuse(self):
        from test_slam_activity_lease import ActivityLeaseTests
        with patch.object(ActivityLeaseTests, 'process_identity', return_value=('R', '100')):
            self.assertTrue(ActivityLeaseTests.same_live_process(123, ('S', '100')))
        for identity in (('S', '101'), ('Z', '100'), ('X', '100')):
            with patch.object(ActivityLeaseTests, 'process_identity', return_value=identity):
                self.assertFalse(ActivityLeaseTests.same_live_process(123, ('S', '100')))


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() != 0, 'requires ordinary-user Linux owner processes')
class OwnerProcesses(unittest.TestCase):
    def run_case(self, scenario, expected, code=None, phase=None):
        with tempfile.TemporaryDirectory(prefix='lx-own-') as directory:
            output = Path(directory) / 'run'
            child = subprocess.run([sys.executable, '-B', str(ROOT / 'tools/tests/fixtures/mapping_owner_child.py'),
                'owner', scenario, str(output)], capture_output=True, timeout=8)
            self.assertEqual(expected, child.returncode, child.stderr.decode(errors='replace'))
            report_file = output / 'owner-report.json'
            report = json.loads(report_file.read_bytes())
            self.assertEqual(0o600, report_file.stat().st_mode & 0o777)
            self.assertNotIn('PRIVATE_MARKER', report_file.read_text())
            self.assertEqual('stopped' if expected == 0 else 'failed', report['result'])
            self.assertEqual(code, report['failureCode'])
            self.assertEqual(phase, report['failurePhase'])
            self.assertEqual(0, report['remainingChildren'])
            for label in ('serviceProcess', 'apiProcess'):
                if report[label] is not None:
                    self.assertFalse(report[label]['processAlive'])
                    self.assertFalse(report[label]['groupAlive'])
            return report

    def test_mapping_and_continuous_slam_owner_keep_success_report_compatible(self):
        for scenario in ('healthy', 'slam-healthy'):
            with self.subTest(scenario=scenario):
                report = self.run_case(scenario, 0)
                self.assertIsNone(report['failure'])
                self.assertEqual([], report['cleanupFailurePhases'])
                self.assertTrue(report['adoptedChildren']['cleanupComplete'])

    def test_startup_deadline_closed_status_and_protocol_errors_identify_each_peer_phase(self):
        behaviors = {'timeout': 'mapping_process_startup_deadline', 'closed': 'mapping_status_closed',
                     'json': 'mapping_status_json_invalid', 'oversize': 'bounded_mapping_status_required',
                     'wrong': 'exact_mapping_listening_status_required'}
        for peer in ('service', 'api'):
            for behavior, code in behaviors.items():
                if peer == 'api' and behavior == 'wrong':
                    code = 'mapping_api_network_port_required'
                with self.subTest(peer=peer, behavior=behavior):
                    self.run_case(peer + '-' + behavior, 1, code, peer + '-ready')
        self.run_case('api-port', 1, 'mapping_api_network_port_required', 'api-ready')

    def test_running_peer_exit_identifies_failed_peer_and_lifecycle_phase(self):
        for peer in ('service', 'api'):
            with self.subTest(peer=peer):
                self.run_case(peer + '-running-exit', 1, 'mapping_' + peer + '_process_exited', 'running')

    def test_forced_cleanup_stays_failed_without_an_operational_exception(self):
        report = self.run_case('service-stubborn', 1, 'mapping_process_cleanup_unconfirmed', 'cleanup-check')
        self.assertIsNone(report['failure'])
        self.assertTrue(report['serviceProcess']['forcedTermination'])
        self.assertFalse(report['apiProcess']['forcedTermination'])

    def test_callback_failure_is_sanitized_and_cleanup_does_not_skip_api(self):
        report = self.run_case('callback-error', 1, 'mapping_unexpected_error', 'listening-callback')
        self.assertEqual('RuntimeError', report['failure'])
        report = self.run_case('cleanup-error', 1, 'mapping_process_cleanup_failed', 'adopted-children-stop')
        self.assertEqual(['adopted-children-stop'], report['cleanupFailurePhases'])
        self.assertEqual(0, report['apiProcess']['exitCode'])
        report = self.run_case('callback-error-cleanup', 1, 'mapping_unexpected_error', 'listening-callback')
        self.assertEqual(['adopted-children-stop'], report['cleanupFailurePhases'])
        self.assertEqual(0, report['apiProcess']['exitCode'])


if __name__ == '__main__':
    unittest.main()
