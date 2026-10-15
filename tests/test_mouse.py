"""All movement uses fakes, on every platform; never calls real SendInput."""

import ctypes
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rhythia_tool import mouse, windows


TARGET = windows.WindowInfo(123, 456, "Captured application", (100, 200, 900, 800), 144)


class FakeNative:
    def __init__(self, desktop=(-1920, -120, 4480, 1560)):
        self.desktop = desktop
        self.events = []
        self.return_count = 1
        self.error = None
        self.calls = []

    def virtual_desktop(self):
        self.calls.append("desktop")
        return self.desktop

    def send_input(self, event):
        self.calls.append("send")
        if self.error:
            raise self.error
        self.events.append(mouse._INPUT.from_buffer_copy(event))
        return self.return_count

    def error_description(self):
        return "Windows error 5: Access is denied"


class InputABITests(unittest.TestCase):
    def test_windows_structures_use_fixed_width_fields_and_pointer_size(self):
        pointer_bytes = ctypes.sizeof(ctypes.c_void_p)
        self.assertIn(pointer_bytes, (4, 8))
        self.assertEqual(ctypes.sizeof(mouse._MOUSEINPUT), 32 if pointer_bytes == 8 else 24)
        self.assertEqual(ctypes.sizeof(mouse._KEYBDINPUT), 24 if pointer_bytes == 8 else 16)
        self.assertEqual(ctypes.sizeof(mouse._HARDWAREINPUT), 8)
        self.assertEqual(ctypes.sizeof(mouse._INPUT), 40 if pointer_bytes == 8 else 28)
        self.assertEqual(mouse._INPUT.data.offset, 8 if pointer_bytes == 8 else 4)
        self.assertEqual(mouse._MOUSEINPUT.dx.offset, 0)
        self.assertEqual(mouse._MOUSEINPUT.dy.offset, 4)
        self.assertEqual(mouse._MOUSEINPUT.dwExtraInfo.offset, 24 if pointer_bytes == 8 else 20)

    def test_native_adapter_uses_one_input_and_full_union_size(self):
        calls = []

        class Function:
            def __call__(self, count, pointer, size):
                event = ctypes.cast(pointer, ctypes.POINTER(mouse._INPUT)).contents
                calls.append((count, size, event.type, event.mi.dx))
                return 1

        send = Function()
        metrics = Function()
        api = SimpleNamespace(user32=SimpleNamespace(SendInput=send, GetSystemMetrics=metrics))
        with patch.object(windows, "_api", return_value=api), patch.object(
            ctypes, "set_last_error", create=True
        ) as clear_error:
            native = mouse._MouseAPI()
            event = mouse._INPUT()
            event.mi.dx = -50
            self.assertEqual(native.send_input(event), 1)
        self.assertEqual(calls, [(1, ctypes.sizeof(mouse._INPUT), mouse.INPUT_MOUSE, -50)])
        clear_error.assert_called_once_with(0)
        self.assertEqual(send.argtypes, [ctypes.c_uint32, ctypes.POINTER(mouse._INPUT), ctypes.c_int32])
        self.assertIs(send.restype, ctypes.c_uint32)
        self.assertEqual(metrics.argtypes, [ctypes.c_int32])
        self.assertIs(metrics.restype, ctypes.c_int32)


