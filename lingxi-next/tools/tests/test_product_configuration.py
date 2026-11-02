"""Explicit product sidecar admission using isolated synthetic local files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-runtime/python'))
import product_configuration as product


class ProductConfiguration(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='lx-product-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.state, self.maps = self.root / 'state', self.root / 'maps'
        self.state.mkdir(mode=0o700)
        self.maps.mkdir(mode=0o700)
        self.token = self.root / 'agent-token'
        self.token.write_text('synthetic-agent-token-not-a-real-secret\n')
        self.token.chmod(0o600)
        self.file = self.root / 'product.json'
        self.args = argparse.Namespace(product_config=self.file, product_config_sha256=None,
            config=self.root / 'native.json', node=self.root / 'node', api_bundle=self.root / 'api.mjs',
            maps=self.maps, output=self.root / 'run')
        self.save()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {}, clear=True).start()

    def save(self, **changes):
        value = {'schemaVersion': 1, 'stateDirectory': str(self.state),
            'agentUrl': 'http://127.0.0.1:18180', 'agentTokenFile': str(self.token), **changes}
        self.file.write_text(json.dumps(value))
        self.file.chmod(0o600)
        self.args.product_config_sha256 = hashlib.sha256(self.file.read_bytes()).hexdigest()

    def test_only_explicit_selection_forwards_configuration_identity_not_token_contents(self):
        self.assertEqual({'LINGXI_PRODUCT_CONFIG': str(self.file),
            'LINGXI_PRODUCT_CONFIG_SHA256': self.args.product_config_sha256}, product.selection(self.args, {}))
        self.assertFalse(self.args.output.exists())
        self.assertEqual({}, product.selection(argparse.Namespace(), {}))

    def test_configuration_requires_both_path_and_digest(self):
        for args in (argparse.Namespace(product_config=self.file),
                argparse.Namespace(product_config_sha256='a' * 64)):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, 'paired_product'):
                product.selection(args, {})

    def test_changes_and_non_schema_fields_reject_before_runtime(self):
        self.file.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'unchanged_product'):
            product.selection(self.args, {})
        for changes in ({'apiKey': 'not-allowed'}, {'schemaVersion': True}, {'stateDirectory': 1}):
            self.save(**changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                product.selection(self.args, {})

    def test_only_explicit_loopback_agent_port_is_supported(self):
        for value in ('http://localhost:18180', 'https://example.invalid', 'http://127.0.0.1:65536',
                'http://127.0.0.1:018180', 'http://127.0.0.1:18180/v1', 'http://user@127.0.0.1:18180'):
            self.save(agentUrl=value)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'local_product_agent'):
                product.selection(self.args, {})

    def test_runtime_catalog_native_inputs_and_configuration_cannot_overlap_state(self):
        for state in (self.maps, self.root, self.args.output):
            self.save(stateDirectory=str(state))
            if not state.exists():
                state.mkdir(mode=0o700)
            with self.subTest(state=state), self.assertRaises(ValueError):
                product.selection(self.args, {})
        self.save()
        with self.assertRaises(ValueError):
            product.selection(self.args, {'bundle': str(self.state)})

    def test_bounded_token_requires_printable_single_line(self):
        for value in ('short', 'a' * 4100, 'a' * 32 + '\nextra', 'a' * 31 + ' '):
            self.token.write_text(value)
            with self.subTest(length=len(value)), self.assertRaises(ValueError):
                product.selection(self.args, {})

    def test_packaged_web_root_excludes_state_and_credentials_from_release_sources(self):
        web = self.root / 'package' / 'web'
        web.mkdir(parents=True)
        with patch.dict(os.environ, {'LINGXI_WEB_ROOT': str(web)}):
            self.assertIn('LINGXI_PRODUCT_CONFIG', product.selection(self.args, {}))
            state = web.parent / 'state'
            state.mkdir(mode=0o700)
            self.save(stateDirectory=str(state))
            with self.assertRaisesRegex(ValueError, 'separate_product'):
                product.selection(self.args, {})

    @unittest.skipUnless(os.name == 'posix', 'requires Linux private owner semantics')
    def test_public_readable_paths_and_symlinks_reject(self):
        for path in (self.file, self.state, self.token):
            path.chmod(0o755 if path.is_dir() else 0o644)
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'private_product_path'):
                product.selection(self.args, {})
            path.chmod(0o700 if path.is_dir() else 0o600)
        link = self.root / 'token-link'
        link.symlink_to(self.token)
        self.save(agentTokenFile=str(link))
        with self.assertRaises(ValueError):
            product.selection(self.args, {})


if __name__ == '__main__':
    unittest.main()
