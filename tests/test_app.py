"""Functional Tk checks; use a real display or an isolated Xvfb server."""

import os
import gc
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from rhythia_tool.app import Application
from rhythia_tool.playback import PlaybackState
from rhythia_tool.settings import load_settings
from rhythia_tool.windows import WindowInfo
from test_sspm import build_sspm_v2


@unittest.skipUnless(os.name == "nt" or os.environ.get("DISPLAY"), "Tk functional tests require a display")
class ApplicationTests(unittest.TestCase):
    def setUp(self):
        # GUI tests exercise controls without registering real global hotkeys.
        self.windows_patch = patch("rhythia_tool.app.IS_WINDOWS", False)
        self.windows_patch.start()
        self.addCleanup(self.windows_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.app = Application(self.root, Path(self.temp.name) / "settings.json")
        self.root.update()

    def tearDown(self):
        self.app.shutdown()
        self.temp.cleanup()
        # Dispose Tcl interpreters on their owning thread before a later test's
        # scheduler can trigger cyclic collection (especially with Tcl/Tk 9).
        self.app = None
        self.root = None
        gc.collect()

    def wait_for(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Timed out waiting for expected GUI behavior")

    def test_invalid_map_gates_both_button_and_hotkey_playback(self):
        chart = Path(self.temp.name) / "unverified.sspm"
        chart.write_bytes(b"not a known Rhythia format")
        with patch("rhythia_tool.app.messagebox.showerror"):
            self.app.select_map(chart)
        self.assertEqual(str(self.app.start_button.cget("state")), "disabled")
        self.app.start()
        self.app._hotkey_action("start")
        self.assertEqual(self.app.engine.snapshot().emitted_count, 0)
        self.assertEqual(self.app.engine.snapshot().state, PlaybackState.STOPPED)
        self.assertIn("Could not load", self.app.source_var.get())
        self.app.load_synthetic()
        self.assertEqual(str(self.app.start_button.cget("state")), "normal")

    def test_valid_sspm_loads_and_runs_without_live_input(self):
        chart = Path(self.temp.name) / "fixture.sspm"
        chart.write_bytes(build_sspm_v2(notes=((100, -0.5, 0), (500, 1, 1), (1000, 2.5, 2.3))))
        self.app.select_map(chart)
        self.assertEqual(str(self.app.start_button.cget("state")), "normal")
        self.assertEqual(len(self.app.loaded_map.notes), 3)
        self.assertEqual(self.app.loaded_map.notes[0].x, -0.5)
        self.assertIsNone(self.app.pending_map)
        self.assertIn("SSPM v2", self.app.source_var.get())
        self.app.vars["countdown_s"].set("0")
        self.app.vars["speed"].set("4")
        self.app.start()
        self.wait_for(lambda: self.app.engine.snapshot().state == PlaybackState.COMPLETED)
        self.wait_for(lambda: self.app.last_note is not None)
        self.assertEqual(self.app.engine.snapshot().emitted_count, 3)

    def test_empty_sspm_cannot_start_and_new_source_discards_old_points(self):
        chart = Path(self.temp.name) / "empty.sspm"
        chart.write_bytes(build_sspm_v2(notes=()))
        self.app.events.put(("note", (self.app.preview_generation, self.app.demo.notes[0])))
        with patch("rhythia_tool.app.messagebox.showerror") as error:
            self.app.select_map(chart)
        error.assert_called_once()
        self.wait_for(lambda: self.app.events.empty())
        self.assertIsNone(self.app.last_note)
        self.assertEqual(str(self.app.start_button.cget("state")), "disabled")

    def test_settings_validation_reports_error_and_round_trips(self):
        self.app.vars["speed"].set("nan")
        with patch("rhythia_tool.app.messagebox.showerror") as error:
            self.app.save_form()
        error.assert_called_once()
        self.assertFalse(self.app.settings_path.exists())
        self.app.vars["speed"].set("1.5")
        self.app.vars["left"].set("-1920")
        self.app.save_form()
        saved = load_settings(self.app.settings_path)
        self.assertEqual(saved.speed, 1.5)
        self.assertEqual(saved.left, -1920)

    def test_threaded_preview_pause_resume_and_emergency(self):
        self.app.vars["countdown_s"].set("0")
        self.app.start()
        self.wait_for(lambda: self.app.engine.snapshot().emitted_count > 0 and self.app.last_note is not None)
        self.app.pause()
        self.assertEqual(self.app.engine.snapshot().state, PlaybackState.PAUSED)
        self.app.start()
        self.assertEqual(self.app.engine.snapshot().state, PlaybackState.RUNNING)
        self.app._hotkey_action("emergency")
        count = self.app.engine.snapshot().emitted_count
        self.wait_for(lambda: "Emergency" in self.app.status_var.get())
        self.assertEqual(self.app.engine.snapshot().state, PlaybackState.STOPPED)
        self.assertEqual(self.app.engine.snapshot().emitted_count, count)

    def test_focus_loss_stops_preview_before_emitting(self):
        with patch("rhythia_tool.app.foreground_window", return_value=None):
            with self.app.target_lock:
                self.app.target = WindowInfo(123, 456, "Test target", (0, 0, 800, 600), 96)
                self.app.guard_enabled = True
            self.app.vars["countdown_s"].set("0")
            self.app.start()
            self.wait_for(lambda: self.app.engine.snapshot().state == PlaybackState.STOPPED)
            self.assertIn("lost focus", self.app.engine.snapshot().message)
            self.assertEqual(self.app.engine.snapshot().emitted_count, 0)

    def test_source_switch_discards_queued_old_preview_points(self):
        old_generation = self.app.preview_generation
        note = self.app.demo.notes[0]
        self.app.events.put(("note", (old_generation, note)))
        self.app.load_synthetic()
        self.wait_for(lambda: self.app.events.empty())
        self.assertIsNone(self.app.last_note)

    def test_worker_and_gui_complete_the_entire_preview(self):
        self.app.vars["countdown_s"].set("0")
        self.app.vars["speed"].set("4")
        self.app.start()
        self.wait_for(lambda: self.app.engine.snapshot().state == PlaybackState.COMPLETED, timeout=7)
        self.wait_for(lambda: self.app.last_note == self.app.demo.notes[-1])
        snapshot = self.app.engine.snapshot()
        self.assertEqual(snapshot.emitted_count, len(self.app.demo.notes))
        self.assertLessEqual(snapshot.max_lateness_ms, 250)


if __name__ == "__main__":
    unittest.main()
