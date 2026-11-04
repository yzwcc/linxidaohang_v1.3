"""Synthetic wheel packaging only; never import/install a wheel or contact a robot."""
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import python_runtime_kit as kit


class KitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.wheels = self.root / 'wheels'
        self.wheels.mkdir()
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as wheel:
            wheel.writestr('synthetic.dist-info/LICENSE', 'Synthetic packaging fixture, never installed.')
        self.payload = data.getvalue()
        self.lock = {'platform': 'linux-aarch64-cp310', 'packages': [dict(name='synthetic', version='1',
            filename='synthetic.whl', bytes=len(self.payload), sha256=kit.digest(self.payload))]}
        (self.wheels / 'synthetic.whl').write_bytes(self.payload)
        (self.wheels / 'requirements.txt').write_text(kit.runtime.requirements(self.lock), encoding='utf-8')
        lock = self.root / 'lock.json'
        lock.write_text(json.dumps(self.lock), encoding='utf-8')
        for item in (patch.object(kit.runtime, 'load_lock', return_value=self.lock),
                     patch.object(kit.runtime, 'LOCK', lock)):
            item.start()
            self.addCleanup(item.stop)
        self.output = self.root / 'kit.tar.gz'

    def test_deterministic_complete_archive_keeps_install_source_and_licenses_without_execution(self):
        with patch.object(kit.runtime.subprocess, 'run') as run:
            result = kit.build(self.wheels, self.output)
        run.assert_not_called()
        other = self.root / 'second.tar.gz'
        kit.build(self.wheels, other)
        self.assertEqual(self.output.read_bytes(), other.read_bytes())
        self.assertEqual(7, result['verifiedFiles'])
        self.assertFalse(result['targetExecuted'])
        self.assertFalse(result['runtimeCompatibilityAccepted'])
        self.assertIn('synthetic.dist-info/LICENSE', result['wheelLicenses']['synthetic'])
        with tarfile.open(self.output) as archive:
            actual = archive.extractfile(kit.PREFIX + '/tools/arm64_python_runtime.py').read()
        self.assertEqual(kit.INSTALLER.read_bytes(), actual)

    def test_changed_wheel_is_rejected_before_archive_creation(self):
        (self.wheels / 'synthetic.whl').write_bytes(b'changed input')
        with self.assertRaises(ValueError):
            kit.build(self.wheels, self.output)
        self.assertFalse(self.output.exists())

    def test_existing_archive_is_preserved(self):
        self.output.write_bytes(b'preserve')
        with self.assertRaisesRegex(ValueError, 'new_direct_dependency_kit'):
            kit.build(self.wheels, self.output)
        self.assertEqual(b'preserve', self.output.read_bytes())

    def test_verification_requires_expected_whole_archive_identity(self):
        kit.build(self.wheels, self.output)
        with self.assertRaisesRegex(ValueError, 'archive_digest_mismatch'):
            kit.verify(self.wheels, self.output, '0' * 64)

    def test_existing_completion_sidecar_is_preserved_before_output_creation(self):
        sidecar = Path(str(self.output) + '.verification.json')
        sidecar.write_bytes(b'preserve existing report')
        with self.assertRaisesRegex(ValueError, 'new_direct_dependency_kit'):
            kit.build(self.wheels, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(b'preserve existing report', sidecar.read_bytes())

    def test_missing_license_does_not_produce_a_delivery(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as wheel:
            wheel.writestr('synthetic.dist-info/METADATA', 'Synthetic fixture')
        with self.assertRaisesRegex(ValueError, 'wheel_license_inventory_required'):
            kit.licenses(stream.getvalue())


if __name__ == '__main__':
    unittest.main()
