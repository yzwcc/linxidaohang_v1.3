import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release_scope import (BOUND_SOURCE_MODULES, CORE_FILES, INDEPENDENT_FILES,
                           verify_bound_source_references, verify_independent_scope, verify_scope,
                           verify_staged_source_references)


class ReleaseScopeTests(unittest.TestCase):
    def setUp(self):
        self.raw = (Path(__file__).resolve().parents[2] / 'deploy/release-scope.json').read_bytes()
        self.scope = json.loads(self.raw)
        self.status = {'classification': 'commissioning', 'productionAccepted': False, 'scope': self.scope}

    def test_declares_actual_core_components_not_independent_product(self):
        self.assertEqual(self.scope, verify_scope(self.status, self.raw, CORE_FILES))

    def test_each_declared_component_is_required(self):
        for filename in CORE_FILES:
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                verify_scope(self.status, self.raw, CORE_FILES - {filename})

    def test_complete_independent_and_production_claims_rejected(self):
        for field in ('completeProduct', 'independentStackIncluded'):
            scope = copy.deepcopy(self.scope)
            scope[field] = True
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_scope({**self.status, 'scope': scope}, json.dumps(scope), CORE_FILES)
        with self.assertRaises(ValueError):
            verify_scope({**self.status, 'productionAccepted': True}, self.raw, CORE_FILES)

    def test_scope_divergence_unknown_profile_and_boolean_schema_rejected(self):
        with self.assertRaises(ValueError):
            verify_scope({**self.status, 'scope': {}}, self.raw, CORE_FILES)
        for field, value in (('profile', 'full-product'), ('schemaVersion', True),
                             ('notIncluded', []), ('requiresExternal', [])):
            scope = {**self.scope, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_scope({**self.status, 'scope': scope}, json.dumps(scope), CORE_FILES)

    def test_independent_commissioning_scope_requires_runtime_contract(self):
        scope = json.loads((Path(__file__).resolve().parents[2] / 'deploy/independent-scope.json').read_bytes())
        status = {'classification': 'commissioning', 'productionAccepted': False,
                  'independentStackIncluded': True, 'independent': scope}
        files = set(INDEPENDENT_FILES)
        self.assertEqual(scope, verify_independent_scope(status, json.dumps(scope), files))
        for filename in files:
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                verify_independent_scope(status, json.dumps(scope), files - {filename})
        with self.assertRaises(ValueError):
            verify_independent_scope({**status, 'independent': {}}, json.dumps(scope), files)
        with self.assertRaises(ValueError):
            verify_independent_scope({**status, 'productionAccepted': True}, json.dumps(scope), files)

    def test_production_nav2_startup_sources_are_mandatory(self):
        scope = json.loads((Path(__file__).resolve().parents[2] / 'deploy/independent-scope.json').read_bytes())
        status = {'classification': 'commissioning', 'productionAccepted': False,
                  'independentStackIncluded': True, 'independent': scope}
        for module in ('nav2_producer', 'nav2_action', 'lifecycle_startup'):
            filename = 'independent/source/robot-navigation/lingxi_navigation/' + module + '.py'
            with self.subTest(module=module):
                self.assertIn(filename, INDEPENDENT_FILES)
                with self.assertRaisesRegex(ValueError, 'declared_independent_commissioning_components_missing'):
                    verify_independent_scope(status, json.dumps(scope), INDEPENDENT_FILES - {filename})

    def test_installed_mapping_provisioning_helpers_are_mandatory(self):
        scope = json.loads((Path(__file__).resolve().parents[2] / 'deploy/independent-scope.json').read_bytes())
        status = {'classification': 'commissioning', 'productionAccepted': False,
                  'independentStackIncluded': True, 'independent': scope}
        for module in ('arm64_python_runtime', 'check_installed_slam_python',
                       'prepare_installed_mapping_inputs', 'prepare_installed_acquisition_inputs'):
            filename = 'independent/source/tools/' + module + '.py'
            with self.subTest(module=module):
                self.assertIn(filename, INDEPENDENT_FILES)
                with self.assertRaisesRegex(ValueError, 'declared_independent_commissioning_components_missing'):
                    verify_independent_scope(status, json.dumps(scope), INDEPENDENT_FILES - {filename})

    def test_process_isolated_offline_diagnostic_sources_are_mandatory(self):
        scope = json.loads((Path(__file__).resolve().parents[2] / 'deploy/independent-scope.json').read_bytes())
        status = {'classification': 'commissioning', 'productionAccepted': False,
                  'independentStackIncluded': True, 'independent': scope}
        for name in ('offline_nav2_service.py', 'offline_nav2_worker.py', 'run_nav2_offline.py',
                     'prepare_nav2_offline.py', 'humble_velocity_source.py',
                     'nav2-offline-service-isolated.sh', 'nav2_service_diagnostic.py',
                     'native-service-diagnostic/service_diagnostic.cpp'):
            filename = 'independent/source/tools/' + name
            with self.subTest(name=name):
                self.assertIn(filename, INDEPENDENT_FILES)
                with self.assertRaisesRegex(ValueError, 'declared_independent_commissioning_components_missing'):
                    verify_independent_scope(status, json.dumps(scope), INDEPENDENT_FILES - {filename})

    def test_native_source_reference_closure_checks_actual_verifiers_without_importing(self):
        root = Path(__file__).resolve().parents[2]
        captured = {'independent/source/' + name: (root / name).read_bytes() for name in BOUND_SOURCE_MODULES}
        files = {'independent/source/' + path.relative_to(root).as_posix()
                 for directory in ('tools', 'robot-runtime', 'robot-slam', 'robot-navigation', 'docs')
                 for path in (root / directory).rglob('*') if path.is_file()}
        self.assertGreater(verify_bound_source_references(captured, files), 50)
        required = 'independent/source/tools/glim-full-mapping/full_mapping.cpp'
        with self.assertRaisesRegex(ValueError, 'native_source_reference_missing'):
            verify_bound_source_references(captured, files - {required})
        with self.assertRaisesRegex(ValueError, 'native_source_verifier_missing'):
            verify_bound_source_references({}, files)

    def test_staged_source_check_rejects_filtered_runtime_verifier_before_compression(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in BOUND_SOURCE_MODULES:
                path = root / 'independent/source' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("SOURCE = 'tools/check_ground_native.py'\n", encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'native_source_reference_missing:.*check_ground_native'):
                verify_staged_source_references(root)
            verifier = root / 'independent/source/tools/check_ground_native.py'
            verifier.parent.mkdir(parents=True, exist_ok=True)
            verifier.write_text('# verifier fixture\n', encoding='utf-8')
            self.assertEqual(1, verify_staged_source_references(root))


if __name__ == '__main__':
    unittest.main()
