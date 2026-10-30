"""Launcher admission: immutable binaries, private TLS, outputs and environment."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam"), str(ROOT / "tools")]
AVAILABLE = sys.platform == "linux" and importlib.util.find_spec("numpy") is not None
if AVAILABLE:
    import managed_localization as runtime


@unittest.skipUnless(AVAILABLE, "requires ordinary-user Linux localization environment")
class LauncherAdmission(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="lx-lu-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.key, self.cert = self.root / "key", self.root / "cert"
        for file in (self.key, self.cert):
            file.write_bytes(b"synthetic-launcher-admission-only")
            file.chmod(0o600)
        self.environment = {
            "LINGXI_TLS_KEY": str(self.key), "LINGXI_TLS_CERT": str(self.cert),
            "LINGXI_PAIRING_CODE_SHA256": "a" * 64,
        }

    def test_environment_is_explicit_and_ignores_execution_overrides(self):
        with patch.dict(os.environ, {**self.environment, "NODE_OPTIONS": "--inspect",
                "LD_PRELOAD": "/never/load.so", "LINGXI_DEMO_MODE": "1",
                "LINGXI_MAPPING_SOCKET": "/never/mapping.sock",
                "LINGXI_LOCALIZATION_SOCKET": "/never/localization.sock"}, clear=True):
            api, files = runtime.api_configuration()
        self.assertEqual(set(api), set(self.environment) | {"PATH", "LANG"})
        self.assertEqual(set(files), {self.key, self.cert})
        self.assertEqual(files[self.key], runtime.digest(self.key.read_bytes()))

    def test_missing_tls_and_invalid_pairing_are_rejected(self):
        for field, value in (("LINGXI_TLS_KEY", ""), ("LINGXI_TLS_CERT", ""),
                ("LINGXI_PAIRING_CODE_SHA256", "short"), ("LINGXI_PAIRING_CODE_SHA256", "A" * 64)):
            with self.subTest(field=field, value=value), patch.dict(
                    os.environ, {**self.environment, field: value}, clear=True):
                with self.assertRaisesRegex(ValueError, "explicit_production_tls_and_pairing"):
                    runtime.api_configuration()

    def test_tls_requires_direct_bounded_files_and_private_key(self):
        link = self.root / "link"
        link.symlink_to(self.key)
        with patch.dict(os.environ, {**self.environment, "LINGXI_TLS_KEY": str(link)}, clear=True):
            with self.assertRaisesRegex(ValueError, "direct_absolute"):
                runtime.api_configuration()
        self.key.chmod(0o644)
        with patch.dict(os.environ, self.environment, clear=True):
            with self.assertRaisesRegex(ValueError, "private_owned"):
                runtime.api_configuration()
        self.key.chmod(0o600)
        self.cert.write_bytes(b"")
        with patch.dict(os.environ, self.environment, clear=True):
            with self.assertRaisesRegex(ValueError, "bounded_localization_tls"):
                runtime.api_configuration()

    def test_output_cannot_cover_runtime_map_or_tls_inputs(self):
        config = {"bundle": str(self.root / "bundle")}
        for output in (self.root, self.root / "bundle" / "nested"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                runtime.validate_output(output, config, (self.key, self.cert))
        runtime.validate_output(self.root / "new", config, (self.key, self.cert))

    def test_binary_hash_failure_prevents_owner_creation(self):
        saved = self.root / "maps" / "map"
        saved.mkdir(parents=True)
        node, bundle, config = [self.root / name for name in ("node", "bundle.mjs", "config.json")]
        for file in (node, bundle, config):
            file.write_bytes(b"synthetic")
        args = SimpleNamespace(seconds=5., config=config, node=node, api_bundle=bundle,
            node_sha256="0" * 64, api_sha256="0" * 64, saved_map=saved,
            output=self.root / "new")
        with patch.object(runtime, "verified_configuration", return_value=({}, {})), patch.object(
                runtime, "MappingPlatform"), patch.object(runtime, "ManagedLocalizationOwner") as owner:
            with self.assertRaises(ValueError):
                runtime.run(args)
            owner.assert_not_called()
        self.assertFalse(args.output.exists())

    def test_changed_tls_is_rejected_by_final_input_verifier(self):
        saved = self.root / "maps" / "map"
        saved.mkdir(parents=True)
        config, node, bundle, profile = [self.root / name for name in (
            "config.json", "node", "bundle.mjs", "private-profile.json")]
        for file in (config, node, bundle, profile):
            file.write_bytes(b"synthetic")
        args = SimpleNamespace(seconds=5., config=config, node=node, api_bundle=bundle,
            node_sha256=runtime.digest(node.read_bytes()), api_sha256=runtime.digest(bundle.read_bytes()),
            saved_map=saved, output=self.root / "new", map_id="synthetic",
            receipt_sha256="b" * 64, profile_sha256="c" * 64)
        platform = Mock(args=SimpleNamespace(bundle=self.root), frozen={"journal": {"records": 1}})
        platform.verify.return_value = platform.frozen
        owner = Mock()
        owner.native.session = "d" * 32
        identity = Mock()

        def execute(*_, **kwargs):
            self.key.write_bytes(b"changed-synthetic-input")
            with self.assertRaisesRegex(ValueError, "unchanged_localization_tls_files"):
                kwargs["verify_inputs"]()
            return {"result": "failed"}

        owner.run.side_effect = execute
        with patch.dict(os.environ, self.environment, clear=True), patch.object(
                runtime, "verified_configuration", return_value=({}, {})), patch.object(
                runtime, "MappingPlatform", return_value=platform), patch.object(
                runtime, "load_map", return_value=(identity, None)), patch.object(
                runtime, "ManagedLocalizationOwner", return_value=owner):
            self.assertEqual(1, runtime.run(args))
        self.assertFalse(args.output.exists())


if __name__ == "__main__":
    unittest.main()
