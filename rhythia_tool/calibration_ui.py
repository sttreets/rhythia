"""Two-click playfield calibration in physical screen coordinates.

The process must enable DPI awareness before creating Tk. This overlay only
observes local clicks; it never injects input or grabs the keyboard globally.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import tkinter as tk
from typing import Callable

from .windows import IS_WINDOWS, WindowInfo


def _set_window_position(handle: int, left: int, top: int, width: int, height: int) -> None:
    """Place the native frame explicitly, including on negative-coordinate monitors."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    function = user32.SetWindowPos
    function.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, wintypes.UINT]
    function.restype = wintypes.BOOL
    # HWND_TOPMOST and SWP_SHOWWINDOW. No cursor or keyboard input is generated.
    if not function(handle, wintypes.HWND(-1), left, top, width, height, 0x0040):
        code = ctypes.get_last_error()
        raise RuntimeError(f"Could not position the calibration overlay (Windows error {code}).")


class PlayfieldSelector:
    """Select the outer top-left and bottom-right pixels of the full 3×3 frame.

    ``on_selected(left, top, width, height)`` receives inclusive pixel dimensions,
    so the second corner is ``left + width - 1, top + height - 1``. Either callback
    runs once, after the overlay is destroyed. ``cancel()`` is safe to repeat.
    """

    FIRST_INSTRUCTION = "Click the TOP-LEFT outer corner of the whole 3×3 playfield. Esc cancels."
    SECOND_INSTRUCTION = "Click the BOTTOM-RIGHT outer corner of the whole 3×3 playfield. Esc cancels."

    def __init__(
        self,
        root: tk.Misc,
        target: WindowInfo,
        on_selected: Callable[[int, int, int, int], None],
        on_cancel: Callable[[], None],
    ) -> None:
        rect = target.rect
        if (not isinstance(rect, tuple) or len(rect) != 4
                or any(type(value) is not int or not -(1 << 31) <= value < (1 << 31) for value in rect)
                or rect[2] - rect[0] < 3 or rect[3] - rect[1] < 3):
            raise ValueError("The captured window must have valid screen bounds at least 3×3 pixels.")
        self.rect = rect
        self.on_selected = on_selected
        self.on_cancel = on_cancel
        self.first: tuple[int, int] | None = None
        self.closed = False
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        try:
            self.window.title("Select Rhythia playfield")
            self.window.overrideredirect(True)
            self.window.attributes("-topmost", True)
            self.window.attributes("-alpha", 0.48)
            self.window.protocol("WM_DELETE_WINDOW", self.cancel)
            self.canvas = tk.Canvas(self.window, bg="#121824", highlightthickness=0, cursor="crosshair")
            self.canvas.pack(fill="both", expand=True)
            self.instruction = self.FIRST_INSTRUCTION
            self.instruction_item = self.canvas.create_text(
                20, 20, anchor="nw", text=self.instruction, fill="white",
                font=("Segoe UI", 14, "bold"), width=max(40, rect[2] - rect[0] - 40),
            )
            self.canvas.bind("<Button-1>", self._click)
            self.canvas.bind("<Motion>", self._motion)
            self.window.bind("<Escape>", lambda event: self.cancel())
            self.window.bind("<Destroy>", self._destroyed)
            left, top, right, bottom = rect
            width, height = right - left, bottom - top
            # Tk treats a negative geometry offset as distance from the far edge.
            # Start at a positive location, then use the physical Win32 frame API.
            self.window.geometry(f"{width}x{height}+{max(0, left)}+{max(0, top)}")
            self.window.deiconify()
            self.window.update_idletasks()
            if IS_WINDOWS:
                _set_window_position(int(self.window.wm_frame(), 0), left, top, width, height)
            self.window.lift()
            self.window.focus_force()
        except Exception:
            self.closed = True
            self.window.destroy()
            raise

    def _message(self, text: str) -> None:
        self.instruction = text
        self.canvas.itemconfigure(self.instruction_item, text=text)

    def _inside(self, point: tuple[int, int]) -> bool:
        left, top, right, bottom = self.rect
        return left <= point[0] < right and top <= point[1] < bottom

    def _click(self, event: tk.Event) -> None:
        if self.closed:
            return
        point = (int(event.x_root), int(event.y_root))
        if not self._inside(point):
            self._message("Choose a point inside the captured game window. Esc cancels.")
            return
        if self.first is None:
            self.first = point
            self._message(self.SECOND_INSTRUCTION)
            self._draw(point)
            return
        left, top = self.first
        width, height = point[0] - left + 1, point[1] - top + 1
        if width < 3 or height < 3:
            self._message("Choose BOTTOM-RIGHT below and right of the first point (at least 3×3 pixels). Esc cancels.")
            return
        self._finish((left, top, width, height))

    def _motion(self, event: tk.Event) -> None:
        if not self.closed and self.first is not None:
            self._draw((int(event.x_root), int(event.y_root)))

    def _draw(self, point: tuple[int, int]) -> None:
        self.canvas.delete("selection")
        if self.first is None:
            return
        origin_x, origin_y = self.canvas.winfo_rootx(), self.canvas.winfo_rooty()
        left, top = self.first[0] - origin_x, self.first[1] - origin_y
        x, y = point[0] - origin_x, point[1] - origin_y
        self.canvas.create_rectangle(left, top, x, y, outline="#67ff9c", width=3, tags="selection")
        self.canvas.create_oval(left - 4, top - 4, left + 4, top + 4,
                                fill="#67ff9c", outline="", tags="selection")

    def _finish(self, result: tuple[int, int, int, int] | None) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.window.destroy()
        except tk.TclError:
            pass  # The owning root may already have been destroyed.
        if result is None:
            self.on_cancel()
        else:
            self.on_selected(*result)

    def _destroyed(self, event: tk.Event) -> None:
        if event.widget is self.window and not self.closed:
            self.closed = True
            self.on_cancel()

    def cancel(self) -> None:
        self._finish(None)
