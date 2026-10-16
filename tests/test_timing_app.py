"""Tk timing-learning and smooth playback integration, with fake input only."""

import json
import os
import threading
import unittest
from unittest.mock import patch

from rhythia_tool.playback import PlaybackState
import test_live_app as live_fixtures


@unittest.skipUnless(os.name == "nt" or os.environ.get("DISPLAY"), "Tk functional tests require a display")
class TimingApplicationTests(unittest.TestCase):
    # Reuse the fake-input fixture without inheriting its already-covered tests.
    setUp = live_fixtures.LiveApplicationTests.setUp
    tearDown = live_fixtures.LiveApplicationTests.tearDown
    wait_for = live_fixtures.LiveApplicationTests.wait_for
    tick_for = live_fixtures.LiveApplicationTests.tick_for
    select_fixture = live_fixtures.LiveApplicationTests.select_fixture
    configure_live = live_fixtures.LiveApplicationTests.configure_live
    start_live = live_fixtures.LiveApplicationTests.start_live
    assert_stopped = live_fixtures.LiveApplicationTests.assert_stopped

    def prepare_learning(self, *, speed=1, offset=0, old_countdown=0):
        self.configure_live(countdown=old_countdown, notes=((491, 0, 0), (1500, 1, 1)))
        self.app.vars["speed"].set(str(speed))
        self.app.vars["offset_ms"].set(str(offset))
        self.assertTrue(self.app.save_form())
        self.app.learn_timing()
        self.assertEqual(self.app.timing_learning, "armed")
        self.assertFalse(self.app.live_requested)
        self.assertEqual(self.app.output_mode_var.get(), "Preview")
        self.assertEqual(self.factory.created, [])

    def mark_known_delay(self, *, elapsed_ns=3_491_000_000):
        with patch("rhythia_tool.app.time.perf_counter_ns", side_effect=(1_000_000_000, 1_000_000_000 + elapsed_ns)):
            self.app._hotkey_action("start")
            self.assertEqual(self.app.timing_learning, "waiting")
            self.assertEqual(self.app.learning_reference_ns, 1_000_000_000)
            self.assertEqual(self.factory.created, [])
            self.app._hotkey_action("mark")

    def refresh_now(self):
        # Replace the existing scheduled callback, keeping exactly one UI loop.
        if self.app.after_id is not None:
            self.root.after_cancel(self.app.after_id)
            self.app.after_id = None
        self.app._refresh()

    def test_learn_first_note_delay_saves_three_seconds_without_mouse_input(self):
        self.prepare_learning()
        self.mark_known_delay()
        self.assertEqual(self.app.timing_learning, "off")
        # F9 runs on the hotkey thread; only the main-thread refresh saves Tk
        # settings. No movement or live session is involved in the measurement.
        self.assertEqual(self.app.settings.countdown_s, 0)
        self.refresh_now()
        self.assertEqual(self.app.settings.countdown_s, 3)
        saved = json.loads(self.app.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["countdown_s"], 3)
        self.assertIn("3.000s", self.app.quick_setup_var.get())
        self.assertEqual(self.factory.created, [])
        self.assertEqual(self.factory.events, [])

    def test_learning_accounts_for_speed_and_offset_and_replaces_old_delay(self):
        self.prepare_learning(speed=2, offset=125, old_countdown=7)
        # 491 / 2 + 125 = 370.5 ms of chart time, plus 3 s of game lead-in.
        self.mark_known_delay(elapsed_ns=3_370_500_000)
        self.refresh_now()
        self.assertEqual(self.app.settings.countdown_s, 3)
        self.assertEqual(self.app.settings.speed, 2)
        self.assertEqual(self.app.settings.offset_ms, 125)
        self.assertEqual(self.factory.created, [])

    def test_mark_before_first_map_hit_is_rejected_without_false_zero_delay(self):
        self.prepare_learning(old_countdown=7)
        self.mark_known_delay(elapsed_ns=300_000_000)
        self.refresh_now()
        self.assertEqual(self.app.settings.countdown_s, 7)
        self.assertIn("first note", self.app.notice_var.get().lower())
        self.assertEqual(self.factory.created, [])

    def test_learning_hotkeys_do_not_touch_tk_from_background_thread(self):
        self.prepare_learning()
        with patch("rhythia_tool.app.time.perf_counter_ns", side_effect=(1_000_000_000, 4_491_000_000)):
            for action in ("start", "mark"):
                thread = threading.Thread(target=self.app._hotkey_action, args=(action,))
                thread.start()
                thread.join(0.5)
                self.assertFalse(thread.is_alive(), f"Learning {action} accessed Tk from the hotkey thread")
        self.assertEqual(self.app.settings.countdown_s, 0)
        self.refresh_now()
        self.assertEqual(self.app.settings.countdown_s, 3)
        self.assertEqual(self.factory.created, [])

    def test_stop_cancels_waiting_learner_and_later_mark_does_nothing(self):
        self.prepare_learning()
        with patch("rhythia_tool.app.time.perf_counter_ns", return_value=1_000_000_000):
            self.app._hotkey_action("start")
        self.app.stop()
        self.assertEqual(self.app.timing_learning, "off")
        with patch("rhythia_tool.app.time.perf_counter_ns", return_value=4_491_000_000):
            self.app._hotkey_action("mark")
        self.refresh_now()
        self.assertEqual(self.app.settings.countdown_s, 0)
        self.assertEqual(self.factory.created, [])

    def test_later_stop_invalidates_queued_learned_result(self):
        self.prepare_learning()
        self.mark_known_delay()
        self.app.emergency_stop()
        self.refresh_now()
        self.assertEqual(self.app.settings.countdown_s, 0)
        self.assertIn("Emergency", self.app.engine.snapshot().message)
        self.assertEqual(self.factory.created, [])

    def test_source_change_cancels_learner_and_queued_result(self):
        for queued in (False, True):
            with self.subTest(queued=queued):
                self.prepare_learning()
                if queued:
                    self.mark_known_delay()
                else:
                    with patch("rhythia_tool.app.time.perf_counter_ns", return_value=1_000_000_000):
                        self.app._hotkey_action("start")
                self.select_fixture(notes=((1000, 2, 2), (2000, 0, 0)))
                self.refresh_now()
                self.assertEqual(self.app.timing_learning, "off")
                self.assertEqual(self.app.settings.countdown_s, 0)
                self.assertEqual(self.factory.created, [])

    def test_setting_edit_cancels_waiting_learner(self):
        self.prepare_learning()
        with patch("rhythia_tool.app.time.perf_counter_ns", return_value=1_000_000_000):
            self.app._hotkey_action("start")
        self.app.vars["countdown_s"].set("4")
        self.assertEqual(self.app.timing_learning, "off")
        with patch("rhythia_tool.app.time.perf_counter_ns", return_value=4_491_000_000):
            self.app._hotkey_action("mark")
        self.refresh_now()
        self.assertEqual(self.app.vars["countdown_s"].get(), "4")
        self.assertEqual(self.app.settings.countdown_s, 0)
        self.assertEqual(self.factory.created, [])

    def test_setting_edit_after_mark_does_not_apply_stale_measurement(self):
        self.prepare_learning()
        self.mark_known_delay()
        self.app.vars["countdown_s"].set("4")
        self.refresh_now()
        self.assertEqual(self.app.vars["countdown_s"].get(), "4")
        self.assertEqual(self.app.settings.countdown_s, 0)
        self.assertEqual(self.factory.created, [])

    def test_edited_delay_blocks_hotkey_until_ready_applies_it(self):
        self.configure_live(notes=((491, 0, 0), (1500, 1, 1)))
        self.app.vars["speed"].set("1")
        self.assertTrue(self.app.save_form())
        self.app.vars["countdown_s"].set("3")
        self.app._hotkey_action("start")
        self.assert_stopped()
        self.assertEqual(self.factory.created, [])
        self.refresh_now()
        self.assertIn("settings changed", self.app.notice_var.get())
        self.app.quick_ready()
        self.assertEqual(self.app.settings.countdown_s, 3)
        self.start_live()
        self.assertEqual(self.app.engine.snapshot().state, PlaybackState.COUNTDOWN)
        self.assertGreater(self.app.engine.snapshot().countdown_remaining_s, 2)
        self.tick_for(0.1)
        self.assertEqual(self.factory.events, [])

    def test_invalid_unsaved_setting_blocks_hotkey_and_ready_without_input(self):
        self.configure_live()
        self.assertTrue(self.app.save_form())
        self.app.vars["countdown_s"].set("invalid")
        self.app._hotkey_action("start")
        self.assert_stopped()
        self.assertEqual(self.factory.created, [])
        with patch("rhythia_tool.app.messagebox.showerror") as error:
            self.app.quick_ready()
        error.assert_called_once()
        self.assertEqual(self.factory.created, [])

    def prepare_smooth_run(self):
        self.configure_live(notes=((300, 0, 0), (700, 1, 1), (1000, 2, 2)))
        self.app.vars["speed"].set("1")
        self.app.vars["smooth_motion"].set(True)
        self.app.vars["approach_ms"].set("120")
        self.app.vars["arrival_lead_ms"].set("12")
        self.assertTrue(self.app.save_form())

    def test_smooth_run_holds_initial_silence_and_reaches_note_endpoints(self):
        self.prepare_smooth_run()
        self.start_live()
        self.tick_for(0.15)
        self.assertEqual(self.factory.events, [], "The map's initial silence must not be skipped")
        self.wait_for(lambda: self.app.engine.snapshot().state == PlaybackState.COMPLETED)
        self.assertEqual(self.app.engine.snapshot().emitted_count, 3)
        self.assertGreater(len(self.factory.events), 3)
        self.assertIn(("absolute", 60, 70), self.factory.events)
        self.assertIn(("absolute", 160, 170), self.factory.events)
        self.assertEqual(self.factory.events[-1], ("absolute", 259, 269))
        self.assertTrue(any(60 < event[1] < 160 for event in self.factory.events))
        self.assertTrue(self.factory.created[-1].closed)
        self.assertFalse(self.app.active_run_live)

    def test_emergency_during_smooth_approach_prevents_all_further_input(self):
        self.prepare_smooth_run()
        self.start_live()
        self.wait_for(lambda: any(60 < event[1] < 160 for event in self.factory.events))
        self.app.emergency_stop()
        self.assert_stopped()
        stopped_events = list(self.factory.events)
        self.tick_for(0.55)
        self.assertEqual(self.factory.events, stopped_events)
        self.assertTrue(self.factory.created[-1].closed)


if __name__ == "__main__":
    unittest.main()
