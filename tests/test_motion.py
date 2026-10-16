"""Pure path tests: no native input, worker threads, or real clocks."""

import math
from unittest.mock import patch
import unittest

from rhythia_tool.motion import SmoothPath
from rhythia_tool.playback import Note
from rhythia_tool.sspm import MapNote


class SmoothPathTests(unittest.TestCase):
    def setUp(self):
        self.notes = (MapNote(1000, -0.5, 2.5), MapNote(2000, 2.5, -0.5))
        self.path = SmoothPath(self.notes)

    def test_first_note_waits_for_its_map_timestamp_minus_lead(self):
        for time_ms in (-500, 0, 500, 987.999):
            with self.subTest(time_ms=time_ms):
                self.assertIsNone(self.path.sample(time_ms, 0))
        self.assertEqual(self.path.sample(988, 0), MapNote(988, -0.5, 2.5))
        self.assertEqual(self.path.sample(1000, 0), MapNote(1000, -0.5, 2.5))

    def test_first_note_near_zero_does_not_move_during_countdown(self):
        path = SmoothPath((MapNote(3, 0, 2),))
        self.assertIsNone(path.sample(-0.001, 0))
        self.assertEqual(path.sample(0, 0), MapNote(0, 0, 2))

    def test_first_note_does_not_guess_an_anchor_or_interpolate_from_origin(self):
        path = SmoothPath((MapNote(100, 0.137, 2.333),))
        self.assertIsNone(path.sample(87, 0))
        self.assertEqual(path.sample(88, 0), MapNote(88, 0.137, 2.333))

    def test_long_silence_holds_until_bounded_approach_window(self):
        path = SmoothPath((MapNote(1000, 0, 2), MapNote(61000, 2, 0)))
        for time_ms in (1000, 1008, 5000, 30000, 60868):
            with self.subTest(time_ms=time_ms):
                self.assertEqual(path.sample(time_ms, 1), MapNote(time_ms, 0, 2))
        self.assertEqual(path.sample(60928, 1), MapNote(60928, 1, 1))
        self.assertEqual(path.sample(60988, 1), MapNote(60988, 2, 0))

    def test_interpolation_uses_minimum_jerk_instead_of_linear_speed(self):
        # Default second-note window is [1868, 1988], so 1898 is one quarter.
        point = self.path.sample(1898, 1)
        expected_fraction = 0.25 ** 3 * (10 - 15 * 0.25 + 6 * 0.25 ** 2)
        self.assertAlmostEqual(point.x, -0.5 + 3 * expected_fraction)
        self.assertAlmostEqual(point.y, 2.5 - 3 * expected_fraction)
        self.assertLess(point.x, -0.5 + 3 * 0.25)
        self.assertEqual(point.time_ms, 1898)

    def test_midpoint_stays_on_straight_raw_coordinate_segment(self):
        point = self.path.sample(1928, 1)
        self.assertEqual(point, MapNote(1928, 1, 1))
        quantum = SmoothPath((MapNote(0, 0.125, 0.375), MapNote(200, 2.25, 1.625)))
        point = quantum.sample(128, 1)
        self.assertEqual(point, MapNote(128, 1.1875, 1.0))

    def test_approach_has_flat_eased_ends(self):
        at_start = self.path.sample(1868, 1)
        just_after = self.path.sample(1868.01, 1)
        just_before = self.path.sample(1987.99, 1)
        at_end = self.path.sample(1988, 1)
        self.assertEqual(at_start.x, -0.5)
        self.assertLess(just_after.x - at_start.x, 1e-9)
        self.assertLess(at_end.x - just_before.x, 1e-9)
        self.assertEqual(at_end.x, 2.5)

    def test_exact_endpoint_at_arrival_and_original_deadline(self):
        for time_ms in (1988, 1990, 2000, 2500):
            with self.subTest(time_ms=time_ms):
                self.assertEqual(self.path.sample(time_ms, 1), MapNote(time_ms, 2.5, -0.5))

    def test_close_note_gap_clamps_lead_and_initial_hold(self):
        path = SmoothPath((MapNote(100, 0, 0), MapNote(120, 2, 2)))
        self.assertEqual(path.sample(105, 1), MapNote(105, 0, 0))
        self.assertEqual(path.sample(110, 1), MapNote(110, 1, 1))
        self.assertEqual(path.sample(115, 1), MapNote(115, 2, 2))

    def test_custom_durations_and_zero_arrival_lead(self):
        path = SmoothPath(self.notes, approach_ms=20, arrival_lead_ms=0)
        self.assertIsNone(path.sample(999, 0))
        self.assertEqual(path.sample(1980, 1), MapNote(1980, -0.5, 2.5))
        self.assertEqual(path.sample(1990, 1), MapNote(1990, 1, 1))
        self.assertEqual(path.sample(2000, 1), MapNote(2000, 2.5, -0.5))

    def test_large_lead_cannot_consume_close_note_interval(self):
        path = SmoothPath(self.notes, arrival_lead_ms=10000)
        # For this 1000ms gap lead is capped at 250ms, approach starts at 1630.
        self.assertEqual(path.sample(1630, 1), MapNote(1630, -0.5, 2.5))
        self.assertEqual(path.sample(1690, 1), MapNote(1690, 1, 1))
        self.assertEqual(path.sample(1750, 1), MapNote(1750, 2.5, -0.5))

    def test_simultaneous_notes_switch_at_deadline_without_division(self):
        path = SmoothPath((MapNote(100, 0, 0), MapNote(100, 2, 2), MapNote(100, 1, 1)))
        self.assertEqual(path.sample(99.9, 1), MapNote(99.9, 0, 0))
        self.assertEqual(path.sample(100, 1), MapNote(100, 2, 2))
        self.assertEqual(path.sample(100, 2), MapNote(100, 1, 1))

    def test_empty_and_finished_paths_do_not_request_movement(self):
        self.assertIsNone(SmoothPath(()).sample(0, 0))
        self.assertIsNone(self.path.sample(1000, len(self.notes)))
        self.assertIsNone(self.path.sample(3000, len(self.notes)))

    def test_constructor_copies_mutable_note_sequence(self):
        original = list(self.notes)
        path = SmoothPath(original)
        original[0] = MapNote(0, 1, 1)
        original.clear()
        self.assertEqual(path.sample(988, 0), MapNote(988, -0.5, 2.5))

    def test_all_sampled_points_stay_inside_fixed_raw_frame(self):
        for time_ms in range(0, 2101):
            point = self.path.sample(time_ms, 0 if time_ms < 1000 else 1)
            if point is not None:
                self.assertTrue(-0.5 <= point.x <= 2.5)
                self.assertTrue(-0.5 <= point.y <= 2.5)
                self.assertAlmostEqual(point.x + point.y, 2)

    def test_sampling_is_repeatable_and_does_not_advance_index(self):
        expected = self.path.sample(1900, 1)
        self.path.sample(2000, 1)
        self.assertEqual(self.path.sample(1900, 1), expected)
        self.assertEqual(self.path.sample(988, 0), MapNote(988, -0.5, 2.5))

    def test_invalid_configuration_values_fail_clearly(self):
        for approach_ms in (0, -1, True, "120", None, math.nan, math.inf, 10 ** 1000):
            with self.subTest(approach_ms=str(approach_ms)[:40]):
                with self.assertRaisesRegex(ValueError, "Approach duration"):
                    SmoothPath(self.notes, approach_ms=approach_ms)
        for lead in (-1, False, "12", None, math.nan, -math.inf, 10 ** 1000):
            with self.subTest(lead=str(lead)[:40]):
                with self.assertRaisesRegex(ValueError, "Arrival lead"):
                    SmoothPath(self.notes, arrival_lead_ms=lead)

    def test_invalid_sample_times_and_indices_fail_clearly(self):
        for time_ms in (True, "1000", None, math.nan, math.inf, 10 ** 1000):
            with self.subTest(time_ms=str(time_ms)[:40]):
                with self.assertRaisesRegex(ValueError, "Sample timeline"):
                    self.path.sample(time_ms, 0)
        for index in (-1, 3, True, 1.0, "0", None):
            with self.subTest(index=index):
                with self.assertRaisesRegex(ValueError, "index"):
                    self.path.sample(1000, index)

    def test_invalid_map_types_bounds_and_order_fail_clearly(self):
        bad_sequences = (
            None, "map", iter(self.notes), (Note(100, 0, 0),),
            (MapNote(-1, 0, 0),), (MapNote(100, 0, 0), MapNote(99, 0, 0)),
            (MapNote(math.nan, 0, 0),), (MapNote(True, 0, 0),),
            (MapNote(100, -0.50001, 0),), (MapNote(100, 0, 2.50001),),
            (MapNote(100, math.inf, 0),), (MapNote(100, 0, "1"),),
        )
        for notes in bad_sequences:
            with self.subTest(notes=str(notes)[:100]):
                with self.assertRaises(ValueError):
                    SmoothPath(notes)

    def test_note_count_is_bounded_before_copying(self):
        with patch("rhythia_tool.motion.MAX_NOTES", 1):
            with self.assertRaisesRegex(ValueError, "at most 1 notes"):
                SmoothPath(self.notes)


if __name__ == "__main__":
    unittest.main()
