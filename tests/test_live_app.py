"""Real Tk/worker integration with injected input: never calls Windows APIs."""

import gc
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from rhythia_tool.app import Application
from rhythia_tool.live import ABSOLUTE_MODE, RELATIVE_MODE
from rhythia_tool.playback import PlaybackState
from rhythia_tool.windows import WindowInfo
from test_sspm import build_sspm_v2


class FakeHotkeys:
    last_error = None

    def stop(self):
        pass


class FakeMouse:
    """A focus-aware, permanently closeable list sink, with no OS input."""

    def __init__(self, factory, target):
        self.factory = factory
        self.target = target
        self.events = []
        self.closed = False

    def check_target(self):
        if self.closed:
            raise RuntimeError("Mouse session is closed")
        if not self.factory.focused:
            raise RuntimeError("Captured window lost focus")
        if not self.factory.geometry_matches:
            raise RuntimeError("Captured window geometry or DPI changed")

    def move_absolute(self, x, y):
        self.check_target()
        self.events.append(("absolute", x, y))

    def move_relative(self, dx, dy):
        self.check_target()
        self.events.append(("relative", dx, dy))

    def close(self):
        self.closed = True


class FakeMouseFactory:
    def __init__(self):
        self.focused = True
        self.geometry_matches = True
        self.created = []

    def __call__(self, target):
        mouse = FakeMouse(self, target)
        self.created.append(mouse)
        return mouse

    @property
    def events(self):
        return [event for mouse in self.created for event in mouse.events]


