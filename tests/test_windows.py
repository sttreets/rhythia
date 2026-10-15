"""Portable hotkey validation; no global registration or mouse input occurs."""

import ctypes
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rhythia_tool import windows


class HotkeyParserTests(unittest.TestCase):
    def test_modifiers_and_function_key(self):
        hotkey = windows.parse_hotkey(" shift + CTRL + alt + F6 ")
        self.assertEqual(hotkey.virtual_key, 0x75)
        self.assertEqual(
            hotkey.modifiers,
            windows.MOD_SHIFT | windows.MOD_CONTROL | windows.MOD_ALT,
        )
        self.assertEqual(hotkey.canonical, "Ctrl+Alt+Shift+F6")

    def test_supported_keys(self):
        for text, key, canonical in (
            ("F1", 0x70, "F1"),
            ("f12", 0x7B, "F12"),
            ("Win+a", ord("A"), "Win+A"),
            ("Alt+0", ord("0"), "Alt+0"),
            ("Control+9", ord("9"), "Ctrl+9"),
            ("ESC", 0x1B, "Escape"),
            ("Shift+Space", 0x20, "Shift+Space"),
        ):
            with self.subTest(text=text):
                parsed = windows.parse_hotkey(text)
                self.assertEqual(parsed.virtual_key, key)
                self.assertEqual(parsed.canonical, canonical)

    def test_rejects_invalid_combinations(self):
        for text in (
            "", " ", "+F6", "Ctrl+", "Ctrl++F6", "F13", "F0", "F01",
            "Ctrl+Alt", "Ctrl+Ctrl+F6", "Ctrl+Control+F6", "F6+Ctrl",
            "Ctrl+A+B", "Cmd+F6", "Left", "é", None,
        ):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    windows.parse_hotkey(text)

    def test_aliases_have_identical_identity(self):
        self.assertEqual(
            windows.parse_hotkey("Ctrl+Alt+F6"),
            windows.parse_hotkey("ALT+Control+f6"),
        )


class HotkeyOwnershipTests(unittest.TestCase):
    def test_duplicate_combinations_are_rejected_before_windows_calls(self):
        with self.assertRaisesRegex(ValueError, "assigned to both"):
            windows.GlobalHotkeys(
                {"start": "Ctrl+F6", "stop": "control+f6"}, lambda action: None
            )

    def test_empty_configuration_and_action_are_rejected(self):
        for mapping in ({}, {"": "F6"}, {" ": "F6"}):
            with self.subTest(mapping=mapping):
                with self.assertRaises(ValueError):
                    windows.GlobalHotkeys(mapping, lambda action: None)

    def test_callback_must_be_callable(self):
        with self.assertRaises(TypeError):
            windows.GlobalHotkeys({"emergency": "Ctrl+Alt+F10"}, None)

    def test_f12_is_reserved_even_with_modifiers(self):
        for value in ("F12", "Ctrl+Alt+F12", "Shift+f12"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "Windows reserves it for debuggers"):
                    windows.GlobalHotkeys({"emergency": value}, lambda action: None)

    def test_stop_is_safe_before_start_and_can_repeat(self):
        listener = windows.GlobalHotkeys({"emergency": "Ctrl+Alt+F10"}, lambda action: None)
        listener.stop()
        listener.stop()
        self.assertIsNone(listener.last_error)

    def test_unavailable_features_fail_clearly_on_non_windows(self):
        with patch.object(windows, "IS_WINDOWS", False):
            listener = windows.GlobalHotkeys({"emergency": "Ctrl+Alt+F10"}, lambda action: None)
            for feature in (listener.start, windows.cursor_position, windows.foreground_window):
                with self.subTest(feature=feature):
                    with self.assertRaisesRegex(RuntimeError, "requires Windows"):
                        feature()
            self.assertIn("not applicable", windows.set_dpi_awareness())


class _FakeUser32:
    """Exercise ownership and dispatch, without registering any real hotkeys."""

    def __init__(self, fail_identifier=None):
        self.fail_identifier = fail_identifier
        self.messages = queue.Queue()
        self.registered = []
        self.unregistered = []
        self.owner_threads = []
        self.cleaned_up = threading.Event()

    def PeekMessageW(self, *args):
        return 0

    def RegisterHotKey(self, hwnd, identifier, modifiers, key):
        self.owner_threads.append(threading.get_ident())
        self.registered.append((identifier, modifiers, key))
        return identifier != self.fail_identifier

    def UnregisterHotKey(self, hwnd, identifier):
        self.owner_threads.append(threading.get_ident())
        self.unregistered.append(identifier)
        self.cleaned_up.set()
        return 1

    def GetMessageW(self, pointer, *args):
        kind, identifier = self.messages.get(timeout=2)
        if kind == windows.WM_QUIT:
            return 0
        message = ctypes.cast(pointer, ctypes.POINTER(windows._Message)).contents
        message.message = kind
        message.wParam = identifier
        return 1

    def PostThreadMessageW(self, thread_id, kind, wparam, lparam):
        self.messages.put((kind, wparam))
        return 1