class MouseTests(unittest.TestCase):
    def setUp(self):
        self.platform_patch = patch.object(windows, "IS_WINDOWS", True)
        self.platform_patch.start()
        self.addCleanup(self.platform_patch.stop)
        self.native = FakeNative()
        self.current = TARGET

        def inspect():
            self.native.calls.append("focus")
            if isinstance(self.current, Exception):
                raise self.current
            return self.current

        self.device = mouse.WindowsMouse(
            TARGET, native=self.native, inspect_foreground=inspect,
        )

    def assert_movement_only(self, event):
        self.assertEqual(event.type, mouse.INPUT_MOUSE)
        self.assertEqual(event.mi.mouseData, 0)
        self.assertEqual(event.mi.time, 0)
        self.assertEqual(event.mi.dwExtraInfo, 0)
        self.assertEqual(event.mi.dwFlags & ~(0x0001 | 0x2000 | 0x4000 | 0x8000), 0)

    def test_relative_values_flags_and_focus_check_immediately_before_send(self):
        self.device.move_relative(-2147483648, 2147483647)
        event = self.native.events[0]
        self.assertEqual((event.mi.dx, event.mi.dy), (-2147483648, 2147483647))
        self.assertEqual(event.mi.dwFlags, mouse.MOUSEEVENTF_MOVE | mouse.MOUSEEVENTF_MOVE_NOCOALESCE)
        self.assertEqual(self.native.calls, ["focus", "send"])
        self.assert_movement_only(event)

    def test_absolute_inclusive_virtual_desktop_endpoints_and_flags(self):
        self.device.move_absolute(-1920, -120)
        self.device.move_absolute(2559, 1439)
        self.assertEqual(
            [(event.mi.dx, event.mi.dy) for event in self.native.events],
            [(0, 0), (65535, 65535)],
        )
        self.assertEqual(self.native.calls, ["desktop", "focus", "send"] * 2)
        for event in self.native.events:
            self.assert_movement_only(event)
            self.assertEqual(
                event.mi.dwFlags,
                mouse.MOUSEEVENTF_MOVE | mouse.MOUSEEVENTF_MOVE_NOCOALESCE
                | mouse.MOUSEEVENTF_ABSOLUTE | mouse.MOUSEEVENTF_VIRTUALDESK,
            )

    def test_absolute_midpoint_rounding_and_one_pixel_desktop(self):
        self.native.desktop = (0, 0, 3, 3)
        self.device.move_absolute(1, 1)
        self.assertEqual((self.native.events[-1].mi.dx, self.native.events[-1].mi.dy), (32768, 32768))
        self.native.desktop = (7, -2, 1, 1)
        self.device.move_absolute(7, -2)
        self.assertEqual((self.native.events[-1].mi.dx, self.native.events[-1].mi.dy), (0, 0))

    def test_outside_coordinates_are_rejected_without_clipping_or_send(self):
        for position in ((-1921, 0), (2560, 0), (0, -121), (0, 1440)):
            with self.subTest(position=position):
                with self.assertRaisesRegex(ValueError, "outside the virtual desktop"):
                    self.device.move_absolute(*position)
        self.assertEqual(self.native.events, [])
        self.assertNotIn("send", self.native.calls)

    def test_rejects_non_integer_and_out_of_long_range_values_before_native_calls(self):
        for function in (self.device.move_relative, self.device.move_absolute):
            for value in (True, 1.0, "1", None, -(1 << 31) - 1, 1 << 31):
                for args in ((value, 0), (0, value)):
                    with self.subTest(function=function, args=args):
                        with self.assertRaisesRegex(ValueError, "integer in signed 32-bit"):
                            function(*args)
        self.assertEqual(self.native.calls, [])

    def test_focus_pid_geometry_or_dpi_change_disables_device_permanently(self):
        for changed, message in (
            (None, "lost focus"),
            (replace(TARGET, handle=999), "lost focus"),
            (replace(TARGET, pid=999), "lost focus"),
            (replace(TARGET, rect=(101, 200, 901, 800)), "moved or resized"),
            (replace(TARGET, rect=(100, 200, 901, 800)), "moved or resized"),
            (replace(TARGET, dpi=96), "DPI changed"),
        ):
            with self.subTest(changed=changed):
                device = mouse.WindowsMouse(
                    TARGET, native=self.native, inspect_foreground=lambda: changed,
                )
                with self.assertRaisesRegex(RuntimeError, message):
                    device.move_relative(1, 1)
                with patch.object(device, "_inspect_foreground", return_value=TARGET):
                    with self.assertRaisesRegex(RuntimeError, message):
                        device.move_relative(1, 1)
                    with self.assertRaises(RuntimeError):
                        device.check_target()
        self.assertEqual(self.native.events, [])

    def test_target_title_can_change_without_identity_or_calibration_change(self):
        self.current = replace(TARGET, title="A different title")
        self.device.check_target()
        self.device.move_relative(1, -1)
        self.assertEqual(len(self.native.events), 1)

    def test_check_target_sends_nothing_and_latches_inspection_error(self):
        self.device.check_target()
        self.assertEqual(self.native.calls, ["focus"])
        self.current = OSError("Cannot inspect window")
        with self.assertRaisesRegex(RuntimeError, "Unable to inspect.*Cannot inspect"):
            self.device.check_target()
        self.current = TARGET
        with self.assertRaisesRegex(RuntimeError, "Restart playback"):
            self.device.move_absolute(0, 0)
        self.assertEqual(self.native.events, [])

    def test_native_send_failure_latches_and_explains_unknown_uipi_cause(self):
        self.native.return_count = 0
        with self.assertRaisesRegex(RuntimeError, "accepted 0 of 1.*error 5.*does not uniquely identify UIPI"):
            self.device.move_relative(3, 4)
        self.native.return_count = 1
        with self.assertRaisesRegex(RuntimeError, "Restart playback"):
            self.device.move_relative(3, 4)
        self.assertEqual(len(self.native.events), 1)

    def test_native_exception_latches(self):
        self.native.error = OSError("native failure")
        with self.assertRaisesRegex(RuntimeError, "mouse movement failed: native failure"):
            self.device.move_relative(1, 2)
        self.native.error = None
        with self.assertRaisesRegex(RuntimeError, "Restart playback"):
            self.device.move_relative(1, 2)
        self.assertEqual(self.native.events, [])

    def test_invalid_desktop_latches_before_focus_or_send(self):
        for desktop in ((0, 0, 0, 100), (0, 0, 100, -1), (0, 0, 1.0, 1), (2147483647, 0, 2, 1)):
            with self.subTest(desktop=desktop):
                native = FakeNative(desktop)
                device = mouse.WindowsMouse(TARGET, native=native, inspect_foreground=lambda: TARGET)
                with self.assertRaisesRegex(RuntimeError, "virtual-desktop bounds"):
                    device.move_absolute(0, 0)
                with self.assertRaises(RuntimeError):
                    device.move_relative(1, 1)
                self.assertEqual(native.calls, ["desktop"])

    def test_close_is_idempotent_and_blocks_all_followup_calls(self):
        self.device.close()
        self.device.close()
        for function, args in ((self.device.check_target, ()), (self.device.move_relative, (0, 0)), (self.device.move_absolute, (0, 0))):
            with self.subTest(function=function):
                with self.assertRaisesRegex(RuntimeError, "stopped"):
                    function(*args)
        self.assertEqual(self.native.calls, [])

    def test_invalid_targets_are_rejected_before_backend_construction(self):
        for target in (
            None, replace(TARGET, handle=0), replace(TARGET, pid=True),
            replace(TARGET, dpi=0), replace(TARGET, rect=(0, 0, 0, 100)),
            replace(TARGET, rect=(0, 0, 100)), replace(TARGET, rect=(0, 0, 100.0, 100)),
        ):
            with self.subTest(target=target), patch.object(mouse, "_MouseAPI") as loader:
                with self.assertRaises(ValueError):
                    mouse.WindowsMouse(target)
                loader.assert_not_called()

    def test_unsupported_platform_does_not_load_native_api_even_with_injection(self):
        with patch.object(windows, "IS_WINDOWS", False), patch.object(mouse, "_MouseAPI") as loader:
            with self.assertRaisesRegex(RuntimeError, "requires Windows"):
                mouse.WindowsMouse(TARGET, native=self.native, inspect_foreground=lambda: TARGET)
        loader.assert_not_called()
        self.assertEqual(self.native.calls, [])


if __name__ == "__main__":
    unittest.main()
