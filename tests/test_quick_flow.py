"""Quick setup wiring with real Tk and fake game/window services."""
import gc
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from rhythia_tool.app import Application
from rhythia_tool.live import ABSOLUTE_MODE
from rhythia_tool.settings import load_settings
from rhythia_tool.windows import WindowInfo
from test_sspm import build_sspm_v2


@unittest.skipUnless(os.name == 'nt' or os.environ.get('DISPLAY'), 'Tk tests require a display')
class QuickFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        with patch('rhythia_tool.app.IS_WINDOWS', False):
            self.app = Application(self.root, Path(self.temp.name) / 'settings.json')
        self.root.update()
        self.platform = patch('rhythia_tool.app.IS_WINDOWS', True)
        self.platform.start()
        self.addCleanup(self.platform.stop)
        class Keys:
            last_error = None
            def stop(self): pass
        self.app.hotkeys = Keys()
        self.app.hotkey_signature = tuple(self.app._hotkey_mapping(self.app.settings).items())
        self.target = WindowInfo(12, 345, 'Rhythia', (0, 0, 1600, 1000), 144)

    def tearDown(self):
        self.app.shutdown()
        self.app = self.root = None
        gc.collect()
        self.temp.cleanup()

    def choose_map(self):
        path = Path(self.temp.name) / 'song.sspm'
        path.write_bytes(build_sspm_v2(notes=((100, 0, 0), (200, 1, 1))))
        self.app.select_map(path)

    def begin_setup(self):
        pending = []
        original = self.root.after
        def later(ms, callback=None, *args):
            if ms == 3000:
                pending.append(callback)
                return None
            return original(ms, callback, *args)
        with patch.object(self.root, 'after', side_effect=later):
            self.app.quick_setup()
        self.assertEqual(len(pending), 1)
        return pending[0]

    def test_quick_page_is_default_and_advanced_can_be_opened_and_hidden(self):
        self.assertEqual(self.app.tabs.select(), str(self.app.quick_panel))
        self.assertEqual(len([x for x in self.app.tabs.tabs() if self.app.tabs.tab(x, 'state') == 'normal']), 1)
        self.app.show_advanced('Mouse control')
        self.assertEqual(self.app.tabs.select(), str(self.app.advanced_tabs['Mouse control']))
        self.app.advanced_var.set(False)
        self.app._toggle_advanced()
        self.assertEqual(self.app.tabs.select(), str(self.app.quick_panel))

    def test_two_corner_setup_saves_and_ready_arms_without_creating_mouse(self):
        self.choose_map()
        callback = self.begin_setup()
        self.assertTrue(self.app.calibration_pending)
        with patch('rhythia_tool.app.foreground_window', return_value=self.target), patch('rhythia_tool.app.PlayfieldSelector') as selector:
            callback()
        self.assertTrue(self.app.calibration_pending)
        args = selector.call_args.args
        args[2](100, 50, 900, 900)
        saved = load_settings(self.app.settings_path)
        self.assertEqual((saved.left, saved.top, saved.width, saved.height), (100, 50, 900, 900))
        self.assertEqual(saved.input_mode, ABSOLUTE_MODE)
        self.assertEqual(saved.dpi_percent, 150)
        self.assertEqual(self.app.target, self.target)
        self.assertFalse(self.app.live_requested)
        self.assertFalse(self.app.calibration_pending)
        self.app.quick_ready()
        self.assertTrue(self.app.live_requested)
        self.assertIsNone(self.app.live_session)
        self.assertIsNone(self.app.pending_live_start)
        self.assertIn('Ready', self.app.quick_setup_var.get())

    def test_stop_cancels_pending_capture_before_overlay_or_settings(self):
        callback = self.begin_setup()
        self.app.emergency_stop()
        with patch('rhythia_tool.app.PlayfieldSelector') as selector:
            callback()
        selector.assert_not_called()
        self.assertFalse(self.app.calibration_pending)
        self.assertFalse(self.app.settings_path.exists())

    def test_stop_invalidates_open_selector_and_prevents_stale_save(self):
        callback = self.begin_setup()
        with patch('rhythia_tool.app.foreground_window', return_value=self.target), patch('rhythia_tool.app.PlayfieldSelector') as selector:
            callback()
        selected = selector.call_args.args[2]
        self.app.emergency_stop()
        selected(100, 50, 900, 900)
        self.assertFalse(self.app.settings_path.exists())
        self.assertIsNone(self.app.target)
        self.assertFalse(self.app.live_requested)

    def test_stop_during_foreground_lookup_cannot_become_new_setup_intent(self):
        callback = self.begin_setup()
        def stop_during_lookup():
            self.app.emergency_stop()
            return self.target
        with patch('rhythia_tool.app.foreground_window', side_effect=stop_during_lookup), patch('rhythia_tool.app.PlayfieldSelector') as selector:
            callback()
        selector.assert_not_called()
        self.assertFalse(self.app.calibration_pending)
        self.assertIsNone(self.app.quick_setup_intent)
        self.assertFalse(self.app.settings_path.exists())

    def test_capture_of_tool_is_rejected_and_ready_explains_missing_map(self):
        callback = self.begin_setup()
        own = WindowInfo(12, os.getpid(), 'Tool', (0, 0, 1600, 1000), 96)
        with patch('rhythia_tool.app.foreground_window', return_value=own), patch('rhythia_tool.app.messagebox.showerror') as error:
            callback()
        error.assert_called_once()
        self.assertFalse(self.app.calibration_pending)
        with patch('rhythia_tool.app.messagebox.showerror') as error:
            self.app.quick_ready()
        self.assertIn('Choose your', error.call_args.args[1])
        self.assertFalse(self.app.live_requested)