class SimulatedWindowsOwnershipTests(unittest.TestCase):
    def patches(self, user32):
        api = SimpleNamespace(
            user32=user32,
            kernel32=SimpleNamespace(GetCurrentThreadId=threading.get_ident),
        )
        return (
            patch.object(windows, "IS_WINDOWS", True),
            patch.object(windows, "_api", return_value=api),
        )

    def test_dispatch_idempotent_start_and_same_thread_cleanup(self):
        user32 = _FakeUser32()
        received = []
        delivered = threading.Event()

        def callback(action):
            received.append(action)
            delivered.set()

        platform_patch, api_patch = self.patches(user32)
        with platform_patch, api_patch:
            listener = windows.GlobalHotkeys(
                {"start": "Ctrl+F6", "emergency": "Ctrl+Alt+F10"}, callback
            )
            try:
                listener.start()
                listener.start()
                user32.messages.put((windows.WM_HOTKEY, 1))
                self.assertTrue(delivered.wait(1), "The fake hotkey was not delivered.")
            finally:
                listener.stop()
            listener.stop()
        self.assertEqual(received, ["start"])
        self.assertEqual(len(user32.registered), 2)
        self.assertEqual(user32.unregistered, [2, 1])
        self.assertEqual(len(set(user32.owner_threads)), 1)
        self.assertNotEqual(user32.owner_threads[0], threading.get_ident())
        self.assertTrue(all(flags & windows.MOD_NOREPEAT for _, flags, _ in user32.registered))

    def test_partial_registration_is_cleaned_before_start_raises(self):
        user32 = _FakeUser32(fail_identifier=2)
        platform_patch, api_patch = self.patches(user32)
        with platform_patch, api_patch, patch.object(
            windows, "_error", return_value=RuntimeError("Cannot register Ctrl+Alt+F10: reserved")
        ):
            listener = windows.GlobalHotkeys(
                {"start": "F6", "emergency": "Ctrl+Alt+F10"}, lambda action: None
            )
            with self.assertRaisesRegex(RuntimeError, "Cannot register Ctrl"):
                listener.start()
            listener.stop()
        self.assertEqual(user32.unregistered, [1])
        self.assertFalse(listener._thread.is_alive())

    def test_callback_failure_stops_and_exposes_error(self):
        user32 = _FakeUser32()

        def callback(action):
            raise RuntimeError("callback failed")

        platform_patch, api_patch = self.patches(user32)
        with platform_patch, api_patch:
            listener = windows.GlobalHotkeys({"emergency": "Ctrl+Alt+F10"}, callback)
            try:
                listener.start()
                user32.messages.put((windows.WM_HOTKEY, 1))
                self.assertTrue(user32.cleaned_up.wait(1))
            finally:
                listener.stop()
            self.assertEqual(listener.last_error, "callback failed")

    def test_callback_can_request_stop_without_joining_itself(self):
        user32 = _FakeUser32()
        delivered = threading.Event()

        def callback(action):
            listener.stop()
            delivered.set()

        platform_patch, api_patch = self.patches(user32)
        with platform_patch, api_patch:
            listener = windows.GlobalHotkeys({"emergency": "Ctrl+Alt+F10"}, callback)
            try:
                listener.start()
                user32.messages.put((windows.WM_HOTKEY, 1))
                self.assertTrue(delivered.wait(1))
            finally:
                listener.stop()
            self.assertIsNone(listener.last_error)

    def test_manifest_already_set_dpi_awareness_is_success(self):
        user32 = SimpleNamespace(SetProcessDpiAwarenessContext=lambda context: False)
        platform_patch, api_patch = self.patches(user32)
        with platform_patch, api_patch, patch.object(
            ctypes, "set_last_error", create=True
        ), patch.object(ctypes, "get_last_error", return_value=5, create=True):
            self.assertIn("already configured", windows.set_dpi_awareness())


if __name__ == "__main__":
    unittest.main()
