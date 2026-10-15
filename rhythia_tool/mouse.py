"""Generic guarded Windows mouse movement; this module is not wired to the UI.

Call ``windows.set_dpi_awareness()`` before creating a window or capturing the
target with ``windows.foreground_window()``. Capture after the target has reached
its final position, size and monitor. Each movement checks that the same HWND
and PID remain foreground and that its rectangle and DPI have not changed.
These checks cannot make SendInput atomic with a foreground-window change.

Relative movement is affected by Windows pointer speed and acceleration. Absolute
movement uses physical virtual-desktop coordinates; it does not establish that
an application accepts injected input or supports that cursor mode. This class
does not transform map coordinates, start gameplay, submit scores or bypass input
restrictions. It never generates buttons, wheel movement or keyboard input.

The default backend is loaded only when WindowsMouse is instantiated on Windows.
Tests can inject a backend and foreground reader; they never need real input.

Microsoft API contracts:
https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-sendinput
https://learn.microsoft.com/windows/win32/api/winuser/ns-winuser-mouseinput
https://learn.microsoft.com/windows/win32/api/winuser/ns-winuser-input
https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-getsystemmetrics
"""

from __future__ import annotations

import ctypes
import threading
from typing import Callable, Protocol

from . import windows
from .windows import WindowInfo


INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_MOVE_NOCOALESCE = 0x2000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

_LONG_MIN = -(1 << 31)
_LONG_MAX = (1 << 31) - 1


# Explicit Win32 widths also make the ABI inspectable on Unix, where c_long
# and ctypes.wintypes.LONG may be 64-bit. ULONG_PTR follows the process width.
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_uint16),
        ("wScan", ctypes.c_uint16),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_uint32),
        ("wParamL", ctypes.c_uint16),
        ("wParamH", ctypes.c_uint16),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", ctypes.c_uint32), ("data", _INPUTUNION)]


class _NativeMouse(Protocol):
    """Small injectable boundary; a test implementation must not call Windows."""

    def virtual_desktop(self) -> tuple[int, int, int, int]: ...

    def send_input(self, event: _INPUT) -> int: ...

    def error_description(self) -> str: ...


class _MouseAPI:
    def __init__(self) -> None:
        user32 = windows._api().user32
        self._send_input = user32.SendInput
        self._send_input.argtypes = [
            ctypes.c_uint32, ctypes.POINTER(_INPUT), ctypes.c_int32,
        ]
        self._send_input.restype = ctypes.c_uint32
        self._get_system_metrics = user32.GetSystemMetrics
        self._get_system_metrics.argtypes = [ctypes.c_int32]
        self._get_system_metrics.restype = ctypes.c_int32

    def virtual_desktop(self) -> tuple[int, int, int, int]:
        return tuple(
            int(self._get_system_metrics(metric))
            for metric in (
                SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
                SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN,
            )
        )

    def send_input(self, event: _INPUT) -> int:
        ctypes.set_last_error(0)
        return int(self._send_input(1, ctypes.byref(event), ctypes.sizeof(_INPUT)))

    def error_description(self) -> str:
        code = ctypes.get_last_error()
        detail = ctypes.FormatError(code).strip() if code else "no error code supplied"
        return f"Windows error {code}: {detail}"


