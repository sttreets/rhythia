"""Guided front page tests with real Tk and no mouse or window APIs."""

import gc
import os
from types import SimpleNamespace
import tkinter as tk
import unittest

from rhythia_tool.live import ABSOLUTE_MODE, RELATIVE_MODE
from rhythia_tool.playback import PlaybackState
from rhythia_tool.quickstart import QuickStartPanel


@unittest.skipUnless(os.name == "nt" or os.environ.get("DISPLAY"), "Tk tests require a display")
class QuickStartTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("820x660")
        self.calls = []
        self.snapshot = SimpleNamespace(state=PlaybackState.IDLE)
        self.app = SimpleNamespace(
            vars={"input_mode": tk.StringVar(self.root, value="Unverified"),
                  "countdown_s": tk.StringVar(self.root, value="0"),
                  "offset_ms": tk.StringVar(self.root, value="0"),
                  "speed": tk.StringVar(self.root, value="1"),
                  "smooth_motion": tk.BooleanVar(self.root, value=True)},
            quick_setup_var=tk.StringVar(self.root, value="Open Rhythia and choose Guided setup."),
            next_note_var=tk.StringVar(self.root, value="First map note: 0.491s. Not playing."),
            timing_learning="off",
            status_var=tk.StringVar(self.root, value="Ready."), loaded_map=None, pending_map=None,
            chart=SimpleNamespace(title="Demo", notes=(1, 2, 3), duration_ms=12345),
            target=None, calibration_pending=False, live_preparing=False, active_run_live=False,
            live_requested=False, hotkeys=SimpleNamespace(last_error=None), hotkey_signature=(("start", "F6"),),
            settings=SimpleNamespace(start_hotkey="F6", stop_hotkey="F8", input_mode=ABSOLUTE_MODE,
                                     countdown_s=0, offset_ms=0, speed=1, smooth_motion=True),
            engine=SimpleNamespace(snapshot=lambda: self.snapshot),
            _hotkey_mapping=lambda _: {"start": "F6"},
            pick_map=lambda: self.calls.append("pick"), quick_setup=lambda: self.calls.append("setup"),
            quick_ready=lambda: self.calls.append("ready"), stop=lambda: self.calls.append("stop"),
            emergency_stop=lambda: self.calls.append("emergency"),
            learn_timing=lambda: self.calls.append("learn"),
            show_advanced=lambda tab: self.calls.append(("advanced", tab)),
        )
        self.panel = QuickStartPanel(self.root, self.app)
        self.panel.pack(fill="both", expand=True)
        self.root.update()

    def tearDown(self):
        self.root.destroy()
        self.panel = None
        self.app = None
        self.root = None
        gc.collect()

    def select_map_and_window(self):
        self.app.loaded_map = object()
        self.app.target = SimpleNamespace(title="Rhythia")
        self.app.vars["input_mode"].set(ABSOLUTE_MODE)
        self.panel.refresh()

    def test_initial_page_has_no_side_effects_and_blocks_unprepared_ready(self):
        self.assertEqual(self.calls, [])
        self.assertTrue(self.panel.ready_button.instate(["disabled"]))
        self.assertIn("First choose", self.panel.readiness_var.get())
        self.assertIn(".sspm", self.panel.map_var.get())
        self.assertTrue(self.panel.stop_button.instate(["!disabled"]))
        self.assertTrue(self.panel.emergency_button.instate(["!disabled"]))

    def test_buttons_delegate_only_explicit_user_actions(self):
        self.panel.map_button.invoke()
        self.panel.setup_button.invoke()
        self.select_map_and_window()
        self.panel.ready_button.invoke()
        self.panel.stop_button.invoke()
        self.panel.emergency_button.invoke()
        self.assertEqual(self.calls, ["pick", "setup", "ready", "stop", "emergency"])

    def test_map_summary_and_game_window_are_inert_bounded_text(self):
        self.app.chart.title = "Long\nTitle\u202e" + "X" * 300
        self.app.target = SimpleNamespace(title="Rhythia\nWindow\u202e")
        self.app.loaded_map = object()
        self.panel.refresh()
        self.assertIn("3 notes · 0:12", self.panel.map_var.get())
        self.assertLess(len(self.panel.map_var.get()), 150)
        self.assertNotIn("\n", self.panel.map_var.get())
        self.assertNotIn("\u202e", self.panel.target_var.get())

    def test_pending_damaged_map_is_never_reported_ready(self):
        self.select_map_and_window()
        self.app.pending_map = "broken.sspm"
        self.panel.refresh()
        self.assertTrue(self.panel.ready_button.instate(["disabled"]))
        self.assertIn("could not be loaded", self.panel.map_var.get())

    def test_calibration_and_startup_keep_stop_controls_available(self):
        self.select_map_and_window()
        for field, text in (("calibration_pending", "guided capture"), ("live_preparing", "Preparing")):
            setattr(self.app, field, True)
            self.panel.refresh()
            self.assertTrue(self.panel.ready_button.instate(["disabled"]))
            self.assertTrue(self.panel.setup_button.instate(["disabled"]))
            self.assertTrue(self.panel.emergency_button.instate(["!disabled"]))
            self.assertIn(text, self.panel.readiness_var.get())
            setattr(self.app, field, False)

    def test_active_live_and_preview_states_explain_stop(self):
        self.select_map_and_window()
        self.snapshot.state = PlaybackState.RUNNING
        self.panel.refresh()
        self.assertIn("Preview is active", self.panel.readiness_var.get())
        self.app.active_run_live = True
        self.panel.refresh()
        self.assertIn("Mouse playback is active", self.panel.readiness_var.get())
        self.assertTrue(self.panel.ready_button.instate(["disabled"]))

    def test_hotkey_failure_can_be_retried_by_ready_button(self):
        self.select_map_and_window()
        self.app.hotkeys.last_error = "Registration conflict"
        self.panel.refresh()
        self.assertIn("hotkeys are unavailable", self.panel.readiness_var.get())
        self.assertTrue(self.panel.ready_button.instate(["!disabled"]))
        self.app.hotkeys.last_error = None
        self.app.hotkey_signature = ()
        self.panel.refresh()
        self.assertIn("hotkeys are unavailable", self.panel.readiness_var.get())

    def test_live_ready_message_requires_current_global_hotkeys(self):
        self.select_map_and_window()
        self.app.live_requested = True
        self.panel.refresh()
        self.assertIn("Ready for a manual start", self.panel.readiness_var.get())
        self.assertIn("Ctrl+Alt+F9", self.panel.readiness_var.get())
        self.assertEqual(self.calls, [])

    def test_relative_selection_uses_same_setting_and_explains_reset(self):
        self.panel.mode_selector.set(RELATIVE_MODE)
        self.panel.refresh()
        self.assertEqual(self.app.vars["input_mode"].get(), RELATIVE_MODE)
        self.assertIn("before every start", self.panel.mode_hint_var.get())
        self.assertIn("Relative mode settings", self.panel.mode_hint_var.get())

    def test_edits_after_ready_require_applying_again(self):
        self.select_map_and_window()
        self.app.live_requested = True
        for key, value in (("countdown_s", "3"), ("offset_ms", "10"), ("input_mode", RELATIVE_MODE),
                           ("speed", "4"), ("smooth_motion", False)):
            original = self.app.vars[key].get()
            self.app.vars[key].set(value)
            self.panel.refresh()
            self.assertIn("apply these choices", self.panel.readiness_var.get())
            self.app.vars[key].set(original)
        self.app.vars["countdown_s"].set("unfinished number")
        self.panel.refresh()
        self.assertIn("apply these choices", self.panel.readiness_var.get())

    def test_timing_guide_matches_start_delay_and_configured_hotkeys(self):
        self.assertIn("F6 at song timeline start", self.panel.guide_var.get())
        self.app.vars["countdown_s"].set("3")
        self.app.settings.start_hotkey = "Ctrl+S"
        self.app.settings.stop_hotkey = "Ctrl+X"
        self.panel.refresh()
        self.assertIn("Ctrl+S as you start the game", self.panel.guide_var.get())
        self.assertIn("Ctrl+X stops", self.panel.guide_var.get())
        self.assertIn("does not start audio", self.panel.guide_var.get())
        self.app.vars["countdown_s"].set("invalid")
        self.panel.refresh()  # Editing timing never causes a refresh exception.

    def test_learning_requires_map_but_not_window_and_only_delegates_explicit_click(self):
        self.assertTrue(self.panel.learn_button.instate(["disabled"]))
        self.app.loaded_map = object()
        self.panel.refresh()
        self.assertIsNone(self.app.target)
        self.assertTrue(self.panel.learn_button.instate(["!disabled"]))
        self.assertEqual(self.calls, [])
        self.panel.learn_button.invoke()
        self.assertEqual(self.calls, ["learn"])
        self.assertIn("F6 as you start the game", self.panel.learning_hint_var.get())
        self.assertIn("SHOULD BE HIT, not when it first appears", self.panel.learning_hint_var.get())
        self.assertIn("no mouse input", self.panel.learning_hint_var.get())

    def test_learning_states_disable_ready_and_show_current_step_without_hiding_stop(self):
        self.select_map_and_window()
        for state, expected in (("armed", "F6 as you start the game"),
                                ("waiting", "F9 when the first note SHOULD BE HIT")):
            self.app.timing_learning = state
            self.panel.refresh()
            self.assertTrue(self.panel.ready_button.instate(["disabled"]))
            self.assertTrue(self.panel.setup_button.instate(["disabled"]))
            self.assertTrue(self.panel.learn_button.instate(["!disabled"]))
            self.assertTrue(self.panel.emergency_button.instate(["!disabled"]))
            self.assertIn(expected, self.panel.learning_hint_var.get())
            self.assertIn("Stop cancels", self.panel.readiness_var.get())
        self.app.timing_learning = "off"
        self.panel.refresh()
        self.assertTrue(self.panel.ready_button.instate(["!disabled"]))

    def test_speed_smoothness_and_next_note_are_visible_shared_settings(self):
        self.app.vars["speed"].set("4")
        self.assertEqual(self.panel.speed_selector.get(), "4")
        self.panel.speed_selector.set("1")
        self.assertEqual(self.app.vars["speed"].get(), "1")
        self.panel.smooth_checkbutton.invoke()
        self.assertFalse(self.app.vars["smooth_motion"].get())
        self.assertEqual(self.panel.next_note_label.cget("textvariable"), str(self.app.next_note_var))
        self.app.next_note_var.set("Next note in 0.125s · first map note 0.491s")
        self.root.update()
        self.assertIn("added to every note", self.panel._delay_help_var.get())

    def test_page_fits_beginner_window_without_hiding_stop(self):
        self.assertLessEqual(self.panel.winfo_reqheight(), 660)
        self.assertLessEqual(self.panel.winfo_reqwidth(), 820)
        self.assertLess(self.panel.emergency_button.winfo_rooty() + self.panel.emergency_button.winfo_height(),
                        self.root.winfo_rooty() + self.root.winfo_height())

    def test_high_dpi_short_window_keeps_stops_visible_and_last_step_scrolls_into_view(self):
        self.panel.destroy()
        self.root.tk.call("tk", "scaling", 2.0)
        self.root.geometry("820x420")
        self.panel = QuickStartPanel(self.root, self.app)
        self.panel.pack(fill="both", expand=True)
        self.select_map_and_window()
        self.root.update()
        canvas = self.panel.scroll_canvas
        self.assertGreater(self.panel.scroll_content.winfo_height(), canvas.winfo_height())
        self.assertLess(canvas.yview()[1], 1)
        stop_top = self.panel.emergency_button.winfo_rooty()
        stop_bottom = stop_top + self.panel.emergency_button.winfo_height()
        self.assertLessEqual(stop_bottom, self.root.winfo_rooty() + self.root.winfo_height())
        self.assertGreaterEqual(stop_top, self.root.winfo_rooty())

        # Keyboard focus reveals the last action, without moving fixed stops.
        self.panel.ready_button.focus_force()
        self.root.update()
        self.assertGreater(canvas.yview()[0], 0)
        self.assertGreaterEqual(self.panel.ready_button.winfo_rooty(), canvas.winfo_rooty())
        self.assertLessEqual(self.panel.ready_button.winfo_rooty() + self.panel.ready_button.winfo_height(),
                             canvas.winfo_rooty() + canvas.winfo_height())
        self.assertEqual(self.panel.emergency_button.winfo_rooty(), stop_top)
        canvas.yview_moveto(1)
        self.root.update()
        self.assertAlmostEqual(canvas.yview()[1], 1, places=2)
        self.assertEqual(self.panel.emergency_button.winfo_rooty(), stop_top)

    def test_wheel_is_scoped_to_panel_and_does_not_scroll_selector(self):
        self.root.geometry("820x350")
        self.root.update()
        canvas = self.panel.scroll_canvas
        canvas.yview_moveto(0)
        self.panel.map_button.event_generate("<MouseWheel>", delta=-120)
        self.root.update()
        self.assertGreater(canvas.yview()[0], 0)
        canvas.yview_moveto(0)
        self.panel.mode_selector.event_generate("<MouseWheel>", delta=-120)
        self.root.update()
        self.assertEqual(canvas.yview()[0], 0)
        self.assertNotIn(self.panel._scroll_bindtag, self.panel.emergency_button.bindtags())
        self.assertEqual(self.root.bind_all("<MouseWheel>"), "")
        tag = self.panel._scroll_bindtag
        self.panel.destroy()
        self.assertEqual(self.root.bind_class(tag, "<MouseWheel>"), "")


if __name__ == "__main__":
    unittest.main()
