"""Meaningful deterministic timing checks; none of these control a real mouse."""

import math
import threading
import unittest

from rhythia_tool.playback import Note, PlaybackEngine, PlaybackState


class FakeClock:
    def __init__(self):
        self.now_ns = 10_000_000_000

    def __call__(self):
        return self.now_ns

    def advance_ms(self, amount):
        self.now_ns += round(amount * 1_000_000)


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.emitted = []

    def engine(self, times=(0, 100, 200), **kwargs):
        return PlaybackEngine(
            [Note(t, 0.25, 0.75) for t in times],
            self.emitted.append,
            self.clock,
            **kwargs,
        )

    def test_countdown_and_equal_time_notes(self):
        engine = self.engine((0, 0, 100))
        self.assertEqual(engine.snapshot().state, PlaybackState.IDLE)
        snapshot = engine.start(countdown_s=2)
        self.assertEqual(snapshot.state, PlaybackState.COUNTDOWN)
        self.assertEqual(snapshot.countdown_remaining_s, 2)
        self.clock.advance_ms(1999)
        engine.pump()
        self.assertEqual(self.emitted, [])
        self.clock.advance_ms(1)
        snapshot = engine.pump()
        self.assertEqual(snapshot.state, PlaybackState.RUNNING)
        self.assertEqual(snapshot.emitted_count, 2)
        self.assertEqual([note.time_ms for note in self.emitted], [0, 0])
        self.clock.advance_ms(100)
        self.assertEqual(engine.pump().state, PlaybackState.COMPLETED)

    def test_speed_and_positive_offset_have_explicit_wall_clock_semantics(self):
        engine = self.engine((0, 1000))
        engine.start(countdown_s=1, offset_ms=100, speed=2)
        self.clock.advance_ms(1000)
        self.assertEqual(engine.pump().emitted_count, 0)
        self.clock.advance_ms(100)
        self.assertEqual(engine.pump().emitted_count, 1)
        self.clock.advance_ms(499)
        self.assertEqual(engine.pump().emitted_count, 1)
        self.clock.advance_ms(1)
        snapshot = engine.pump()
        self.assertEqual(snapshot.state, PlaybackState.COMPLETED)
        self.assertEqual(snapshot.elapsed_ms, 1200)
        self.assertEqual(snapshot.max_lateness_ms, 0)

    def test_negative_offset_can_emit_in_final_countdown(self):
        engine = self.engine((0, 1000))
        engine.start(countdown_s=1, offset_ms=-100)
        self.clock.advance_ms(900)
        snapshot = engine.pump()
        self.assertEqual(snapshot.state, PlaybackState.COUNTDOWN)
        self.assertAlmostEqual(snapshot.countdown_remaining_s, 0.1)
        self.assertEqual(snapshot.elapsed_ms, 0)
        self.assertEqual(snapshot.emitted_count, 1)

    def test_start_reference_includes_preparation_time_in_note_deadlines(self):
        self.clock.now_ns = 100_000_000
        engine = self.engine((150,), max_lateness_ms=50)
        snapshot = engine.start(countdown_s=0, reference_ns=0)
        self.assertEqual(snapshot.state, PlaybackState.RUNNING)
        self.assertEqual(snapshot.elapsed_ms, 100)
        self.assertEqual(engine.pump().emitted_count, 0)
        self.clock.advance_ms(49)
        self.assertEqual(engine.pump().emitted_count, 0)
        self.clock.advance_ms(1)
        snapshot = engine.pump()
        self.assertEqual(snapshot.state, PlaybackState.COMPLETED)
        self.assertEqual(snapshot.elapsed_ms, 150)
        self.assertEqual(snapshot.max_lateness_ms, 0)

    def test_start_reference_stops_before_input_if_preparation_lost_sync(self):
        self.clock.now_ns = 100_000_000
        engine = self.engine((0, 150), max_lateness_ms=50)
        engine.start(countdown_s=0, reference_ns=0)
        snapshot = engine.pump()
        self.assertEqual(snapshot.state, PlaybackState.STOPPED)
        self.assertEqual(snapshot.max_lateness_ms, 100)
        self.assertEqual(snapshot.emitted_count, 0)
        self.assertEqual(self.emitted, [])
        self.assertIn("Synchronization lost", snapshot.message)

    def test_start_reference_countdown_offset_and_speed_share_one_origin(self):
        reference = self.clock.now_ns
        self.clock.advance_ms(100)
        engine = self.engine((100,))
        snapshot = engine.start(countdown_s=0.25, offset_ms=50, speed=2, reference_ns=reference)
        self.assertEqual(snapshot.state, PlaybackState.COUNTDOWN)
        self.assertAlmostEqual(snapshot.countdown_remaining_s, 0.15)
        self.clock.advance_ms(249)
        self.assertEqual(engine.pump().emitted_count, 0)
        self.clock.advance_ms(1)
        snapshot = engine.pump()
        self.assertEqual(snapshot.state, PlaybackState.COMPLETED)
        self.assertEqual(snapshot.elapsed_ms, 200)
        self.assertEqual(snapshot.max_lateness_ms, 0)

    def test_start_reference_uses_actual_start_observation_for_clock_regression(self):
        self.clock.now_ns = 100_000_000
        engine = self.engine((150,))
        engine.start(countdown_s=0, reference_ns=0)
        self.clock.advance_ms(-1)
        snapshot = engine.pump()
        self.assertEqual(snapshot.state, PlaybackState.ERROR)
        self.assertIn("backwards", snapshot.message)
        self.assertEqual(self.emitted, [])

    def test_invalid_start_reference_leaves_existing_run_unchanged(self):
        engine = self.engine()
        original = engine.start(countdown_s=0)
        for reference in (
            True, False, 0.0, "0", math.nan, math.inf,
            self.clock.now_ns + 1,
            self.clock.now_ns - 3_600_000_000_001,
            -(10 ** 1000), 10 ** 1000,
        ):
            with self.subTest(reference=str(reference)[:80]):
                with self.assertRaisesRegex(ValueError, "reference_ns"):
                    engine.start(reference_ns=reference)
                self.assertEqual(engine.snapshot(), original)
        self.assertEqual(engine.pump().emitted_count, 1)

    def test_reference_exactly_one_hour_old_is_supported(self):
        reference = self.clock.now_ns - 3_600_000_000_000
        engine = self.engine((1,))
        snapshot = engine.start(countdown_s=3600, reference_ns=reference)
        self.assertEqual(snapshot.state, PlaybackState.RUNNING)
        self.assertEqual(snapshot.elapsed_ms, 0)
        self.assertEqual(engine.pump().emitted_count, 0)

    def test_absolute_deadlines_do_not_accumulate_polling_drift(self):
        engine = self.engine((0, 100, 200, 300))
        engine.start(countdown_s=0)
        observed = []
        for clock_ms in (7, 107, 207, 307):
            self.clock.now_ns = 10_000_000_000 + clock_ms * 1_000_000
            observed.append(engine.pump().emitted_count)
        self.assertEqual(observed, [1, 2, 3, 4])
        self.assertEqual(engine.snapshot().max_lateness_ms, 7)

    def test_pause_resume_freezes_time_and_moves_remaining_deadlines(self):
        engine = self.engine((0, 1000))
        engine.start(countdown_s=0)
        engine.pump()
        self.clock.advance_ms(400)
        self.assertEqual(engine.pause().elapsed_ms, 400)
        self.clock.advance_ms(10_000)
        self.assertEqual(engine.pump().state, PlaybackState.PAUSED)
        self.assertEqual(engine.snapshot().elapsed_ms, 400)
        engine.resume()
        self.clock.advance_ms(599)
        self.assertEqual(engine.pump().emitted_count, 1)
        self.clock.advance_ms(1)
        self.assertEqual(engine.pump().state, PlaybackState.COMPLETED)
        self.assertEqual(engine.snapshot().elapsed_ms, 1000)

    def test_pause_during_countdown_preserves_remaining_countdown(self):
        engine = self.engine((0,))
        engine.start(countdown_s=2)
        self.clock.advance_ms(750)
        self.assertEqual(engine.pause().countdown_remaining_s, 1.25)
        self.clock.advance_ms(5000)
        self.assertEqual(engine.resume().countdown_remaining_s, 1.25)
        self.clock.advance_ms(1250)
        self.assertEqual(engine.pump().state, PlaybackState.COMPLETED)

    def test_stop_cancels_remaining_events_until_explicit_restart(self):
        engine = self.engine()
        engine.start(countdown_s=0)
        engine.pump()
        snapshot = engine.stop("Emergency stop")
        self.assertEqual(snapshot.message, "Emergency stop")
        self.clock.advance_ms(2000)
        engine.resume()
        self.assertEqual(engine.pump().state, PlaybackState.STOPPED)
        self.assertEqual(len(self.emitted), 1)
        self.assertEqual(engine.snapshot().elapsed_ms, 0)
        engine.start(countdown_s=0)
        self.assertEqual(engine.pump().emitted_count, 1)
        self.assertEqual(len(self.emitted), 2)

    def test_late_poll_stops_before_catch_up_burst(self):
        engine = self.engine((0, 100, 200), max_lateness_ms=50)
        engine.start(countdown_s=0)
        self.clock.advance_ms(201)
        snapshot = engine.pump()
        self.assertEqual(snapshot.state, PlaybackState.STOPPED)
        self.assertEqual(snapshot.emitted_count, 0)
        self.assertEqual(self.emitted, [])
        self.assertEqual(snapshot.max_lateness_ms, 201)
        self.assertIn("Restart", snapshot.message)

    def test_slow_sink_cannot_cause_unchecked_catch_up(self):
        def slow_sink(note):
            self.emitted.append(note)
            self.clock.advance_ms(1000)

        engine = PlaybackEngine((Note(0, 0, 0), Note(100, 1, 1)), slow_sink, self.clock)
        engine.start(countdown_s=0)
        snapshot = engine.pump()
        self.assertEqual(snapshot.state, PlaybackState.STOPPED)
        self.assertEqual(snapshot.emitted_count, 1)
        self.assertEqual(len(self.emitted), 1)

    def test_sink_error_is_terminal_and_clear(self):
        def broken_sink(note):
            raise RuntimeError("preview unavailable")

        engine = PlaybackEngine((Note(0, 0, 0),), broken_sink, self.clock)
        engine.start(countdown_s=0)
        snapshot = engine.pump()
        self.assertEqual(snapshot.state, PlaybackState.ERROR)
        self.assertIn("preview unavailable", snapshot.message)
        self.assertEqual(snapshot.emitted_count, 0)
        self.clock.advance_ms(1000)
        self.assertEqual(engine.pump().state, PlaybackState.ERROR)
        self.assertEqual(engine.snapshot().elapsed_ms, 0)

    def test_backwards_clock_stops_before_emitting(self):
        engine = self.engine()
        engine.start(countdown_s=0)
        self.clock.advance_ms(-1)
        snapshot = engine.pump()
        self.assertEqual(snapshot.state, PlaybackState.ERROR)
        self.assertIn("backwards", snapshot.message)
        self.assertEqual(self.emitted, [])

    def test_emergency_stop_still_works_if_clock_fails(self):
        engine = self.engine()
        engine.start(countdown_s=0)

        def broken_clock():
            raise RuntimeError("clock unavailable")

        engine._clock_ns = broken_clock
        snapshot = engine.stop("Emergency stop")
        self.assertEqual(snapshot.state, PlaybackState.STOPPED)
        self.assertEqual(snapshot.message, "Emergency stop")
        self.assertEqual(engine.pump().emitted_count, 0)

    def test_stopping_completed_run_preserves_final_elapsed_time(self):
        engine = self.engine((0,))
        engine.start(countdown_s=0)
        engine.pump()
        self.clock.advance_ms(50_000)
        self.assertEqual(engine.stop().elapsed_ms, 0)

    def test_reentrant_stop_in_sink_prevents_next_equal_time_event(self):
        engine = None

        def stop_sink(note):
            self.emitted.append(note)
            engine.stop("Stopped from callback")

        engine = PlaybackEngine((Note(0, 0, 0), Note(0, 1, 1)), stop_sink, self.clock)
        engine.start(countdown_s=0)
        snapshot = engine.pump()
        self.assertEqual(snapshot.state, PlaybackState.STOPPED)
        self.assertEqual(snapshot.emitted_count, 1)
        self.assertEqual(len(self.emitted), 1)

    def test_stop_waits_for_current_callback_and_prevents_future_callbacks(self):
        entered = threading.Event()
        release = threading.Event()
        stopped = threading.Event()

        def sink(note):
            entered.set()
            release.wait(timeout=2)

        engine = PlaybackEngine((Note(0, 0, 0), Note(100, 1, 1)), sink, self.clock)
        engine.start(countdown_s=0)
        worker = threading.Thread(target=engine.pump)
        worker.start()
        self.assertTrue(entered.wait(timeout=2))

        def stop_worker():
            engine.stop()
            stopped.set()

        stopper = threading.Thread(target=stop_worker)
        stopper.start()
        self.assertFalse(stopped.wait(timeout=0.02))
        release.set()
        worker.join(timeout=2)
        stopper.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(stopper.is_alive())
        self.assertTrue(stopped.is_set())
        self.clock.advance_ms(100)
        self.assertEqual(engine.pump().state, PlaybackState.STOPPED)
        self.assertEqual(engine.snapshot().emitted_count, 1)

    def test_note_and_sequence_validation(self):
        for args in ((-1, 0, 0), (0, -0.1, 0), (0, 0, 1.1), (math.nan, 0, 0),
                     (0, math.inf, 0), ("10", 0, 0), (False, 0, 0)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                Note(*args)
        with self.assertRaisesRegex(ValueError, "at least one"):
            PlaybackEngine((), self.emitted.append)
        with self.assertRaisesRegex(ValueError, "sorted"):
            self.engine((100, 0))
        with self.assertRaisesRegex(ValueError, "Note objects"):
            PlaybackEngine(("not a note",), self.emitted.append)

    def test_settings_validation_leaves_existing_run_unchanged(self):
        engine = self.engine()
        engine.start(countdown_s=0)
        for kwargs in ({"countdown_s": -1}, {"countdown_s": 3601},
                       {"offset_ms": 5001}, {"offset_ms": math.nan},
                       {"speed": 0}, {"speed": 4.1}, {"speed": math.inf}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                engine.start(**kwargs)
        self.assertEqual(engine.pump().emitted_count, 1)
        for lateness in (0, -1, math.nan, 60_001):
            with self.subTest(lateness=lateness), self.assertRaises(ValueError):
                self.engine(max_lateness_ms=lateness)


if __name__ == "__main__":
    unittest.main()
