"""Generic Windows inspection and global hotkeys; never injects mouse input.

Importing this module is safe on other platforms. Callbacks run on the hotkey
thread, so they must be brief and must not access Tk widgets directly. F12 is
parsed for validation but cannot be registered: Windows reserves it for debuggers.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import re
import sys
import threading
from typing import Callable, Mapping


IS_WINDOWS = sys.platform == "win32"

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012


@dataclass(frozen=True)
class WindowInfo:
    handle: int
    pid: int
    title: str
    rect: tuple[int, int, int, int]
    dpi: int


@dataclass(frozen=True)
class Hotkey:
    modifiers: int
    virtual_key: int

    @property
    def canonical(self) -> str:
        parts = [
            label
            for flag, label in (
                (MOD_CONTROL, "Ctrl"),
                (MOD_ALT, "Alt"),
                (MOD_SHIFT, "Shift"),
                (MOD_WIN, "Win"),
            )
            if self.modifiers & flag
        ]
        if 0x70 <= self.virtual_key <= 0x7B:
            key = f"F{self.virtual_key - 0x6F}"
        elif self.virtual_key == 0x1B:
            key = "Escape"
        elif self.virtual_key == 0x20:
            key = "Space"
        else:
            key = chr(self.virtual_key)
        return "+".join([*parts, key])


def parse_hotkey(value: str) -> Hotkey:
    """Parse Ctrl/Alt/Shift/Win plus F1–F12, A–Z, 0–9, Escape or Space.

    Parsing and duplicate validation work without Windows. RegisterHotKey may
    still reject a valid combination if Windows or another app reserves it.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A hotkey is required, for example Ctrl+Alt+F6.")
    pieces = [piece.strip().upper() for piece in value.split("+")]
    if any(not piece for piece in pieces):
        raise ValueError(f"Invalid hotkey {value!r}: empty key or modifier.")
    flags = {
        "CTRL": MOD_CONTROL,
        "CONTROL": MOD_CONTROL,
        "ALT": MOD_ALT,
        "SHIFT": MOD_SHIFT,
        "WIN": MOD_WIN,
        "WINDOWS": MOD_WIN,
    }
    modifiers = 0
    for modifier in pieces[:-1]:
        flag = flags.get(modifier)
        if flag is None:
            raise ValueError(f"Invalid hotkey {value!r}: put modifiers before one key.")
        if modifiers & flag:
            raise ValueError(f"Invalid hotkey {value!r}: duplicate modifier.")
        modifiers |= flag
    key = pieces[-1]
    if re.fullmatch(r"F(?:[1-9]|1[0-2])", key):
        virtual_key = 0x6F + int(key[1:])
    elif re.fullmatch(r"[A-Z0-9]", key):
        virtual_key = ord(key)
    elif key in ("ESC", "ESCAPE"):
        virtual_key = 0x1B
    elif key == "SPACE":
        virtual_key = 0x20
    else:
        raise ValueError(
            f"Invalid hotkey {value!r}: use F1–F12, A–Z, 0–9, Escape or Space."
        )
    return Hotkey(modifiers, virtual_key)


class _Message(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
        ("lPrivate", wintypes.DWORD),
    ]