@unittest.skipUnless(os.name == "nt" or os.environ.get("DISPLAY"), "Tk functional tests require a display")
class LiveApplicationTests(unittest.TestCase):
    def setUp(self):
        # Construction must never register real global hotkeys, including on
        # Windows. The subsequent platform override exercises only injected I/O.
        self.windows_patch = patch("rhythia_tool.app.IS_WINDOWS", False)
        self.windows_patch.start()
        self.addCleanup(self.windows_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.factory = FakeMouseFactory()
        self.root = tk.Tk()
        self.app = Application(self.root, Path(self.temp.name) / "settings.json", mouse_factory=self.factory)
        self.root.update()
        self.live_patch = patch("rhythia_tool.app.IS_WINDOWS", True)
        self.live_patch.start()
        self.addCleanup(self.live_patch.stop)
        self.app.hotkeys = FakeHotkeys()
        self.map_number = 0

    def tearDown(self):
        self.app.shutdown()
        self.temp.cleanup()
        # Tcl must be collected on its owning main thread; a later scheduler
        # triggering cyclic collection can otherwise abort with Tcl/Tk 9.
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
        self.fail("Timed out waiting for expected live GUI behavior")

    def tick_for(self, seconds=0.05):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.003)

    def select_fixture(self, notes=((100, 0, 0), (400, 1, 1), (700, 2, 2))):
        self.map_number += 1
        path = Path(self.temp.name) / f"fixture-{self.map_number}.sspm"
        path.write_bytes(build_sspm_v2(notes=notes))
        self.app.select_map(path)
        return path

    def configure_live(self, *, countdown=0, input_mode=ABSOLUTE_MODE, notes=None):
        self.select_fixture() if notes is None else self.select_fixture(notes)
        for name, value in {
            "left": 10, "top": 20, "width": 300, "height": 300,
            "input_mode": input_mode, "countdown_s": countdown,
            "speed": 4, "live_lateness_ms": 250,
        }.items():
            self.app.vars[name].set(str(value))
        self.app.settings = self.app._form_settings()
        self.app.hotkey_signature = tuple(self.app._hotkey_mapping(self.app.settings).items())
        self.app.target = WindowInfo(123, 456, "Rhythia", (0, 0, 800, 600), 96)
        self.app.output_mode_var.set("Windows mouse")
        self.app.set_output_mode()

    def start_live(self):
        self.app._hotkey_action("start")
        self.wait_for(lambda: not self.app.live_preparing)

    def assert_stopped(self):
        self.assertEqual(self.app.engine.snapshot().state, PlaybackState.STOPPED)
        self.assertFalse(self.app.active_run_live)
        self.assertIsNone(self.app.live_session)

    def test_default_preview_and_selecting_live_never_auto_starts(self):
        self.assertFalse(self.app.live_requested)
        self.assertEqual(self.app.output_mode_var.get(), "Preview")
        self.configure_live()
        self.tick_for()
        self.assert_stopped()
        self.assertEqual(self.factory.created, [])
        # The focused GUI button applies settings; only the explicit global
        # start command creates a guarded input session for the focused game.
        self.app.start()
        self.tick_for()
        self.assert_stopped()
        self.assertEqual(self.factory.created, [])

    def test_complete_live_run_uses_raw_quantum_points_not_preview_normalization(self):
        self.configure_live(notes=((100, -0.5, 2.5), (400, 1.25, -0.25), (700, 2.5, -0.5)))
        self.start_live()
        self.wait_for(lambda: self.app.engine.snapshot().state == PlaybackState.COMPLETED)
        self.wait_for(lambda: self.app.last_note == self.app.chart.notes[-1])
        self.assertEqual(self.factory.events, [("absolute", 10, 319), ("absolute", 184, 45), ("absolute", 309, 20)])
        self.assertEqual(self.app.engine.snapshot().emitted_count, 3)
        self.assertEqual(len(self.factory.created), 1)
        self.assertTrue(self.factory.created[0].closed)
        self.assertFalse(self.app.active_run_live)

    def test_live_pause_is_a_stop_and_explicit_start_creates_a_fresh_session(self):
        self.configure_live(countdown=1)
        self.start_live()
        original = self.factory.created[-1]
        self.app.pause()
        self.assert_stopped()
        self.assertTrue(original.closed)
        self.assertIn("Restart the song", self.app.engine.snapshot().message)
        self.tick_for()
        self.assertEqual(original.events, [])
        self.start_live()
        self.assertEqual(len(self.factory.created), 2)
        self.assertIsNot(self.factory.created[-1], original)
        self.assertEqual(self.app.engine.snapshot().state, PlaybackState.COUNTDOWN)
        self.assertTrue(original.closed)

    def test_complete_relative_run_tracks_raw_notes_from_manual_anchor(self):
        self.configure_live(input_mode=RELATIVE_MODE, notes=((100, -0.5, -0.5), (400, 1, 1), (700, 2.5, 2.5)))
        self.app.vars["counts_per_unit_x"].set("100")
        self.app.vars["counts_per_unit_y"].set("80")
        self.app.settings = self.app._form_settings()
        self.start_live()
        self.wait_for(lambda: self.app.engine.snapshot().state == PlaybackState.COMPLETED)
        self.assertEqual(self.factory.events, [("relative", -150, -120), ("relative", 150, 120), ("relative", 150, 120)])
        self.assertTrue(self.factory.created[-1].closed)

    def test_hotkey_thread_can_start_and_emergency_stop_without_tk_access(self):
        self.configure_live(countdown=1)
        for action in ("start", "emergency"):
            thread = threading.Thread(target=self.app._hotkey_action, args=(action,))
            thread.start()
            # Do not pump Tk while waiting: accidental Tk access from this
            # callback would block until the main loop runs, and fail this test.
            thread.join(timeout=0.5)
            self.assertFalse(thread.is_alive(), f"{action} touched or blocked on the Tk thread")
            if action == "start":
                self.wait_for(lambda: not self.app.live_preparing)
        self.assert_stopped()
        self.assertEqual(len(self.factory.created), 1)
        self.assertEqual(self.factory.events, [])
        self.assertTrue(self.factory.created[-1].closed)

    def test_pause_hotkey_and_stop_and_emergency_cancel_countdown_without_input(self):
        self.configure_live(countdown=1)
        for action in ("pause", "stop", "emergency"):
            with self.subTest(action=action):
                self.start_live()
                mouse = self.factory.created[-1]
                self.app._hotkey_action(action)
                self.assert_stopped()
                self.assertTrue(mouse.closed)
                self.tick_for()
                self.assertEqual(mouse.events, [])

    def test_emergency_after_first_note_prevents_all_remaining_movement(self):
        self.configure_live(notes=((100, 0, 0), (1500, 1, 1), (2000, 2, 2)))
        self.start_live()
        self.wait_for(lambda: len(self.factory.events) == 1)
        self.app.emergency_stop()
        self.assert_stopped()
        self.assertTrue(self.factory.created[-1].closed)
        self.tick_for(0.55)
        self.assertEqual(len(self.factory.events), 1)
        self.assertIn("Emergency", self.app.engine.snapshot().message)

    def test_worker_focus_loss_during_countdown_latches_stop_without_input(self):
        self.configure_live(countdown=1)
        self.start_live()
        mouse = self.factory.created[-1]
        self.factory.focused = False
        self.wait_for(lambda: self.app.engine.snapshot().state == PlaybackState.STOPPED)
        self.assert_stopped()
        self.assertTrue(mouse.closed)
        self.assertIn("lost focus", self.app.engine.snapshot().message)
        self.factory.focused = True
        self.tick_for()
        self.assert_stopped()
        self.assertEqual(self.factory.events, [])

    def test_changed_target_geometry_stops_active_run_without_further_input(self):
        self.configure_live(notes=((100, 0, 0), (1500, 1, 1), (2000, 2, 2)))
        self.start_live()
        self.wait_for(lambda: len(self.factory.events) == 1)
        self.factory.geometry_matches = False
        self.wait_for(lambda: self.app.engine.snapshot().state == PlaybackState.STOPPED)
        self.assert_stopped()
        self.tick_for(0.55)
        self.assertEqual(len(self.factory.events), 1)
        self.assertTrue(self.factory.created[-1].closed)

    def test_initial_focus_or_geometry_failure_prevents_even_zero_time_note(self):
        self.configure_live(notes=((0, 0, 0), (100, 1, 1)))
        for attribute in ("focused", "geometry_matches"):
            with self.subTest(attribute=attribute):
                setattr(self.factory, attribute, False)
                self.start_live()
                self.assert_stopped()
                self.assertEqual(self.factory.events, [])
                self.assertTrue(self.factory.created[-1].closed)
                setattr(self.factory, attribute, True)

    def test_missing_target_hotkeys_mode_and_platform_block_before_mouse_construction(self):
        self.configure_live()
        target = self.app.target
        signature = self.app.hotkey_signature
        checks = (
            ("target", None),
            ("target", WindowInfo(123, os.getpid(), "Tool", (0, 0, 800, 600), 96)),
            ("hotkeys", None),
            ("hotkey_signature", None),
            ("calibration_pending", True),
        )
        for name, value in checks:
            with self.subTest(prerequisite=name, value=value):
                previous = getattr(self.app, name)
                setattr(self.app, name, value)
                self.start_live()
                self.assert_stopped()
                self.assertEqual(self.factory.created, [])
                setattr(self.app, name, previous)
        self.assertEqual(self.app.target, target)
        self.assertEqual(self.app.hotkey_signature, signature)
        self.app.hotkeys.last_error = "Registration failed"
        self.start_live()
        self.assert_stopped()
        self.assertEqual(self.factory.created, [])
        self.app.hotkeys.last_error = None
        self.app.vars["input_mode"].set("Unverified")
        self.app.settings = self.app._form_settings()
        self.start_live()
        self.assert_stopped()
        self.assertEqual(self.factory.created, [])
        with patch("rhythia_tool.app.IS_WINDOWS", False):
            self.start_live()
        self.assert_stopped()
        self.assertEqual(self.factory.created, [])

    def test_out_of_frame_raw_note_or_playfield_rejects_entire_map_before_input(self):
        self.configure_live(notes=((100, 0, 0), (400, 2.5001, 1)))
        self.start_live()
        self.assert_stopped()
        self.assertEqual(self.factory.created, [])
        self.configure_live()
        self.app.vars["left"].set("700")
        self.app.settings = self.app._form_settings()
        self.start_live()
        self.assert_stopped()
        self.assertEqual(self.factory.created, [])

    def test_synthetic_demo_cannot_be_armed_as_live(self):
        self.configure_live()
        self.app.load_synthetic()
        self.assertFalse(self.app.live_requested)
        self.app.output_mode_var.set("Windows mouse")
        self.app.set_output_mode()
        self.start_live()
        self.assert_stopped()
        self.assertEqual(self.factory.created, [])

    def test_failed_new_map_cancels_and_cannot_reuse_previous_live_map(self):
        self.configure_live(countdown=1)
        self.start_live()
        original = self.factory.created[-1]
        damaged = Path(self.temp.name) / "damaged.sspm"
        damaged.write_bytes(b"damaged")
        with patch("rhythia_tool.app.messagebox.showerror"):
            self.app.select_map(damaged)
        self.assert_stopped()
        self.assertTrue(original.closed)
        self.start_live()
        self.assertEqual(len(self.factory.created), 1)
        self.assertEqual(self.factory.events, [])

    def test_output_mode_and_source_changes_close_live_sessions(self):
        self.configure_live(countdown=1)
        for change in ("preview", "map", "synthetic"):
            with self.subTest(change=change):
                self.app.output_mode_var.set("Windows mouse")
                self.app.set_output_mode()
                self.start_live()
                original = self.factory.created[-1]
                if change == "preview":
                    self.app.output_mode_var.set("Preview")
                    self.app.set_output_mode()
                elif change == "map":
                    self.select_fixture()
                else:
                    self.app.load_synthetic()
                self.assertIn(self.app.engine.snapshot().state, (PlaybackState.IDLE, PlaybackState.STOPPED))
                self.assertFalse(self.app.active_run_live)
                self.assertIsNone(self.app.live_session)
                self.assertTrue(original.closed)
                self.tick_for()
                self.assertEqual(original.events, [])

    def test_saving_changed_settings_closes_session_and_does_not_persist_live_arming(self):
        self.configure_live(countdown=1)
        self.start_live()
        original = self.factory.created[-1]
        self.app.vars["offset_ms"].set("25")
        self.app.save_form()
        self.assert_stopped()
        self.assertTrue(original.closed)
        saved = json.loads(self.app.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["offset_ms"], 25)
        self.assertNotIn("live_requested", saved)
        self.assertNotIn("output_mode", saved)
        self.assertNotIn("target", saved)
        self.tick_for()
        self.assertEqual(original.events, [])

    def test_emergency_cancels_preparation_without_waiting_for_factory(self):
        self.configure_live(countdown=1)
        entered, release = threading.Event(), threading.Event()
        factory = self.factory

        def slow_factory(target):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("Test preparation release timed out")
            return factory(target)

        self.app.mouse_factory = slow_factory
        self.app._hotkey_action("start")
        self.assertTrue(entered.wait(1))
        try:
            thread = threading.Thread(target=self.app.emergency_stop)
            thread.start()
            thread.join(0.5)
            self.assertFalse(thread.is_alive(), "Emergency waited for map preparation")
            self.assert_stopped()
        finally:
            release.set()
        self.wait_for(lambda: bool(factory.created) and factory.created[-1].closed)
        self.tick_for()
        self.assertEqual(factory.events, [])
        self.assert_stopped()

    def test_start_waiting_for_control_cannot_survive_later_emergency(self):
        self.configure_live(countdown=1)
        observed = threading.Event()
        original = self.app._current_intent

        def observe_intent():
            result = original()
            if threading.current_thread().name == "waiting-start":
                observed.set()
            return result

        with patch.object(self.app, "_current_intent", side_effect=observe_intent):
            with self.app.control_lock:
                thread = threading.Thread(target=self.app._hotkey_action,
                                          args=("start",), name="waiting-start")
                thread.start()
                self.assertTrue(observed.wait(1))
                self.app.emergency_stop()
            thread.join(1)
            self.assertFalse(thread.is_alive())
        self.tick_for()
        self.assert_stopped()
        self.assertEqual(self.factory.created, [])

    def test_stop_intent_blocks_next_event_before_control_lock_is_available(self):
        self.configure_live(countdown=1)
        self.start_live()
        with self.app.control_lock:
            thread = threading.Thread(target=self.app.emergency_stop)
            thread.start()
            self.assertTrue(self.app.stop_signal.wait(1))
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                self.app._emit_note(self.app.chart.notes[0])
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.factory.events, [])
        self.assert_stopped()

    def test_absolute_probe_emits_one_center_point_and_closes(self):
        self.configure_live()
        self.app._hotkey_action("probe")
        self.assert_stopped()
        self.assertEqual(self.factory.events, [("absolute", 160, 170)])
        self.assertTrue(self.factory.created[-1].closed)
        self.tick_for()
        self.assertEqual(len(self.factory.events), 1)

    def test_relative_x_and_y_probes_use_manual_anchor_and_signed_counts(self):
        self.configure_live(input_mode=RELATIVE_MODE)
        self.app.vars["counts_per_unit_x"].set("125")
        self.app.vars["counts_per_unit_y"].set("75")
        self.app.vars["anchor_x"].set("2")
        self.app.vars["anchor_y"].set("1")
        self.app.settings = self.app._form_settings()
        for axis, expected in (("X", ("relative", -125, 0)), ("Y", ("relative", 0, 75))):
            with self.subTest(axis=axis):
                self.app.probe_axis_var.set(axis)
                self.app.set_probe_axis()
                self.app._hotkey_action("probe")
                self.assert_stopped()
                self.assertEqual(self.factory.created[-1].events, [expected])
                self.assertTrue(self.factory.created[-1].closed)

    def test_pending_calibration_blocks_both_start_and_probe(self):
        self.configure_live()
        self.app.calibration_pending = True
        for action in ("start", "probe"):
            self.app._hotkey_action(action)
            self.assert_stopped()
            self.assertEqual(self.factory.created, [])


if __name__ == "__main__":
    unittest.main()
