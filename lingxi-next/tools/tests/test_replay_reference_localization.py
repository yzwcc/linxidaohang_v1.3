import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    "replay_reference_localization", Path(__file__).resolve().parents[1] / "replay_reference_localization.py"
)
REPLAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPLAY)


class ReplayTimingTests(unittest.TestCase):
    def test_wall_time_not_sum_of_parallel_candidates(self):
        record = REPLAY.timing_record({
            "match_s": 1.24, "candidate_wall_s": .9,
            "phases": {"proposals": {"seconds": .31}, "gicp": {"seconds": 1.6}},
            "candidate_timings": [{"gicp_s": .8, "diagnose_s": .05}] * 2,
        }, 1.26)
        self.assertEqual(record, {
            "roundtripMs": 1260., "matchMs": 1240., "outsideMatchMs": 20.,
            "candidateWallMs": 900., "proposalMs": 310., "otherMatchMs": 30.,
            "candidates": [{"gicpMs": 800., "diagnoseMs": 50.}] * 2,
        })
        self.assertEqual(REPLAY.timing_summary([record, {**record, "roundtripMs": 1350.}])["roundtripMs"],
                         dict(median=1305., maximum=1350.))

    def test_tracking_can_skip_proposals(self):
        record = REPLAY.timing_record({
            "match_s": .2, "candidate_wall_s": .195, "phases": {},
            "candidate_timings": [{"gicp_s": .15, "diagnose_s": .03}],
        }, .22)
        self.assertEqual(record["proposalMs"], 0.)
        self.assertEqual(record["otherMatchMs"], 5.)
        self.assertIsNone(REPLAY.timing_summary([]))

    def test_age_is_checked_with_and_without_modeled_input_latency(self):
        self.assertIsNone(REPLAY.result_age_reason(1.3, 200, 1.5))
        self.assertEqual(REPLAY.result_age_reason(1.31, 200, 1.5), "modeled_age_exceeds_live_age_limit")
        self.assertEqual(REPLAY.result_age_reason(1.51, 0, 1.5), "worker_elapsed_exceeds_live_age_limit")


if __name__ == "__main__":
    unittest.main()
