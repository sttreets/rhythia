"""Edition-independent playback driven by an absolute monotonic clock.

This module does not parse game maps, move the OS cursor, start audio, or submit
scores. ``emit`` is an injected output callback and must return promptly. A
worker may call ``pump`` frequently; sleeping and thread creation belong to the
caller. Deadlines never depend on the previous pump or event, so polling jitter
does not accumulate as drift.

``start`` sets the song origin to ``reference_ns + countdown_s`` when an explicit
monotonic reference is supplied, otherwise ``now + countdown_s``. Callers can
capture that reference when a Start hotkey arrives, before preparing a session.
Preparation time then counts toward the timeline instead of silently delaying
it; the usual lateness cutoff stops an already-late first note before output.
A note's deadline is ``origin + offset_ms + note.time_ms / speed``. Offset is
measured in wall-clock milliseconds; a positive value emits later. A negative
offset may emit notes during the final countdown. ``elapsed_ms`` is song
position, scaled by speed, independent of offset, and zero before the origin.

Pause freezes this simulated timeline. Resume moves all remaining deadlines by
the paused duration. This does not pause a real game or song and establishes no
real-world synchronization. A pump more than ``max_lateness_ms`` behind the next
note stops before emitting it and requires a fresh start. Equal-time notes are
allowed. Callbacks run under the state lock: when ``stop`` returns, no new
callback can begin until another explicit ``start``. An already-running callback
cannot be interrupted, so sinks must be short and nonblocking.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from numbers import Real


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number.")
    return result


@dataclass(frozen=True, slots=True)
class Note:
    """One timed point; coordinates are normalized to the preview rectangle."""

    time_ms: float
    x: float
    y: float

    def __post_init__(self) -> None:
        for field in ("time_ms", "x", "y"):
            object.__setattr__(self, field, _number(getattr(self, field), field))
        if self.time_ms < 0:
            raise ValueError("Note time_ms must be nonnegative.")
        if not 0 <= self.x <= 1 or not 0 <= self.y <= 1:
            raise ValueError("Demo note coordinates must be within [0, 1].")


class PlaybackState(str, Enum):
    IDLE = "idle"
    COUNTDOWN = "countdown"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class PlaybackSnapshot:
    state: PlaybackState
    elapsed_ms: float
    countdown_remaining_s: float
    emitted_count: int
    max_lateness_ms: float
    message: str


class PlaybackEngine:
    """Thread-safe state machine for a short-lived injected event sink."""

    _ACTIVE = frozenset((PlaybackState.COUNTDOWN, PlaybackState.RUNNING))

    def __init__(
        self,
        notes: Iterable[Note],
        emit: Callable[[Note], object],
        clock_ns: Callable[[], int] = time.perf_counter_ns,
        *,
        max_lateness_ms: float = 250.0,
    ) -> None:
        self._notes = tuple(notes)
        if not self._notes:
            raise ValueError("Playback requires at least one note.")
        if any(not isinstance(note, Note) for note in self._notes):
            raise ValueError("Playback accepts Note objects only.")
        if any(a.time_ms > b.time_ms for a, b in zip(self._notes, self._notes[1:])):
            raise ValueError("Notes must be sorted by nondecreasing time_ms.")
        if not callable(emit) or not callable(clock_ns):
            raise ValueError("emit and clock_ns must be callable.")
        lateness = _number(max_lateness_ms, "max_lateness_ms")
        if not 0 < lateness <= 60_000:
            raise ValueError("max_lateness_ms must be greater than 0 and at most 60000.")
        self._lateness_limit_ns = round(lateness * 1_000_000)
        self._emit = emit
        self._clock_ns = clock_ns
        self._lock = threading.RLock()
        self._state = PlaybackState.IDLE
        self._message = "Ready."
        self._origin_ns: int | None = None
        self._paused_at_ns: int | None = None
        self._terminal_at_ns: int | None = None
        self._last_active_ns: int | None = None
        self._offset_ns = 0
        self._speed = 1.0
        self._deadlines: tuple[int, ...] = ()
        self._index = 0
        self._max_lateness_ns = 0
        self._generation = 0

    def _read_clock(self) -> int:
        now = self._clock_ns()
        if isinstance(now, bool) or not isinstance(now, int):
            raise ValueError("clock_ns must return integer monotonic nanoseconds.")
        return now

    def _active_clock(self) -> int | None:
        """Reject clock regressions before allowing a simulated callback."""
        try:
            now = self._read_clock()
        except Exception as exc:
            self._finish(PlaybackState.ERROR, f"Clock error: {exc}", self._last_active_ns)
            return None
        if self._last_active_ns is not None and now < self._last_active_ns:
            self._finish(
                PlaybackState.ERROR,
                "Monotonic clock moved backwards; restart playback.",
                self._last_active_ns,
            )
            return None
        self._last_active_ns = now
        return now

    def _finish(self, state: PlaybackState, message: str, now: int | None) -> None:
        self._state = state
        self._message = message
        self._terminal_at_ns = now
        self._paused_at_ns = None

    def start(
        self,
        countdown_s: float = 3.0,
        offset_ms: float = 0.0,
        speed: float = 1.0,
        *,
        reference_ns: int | None = None,
    ) -> PlaybackSnapshot:
        """Start fresh, optionally anchored to an earlier Start-hotkey instant.

        ``reference_ns`` uses the same monotonic clock as ``clock_ns``. It must
        be an integer no later than the actual start-time observation and no
        more than one hour earlier. Invalid arguments leave any run unchanged.
        """
        countdown = _number(countdown_s, "countdown_s")
        offset = _number(offset_ms, "offset_ms")
        rate = _number(speed, "speed")
        if not 0 <= countdown <= 3600:
            raise ValueError("countdown_s must be between 0 and 3600 seconds.")
        if not -5000 <= offset <= 5000:
            raise ValueError("offset_ms must be between -5000 and 5000 milliseconds.")
        if not 0.1 <= rate <= 4.0:
            raise ValueError("speed must be between 0.1 and 4.0.")
        if reference_ns is not None and (
            isinstance(reference_ns, bool) or not isinstance(reference_ns, int)
        ):
            raise ValueError("reference_ns must be integer monotonic nanoseconds.")
        try:
            relative_deadlines = tuple(
                round(note.time_ms * (1_000_000 / rate)) for note in self._notes
            )
        except (OverflowError, ValueError) as exc:
            raise ValueError("Note timestamps are too large for playback.") from exc
        with self._lock:
            now = self._read_clock()
            reference = now if reference_ns is None else reference_ns
            if not 0 <= now - reference <= 3_600_000_000_000:
                raise ValueError("reference_ns must not be in the future or more than one hour old.")
            self._origin_ns = reference + round(countdown * 1_000_000_000)
            self._offset_ns = round(offset * 1_000_000)
            self._speed = rate
            self._deadlines = tuple(
                self._origin_ns + self._offset_ns + relative for relative in relative_deadlines
            )
            self._paused_at_ns = None
            self._terminal_at_ns = None
            self._last_active_ns = now
            self._index = 0
            self._max_lateness_ns = 0
            self._generation += 1
            self._state = PlaybackState.COUNTDOWN if now < self._origin_ns else PlaybackState.RUNNING
            self._message = (
                "Countdown."
                if self._state == PlaybackState.COUNTDOWN
                else "Running."
            )
            return self._snapshot(now)

    def pump(self) -> PlaybackSnapshot:
        """Emit due simulated points, or stop before an excessively late point."""
        with self._lock:
            if self._state not in self._ACTIVE:
                return self.snapshot()
            now = self._active_clock()
            if now is None:
                return self._snapshot(self._terminal_at_ns)
            self._state = (
                PlaybackState.COUNTDOWN
                if self._origin_ns is not None and now < self._origin_ns
                else PlaybackState.RUNNING
            )
            self._message = (
                "Countdown."
                if self._state == PlaybackState.COUNTDOWN
                else "Running."
            )
            generation = self._generation
            while self._state in self._ACTIVE and self._index < len(self._notes):
                deadline = self._deadlines[self._index]
                if deadline > now:
                    break
                lateness = now - deadline
                self._max_lateness_ns = max(self._max_lateness_ns, lateness)
                if lateness > self._lateness_limit_ns:
                    self._finish(
                        PlaybackState.STOPPED,
                        "Synchronization lost: a point is "
                        f"{lateness / 1_000_000:.1f} ms late. Restart playback.",
                        now,
                    )
                    break
                note = self._notes[self._index]
                try:
                    self._emit(note)
                except Exception as exc:
                    if generation == self._generation and self._state in self._ACTIVE:
                        self._finish(PlaybackState.ERROR, f"Output failed: {exc}", now)
                    break
                # A reentrant callback can explicitly stop or replace this run.
                if generation != self._generation:
                    break
                self._index += 1
                if self._state not in self._ACTIVE:
                    break
                observed = self._active_clock()
                if observed is None:
                    break
                now = observed
            if self._state in self._ACTIVE and self._index == len(self._notes):
                self._finish(PlaybackState.COMPLETED, "Playback complete.", now)
            return self._snapshot(now)

    def pause(self) -> PlaybackSnapshot:
        """Freeze simulation only; a real song would require separate handling."""
        with self._lock:
            if self._state in self._ACTIVE:
                now = self._active_clock()
                if now is not None:
                    self._paused_at_ns = now
                    self._state = PlaybackState.PAUSED
                    self._message = "Timeline paused; no game or song was paused."
            return self.snapshot()

    def resume(self) -> PlaybackSnapshot:
        """Resume the frozen simulation by shifting every remaining deadline."""
        with self._lock:
            if self._state == PlaybackState.PAUSED:
                now = self._active_clock()
                if now is not None:
                    assert self._paused_at_ns is not None and self._origin_ns is not None
                    shift = now - self._paused_at_ns
                    self._origin_ns += shift
                    self._deadlines = tuple(deadline + shift for deadline in self._deadlines)
                    self._paused_at_ns = None
                    self._state = (
                        PlaybackState.COUNTDOWN
                        if now < self._origin_ns
                        else PlaybackState.RUNNING
                    )
                    self._message = "Timeline resumed."
            return self.snapshot()

    def stop(self, reason: str = "Stopped") -> PlaybackSnapshot:
        """Cancel remaining simulated points; another start is required to run."""
        with self._lock:
            now = self._paused_at_ns
            if now is None:
                now = self._terminal_at_ns
            if now is None:
                try:
                    now = self._read_clock()
                except Exception:
                    # Cancellation must remain possible even if a custom clock fails.
                    now = self._last_active_ns
            self._finish(PlaybackState.STOPPED, str(reason), now)
            return self._snapshot(now)

    def _snapshot(self, now: int | None) -> PlaybackSnapshot:
        effective_now = self._paused_at_ns
        if effective_now is None:
            effective_now = self._terminal_at_ns
        if effective_now is None:
            effective_now = now
        if self._origin_ns is None or effective_now is None:
            elapsed_ms = 0.0
            remaining_s = 0.0
        else:
            elapsed_ms = max(0.0, (effective_now - self._origin_ns) / 1_000_000 * self._speed)
            remaining_s = max(0.0, (self._origin_ns - effective_now) / 1_000_000_000)
        return PlaybackSnapshot(
            state=self._state,
            elapsed_ms=elapsed_ms,
            countdown_remaining_s=remaining_s,
            emitted_count=self._index,
            max_lateness_ms=self._max_lateness_ns / 1_000_000,
            message=self._message,
        )

    def snapshot(self) -> PlaybackSnapshot:
        """Read immutable status without pumping or emitting any points."""
        with self._lock:
            # Terminal/paused snapshots never need to call a potentially failed clock.
            now = None
            if self._state in self._ACTIVE:
                try:
                    now = self._read_clock()
                except Exception:
                    now = self._last_active_ns
            return self._snapshot(now)
