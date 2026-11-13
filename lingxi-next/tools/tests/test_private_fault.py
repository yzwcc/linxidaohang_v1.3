"""Fault records preserve locations without copying exception payloads."""
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
from lingxi_navigation.runtime_fault import record_fault


class PrivateFaultTests(unittest.TestCase):
    def test_fixed_error_code_location_and_elapsed_are_available(self):
        output = io.StringIO()
        with redirect_stderr(output):
            try:
                raise TimeoutError('terrain_process_deadline_exceeded')
            except TimeoutError as error:
                record_fault('terrain_process_exchange', error, started_at=time.monotonic() - .02,
                             code='terrain_process_update_failed')
        value = json.loads(output.getvalue())
        self.assertEqual('terrain_process_update_failed', value['code'])
        self.assertEqual('TimeoutError', value['exception'])
        self.assertGreaterEqual(value['elapsedMs'], 20.)
        self.assertEqual(Path(__file__).name, value['frames'][-1]['file'])
        self.assertEqual('test_fixed_error_code_location_and_elapsed_are_available', value['frames'][-1]['function'])

    def test_exception_payload_is_not_copied(self):
        output = io.StringIO()
        secret = 'token=do-not-log point cloud 1,2,3\nINJECTED'
        with redirect_stderr(output):
            try:
                raise ValueError(secret)
            except ValueError as error:
                record_fault('registered_terrain_input', error)
        value = json.loads(output.getvalue())
        self.assertNotIn('code', value)
        for forbidden in (secret, 'INJECTED', 'do-not-log', str(ROOT), 'locals', 'source'):
            self.assertNotIn(forbidden, output.getvalue())
        self.assertEqual(1, len(output.getvalue().splitlines()))

    def test_simple_lowercase_secret_is_not_mistaken_for_error_code(self):
        output = io.StringIO()
        with redirect_stderr(output):
            record_fault('registered_terrain_input', ValueError('credentials'))
        self.assertNotIn('credentials', output.getvalue())
        self.assertNotIn('code', json.loads(output.getvalue()))

    def test_exception_str_is_never_called_and_frame_count_is_bounded(self):
        class UnprintableError(Exception):
            def __str__(self):
                raise AssertionError('exception_stringification_forbidden')
        def recurse(depth):
            if depth:
                recurse(depth - 1)
            else:
                raise UnprintableError()
        output = io.StringIO()
        with redirect_stderr(output):
            try:
                recurse(40)
            except UnprintableError as error:
                record_fault('terrain_lane_current', error)
        value = json.loads(output.getvalue())
        self.assertEqual(12, len(value['frames']))
        self.assertEqual('UnprintableError', value['exception'])

    def test_broken_stderr_does_not_replace_original_failure_or_cleanup(self):
        class BrokenStream:
            def write(self, data):
                raise OSError('full')
        with patch('sys.stderr', BrokenStream()):
            record_fault('terrain_lane_cleanup', RuntimeError('original_failure'))

    def test_invalid_identifiers_are_omitted(self):
        output = io.StringIO()
        with redirect_stderr(output):
            record_fault('invalid\nphase')
            record_fault('acquisition_health', code='payload\nINJECTED')
        self.assertEqual(dict(event='lingxi_private_fault', phase='acquisition_health'), json.loads(output.getvalue()))


if __name__ == '__main__':
    unittest.main()