class _WindowsAPI:
    """Load DLLs lazily and declare pointer-sized Win32 signatures explicitly."""

    def __init__(self) -> None:
        _require_windows()
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "RegisterHotKey": ([wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
            "UnregisterHotKey": ([wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            "PeekMessageW": ([ctypes.POINTER(_Message), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
            "GetMessageW": ([ctypes.POINTER(_Message), wintypes.HWND, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
            "PostThreadMessageW": ([wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
            "GetForegroundWindow": ([], wintypes.HWND),
            "GetWindowThreadProcessId": ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            "GetWindowTextLengthW": ([wintypes.HWND], ctypes.c_int),
            "GetWindowTextW": ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetWindowRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "GetCursorPos": ([ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            "SetProcessDPIAware": ([], wintypes.BOOL),
        }
        optional = {
            "SetProcessDpiAwarenessContext": ([wintypes.HANDLE], wintypes.BOOL),
            "GetDpiForWindow": ([wintypes.HWND], wintypes.UINT),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.user32, name)
            function.argtypes = arguments
            function.restype = result
        for name, (arguments, result) in optional.items():
            function = getattr(self.user32, name, None)
            if function is not None:
                function.argtypes = arguments
                function.restype = result
        self.kernel32.GetCurrentThreadId.argtypes = []
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD


_native: _WindowsAPI | None = None
_native_lock = threading.Lock()


def _require_windows() -> None:
    if not IS_WINDOWS:
        raise RuntimeError("This feature requires Windows 10 or 11.")


def _api() -> _WindowsAPI:
    global _native
    _require_windows()
    with _native_lock:
        if _native is None:
            _native = _WindowsAPI()
        return _native


def _error(message: str) -> RuntimeError:
    code = ctypes.get_last_error()
    detail = ctypes.FormatError(code).strip() if code else "Windows returned no error code"
    return RuntimeError(f"{message}: {detail} (Windows error {code}).")


def set_dpi_awareness() -> str:
    """Request physical screen coordinates before creating Tk.

    A packaged app may already have awareness set by its manifest. Windows error
    5 (access denied) means awareness is already set and is not a setup failure.
    On other platforms this is a no-op so the simulation interface can run.
    """
    if not IS_WINDOWS:
        return "DPI awareness: not applicable on this platform."
    user32 = _api().user32
    setter = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if setter is not None:
        for context, description in ((-4, "per-monitor v2"), (-3, "per-monitor")):
            ctypes.set_last_error(0)
            if setter(wintypes.HANDLE(context)):
                return f"DPI awareness: {description}."
            if ctypes.get_last_error() == 5:
                return "DPI awareness is already configured by the manifest or host."
    ctypes.set_last_error(0)
    if user32.SetProcessDPIAware():
        return "DPI awareness: system aware (legacy fallback)."
    if ctypes.get_last_error() == 5:
        return "DPI awareness is already configured by the manifest or host."
    raise _error("Unable to configure DPI awareness")


def cursor_position() -> tuple[int, int]:
    """Return the current cursor position in screen coordinates."""
    api = _api()
    point = wintypes.POINT()
    if not api.user32.GetCursorPos(ctypes.byref(point)):
        raise _error("Unable to read cursor position")
    return point.x, point.y


def foreground_window() -> WindowInfo | None:
    """Inspect the foreground window without guessing which application it is."""
    user32 = _api().user32
    handle = user32.GetForegroundWindow()
    if not handle:
        return None
    pid = wintypes.DWORD()
    if not user32.GetWindowThreadProcessId(handle, ctypes.byref(pid)):
        raise _error("Unable to identify the foreground window")
    rect = wintypes.RECT()
    if not user32.GetWindowRect(handle, ctypes.byref(rect)):
        raise _error("Unable to read foreground window bounds")
    length = min(user32.GetWindowTextLengthW(handle), 32767)
    title = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, title, length + 1)
    get_dpi = getattr(user32, "GetDpiForWindow", None)
    dpi = int(get_dpi(handle)) if get_dpi else 96
    return WindowInfo(
        handle=int(handle),
        pid=int(pid.value),
        title=title.value,
        rect=(rect.left, rect.top, rect.right, rect.bottom),
        dpi=dpi or 96,
    )


class GlobalHotkeys:
    """Own RegisterHotKey registrations on one dedicated message thread.

    Pass the complete mapping, including ``{"emergency": "Ctrl+Alt+F10"}``.
    ``start`` either registers every key or raises with registrations cleaned up.
    Check ``last_error`` for message-loop or callback failures after startup.
    ``stop`` is bounded, idempotent and safe to call inside a callback.

    Microsoft reserves F12 for debuggers at all times, even with modifiers:
    https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-registerhotkey
    """

    _START_TIMEOUT = 5.0
    _STOP_TIMEOUT = 3.0

    def __init__(self, mapping: Mapping[str, str], callback: Callable[[str], None]) -> None:
        if not mapping:
            raise ValueError("Configure at least one global hotkey.")
        if not callable(callback):
            raise TypeError("The hotkey callback must be callable.")
        parsed: dict[int, tuple[str, Hotkey]] = {}
        seen: dict[Hotkey, str] = {}
        for identifier, (action, value) in enumerate(mapping.items(), start=1):
            if not isinstance(action, str) or not action.strip():
                raise ValueError("Every hotkey must have a nonempty action name.")
            hotkey = parse_hotkey(value)
            if hotkey.virtual_key == 0x7B:
                raise ValueError(
                    "F12 cannot be a global hotkey: Windows reserves it for debuggers. "
                    "Choose another key, for example Ctrl+Alt+F10."
                )
            if hotkey in seen:
                raise ValueError(
                    f"{hotkey.canonical} is assigned to both {seen[hotkey]!r} and {action!r}."
                )
            seen[hotkey] = action
            parsed[identifier] = (action, hotkey)
        self._mapping = parsed
        self._callback = callback
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._stop_requested = threading.Event()
        self._ready = threading.Event()
        self._failure: BaseException | None = None

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return str(self._failure) if self._failure else None

    def start(self) -> None:
        _require_windows()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if self._stop_requested.is_set():
                    raise RuntimeError("Global hotkeys are still stopping; retry after shutdown.")
                return
            self._stop_requested.clear()
            self._ready.clear()
            self._failure = None
            self._thread = threading.Thread(
                target=self._run, name="rhythia-global-hotkeys", daemon=True
            )
            self._thread.start()
        if not self._ready.wait(self._START_TIMEOUT):
            self.stop()
            raise RuntimeError("Timed out while registering global hotkeys.")
        with self._lock:
            failure = self._failure
            stopped = self._stop_requested.is_set()
        if failure is not None:
            self.stop()
            raise RuntimeError(str(failure)) from failure
        if stopped:
            raise RuntimeError("Global hotkey registration was cancelled.")

    def stop(self) -> None:
        with self._lock:
            self._stop_requested.set()
            thread = self._thread
            thread_id = self._thread_id
        if thread is None or not thread.is_alive():
            return
        if thread_id:
            # The worker creates its queue before publishing the thread ID.
            _api().user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
        if thread is not threading.current_thread():
            thread.join(self._STOP_TIMEOUT)
            if thread.is_alive():
                raise RuntimeError("Global hotkey shutdown timed out; close the application.")

    def _run(self) -> None:
        registered: list[int] = []
        api: _WindowsAPI | None = None
        try:
            api = _api()
            message = _Message()
            api.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            with self._lock:
                self._thread_id = int(api.kernel32.GetCurrentThreadId())
            for identifier, (_, hotkey) in self._mapping.items():
                if self._stop_requested.is_set():
                    return
                if not api.user32.RegisterHotKey(
                    None, identifier, hotkey.modifiers | MOD_NOREPEAT, hotkey.virtual_key
                ):
                    raise _error(
                        f"Cannot register {hotkey.canonical}; another app or Windows may reserve it"
                    )
                registered.append(identifier)
            self._ready.set()
            while not self._stop_requested.is_set():
                result = api.user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == -1:
                    raise _error("The global hotkey message loop failed")
                if result == 0:
                    break
                if message.message == WM_HOTKEY:
                    entry = self._mapping.get(int(message.wParam))
                    if entry is not None and not self._stop_requested.is_set():
                        self._callback(entry[0])
        except BaseException as error:
            with self._lock:
                self._failure = error
            self._stop_requested.set()
        finally:
            if api is not None:
                for identifier in reversed(registered):
                    api.user32.UnregisterHotKey(None, identifier)
            with self._lock:
                self._thread_id = 0
            self._ready.set()
