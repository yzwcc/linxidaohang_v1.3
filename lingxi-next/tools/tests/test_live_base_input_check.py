"""Live diagnostics require actual timed transforms, not just installed TF2."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from check_managed_live_localization import verify_base_input


class LiveBaseEvidence(unittest.TestCase):
    def fixture(self):
        return {"closed": True, "pendingScanTimes": 0, "retainedDynamicTransforms": 0,
                "physicalScaleValidated": False, "navigationReady": False,
                "observations": {"imuMessages": 100, "cloudMessages": 10,
                    "staticTransforms": 2, "dynamicTransforms": 60, "timedFrames": 8,
                    "tfLookupsAvailable": 7, "tfLookupsUnavailable": 1, "scanTimeUnavailable": 0,
                    "invalidScanTimes": 0, "invalidTransforms": 0, "lookupErrors": 0}}

    def test_observed_timing_and_clean_shutdown_pass(self):
        verify_base_input(self.fixture())

    def test_no_tf_missing_timing_invalid_data_or_readiness_claims_fail(self):
        mutations = (
            ("closed", False), ("pendingScanTimes", 1), ("retainedDynamicTransforms", 1),
            ("physicalScaleValidated", True), ("navigationReady", True),
            ("imuMessages", 0), ("cloudMessages", 0), ("staticTransforms", 0),
            ("dynamicTransforms", 0), ("timedFrames", 0), ("tfLookupsAvailable", 0),
            ("tfLookupsUnavailable", -1), ("tfLookupsAvailable", True),
            ("scanTimeUnavailable", 1), ("invalidScanTimes", 1), ("invalidTransforms", 1),
            ("lookupErrors", 1), ("timedFrames", 9), ("tfLookupsUnavailable", False),
        )
        for key, value in mutations:
            inputs = deepcopy(self.fixture())
            target = inputs if key in inputs else inputs["observations"]
            target[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                verify_base_input(inputs)

    def test_missing_observations_fail(self):
        inputs = self.fixture()
        inputs.pop("observations")
        with self.assertRaises(ValueError):
            verify_base_input(inputs)


if __name__ == "__main__":
    unittest.main()
