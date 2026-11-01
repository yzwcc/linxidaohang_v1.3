"""Timestamp validation is independent of ROS; native DDS has a separate probe."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from humble_velocity_source import HumbleTwistSource, PublicationClock, SourceTimeError


class Clock:
    def __init__(self):
        self.wall, self.mono = 1_800_000_000_000_000_000, 10_000_000_000

    def advance(self, ns):
        self.wall += ns
        self.mono += ns


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.time = Clock()
        self.clock = PublicationClock(wall=lambda: self.time.wall, monotonic=lambda: self.time.mono)

    def stamp(self, source=None):
        return dict(source_timestamp=self.time.wall if source is None else source,
                    received_timestamp=0)

    def test_fresh_publication_keeps_source_age_not_callback_arrival(self):
        source = self.time.wall
        self.time.advance(100_000_000)
        self.assertEqual(10., self.clock.convert(self.stamp(source)))
        self.assertEqual(100_000_000, self.clock.maximum_age_observed_ns)
        self.assertEqual(1, self.clock.accepted)

    def test_old_queue_rejected_and_new_publication_cannot_revive_generation(self):
        source = self.time.wall
        self.time.advance(150_000_001)
        with self.assertRaisesRegex(SourceTimeError, 'expired_or_future'):
            self.clock.convert(self.stamp(source))
        with self.assertRaisesRegex(SourceTimeError, 'expired_or_future'):
            self.clock.convert(self.stamp())
        self.assertEqual(0, self.clock.accepted)

    def test_missing_zero_boolean_float_negative_and_oversized_source_rejected(self):
        for value in (None, 0, True, 1.8e18, -1, 2**63):
            with self.subTest(value=value):
                self.setUp()
                with self.assertRaisesRegex(SourceTimeError, 'timestamp_required'):
                    self.clock.convert(dict(source_timestamp=value))

    def test_wrong_metadata_shape_fails_closed(self):
        for value in (None, [], (1, 2)):
            with self.subTest(value=value):
                self.setUp()
                with self.assertRaisesRegex(SourceTimeError, 'metadata_required'):
                    self.clock.convert(value)

    def test_future_source_and_pre_generation_queue_rejected(self):
        for source, reason in ((self.time.wall + 1, 'future'),
                               (self.time.wall - 1, 'predates_generation')):
            with self.subTest(source=source):
                self.setUp()
                with self.assertRaisesRegex(SourceTimeError, reason):
                    self.clock.convert(self.stamp(source))

    def test_duplicate_and_backwards_publication_rejected(self):
        for delta in (0, -1):
            self.setUp()
            self.time.advance(10_000_000)
            source = self.time.wall
            self.clock.convert(self.stamp())
            self.time.advance(1_000_000)
            with self.assertRaisesRegex(SourceTimeError, 'replayed'):
                self.clock.convert(self.stamp(source + delta))

    def test_clock_steps_forward_backward_and_monotonic_reversal_rejected(self):
        for wall, mono in ((10_000_000, 0), (-10_000_000, 0), (0, -1)):
            self.setUp()
            self.time.wall += wall
            self.time.mono += mono
            with self.assertRaisesRegex(SourceTimeError, 'discontinuity'):
                self.clock.convert(self.stamp())

    def test_bounded_clock_jitter_never_makes_publication_newer(self):
        source = self.time.wall
        self.time.advance(100_000_000)
        self.time.wall += 2_000_000
        self.assertLessEqual(self.clock.convert(self.stamp(source)), 10.)

    def test_unbounded_clock_sampling_window_rejected(self):
        values = iter((10, 10_000_010))
        with self.assertRaisesRegex(SourceTimeError, 'clock_sample_invalid'):
            PublicationClock(wall=lambda: self.time.wall, monotonic=lambda: next(values))

    def test_configuration_rejects_unbounded_and_boolean_limits(self):
        for value in (True, 0, -1, 1_000_000_001, .15):
            with self.assertRaises(ValueError):
                PublicationClock(maximum_age_ns=value)

    def test_retired_reader_does_not_touch_destroyed_handle(self):
        source = object.__new__(HumbleTwistSource)
        source.closed, source.node = False, Mock()
        source.close()
        source.close()
        self.assertIsNone(source.take())
        source.node.destroy_node.assert_called_once()

    def test_reader_rejects_executor_attachment_and_changed_take_contract(self):
        source = object.__new__(HumbleTwistSource)
        source.closed, source.node = False, Mock()
        with self.assertRaisesRegex(SourceTimeError, 'attached_to_executor'):
            source.take()
        source.node.executor = None
        source.message_type, source.clock = object, self.clock
        class Handle:
            def __enter__(self):
                pass
            def __exit__(self, *args):
                pass
            def take_message(self, *args):
                return [object(), {}]
        source.subscription = Mock(handle=Handle())
        with self.assertRaisesRegex(SourceTimeError, 'contract_changed'):
            source.take()


if __name__ == '__main__':
    unittest.main()