def _long(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer in signed 32-bit range.")
    if not _LONG_MIN <= value <= _LONG_MAX:
        raise ValueError(f"{label} must be an integer in signed 32-bit range.")
    return value


class WindowsMouse:
    """Movement-only SendInput with a foreground-window safety latch.

    ``target`` is a previously captured WindowInfo. Window focus, bounds or DPI
    changes, native failures, or ``close()`` disable the instance permanently.
    Recreate it after recapturing/calibrating the target and restarting playback;
    restoring focus alone does not resume input. Invalid movement arguments raise
    ValueError without sending input. The caller must stop playback on any error.

    The optional dependencies exist for simulated tests. Defaults use Windows
    APIs, and construction on other platforms always fails clearly. Input calls
    and close are serialized: after close returns, this instance sends no input.
    """

    def __init__(
        self,
        target: WindowInfo,
        *,
        native: _NativeMouse | None = None,
        inspect_foreground: Callable[[], WindowInfo | None] | None = None,
    ) -> None:
        windows._require_windows()
        if not isinstance(target, WindowInfo):
            raise ValueError("Capture a valid foreground target window first.")
        if (
            isinstance(target.handle, bool) or not isinstance(target.handle, int)
            or target.handle <= 0
            or isinstance(target.pid, bool) or not isinstance(target.pid, int)
            or target.pid <= 0
            or isinstance(target.dpi, bool) or not isinstance(target.dpi, int)
            or target.dpi <= 0
        ):
            raise ValueError("The target must have a valid window handle, PID and DPI.")
        if not isinstance(target.rect, tuple) or len(target.rect) != 4:
            raise ValueError("The target must have a valid screen rectangle.")
        left, top, right, bottom = (
            _long(value, "Target rectangle coordinate") for value in target.rect
        )
        if right <= left or bottom <= top:
            raise ValueError("The target must have a nonempty screen rectangle.")
        self.target = target
        self._native = native if native is not None else _MouseAPI()
        self._inspect_foreground = (
            inspect_foreground if inspect_foreground is not None
            else windows.foreground_window
        )
        self._lock = threading.RLock()
        self._disabled_reason: str | None = None

    def close(self) -> None:
        """Permanently disable this instance; safe to call repeatedly."""
        with self._lock:
            if self._disabled_reason is None:
                self._disabled_reason = "Mouse input was stopped. Restart playback to rearm."

    def _active(self) -> None:
        if self._disabled_reason is not None:
            raise RuntimeError(self._disabled_reason)

    def _fail(self, reason: str) -> None:
        self._disabled_reason = f"{reason} Restart playback and recapture the target to rearm."
        raise RuntimeError(self._disabled_reason)

    def _check_target(self) -> None:
        self._active()
        try:
            current = self._inspect_foreground()
        except Exception as error:
            self._fail(f"Unable to inspect the foreground window: {error}.")
        if current is None or (current.handle, current.pid) != (
            self.target.handle, self.target.pid,
        ):
            self._fail("The target window lost focus; mouse input was stopped.")
        if current.rect != self.target.rect:
            self._fail("The target window moved or resized; recalibration is required.")
        if current.dpi != self.target.dpi:
            self._fail("The target window DPI changed; recalibration is required.")

    def check_target(self) -> None:
        """Check/latch focus and calibration validity without sending any input."""
        with self._lock:
            self._check_target()

    def _send(self, x: int, y: int, flags: int) -> None:
        event = _INPUT()
        event.type = INPUT_MOUSE
        event.mi.dx = x
        event.mi.dy = y
        event.mi.dwFlags = flags
        # No other API work is performed between the foreground check and send.
        self._check_target()
        try:
            count = self._native.send_input(event)
        except Exception as error:
            self._fail(f"Windows mouse movement failed: {error}.")
        if count != 1:
            try:
                detail = self._native.error_description()
            except Exception:
                detail = "Windows error information is unavailable"
            self._fail(
                f"SendInput accepted {count} of 1 movement events ({detail}). "
                "Windows allows injection only into applications at equal or lower "
                "integrity levels; this result does not uniquely identify UIPI blocking."
            )

    def move_relative(self, dx: int, dy: int) -> None:
        """Send a relative movement, subject to Windows acceleration/speed."""
        dx, dy = _long(dx, "dx"), _long(dy, "dy")
        with self._lock:
            self._active()
            self._send(dx, dy, MOUSEEVENTF_MOVE | MOUSEEVENTF_MOVE_NOCOALESCE)

    def move_absolute(self, x: int, y: int) -> None:
        """Move to an in-bounds physical virtual-desktop pixel; never clip it."""
        x, y = _long(x, "x"), _long(y, "y")
        with self._lock:
            self._active()
            try:
                left, top, width, height = self._native.virtual_desktop()
                left, top = _long(left, "Desktop left"), _long(top, "Desktop top")
                width = _long(width, "Desktop width")
                height = _long(height, "Desktop height")
                if width <= 0 or height <= 0:
                    raise ValueError("the virtual desktop has no usable dimensions")
                right = _long(left + width - 1, "Desktop right")
                bottom = _long(top + height - 1, "Desktop bottom")
            except Exception as error:
                self._fail(f"Unable to read valid virtual-desktop bounds: {error}.")
            if not (left <= x <= right and top <= y <= bottom):
                raise ValueError(
                    f"Position ({x}, {y}) is outside the virtual desktop "
                    f"({left}, {top}) through ({right}, {bottom}); movement was not sent."
                )

            def normalize(value: int, origin: int, extent: int) -> int:
                # Nearest normalized integer; preserve both inclusive endpoints.
                return ((value - origin) * 65535 + (extent - 1) // 2) // (extent - 1) if extent > 1 else 0

            self._send(
                normalize(x, left, width), normalize(y, top, height),
                MOUSEEVENTF_MOVE | MOUSEEVENTF_MOVE_NOCOALESCE
                | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            )
