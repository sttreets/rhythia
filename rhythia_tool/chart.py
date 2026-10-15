"""Faithful chart preview coordinates, separate from unverified game input."""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from .playback import Note
from .sspm import MapFormatError, ParsedMap


@dataclass(frozen=True)
class ChartPreview:
    title: str
    notes: tuple[Note, ...]
    duration_ms: float
    bounds: tuple[float, float, float, float]


def preview_sspm(chart: ParsedMap) -> ChartPreview:
    """Fit raw SSPM positions in a preview without clipping quantum positions.

    SSPM specifies 0/1/2 as left/center/right and top/center/bottom.
    The default frame puts those centers in three equal cells. It expands to
    contain quantum points outside that frame. This defines drawing only, not
    a Steam window-to-input transform or verified in-game calibration.
    """
    if not chart.notes:
        raise MapFormatError("This map contains no playable notes.")
    left = min(-0.5, min(note.x for note in chart.notes))
    right = max(2.5, max(note.x for note in chart.notes))
    top = min(-0.5, min(note.y for note in chart.notes))
    bottom = max(2.5, max(note.y for note in chart.notes))
    notes = tuple(Note(note.time_ms, (note.x - left) / (right - left),
                       (note.y - top) / (bottom - top)) for note in chart.notes)
    return ChartPreview(chart.title, notes, chart.duration_ms, (left, top, right, bottom))


def display_text(value: str, limit: int = 140) -> str:
    """Show map metadata as bounded inert text, without control/bidi characters."""
    cleaned = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in value)
    return cleaned if len(cleaned) <= limit else cleaned[:limit] + "..."
