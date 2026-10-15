"""Built-in synthetic preview data; this is not a supported Rhythia map format."""

from __future__ import annotations

from dataclasses import dataclass

from .playback import Note


@dataclass(frozen=True, slots=True)
class DemoMap:
    title: str
    notes: tuple[Note, ...]
    duration_ms: float


def load_demo() -> DemoMap:
    """Return a deterministic 16-second pattern without reading local game files."""
    corners = ((0.15, 0.15), (0.85, 0.15), (0.85, 0.85), (0.15, 0.85))
    notes = tuple(
        Note(time_ms=index * 500.0, x=corners[index % 4][0], y=corners[index % 4][1])
        for index in range(32)
    )
    return DemoMap(
        title="Synthetic preview — not a Rhythia beatmap",
        notes=notes,
        duration_ms=16_000.0,
    )
