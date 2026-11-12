"""Synthetic materialization contracts; cached real ARM64 receipts are checked separately."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import prepare_installed_mapping_inputs as inputs


@unittest.skipUnless(sys.platform == "linux", "private POSIX native-input directories")
class NativeInputPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lingxi-input-test-")
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name).resolve()
        self.source = self.parent / "source"
        self.source.mkdir(mode=0o700)
        self.value = {"platform": "arm64-humble", "inputMode": "live", "maxSeconds": 60,
                      "hypothesis": "header_is_scan_end", "pointManifestSha256": "a" * 64,
                      "basePose": {"metresPerMapUnit": .97, "navigationFromRaw": [
                          [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]}}
        for name, (directory, manifest) in inputs.COMPONENTS.items():
            root = self.source / directory
            (root / "lib").mkdir(mode=0o700, parents=True)
            library = root / "lib/synthetic.so"
            library.write_bytes(b"synthetic receipt bytes, not an ELF")
            library.chmod(0o600)
            self.write(root / manifest, {"role": name, "files": {
                "lib/synthetic.so": {"bytes": library.stat().st_size, "sha256": inputs.sha256(library)}}})
            if name == "fullMapping":
                self.value[name] = {"root": str(root), "manifestSha256": inputs.sha256(root / manifest),
                                    "captureObservations": True}
            else:
                self.value[name] = str(root)
        self.config = self.parent / "original.json"
        self.write(self.config, self.value)
        self.config_hash = inputs.sha256(self.config)
        self.output = self.parent / "version-1"
        self.checker = patch.object(inputs, "inspect_configuration", side_effect=self.inspect)
        self.checker.start()
        self.addCleanup(self.checker.stop)

    def write(self, path, value):
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def inspect(self, config):
        value = inputs.read_json(config)
        manifests = {}
        for name, root in inputs.roots(value).items():
            manifest = inputs.read_json(root / inputs.COMPONENTS[name][1])
            if manifest["role"] != name:
                raise ValueError("synthetic role mismatch")
            manifests[name] = manifest
        return value, manifests, "b" * 64

    def prepare(self):
        return inputs.prepare(self.config, self.config_hash, self.output)

    def test_exact_receipts_and_nonpath_configuration_survive_materialization(self):
        original = self.config.read_bytes()
        identity = self.prepare()
        report = inputs.verify(self.output, identity)
        value = inputs.read_json(self.output / "mapping.json")
        self.assertEqual(inputs.rewritten(self.value, self.output), value)
        self.assertEqual(original, (self.output / "original-config.json").read_bytes())
        self.assertEqual(original, self.config.read_bytes())
        self.assertEqual(self.value["basePose"], value["basePose"])
        for name, (directory, manifest) in inputs.COMPONENTS.items():
            self.assertEqual((inputs.roots(self.value)[name] / manifest).read_bytes(),
                             (self.output / directory / manifest).read_bytes())
        self.assertTrue(all(value is False for value in report["boundaries"].values()))
        self.assertEqual(12, len(report["files"]))
        self.assertEqual(0o700, self.output.stat().st_mode & 0o777)
        self.source.rename(self.parent / "archived-original-sources")
        inputs.verify(self.output, identity)

    def test_wrong_pin_rejected_before_inspection_or_output(self):
        with patch.object(inputs, "inspect_configuration") as inspect, self.assertRaises(ValueError):
            inputs.prepare(self.config, "0" * 64, self.output)
        inspect.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_existing_output_and_overlapping_inputs_are_retained(self):
        self.output.mkdir(mode=0o700)
        marker = self.output / "preserve"
        marker.write_text("existing user data")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual("existing user data", marker.read_text())
        with self.assertRaises(ValueError):
            inputs.prepare(self.config, self.config_hash, Path(self.value["root"]) / "new-output")

    def test_failed_copy_retains_partial_without_completion_manifest(self):
        with patch.object(inputs.shutil, "copyfile", side_effect=OSError("synthetic copy failure")), \
                self.assertRaises(OSError):
            self.prepare()
        self.assertTrue(self.output.exists())
        self.assertFalse((self.output / inputs.MANIFEST).exists())
        self.assertEqual(self.config_hash, inputs.sha256(self.config))

    def test_failed_destination_validation_does_not_publish_receipt(self):
        def inspect(path):
            if path == self.config:
                return self.inspect(path)
            raise ValueError("synthetic relocated verification failure")
        with patch.object(inputs, "inspect_configuration", side_effect=inspect), self.assertRaises(ValueError):
            self.prepare()
        self.assertTrue((self.output / "mapping.json").exists())
        self.assertFalse((self.output / inputs.MANIFEST).exists())

    def test_native_member_mutation_and_unknown_or_missing_files_rejected(self):
        identity = self.prepare()
        library = self.output / "full/lib/synthetic.so"
        original = library.read_bytes()
        library.write_bytes(b"changed native bytes")
        with self.assertRaises(ValueError):
            inputs.verify(self.output, identity)
        library.write_bytes(original)
        extra = self.output / "extra"
        extra.mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            inputs.verify(self.output, identity)
        extra.rmdir()
        library.unlink()
        with self.assertRaises(ValueError):
            inputs.verify(self.output, identity)

    def test_only_wrapper_paths_may_change_not_scale_or_approval(self):
        identity = self.prepare()
        receipt = self.output / inputs.MANIFEST
        report = inputs.read_json(receipt)
        report["boundaries"]["navigationReady"] = True
        self.write(receipt, report)
        with self.assertRaises(ValueError):
            inputs.verify(self.output, inputs.sha256(receipt))
        report["boundaries"]["navigationReady"] = False
        config = self.output / "mapping.json"
        value = inputs.read_json(config)
        value["basePose"]["metresPerMapUnit"] = 1.
        self.write(config, value)
        report["configSha256"] = inputs.sha256(config)
        report["files"]["mapping.json"] = inputs.fingerprint(config)
        self.write(receipt, report)
        with self.assertRaisesRegex(ValueError, "only_explicit_native_input_paths"):
            inputs.verify(self.output, inputs.sha256(receipt))

    def test_symlinks_and_nonprivate_directories_rejected(self):
        alias = self.parent / "alias-config.json"
        alias.symlink_to(self.config)
        with self.assertRaises(ValueError):
            inputs.prepare(alias, self.config_hash, self.output)
        self.parent.chmod(0o755)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_recordings_are_not_part_of_native_input_closure(self):
        _, manifests, _ = self.inspect(self.config)
        root = Path(self.value["root"])
        recording = root / "scan.db3"
        recording.write_bytes(b"synthetic recording, not sensor data")
        manifests["root"]["files"]["scan.db3"] = {"bytes": recording.stat().st_size,
                                                     "sha256": inputs.sha256(recording)}
        with self.assertRaises(ValueError):
            inputs.members(self.value, manifests)

    def test_published_file_only_summary_binds_historical_helpers_and_no_acceptance(self):
        import audit_historical_evidence as historical
        report = inputs.read_json(ROOT / 'docs/installed-native-inputs-offline-evidence.json')
        self.assertEqual(142, report['preparedFileCount'])
        self.assertTrue(report['freshProcessReverificationPassed'])
        self.assertTrue(report['originalComponentReceiptBytesPreserved'])
        self.assertTrue(report['mappingLibraryLoadsForbiddenDuringReverification'])
        self.assertTrue(report['childCreationForbiddenDuringReverification'])
        self.assertEqual(inputs.BOUNDARIES, report['boundaries'])
        self.assertTrue(all(value is False for value in report['boundaries'].values()))
        verified = historical.verify('docs/installed-native-inputs-offline-evidence.json')
        self.assertTrue(verified['historicalSourcesVerified'])
        # The November 4 receipt must not be rewritten when the launcher changes.
        self.assertEqual(report['sourceSha256']['tools/prepare_installed_mapping_inputs.py'],
                         inputs.sha256(ROOT / 'tools/prepare_installed_mapping_inputs.py'))
        self.assertTrue(inputs.digest(report['preparedManifestSha256']))
        self.assertTrue(inputs.digest(report['observedMapperManifestSha256']))


@unittest.skipUnless(os.environ.get('LINGXI_INSTALLED_NATIVE_INPUTS_ROOT')
                     and os.environ.get('LINGXI_INSTALLED_NATIVE_INPUTS_SHA256'),
                     'requires explicitly selected private native-input preparation')
class RealNativeInputPreparationTests(unittest.TestCase):
    def test_private_real_receipts_verify_without_loading_mapping_libraries(self):
        root = Path(os.environ['LINGXI_INSTALLED_NATIVE_INPUTS_ROOT'])
        identity = os.environ['LINGXI_INSTALLED_NATIVE_INPUTS_SHA256']
        with patch('ctypes.CDLL', side_effect=AssertionError('mapping library load forbidden')), \
                patch('subprocess.Popen', side_effect=AssertionError('child launch forbidden')):
            report = inputs.verify(root, identity)
        self.assertTrue(all(value is False for value in report['boundaries'].values()))
        self.assertEqual(identity, inputs.sha256(root / inputs.MANIFEST))


if __name__ == "__main__":
    unittest.main()
