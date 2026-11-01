"""Ground verification must not attribute an older binary to newer sources."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from check_ground_native import ROOT, digest, verified_build


class GroundBuildVerificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.library = self.root / 'liblingxi_ground.so'
        self.library.write_bytes(b'isolated-verifier-fixture-not-a-native-library')
        self.lock_path = ROOT / 'robot-navigation/patchworkpp.lock.json'
        lock = json.loads(self.lock_path.read_bytes())
        self.report = self.root / 'build-report.json'
        self.value = dict(schemaVersion=1, purpose='offline_ground_candidates_not_navigation_acceptance',
            revision=lock['revision'], license=lock['license'], lockSha256=digest(self.lock_path),
            upstreamFilesSha256=lock['files'], nativeSha256=digest(self.library),
            adapterSha256=digest(ROOT / 'robot-navigation/native-ground/ground_bridge.cpp'),
            freshProcessLoadPassed=True, robotContacted=False, physicalMotionSent=False, navigationReady=False)
        (self.root / 'ground_bridge.cpp').write_bytes((ROOT / 'robot-navigation/native-ground/ground_bridge.cpp').read_bytes())
        # Use hash-checked real pinned sources only in native integration, not this fixture.

    def write(self):
        self.report.write_text(json.dumps(self.value), encoding='utf-8')

    def test_rejects_source_binary_and_acceptance_drift_before_native_loading(self):
        for key, value in (('revision', '0' * 40), ('lockSha256', '0' * 64),
                ('adapterSha256', '0' * 64), ('nativeSha256', '0' * 64),
                ('upstreamFilesSha256', {}), ('freshProcessLoadPassed', False),
                ('navigationReady', True), ('robotContacted', True), ('physicalMotionSent', True)):
            original = self.value[key]
            self.value[key] = value
            self.write()
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'current_source_bound'):
                verified_build(self.library, digest(self.library), self.report)
            self.value[key] = original

    def test_rejects_changed_compiled_adapter_copy(self):
        self.write()
        (self.root / 'ground_bridge.cpp').write_bytes(b'stale-adapter')
        with self.assertRaisesRegex(ValueError, 'compiled_input_copies'):
            verified_build(self.library, digest(self.library), self.report)

    def test_requires_adjacent_direct_bounded_report(self):
        self.write()
        outside = self.root / 'other'
        outside.mkdir()
        detached = outside / 'build-report.json'
        detached.write_bytes(self.report.read_bytes())
        with self.assertRaisesRegex(ValueError, 'direct_adjacent'):
            verified_build(self.library, digest(self.library), detached)
        self.report.write_bytes(b' ' * 262145)
        with self.assertRaisesRegex(ValueError, 'direct_adjacent'):
            verified_build(self.library, digest(self.library), self.report)


if __name__ == '__main__':
    unittest.main()
