"""Read the documented SSPM v2 note container without opening embedded media.

Format reference: https://github.com/basils-garden/types/blob/
4095da95def637822acef7403b8f16278226e169/sspm/v2.md
This format support does not establish compatibility with any game's input,
timing, or score submission. Coordinates stay in the SSPM coordinate system:
0/1/2 mean left/centre/right and top/centre/bottom respectively.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import struct


MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_NOTES = 1_000_000
MAX_MAPPERS = 1024
MAX_CUSTOM_FIELDS = 4096
MAX_VARIABLE_ARRAY_VALUES = 100_000
FIXED_HEADER_BYTES = 128
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class MapFormatError(ValueError):
    """The selected file is damaged, unsupported, or exceeds parser limits."""


@dataclass(frozen=True, slots=True)
class MapNote:
    time_ms: float
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class ParsedMap:
    title: str
    artist: str
    authors: tuple[str, ...]
    notes: tuple[MapNote, ...]
    duration_ms: float
    format_version: int
    map_id: str
    has_audio: bool
    audio_format: str | None
    has_cover: bool
    warnings: tuple[str, ...]
    sha256: str
    declared_duration_ms: float
    difficulty_group: int
    map_rating: int
    requires_mod: bool
    marker_hash_verified: bool


class _Reader:
    def __init__(self, data: memoryview, label: str) -> None:
        self.data = data
        self.label = label
        self.pos = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos

    def take(self, size: int) -> memoryview:
        if size < 0 or size > self.remaining:
            raise MapFormatError(
                f"Truncated {self.label} at byte {self.pos}: "
                f"need {size} bytes, only {self.remaining} remain."
            )
        start = self.pos
        self.pos += size
        return self.data[start:self.pos]

    def number(self, fmt: str) -> int | float:
        size = struct.calcsize("<" + fmt)
        return struct.unpack("<" + fmt, self.take(size))[0]

    def u8(self) -> int:
        return int(self.number("B"))

    def u16(self) -> int:
        return int(self.number("H"))

    def u32(self) -> int:
        return int(self.number("I"))

    def u64(self) -> int:
        return int(self.number("Q"))

    def flag(self, name: str) -> bool:
        value = self.u8()
        if value not in (0, 1):
            raise MapFormatError(f"Invalid {name} flag {value}; expected 0 or 1.")
        return bool(value)

    def text(self, *, long: bool = False) -> str:
        length = self.u32() if long else self.u16()
        raw = self.take(length)
        try:
            return bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MapFormatError(f"Invalid UTF-8 text in {self.label}.") from exc

    def finish(self) -> None:
        if self.remaining:
            raise MapFormatError(
                f"Unexpected {self.remaining} unread bytes in {self.label}; "
                "its length or record count is inconsistent."
            )


def _position(reader: _Reader) -> tuple[float, float]:
    quantum = reader.flag("coordinate format")
    if quantum:
        x, y = float(reader.number("f")), float(reader.number("f"))
    else:
        x, y = float(reader.u8()), float(reader.u8())
    if not math.isfinite(x) or not math.isfinite(y):
        raise MapFormatError("Note coordinates must be finite; found NaN or infinity.")
    return x, y


def _skip_value(reader: _Reader, type_id: int) -> None:
    sizes = {1: 1, 2: 2, 3: 4, 4: 8, 5: 4, 6: 8}
    if type_id in sizes:
        reader.take(sizes[type_id])
    elif type_id == 7:
        _position(reader)
    elif type_id in (8, 10):
        reader.take(reader.u16() if type_id == 8 else reader.u32())
    elif type_id in (9, 11):
        reader.text(long=type_id == 11)
    else:
        raise MapFormatError(f"Unsupported SSPM custom data type 0x{type_id:02x}.")


def _validate_custom_data(data: memoryview) -> None:
    if not data:
        return
    reader = _Reader(data, "custom data block")
    count = reader.u16()
    if count > MAX_CUSTOM_FIELDS:
        raise MapFormatError(f"Custom data exceeds the {MAX_CUSTOM_FIELDS}-field limit.")
    seen: set[str] = set()
    variable_values = 0
    for _ in range(count):
        name = reader.text()
        if name in seen:
            raise MapFormatError("Duplicate custom data field ID.")
        seen.add(name)
        type_id = reader.u8()
        if type_id == 12:
            subtype = reader.u8()
            if not 1 <= subtype <= 11:
                raise MapFormatError(f"Unsupported SSPM array type 0x{subtype:02x}.")
            array = _Reader(reader.take(reader.u32()), "custom data array")
            length = array.u16()
            scalar_sizes = {1: 1, 2: 2, 3: 4, 4: 8, 5: 4, 6: 8}
            if subtype in scalar_sizes:
                # Fixed-size custom values need no per-element interpretation.
                array.take(length * scalar_sizes[subtype])
            else:
                variable_values += length
                if variable_values > MAX_VARIABLE_ARRAY_VALUES:
                    raise MapFormatError(
                        f"Custom arrays exceed the {MAX_VARIABLE_ARRAY_VALUES:,}-value parsing work limit."
                    )
                for _ in range(length):
                    _skip_value(array, subtype)
            array.finish()
        else:
            _skip_value(reader, type_id)
    reader.finish()


def _inert_text(value: str, warnings: list[str]) -> str:
    # Map metadata is data. In particular it never supplies paths, commands,
    # Python expressions, URLs to visit, or instructions to the application.
    safe = "".join(char if char.isprintable() else "\ufffd" for char in value)
    if safe != value and "Metadata control characters were replaced for display." not in warnings:
        warnings.append("Metadata control characters were replaced for display.")
    return safe


def load_sspm(path: Path) -> ParsedMap:
    """Read a local file, with a size bound before parsing or allocating notes."""
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    return parse_sspm(data)


def parse_sspm(data: bytes) -> ParsedMap:
    """Parse SSPMv2 files containing only the standard ``ssp_note`` marker.

    Other container versions and additional marker schemas fail explicitly.
    Media are bounded and identified by their signatures, never decoded or run.
    A missing (all-zero) marker SHA-1 is reported rather than called verified.
    """
    if len(data) > MAX_FILE_BYTES:
        raise MapFormatError(f"Map exceeds the {MAX_FILE_BYTES // (1024 * 1024)} MiB file limit.")
    view = memoryview(data)
    header = _Reader(view, "SSPM header")
    if bytes(header.take(4)) != b"SS+m":
        raise MapFormatError("Incorrect SSPM signature; expected SS+m.")
    version = header.u16()
    if version != 2:
        raise MapFormatError(f"Unsupported SSPM format version {version}; only SSPMv2 is supported.")
    if bytes(header.take(4)) != b"\x00" * 4:
        raise MapFormatError("Invalid SSPMv2 reserved header bytes.")
    marker_hash = bytes(header.take(20))
    declared_duration = header.u32()
    note_count, marker_count = header.u32(), header.u32()
    if note_count > MAX_NOTES:
        raise MapFormatError(f"Map exceeds the {MAX_NOTES:,}-note limit.")
    if marker_count != note_count:
        raise MapFormatError("Marker count differs from note count; non-note markers are unsupported.")
    difficulty = header.u8()
    if difficulty > 5:
        raise MapFormatError(f"Unsupported SSPM difficulty group {difficulty}.")
    rating = header.u16()
    has_audio = header.flag("audio")
    has_cover = header.flag("cover")
    requires_mod = header.flag("requires mod")
    sections: dict[str, tuple[int, int]] = {}
    for name in ("custom data", "audio", "cover", "marker definitions", "markers"):
        offset, length = header.u64(), header.u64()
        if offset > len(view) or length > len(view) - offset:
            raise MapFormatError(f"The {name} block points outside the file.")
        if length and offset < FIXED_HEADER_BYTES:
            raise MapFormatError(f"The {name} block overlaps the fixed header.")
        sections[name] = offset, length

    for name, present in (("audio", has_audio), ("cover", has_cover)):
        offset, length = sections[name]
        if present and not length:
            raise MapFormatError(f"The {name} flag is set, but its block is empty.")
        if not present and (offset or length):
            raise MapFormatError(f"The {name} block is present, but its flag is unset.")

    nonempty = sorted((offset, offset + size, name) for name, (offset, size) in sections.items() if size)
    for (_, end, name), (start, _, other) in zip(nonempty, nonempty[1:]):
        if end > start:
            raise MapFormatError(f"The {name} and {other} blocks overlap.")
    # Metadata must end before any pointed-to data. Reading it through this
    # bounded view prevents hostile lengths from consuming audio as text.
    metadata_end = nonempty[0][0] if nonempty else len(view)
    strings = _Reader(view[FIXED_HEADER_BYTES:metadata_end], "map metadata")
    map_id, map_name, song_name = strings.text(), strings.text(), strings.text()
    mapper_count = strings.u16()
    if mapper_count > MAX_MAPPERS:
        raise MapFormatError(f"Map exceeds the {MAX_MAPPERS}-mapper limit.")
    authors = tuple(strings.text() for _ in range(mapper_count))
    # Exporters may insert padding after metadata, so do not require finish().

    def block(name: str) -> memoryview:
        offset, size = sections[name]
        return view[offset:offset + size]

    _validate_custom_data(block("custom data"))
    definitions = _Reader(block("marker definitions"), "marker definitions block")
    if definitions.u8() != 1:
        raise MapFormatError("Only one standard ssp_note marker definition is supported.")
    if definitions.text() != "ssp_note":
        raise MapFormatError("Unsupported marker definition; expected ssp_note.")
    if definitions.u8() != 1 or definitions.u8() != 7 or definitions.u8() != 0:
        raise MapFormatError("Unsupported ssp_note schema; expected one position value (0x07).")
    definitions.finish()

    markers = _Reader(block("markers"), "marker block")
    if note_count > len(markers.data) // 8:
        raise MapFormatError("Note count cannot fit inside the declared marker block.")
    notes: list[MapNote] = []
    for _ in range(note_count):
        time_ms = markers.u32()
        marker_type = markers.u8()
        if marker_type != 0:
            raise MapFormatError(f"Unsupported marker type {marker_type}; only standard notes are supported.")
        x, y = _position(markers)
        notes.append(MapNote(float(time_ms), x, y))
    markers.finish()

    warnings: list[str] = []
    verified = marker_hash != bytes(20)
    if verified:
        # Reference writer hashes definition bytes followed by marker bytes.
        digest = hashlib.sha1()
        digest.update(block("marker definitions"))
        digest.update(block("markers"))
        if digest.digest() != marker_hash:
            raise MapFormatError("SSPM marker SHA-1 does not match; the map may be damaged.")
    else:
        warnings.append("Marker SHA-1 is absent (all zeros); marker integrity is unverified.")
    if any(first.time_ms > second.time_ms for first, second in zip(notes, notes[1:])):
        notes.sort(key=lambda note: note.time_ms)
        warnings.append("Notes were not in timestamp order and have been sorted.")
    if any(note.x < 0 or note.x > 2 or note.y < 0 or note.y > 2 for note in notes):
        warnings.append("Quantum notes extend outside the 3 by 3 grid; raw coordinates are preserved.")
    if any(first.time_ms == second.time_ms and (first.x, first.y) != (second.x, second.y)
           for first, second in zip(notes, notes[1:])):
        warnings.append("Simultaneous notes have different positions; one cursor may not reach all of them.")
    last_note_ms = notes[-1].time_ms if notes else 0.0
    if declared_duration != last_note_ms:
        warnings.append("Declared last-marker time differs from the last note time.")
    used_end = max([FIXED_HEADER_BYTES + strings.pos] + [end for _, end, _ in nonempty])
    if used_end < len(view):
        warnings.append(f"Ignored {len(view) - used_end} trailing bytes outside declared blocks.")
    audio_format = None
    if has_audio:
        audio = block("audio")
        if bytes(audio[:4]) == b"OggS":
            audio_format = "ogg"
        elif bytes(audio[:3]) == b"ID3" or (len(audio) >= 2 and audio[0] == 0xFF and audio[1] & 0xE0 == 0xE0):
            audio_format = "mp3"
        else:
            raise MapFormatError("Embedded audio has no recognized MP3 or OGG signature.")
        warnings.append("Embedded media were identified by signature only; decoding was not tested.")
    if has_cover and bytes(block("cover")[:8]) != _PNG_SIGNATURE:
        raise MapFormatError("Embedded cover has no PNG signature.")
    clean_name = _inert_text(map_name or song_name or "Untitled map", warnings)
    artist = clean_name.split(" - ", 1)[0].strip() if " - " in clean_name else ""
    return ParsedMap(
        title=clean_name,
        artist=artist,
        authors=tuple(_inert_text(name, warnings) for name in authors),
        notes=tuple(notes),
        duration_ms=max(float(declared_duration), last_note_ms),
        format_version=version,
        map_id=_inert_text(map_id, warnings),
        has_audio=has_audio,
        audio_format=audio_format,
        has_cover=has_cover,
        warnings=tuple(warnings),
        sha256=hashlib.sha256(view).hexdigest(),
        declared_duration_ms=float(declared_duration),
        difficulty_group=difficulty,
        map_rating=rating,
        requires_mod=requires_mod,
        marker_hash_verified=verified,
    )
