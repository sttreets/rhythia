"""Real Tk click handling with no game access or injected Windows input."""

import ctypes
import gc
import os
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import patch

from rhythia_tool.calibration_ui import PlayfieldSelector, _set_window_position
from rhythia_tool.windows import WindowInfo


@unittest.skipUnless(os.name == "nt" or os.environ.get("DISPLAY"), "Tk checks require a display")
class PlayfieldSelectorTests(unittest.TestCase):
    def setUp(self):
        self.platform = patch("rhythia_tool.calibration_ui.IS_WINDOWS", False)
        self.platform.start()
        self.addCleanup(self.platform.stop)
        self.root = tk.Tk()
        self.root.geometry("200x100+20+20")
        self.root.update()
        self.target = WindowInfo(123, 456, "Fake game", (100, 50, 900, 650), 144)
        self.selected = []
        self.cancelled = []
        self.selector = None

    def tearDown(self):
        if self.selector is not None:
            self.selector.cancel()
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        self.selector = None
        self.root = None
        gc.collect()

    def make_selector(self):
        def selected(*bounds):
            self.assertFalse(self.selector.window.winfo_exists())
            self.selected.append(bounds)

        def cancelled():
            self.assertFalse(self.selector.window.winfo_exists())
            self.cancelled.append(True)

        self.selector = PlayfieldSelector(self.root, self.target, selected, cancelled)
        self.root.update()
        return self.selector

    def click(self, x, y):
        canvas = self.selector.canvas
        canvas.event_generate("<Button-1>", x=x - canvas.winfo_rootx(), y=y - canvas.winfo_rooty(),
                              rootx=x, rooty=y)
        self.root.update()

    def test_two_clicks_return_physical_screen_pixels_after_destroy(self):
        selector = self.make_selector()
        self.assertIn("TOP-LEFT", selector.instruction)
        self.click(140, 90)
        self.assertIn("BOTTOM-RIGHT", selector.instruction)
        self.click(739, 539)
        self.assertEqual(self.selected, [(140, 90, 600, 450)])
        self.assertEqual(self.cancelled, [])
        self.assertTrue(selector.closed)

    def test_motion_previews_rectangle_without_accepting_it(self):
        selector = self.make_selector()
        self.click(150, 100)
        selector.canvas.event_generate("<Motion>", x=300, y=250, rootx=400, rooty=300)
        self.root.update()
        rectangle = selector.canvas.find_withtag("selection")[0]
        self.assertEqual(selector.canvas.coords(rectangle), [50.0, 50.0, 300.0, 250.0])
        self.assertFalse(selector.closed)
        self.assertEqual(self.selected, [])

    def test_target_bounds_reject_outside_points_and_allow_edge_pixels(self):
        selector = self.make_selector()
        for point in ((99, 100), (200, 49), (900, 200), (200, 650)):
            selector._click(SimpleNamespace(x_root=point[0], y_root=point[1]))
            self.assertIsNone(selector.first)
        self.click(100, 50)
        selector._click(SimpleNamespace(x_root=901, y_root=649))
        self.assertFalse(selector.closed)
        self.click(899, 649)
        self.assertEqual(self.selected, [(100, 50, 800, 600)])

    def test_reversed_and_small_second_corner_can_be_retried(self):
        selector = self.make_selector()
        self.click(200, 150)
        for point in ((199, 200), (250, 149), (201, 200), (250, 151)):
            self.click(*point)
            self.assertFalse(selector.closed)
            self.assertEqual(selector.first, (200, 150))
            self.assertIn("at least 3×3", selector.instruction)
        self.click(202, 152)
        self.assertEqual(self.selected, [(200, 150, 3, 3)])

    def test_negative_monitor_uses_native_frame_position_and_unscaled_clicks(self):
        self.target = WindowInfo(123, 456, "Left monitor", (-1280, -100, 0, 924), 192)
        with patch("rhythia_tool.calibration_ui.IS_WINDOWS", True), \
                patch("rhythia_tool.calibration_ui._set_window_position") as position:
            selector = self.make_selector()
        position.assert_called_once_with(int(selector.window.wm_frame(), 0), -1280, -100, 1280, 1024)
        selector._click(SimpleNamespace(x_root=-1200, y_root=-50))
        selector._click(SimpleNamespace(x_root=-601, y_root=549))
        self.assertEqual(self.selected, [(-1200, -50, 600, 600)])

    def test_escape_cancels_without_grabbing_global_input(self):
        selector = self.make_selector()
        self.assertIsNone(self.root.grab_current())
        selector.window.event_generate("<Escape>")
        self.root.update()
        selector.cancel()
        self.assertEqual(self.cancelled, [True])
        self.assertEqual(self.selected, [])

    def test_close_cancel_and_late_click_are_idempotent(self):
        selector = self.make_selector()
        self.click(140, 90)
        selector.cancel()
        selector.cancel()
        selector._click(SimpleNamespace(x_root=400, y_root=300))
        self.assertEqual(self.cancelled, [True])
        self.assertEqual(self.selected, [])

    def test_destroying_overlay_notifies_cancel_once(self):
        selector = self.make_selector()
        selector.window.destroy()
        selector.cancel()
        self.assertEqual(self.cancelled, [True])

    def test_root_destruction_cancels_without_tcl_errors(self):
        selector = self.make_selector()
        self.root.destroy()
        selector.cancel()
        self.assertEqual(self.cancelled, [True])

    def test_invalid_target_rejected_before_creating_overlay(self):
        for rect in ((0, 0, 2, 10), (0, 0, 10, 2), (0, 0, 0, 0), (False, 0, 10, 10)):
            target = WindowInfo(123, 456, "Bad bounds", rect, 96)
            with self.assertRaisesRegex(ValueError, "bounds"):
                PlayfieldSelector(self.root, target, self.selected.append, lambda: None)
        self.assertEqual(self.root.winfo_children(), [])

    def test_native_placement_failure_destroys_overlay(self):
        with patch("rhythia_tool.calibration_ui.IS_WINDOWS", True), \
                patch("rhythia_tool.calibration_ui._set_window_position", side_effect=RuntimeError("Position failed")):
            with self.assertRaisesRegex(RuntimeError, "Position failed"):
                self.make_selector()
        self.assertEqual(self.root.winfo_children(), [])


class NativePlacementTests(unittest.TestCase):
    def test_set_window_position_declares_pointer_sized_signature(self):
        class Function:
            def __call__(self, *args):
                self.arguments = args
                return 1

        function = Function()
        native = SimpleNamespace(SetWindowPos=function)
        with patch.object(ctypes, "WinDLL", return_value=native, create=True) as library:
            _set_window_position(0x12345678, -1920, -200, 1920, 1080)
        library.assert_called_once_with("user32", use_last_error=True)
        self.assertEqual(function.arguments[0], 0x12345678)
        self.assertEqual(function.arguments[2:], (-1920, -200, 1920, 1080, 0x0040))
        self.assertEqual(function.argtypes[:2], [ctypes.wintypes.HWND, ctypes.wintypes.HWND])
        self.assertEqual(function.argtypes[2:6], [ctypes.c_int] * 4)
        self.assertEqual(function.restype, ctypes.wintypes.BOOL)

    def test_native_failure_has_clear_error(self):
        function = lambda *args: 0
        with patch.object(ctypes, "WinDLL", return_value=SimpleNamespace(SetWindowPos=function), create=True), \
                patch.object(ctypes, "get_last_error", return_value=5, create=True):
            with self.assertRaisesRegex(RuntimeError, "Windows error 5"):
                _set_window_position(123, 0, 0, 800, 600)


if __name__ == "__main__":
    unittest.main()
