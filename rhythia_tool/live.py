"""Explicitly calibrated SSPM movement with a permanently guarded input session.

SSPM coordinates 0/1/2 identify the three row/column centres. For this tool the
user calibrates the surrounding frame, from -0.5 through 2.5, in physical desktop
pixels. Notes outside that frame are rejected rather than clipped. This is a
user-selected coordinate transform, not verified Steam Rhythia integration.

Relative mode uses empirically measured counts per SSPM unit. The user must
manually put the game's cursor at the configured SSPM anchor before every run.
There is no automatic homing or guessed game sensitivity. Windows acceleration,
pointer speed, and the game's input mode can affect the result. Neither mode
establishes that the game accepts injected movement or awards ranked scores.

Only WindowsMouse performs input. It checks the captured foreground window,
geometry and DPI immediately before each movement; these checks cannot make a
foreground change atomic with SendInput. No buttons, keyboard events, game hooks,
memory access or score submission are used here.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import math
import threading
from typing import Callable, Protocol, Sequence

from .mouse import WindowsMouse
from .sspm import MapNote
from .windows import WindowInfo


ABSOLUTE_MODE = "Absolute screen"
RELATIVE_MODE = "Relative calibrated"
_FRAME_MIN = -0.5
_FRAME_MAX = 2.5


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number.")
    try:
        if math.isfinite(value):
            return float(value)
    except (OverflowError, ValueError):
        pass
    raise ValueError(f"{label} must be a finite number.")


@dataclass(frozen=True)
class LiveCalibration:
    left: int
    top: int
    width: int
    height: int
    input_mode: str
    counts_per_unit_x: float = 300.0
    counts_per_unit_y: float = 300.0
    anchor_x: float = 1.0
    anchor_y: float = 1.0
    invert_x: bool = False
    invert_y: bool = False

    @classmethod
    def from_settings(cls, settings: object) -> LiveCalibration:
        """Copy the explicit live fields without consulting screen/DPI guesses."""
        try:
            result = cls(**{field.name: getattr(settings, field.name) for field in fields(cls)})
        except AttributeError as error:
            raise ValueError("Settings are missing a required live calibration field.") from error
        return result.validate()

    def validate(self) -> LiveCalibration:
        for name in ("left", "top", "width", "height"):
            value = getattr(self, name)
            low = -100000 if name in ("left", "top") else 3
            if type(value) is not int or not low <= value <= 100000:
                raise ValueError(f"{name} must be an integer from {low} through 100000.")
        if self.input_mode not in (ABSOLUTE_MODE, RELATIVE_MODE):
            raise ValueError("Choose Absolute screen or Relative calibrated input mode.")
        for name in ("counts_per_unit_x", "counts_per_unit_y"):
            value = _finite(getattr(self, name), name)
            if not 0 < value <= 100000:
                raise ValueError(f"{name} must be greater than 0 and at most 100000.")
        for name in ("anchor_x", "anchor_y"):
            value = _finite(getattr(self, name), name)
            if not _FRAME_MIN <= value <= _FRAME_MAX:
                raise ValueError(f"{name} must be within the SSPM frame [-0.5, 2.5].")
        for name in ("invert_x", "invert_y"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be true or false.")
        return self


class _Mouse(Protocol):
    def move_absolute(self, x: int, y: int) -> None: ...

    def move_relative(self, dx: int, dy: int) -> None: ...

    def check_target(self) -> None: ...

    def close(self) -> None: ...


def _note(note: MapNote) -> None:
    if not isinstance(note, MapNote):
        raise ValueError("Live playback requires raw SSPM MapNote records.")
    if _finite(note.time_ms, "Note timestamp") < 0:
        raise ValueError("Note timestamps must be nonnegative.")
    for name in ("x", "y"):
        if not _FRAME_MIN <= _finite(getattr(note, name), f"Note {name}") <= _FRAME_MAX:
            raise ValueError(
                f"Note {name} lies outside the calibrated SSPM frame [-0.5, 2.5]. "
                "Live playback does not clamp or rescale out-of-frame notes."
            )


def _target_bounds(target: WindowInfo) -> tuple[int, int, int, int]:
    if not isinstance(target, WindowInfo):
        raise ValueError("Capture a target window before starting live playback.")
    for name in ("handle", "pid", "dpi"):
        value = getattr(target, name)
        if type(value) is not int or value <= 0:
            raise ValueError("The captured target needs a valid window handle, PID and DPI.")
    if not isinstance(target.rect, tuple) or len(target.rect) != 4:
        raise ValueError("The captured target has an invalid screen rectangle.")
    if any(type(value) is not int or not -(1 << 31) <= value < (1 << 31) for value in target.rect):
        raise ValueError("The captured target rectangle needs signed 32-bit integer coordinates.")
    left, top, right, bottom = target.rect
    if right <= left or bottom <= top:
        raise ValueError("The captured target must have a nonempty screen rectangle.")
    return left, top, right, bottom


class LiveSession:
    """A single run of calibrated movement, permanently disabled on any error.

    Construction preflights every raw note and the full calibration without
    moving the mouse. The target must currently have focus. Relative movement
    starts at the manually prepared anchor and carries sub-count rounding error
    between notes. ``close`` is serialized with movement: once it returns this
    instance can never send further input, even if the target regains focus.
    """

    def __init__(
        self,
        calibration: LiveCalibration,
        target: WindowInfo,
        notes: Sequence[MapNote],
        mouse_factory: Callable[[WindowInfo], _Mouse] = WindowsMouse,
    ) -> None:
        if not isinstance(calibration, LiveCalibration):
            raise ValueError("A valid live calibration is required.")
        calibration.validate()
        left, top, right, bottom = _target_bounds(target)
        if not (
            left <= calibration.left
            and top <= calibration.top
            and calibration.left + calibration.width <= right
            and calibration.top + calibration.height <= bottom
        ):
            raise ValueError("The entire calibrated playfield must be inside the captured target window.")
        # Freeze the selected sequence before validation; never arm for a partial
        # chart that discovers invalid geometry after the first movement.
        raw_notes = tuple(notes)
        if not raw_notes:
            raise ValueError("The selected map contains no notes for live playback.")
        previous_time = -1.0
        for note in raw_notes:
            _note(note)
            if note.time_ms < previous_time:
                raise ValueError("Live note timestamps must be sorted in nondecreasing order.")
            previous_time = note.time_ms
        self.calibration = calibration
        self.target = target
        self._lock = threading.RLock()
        self._disabled_reason: str | None = None
        self._last_x = float(calibration.anchor_x)
        self._last_y = float(calibration.anchor_y)
        self._residual_x = 0.0
        self._residual_y = 0.0
        self._mouse = mouse_factory(target)
        try:
            self._mouse.check_target()
        except Exception as error:
            self._fail(error)

    def _active(self) -> None:
        if self._disabled_reason is not None:
            raise RuntimeError(self._disabled_reason)

    def _fail(self, error: Exception) -> None:
        self._disabled_reason = f"Live input stopped: {error} Restart playback to rearm."
        try:
            self._mouse.close()
        except Exception:
            # Preserve the original input/focus error; the session's own latch
            # blocks all further calls even if a fake/backend close fails.
            pass
        raise RuntimeError(self._disabled_reason) from error

    def check_target(self) -> None:
        """Perform a no-input foreground/geometry/DPI check and latch failures."""
        with self._lock:
            self._active()
            try:
                self._mouse.check_target()
            except Exception as error:
                self._fail(error)

    def emit(self, note: MapNote) -> None:
        """Move to a raw SSPM point; the guarded mouse checks focus before input."""
        with self._lock:
            self._active()
            try:
                _note(note)
                calibration = self.calibration
                if calibration.input_mode == ABSOLUTE_MODE:
                    x = (note.x - _FRAME_MIN) / (_FRAME_MAX - _FRAME_MIN)
                    y = (note.y - _FRAME_MIN) / (_FRAME_MAX - _FRAME_MIN)
                    if calibration.invert_x:
                        x = 1 - x
                    if calibration.invert_y:
                        y = 1 - y
                    self._mouse.move_absolute(
                        calibration.left + round(x * (calibration.width - 1)),
                        calibration.top + round(y * (calibration.height - 1)),
                    )
                else:
                    x = (
                        (note.x - self._last_x) * calibration.counts_per_unit_x
                        * (-1 if calibration.invert_x else 1) + self._residual_x
                    )
                    y = (
                        (note.y - self._last_y) * calibration.counts_per_unit_y
                        * (-1 if calibration.invert_y else 1) + self._residual_y
                    )
                    dx, dy = round(x), round(y)
                    self._mouse.move_relative(dx, dy)
                    self._residual_x, self._residual_y = x - dx, y - dy
                    self._last_x, self._last_y = float(note.x), float(note.y)
            except Exception as error:
                self._fail(error)

    def close(self) -> None:
        """Permanently disarm input; safe to call repeatedly or from any thread."""
        with self._lock:
            if self._disabled_reason is None:
                self._disabled_reason = "Live input was stopped. Restart playback to rearm."
                try:
                    self._mouse.close()
                except Exception:
                    # This session is already disabled and cannot send again.
                    pass
