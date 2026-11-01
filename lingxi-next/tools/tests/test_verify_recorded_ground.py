"""Repeated declared ground evidence stays offline and bound to current sources."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import verify_recorded_ground as verifier


class GroundEvidenceTests(unittest.TestCase):
    def report(self):
        return dict.fromkeys(verifier.SUMMARY_KEYS, 'fixture') | dict(
            **dict.fromkeys(verifier.FALSE_FLAGS, False), offlineOnly=True,
            groundFrameValidated=False, obstacleCompletenessValidated=False,
            ground=dict(durationSeconds=.1, candidateSha256='a' * 64, navigationReady=False))

    def test_repeat_identity_ignores_timing_but_not_ground_or_return_accounting(self):
        first = self.report()
        second = copy.deepcopy(first)
        second['ground']['durationSeconds'] = 3.
        second['workerDurationSeconds'] = 6.
        self.assertEqual(verifier.stable_summary(first), verifier.stable_summary(second))
        for key in ('candidateSha256', 'declarationSha256', 'traversabilityCells'):
            changed = copy.deepcopy(second)
            changed['ground'][key] = 'changed'
            self.assertNotEqual(verifier.stable_summary(first), verifier.stable_summary(changed))
        second['selectedReturns'] = 0
        self.assertNotEqual(verifier.stable_summary(first), verifier.stable_summary(second))

    def test_any_acceptance_or_contact_flag_forbids_export(self):
        for key in verifier.FALSE_FLAGS + ('groundFrameValidated', 'obstacleCompletenessValidated'):
            for value in (True, None, 0):
                report = self.report()
                report[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    verifier.stable_summary(report)

    def test_native_receipt_rejects_skips_stale_binary_build_and_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library, build = root / 'native.so', root / 'build.json'
            library.write_bytes(b'not-loaded-offline-unit-fixture')
            build.write_bytes(b'{}')
            source = 'robot-navigation/native-ground/ground_bridge.cpp'
            receipt = dict(result='native_synthetic_ground_fusion_passed',
                nativeSha256=verifier.digest(library), groundBuildReportSha256=verifier.digest(build),
                compiledAdapterSha256=verifier.digest(verifier.ROOT / source), testsRun=21,
                failures=0, errors=0, skipped=0, sourcesSha256={source: verifier.digest(verifier.ROOT / source)},
                **dict.fromkeys(verifier.FALSE_FLAGS, False))
            with patch.object(verifier, 'verified_build') as verify:
                verifier.native_receipt(receipt, library, verifier.digest(library), build)
                verify.assert_called_once()
            for key, value in (('skipped', 1), ('failures', True), ('testsRun', 0),
                    ('navigationReady', True), ('groundBuildReportSha256', 'b' * 64),
                    ('nativeSha256', 'b' * 64), ('sourcesSha256', {}),
                    ('sourcesSha256', {source: 'b' * 64})):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    verifier.native_receipt(receipt | {key: value}, library, verifier.digest(library), build)


if __name__ == '__main__':
    unittest.main()
