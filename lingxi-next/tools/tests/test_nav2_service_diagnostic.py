"""Synthetic diagnostics only; no middleware, sensors or robot transport."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nav2_service_diagnostic as diagnostic

SERVICE = '/lingxi_offline_' + 'a' * 32 + '/controller_server/change_state'


def pair(code=0, *, matched=True, present=2):
    common = dict(schema=1, pid=20, steadySeconds=4., service=SERVICE, sequence=1)
    request = dict(common, operation='request', declaredReader='01.02|00.04', actualReader='01.02|00.04',
        declaredWriter='01.02|00.03', actualWriter='01.02|00.03', matchedRequestCount=1, matchedResponseCount=1)
    observation = dict(matchedListAvailable=True, matchedReaderCount=int(matched), relatedReaderMatched=matched,
                       rmwReaderPresence=present, matchedReaders=['01.02|00.04'] if matched else [])
    response = dict(common, operation='response', relatedReader='01.02|00.04', responseWriter='03.04|00.03',
                    returnCode=code, before=copy.deepcopy(observation), after=observation)
    return [request, response]


class ServiceDiagnosticTests(unittest.TestCase):
    def test_passing_pairs_do_not_establish_a_root_cause(self):
        summary = diagnostic.summarize(pair())
        self.assertEqual(1, summary['requestCount'])
        self.assertEqual(1, summary['responseCount'])
        self.assertEqual(0, summary['unobservedResponseCount'])
        self.assertEqual([], summary['unsuccessfulResponses'])
        self.assertFalse(summary['rootCauseEstablished'])

    def test_failed_reply_exposes_both_sides_without_asserting_a_cause(self):
        summary = diagnostic.summarize(pair(2, matched=False, present=1))
        failure, = summary['unsuccessfulResponses']
        self.assertTrue(failure['clientReaderIdentityMatches'])
        self.assertTrue(failure['clientWriterIdentityMatches'])
        self.assertFalse(failure['serverRelatedReaderMatched'])
        self.assertEqual(1, failure['serverRmwReaderPresence'])
        self.assertEqual('MAYBE', failure['serverRmwReaderPresenceName'])
        self.assertTrue(summary['failureMechanismEstablished'])
        self.assertFalse(summary['rootCauseEstablished'])

    def test_other_failures_do_not_claim_the_observed_discovery_mechanism(self):
        summary = diagnostic.summarize(pair(3, matched=False, present=1))
        self.assertFalse(summary['failureMechanismEstablished'])

    def test_unknown_server_reader_presence_is_rejected(self):
        records = pair(2, present=4)
        with self.assertRaisesRegex(ValueError, 'known_server_reader_presence'):
            diagnostic.summarize(records)

    def test_mismatched_reader_identity_and_unmatched_reply_remain_visible(self):
        records = pair(2, matched=False, present=1)
        records[1]['relatedReader'] = 'ff.00|01.04'
        failure, = diagnostic.summarize(records)['unsuccessfulResponses']
        self.assertFalse(failure['clientReaderIdentityMatches'])
        self.assertFalse(failure['serverRelatedReaderMatched'])

    def test_unobserved_responses_are_counted_not_assumed_delivered(self):
        records = pair()
        extra = dict(records[0], sequence=2)
        summary = diagnostic.summarize([*records, extra])
        self.assertEqual(1, summary['unobservedResponseCount'])

    def test_missing_correlations_and_duplicate_records_are_rejected(self):
        request, response = pair()
        for records in ([], [request], [response], [request, request, response],
                        [request, response, response], [request, dict(response, sequence=2)]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                diagnostic.summarize(records)

    def test_launch_prefix_is_parsed_and_unrelated_log_lines_are_ignored(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'nav2.log'
            expected = pair()
            path.write_text('ordinary ROS log\n' + '\n'.join(
                '[controller_server-2] ' + diagnostic.MARKER + json.dumps(record) for record in expected),
                encoding='utf-8')
            self.assertEqual(expected, diagnostic.records_from_log(path))

    def test_malformed_or_unbounded_diagnostic_logs_are_rejected(self):
        invalid = [dict(pair()[0], sequence=True), dict(pair()[0], service='/vendor/change_state'),
                   dict(pair()[0], operation='motion'), dict(pair()[0], schema=2)]
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'nav2.log'
            for record in invalid:
                with self.subTest(record=record), self.assertRaises(ValueError):
                    path.write_text(diagnostic.MARKER + json.dumps(record), encoding='utf-8')
                    diagnostic.records_from_log(path)
            path.write_text('x' * (2 * 1024 * 1024 + 1), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'bounded_diagnostic_log'):
                diagnostic.records_from_log(path)

    def environment(self, *, machine='x86_64', interfaces=('lo',), versions=None):
        expected = dict(ROS_DISTRO='humble', ROS_DOMAIN_ID='174', ROS_LOCALHOST_ONLY='1',
                        RMW_IMPLEMENTATION='rmw_fastrtps_cpp')
        values = iter(versions or [version + '-1jammy' for version in diagnostic.PACKAGES.values()])
        patches = [patch.dict(os.environ, expected, clear=True), patch.object(diagnostic.platform, 'system', return_value='Linux'),
            patch.object(diagnostic.platform, 'machine', return_value=machine),
            patch.object(diagnostic.os, 'listdir', return_value=list(interfaces)),
            patch.object(diagnostic.subprocess, 'check_output', side_effect=lambda *args, **kwargs: next(values))]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_exact_separate_host_and_abi_are_required(self):
        self.environment()
        self.assertEqual(set(diagnostic.PACKAGES), set(diagnostic.environment()))
        for machine, interfaces in (('aarch64', ('lo',)), ('x86_64', ('lo', 'eth0'))):
            with self.subTest(machine=machine, interfaces=interfaces):
                self.environment(machine=machine, interfaces=interfaces)
                with self.assertRaisesRegex(ValueError, 'separate_loopback'):
                    diagnostic.environment()

    def test_different_middleware_abi_is_rejected_before_native_code(self):
        self.environment(versions=['6.2.9-1jammy'])
        with self.assertRaisesRegex(ValueError, 'exact_diagnostic_abi'):
            diagnostic.environment()

    def test_compiler_failure_is_not_treated_as_a_runtime_trial(self):
        with tempfile.TemporaryDirectory() as raw, patch.object(diagnostic, 'environment', return_value={}), \
                patch.object(diagnostic.subprocess, 'run', side_effect=RuntimeError('compiler_failed')):
            with self.assertRaisesRegex(RuntimeError, 'compiler_failed'):
                diagnostic.build(Path(raw))
            self.assertFalse((Path(raw) / 'report.json').exists())
            self.assertFalse((Path(raw) / 'native-build/liblingxi_service_diagnostic.so').exists())

    def failed_run(self, *, retirement_failed=False):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            library = root / 'native-build/liblingxi_service_diagnostic.so'
            library.parent.mkdir()
            library.write_bytes(b'synthetic-not-a-loadable-library')
            idle = dict(ticket=None, phase='idle', requiresStop=False, outputQuiescent=True)
            failed = dict(ticket=dict(epoch='a' * 32, task_id='diagnostic-0'), phase='failed',
                detail='navigation_inputs_expired', requiresStop=True, outputQuiescent=True)
            retired = failed if retirement_failed else idle
            service = NS(session='synthetic', identity=None, runtime=NS(startup_timeout=5.),
                request=lambda value: None, state=lambda: retired, close=lambda: None)
            ros = NS(init=lambda **kwargs: None, shutdown=lambda: None,
                create_node=lambda *args, **kwargs: NS(destroy_node=lambda: None))
            modules = {'rclpy': ros, 'offline_nav2_service': NS(Service=lambda *args: service)}
            with patch.object(diagnostic, 'environment', return_value={}), \
                    patch.object(diagnostic, 'asdict', return_value={}), \
                    patch.object(diagnostic, 'wait', side_effect=[failed, retired]), \
                    patch.dict(sys.modules, modules), patch.dict(os.environ, {
                        'LD_PRELOAD': str(library),
                        'LINGXI_NAV2_SERVICE_DIAGNOSTIC': 'isolated_humble_fastdds_2_6_12'}):
                return diagnostic.run(root)

    def test_failed_generation_and_final_retirement_remain_explicit_in_report(self):
        report = self.failed_run()
        self.assertEqual('RuntimeError:diagnostic_start_failed:navigation_inputs_expired', report['error'])
        self.assertEqual('failed', report['generations'][0]['state']['phase'])
        self.assertEqual('idle', report['retirements'][0]['state']['phase'])
        self.assertEqual('idle', report['finalState']['phase'])

    def test_failed_retirement_is_not_hidden_by_a_quiescent_flag(self):
        report = self.failed_run(retirement_failed=True)
        self.assertEqual('RuntimeError:diagnostic_generation_retirement_failed', report['error'])
        self.assertEqual('failed', report['retirements'][0]['state']['phase'])


if __name__ == '__main__':
    unittest.main()
