"""Deterministic cursor paths between raw SSPM notes, without input or clocks.

The playback scheduler remains responsible for note deadlines and counts. This
helper only supplies an optional position between the previously processed note
and the next one. It never guesses a starting cursor position or adds random
timing, overshoot, or movement during long empty sections of a map.
"""

from __future__ import annotations

from collections.abc import Sequence
import math

from .sspm import MAX_NOTES, MapNote


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number.")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number.") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number.")
    return result


class SmoothPath:
    """Sample minimum-jerk straight paths while preserving note endpoints.

    ``next_index`` is the original playback scheduler's emitted note count; it
    must not be advanced when this helper supplies an intermediate position.
    ``time_ms`` is the map timeline after the caller applies offset and speed.
    Returned notes carry that sample time, not a new deadline. Negative timeline
    values are valid during a countdown and produce no position.

    Construction validates and copies a bounded, sorted sequence of raw SSPM
    notes. Sampling takes constant work and has no side effects or OS calls.
    """

    def __init__(
        self,
        notes: Sequence[MapNote],
        *,
        approach_ms: float = 120.0,
        arrival_lead_ms: float = 12.0,
    ) -> None:
        self.approach_ms = _finite_number(approach_ms, "Approach duration")
        self.arrival_lead_ms = _finite_number(arrival_lead_ms, "Arrival lead")
        if self.approach_ms <= 0:
            raise ValueError("Approach duration must be greater than zero.")
        if self.arrival_lead_ms < 0:
            raise ValueError("Arrival lead must not be negative.")
        if not isinstance(notes, Sequence) or isinstance(notes, (str, bytes, bytearray)):
            raise ValueError("Notes must be a sequence of raw SSPM MapNote values.")
        if len(notes) > MAX_NOTES:
            raise ValueError(f"Smooth paths support at most {MAX_NOTES:,} notes.")
        self._notes = tuple(notes)
        previous_time = -1.0
        for index, note in enumerate(self._notes):
            if not isinstance(note, MapNote):
                raise ValueError(f"Note {index + 1} must be a raw SSPM MapNote.")
            time_ms = _finite_number(note.time_ms, f"Note {index + 1} timestamp")
            x = _finite_number(note.x, f"Note {index + 1} X coordinate")
            y = _finite_number(note.y, f"Note {index + 1} Y coordinate")
            if time_ms < 0 or time_ms < previous_time:
                raise ValueError("Note timestamps must be nonnegative and sorted.")
            if not -0.5 <= x <= 2.5 or not -0.5 <= y <= 2.5:
                raise ValueError("Smooth paths require raw SSPM coordinates within [-0.5, 2.5].")
            previous_time = time_ms

    def sample(self, time_ms: float, next_index: int) -> MapNote | None:
        """Return an optional raw position without changing the scheduler index.

        Before the first note, wait until its arrival lead and then return its
        endpoint directly. For subsequent notes, hold the previous endpoint and
        move only inside a short approach window. A brief hold and arrival lead
        both shrink for closely spaced notes. Simultaneous notes switch directly
        at their shared deadline and never create a zero-duration division.
        """
        sample_time = _finite_number(time_ms, "Sample timeline")
        if isinstance(next_index, bool) or not isinstance(next_index, int):
            raise ValueError("Next note index must be an integer.")
        if not 0 <= next_index <= len(self._notes):
            raise ValueError("Next note index is outside the map.")
        if next_index == len(self._notes) or sample_time < 0:
            return None

        target = self._notes[next_index]
        if next_index == 0:
            arrival = max(0.0, target.time_ms - self.arrival_lead_ms)
            if sample_time < arrival:
                return None
            return MapNote(sample_time, target.x, target.y)

        previous = self._notes[next_index - 1]
        gap = target.time_ms - previous.time_ms
        lead = min(self.arrival_lead_ms, gap / 4.0)
        arrival = target.time_ms - lead
        start = max(
            previous.time_ms + min(8.0, gap / 4.0),
            arrival - self.approach_ms,
        )
        if sample_time >= arrival:
            return MapNote(sample_time, target.x, target.y)
        if sample_time <= start:
            return MapNote(sample_time, previous.x, previous.y)

        fraction = (sample_time - start) / (arrival - start)
        eased = fraction ** 3 * (10.0 + fraction * (-15.0 + 6.0 * fraction))
        # Roundoff near an endpoint must never move outside the calibrated frame.
        eased = min(1.0, max(0.0, eased))
        return MapNote(
            sample_time,
            min(2.5, max(-0.5, previous.x + (target.x - previous.x) * eased)),
            min(2.5, max(-0.5, previous.y + (target.y - previous.y) * eased)),
        )
