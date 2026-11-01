"""Offline rejection tests for compiled APK manifest and source build records."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("verify_android", Path(__file__).with_name("verify-android.py"))
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)

PRODUCT = {"version": "0.1.19", "androidVersionCode": 20}
MANIFEST = '''A: package="com.agibot.lingxinext"
A: android:versionName(0x0101021c)="0.1.19"
A: android:versionCode(0x0101021b)=20
A: android:allowBackup(0x01010280)=false
A: android:usesCleartextTraffic(0x010104ec)=false
A: android:screenOrientation(0x0101001e)=6
'''


class AndroidVerificationTests(unittest.TestCase):
    def test_compiled_manifest_accepts_current_decimal_and_hex_codes(self):
        verifier.check_manifest(MANIFEST, PRODUCT)
        verifier.check_manifest(MANIFEST.replace("=20\n", "=0x14\n"), PRODUCT)

    def test_stale_manifest_version_is_rejected(self):
        for before, after in [('="0.1.19"', '="0.1.14"'), ("=20\n", "=15\n")]:
            with self.subTest(after=after), self.assertRaises(ValueError):
                verifier.check_manifest(MANIFEST.replace(before, after), PRODUCT)

    def test_security_and_orientation_regressions_are_rejected(self):
        changes = [('="com.agibot.lingxinext"', '="other.app"'),
                   ("=6\n", "=1\n"), ("=false", "=true")]
        for before, after in changes:
            with self.subTest(after=after), self.assertRaises(ValueError):
                verifier.check_manifest(MANIFEST.replace(before, after), PRODUCT)
        with self.assertRaises(ValueError):
            verifier.check_manifest(MANIFEST + "A: android:debuggable(0x0101000f)=true\n", PRODUCT)

    def test_build_records_require_exact_current_inputs(self):
        current = {"webIdentity": {"source": "current"}, "webAssets": ["index"], "nativeSource": "native"}
        record = {"schema": 1, "classification": "commissioning", "productionAccepted": False, **current}
        verifier.check_build_record(record, current)
        for field, value in [("webIdentity", {}), ("webAssets", []), ("nativeSource", "old"),
                             ("classification", "production"), ("productionAccepted", True), ("schema", 2)]:
            stale = copy.deepcopy(record)
            stale[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                verifier.check_build_record(stale, current)


if __name__ == "__main__":
    unittest.main()
