import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release_scope import CORE_FILES, verify_scope


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


if __name__ == '__main__':
    unittest.main()
