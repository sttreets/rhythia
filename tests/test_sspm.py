"""Synthetic format fixtures only: no downloaded beatmaps or embedded songs."""

import hashlib
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from rhythia_tool.sspm import MAX_NOTES, MapFormatError, load_sspm, parse_sspm


def _text(value):
    encoded = value.encode("utf-8")
    return struct.pack("<H", len(encoded)) + encoded


def build_sspm_v2(
    *,
    notes=((100, 0, 0), (500, 1, 1), (1000, 2, 2)),
    title="Synthetic Artist - Synthetic Song",
    song_name=None,
    authors=("Fixture Author",),
    map_id="synthetic-fixture",
    audio=b"",
    cover=b"",
    declared_duration_ms=None,
    custom_data=b"\x00\x00",
    hash_markers=True,
    footer=b"",
):
    """Build SSPMv2 bytes; notes are ``(milliseconds, raw_x, raw_y)`` tuples.

    Raw grid coordinates are 0..2. Floats, negatives, and outer positions use
    the quantum form. ``audio``/``cover`` are caller-owned synthetic bytes.
    """
    notes = tuple(notes)
    strings = (
        _text(map_id) + _text(title) + _text(title if song_name is None else song_name)
        + struct.pack("<H", len(authors)) + b"".join(_text(author) for author in authors)
    )
    definitions = b"\x01" + _text("ssp_note") + b"\x01\x07\x00"
    markers = bytearray()
    for ms, x, y in notes:
        grid = all(isinstance(value, int) and 0 <= value <= 2 for value in (x, y))
        markers += struct.pack("<IBB", ms, 0, not grid)
        markers += struct.pack("<BB" if grid else "<ff", x, y)
    marker_hash = hashlib.sha1(definitions + markers).digest() if hash_markers else bytes(20)
    if declared_duration_ms is None:
        declared_duration_ms = max((note[0] for note in notes), default=0)
    metadata = struct.pack("<IIIBHBBB", declared_duration_ms, len(notes), len(notes), 3, 0, bool(audio), bool(cover), False)
    offset = 128 + len(strings)
    pointers = bytearray()
    blocks = []
    for block in (custom_data, audio, cover, definitions, markers):
        pointers += struct.pack("<QQ", offset if block else 0, len(block))
        blocks.append(bytes(block))
        offset += len(block)
    return b"SS+m\x02\x00" + bytes(4) + marker_hash + metadata + bytes(pointers) + strings + b"".join(blocks) + footer


def _mutate(data, offset, fmt, *values):
    result = bytearray(data)
    struct.pack_into("<" + fmt, result, offset, *values)
    return bytes(result)


class SSPMTests(unittest.TestCase):
    def test_grid_metadata_hash_and_duration(self):
        data = build_sspm_v2()
        result = parse_sspm(data)
        self.assertEqual(result.title, "Synthetic Artist - Synthetic Song")
        self.assertEqual(result.artist, "Synthetic Artist")
        self.assertEqual(result.authors, ("Fixture Author",))
        self.assertEqual(result.map_id, "synthetic-fixture")
        self.assertEqual(result.format_version, 2)
        self.assertEqual(result.duration_ms, 1000)
        self.assertEqual(result.declared_duration_ms, 1000)
        self.assertEqual([(n.time_ms, n.x, n.y) for n in result.notes], [(100, 0, 0), (500, 1, 1), (1000, 2, 2)])
        self.assertTrue(result.marker_hash_verified)
        self.assertEqual(result.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(result.warnings, ())
        self.assertFalse(result.has_audio)
        self.assertIsNone(result.audio_format)
        self.assertFalse(result.has_cover)

    def test_load_local_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.sspm"
            path.write_bytes(build_sspm_v2())
            self.assertEqual(load_sspm(path).notes, parse_sspm(path.read_bytes()).notes)

    def test_float_negative_and_outer_coordinates_are_not_clamped(self):
        parsed = parse_sspm(build_sspm_v2(notes=((123, -0.5, 2.5), (456, 1.25, -0.25))))
        self.assertEqual([(n.x, n.y) for n in parsed.notes], [(-0.5, 2.5), (1.25, -0.25)])
        self.assertTrue(any("outside" in warning for warning in parsed.warnings))

    def test_utf8_metadata_and_inert_control_characters(self):
        parsed = parse_sspm(build_sspm_v2(title="\u97f3\u697d\nTITLE", authors=("author\x00name",), map_id="id\u202e"))
        self.assertEqual(parsed.title, "\u97f3\u697d\ufffdTITLE")
        self.assertEqual(parsed.authors, ("author\ufffdname",))
        self.assertEqual(parsed.map_id, "id\ufffd")
        self.assertTrue(any("control characters" in warning for warning in parsed.warnings))

    def test_no_artist_is_inferred_without_separator(self):
        self.assertEqual(parse_sspm(build_sspm_v2(title="Song")).artist, "")

    def test_title_falls_back_to_song_name(self):
        self.assertEqual(parse_sspm(build_sspm_v2(title="", song_name="Song")).title, "Song")

    def test_audio_signatures_and_cover_are_inspected_without_decoding(self):
        for signature, expected in ((b"ID3", "mp3"), (b"\xff\xfb", "mp3"), (b"OggS", "ogg")):
            with self.subTest(expected=expected, signature=signature):
                parsed = parse_sspm(build_sspm_v2(audio=signature + bytes(10), cover=b"\x89PNG\r\n\x1a\n"))
                self.assertTrue(parsed.has_audio)
                self.assertTrue(parsed.has_cover)
                self.assertEqual(parsed.audio_format, expected)
                self.assertTrue(any("signature only" in warning for warning in parsed.warnings))

    def test_unknown_audio_and_invalid_cover_report_clear_errors(self):
        for kwargs, message in (({"audio": b"bad audio"}, "audio"), ({"cover": b"bad cover"}, "cover")):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(MapFormatError, message):
                parse_sspm(build_sspm_v2(**kwargs))

    def test_missing_marker_hash_reports_integrity_unverified(self):
        parsed = parse_sspm(build_sspm_v2(hash_markers=False))
        self.assertFalse(parsed.marker_hash_verified)
        self.assertTrue(any("integrity is unverified" in warning for warning in parsed.warnings))

    def test_corrupt_nonzero_marker_hash_rejected(self):
        with self.assertRaisesRegex(MapFormatError, "SHA-1 does not match"):
            parse_sspm(_mutate(build_sspm_v2(), 10, "B", 42))

    def test_unsorted_notes_are_sorted_with_warning(self):
        parsed = parse_sspm(build_sspm_v2(notes=((500, 1, 1), (0, 0, 0))))
        self.assertEqual([n.time_ms for n in parsed.notes], [0, 500])
        self.assertTrue(any("sorted" in warning for warning in parsed.warnings))

    def test_simultaneous_notes_are_preserved_and_reported(self):
        parsed = parse_sspm(build_sspm_v2(notes=((100, 0, 0), (100, 2, 2))))
        self.assertEqual(len(parsed.notes), 2)
        self.assertTrue(any("Simultaneous" in warning for warning in parsed.warnings))

    def test_empty_note_set_is_valid_but_playback_must_reject_it(self):
        parsed = parse_sspm(build_sspm_v2(notes=()))
        self.assertEqual(parsed.notes, ())
        self.assertEqual(parsed.duration_ms, 0)

    def test_declared_duration_difference_is_reported_and_maximum_used(self):
        for declared, expected in ((10, 1000), (2000, 2000)):
            with self.subTest(declared=declared):
                parsed = parse_sspm(build_sspm_v2(declared_duration_ms=declared))
                self.assertEqual(parsed.duration_ms, expected)
                self.assertTrue(any("Declared" in warning for warning in parsed.warnings))

    def test_empty_custom_data_and_exporter_footer_are_supported(self):
        parsed = parse_sspm(build_sspm_v2(custom_data=b"", footer=b"inert exporter data"))
        self.assertTrue(any("19 trailing bytes" in warning for warning in parsed.warnings))

    def test_padding_between_metadata_and_sections_is_supported(self):
        data = build_sspm_v2()
        first = struct.unpack_from("<Q", data, 48)[0]
        result = bytearray(data[:first] + b"\x00\x00" + data[first:])
        for pointer in range(48, 128, 16):
            offset = struct.unpack_from("<Q", result, pointer)[0]
            if offset:
                struct.pack_into("<Q", result, pointer, offset + 2)
        self.assertEqual(len(parse_sspm(bytes(result)).notes), 3)

    def test_bad_magic_and_unsupported_versions(self):
        for data, message in ((b"BAD!", "signature"), (b"SS+m\x01\x00", "version 1"), (b"SS+m\x03\x00", "version 3")):
            with self.subTest(data=data), self.assertRaisesRegex(MapFormatError, message):
                parse_sspm(data)

    def test_reserved_bytes_difficulty_and_flags_are_validated(self):
        for offset, message in ((6, "reserved"), (42, "difficulty"), (45, "audio flag"), (46, "cover flag"), (47, "requires mod flag")):
            with self.subTest(offset=offset), self.assertRaisesRegex(MapFormatError, message):
                parse_sspm(_mutate(build_sspm_v2(), offset, "B", 255))

    def test_every_truncated_prefix_fails(self):
        data = build_sspm_v2()
        for length in range(len(data)):
            with self.subTest(length=length), self.assertRaises(MapFormatError):
                parse_sspm(data[:length])

    def test_all_section_pointers_and_lengths_are_bounded(self):
        data = build_sspm_v2()
        for pointer in range(48, 128, 16):
            for field in (pointer, pointer + 8):
                with self.subTest(field=field), self.assertRaisesRegex(MapFormatError, "outside"):
                    parse_sspm(_mutate(data, field, "Q", 2**64 - 1))

    def test_sections_cannot_overlap_header_or_each_other(self):
        data = build_sspm_v2()
        with self.assertRaisesRegex(MapFormatError, "fixed header"):
            parse_sspm(_mutate(data, 48, "Q", 10))
        custom_offset = struct.unpack_from("<Q", data, 48)[0]
        with self.assertRaisesRegex(MapFormatError, "overlap"):
            parse_sspm(_mutate(data, 96, "Q", custom_offset))

    def test_asset_presence_flags_must_match_blocks(self):
        with self.assertRaisesRegex(MapFormatError, "empty"):
            parse_sspm(_mutate(build_sspm_v2(), 45, "B", 1))
        with self.assertRaisesRegex(MapFormatError, "flag is unset"):
            parse_sspm(_mutate(build_sspm_v2(audio=b"ID3"), 45, "B", 0))

    def test_metadata_cannot_read_into_sections(self):
        with self.assertRaisesRegex(MapFormatError, "map metadata"):
            parse_sspm(_mutate(build_sspm_v2(), 128, "H", 65535))

    def test_invalid_utf8_is_rejected(self):
        with self.assertRaisesRegex(MapFormatError, "UTF-8"):
            parse_sspm(_mutate(build_sspm_v2(), 130, "B", 255))

    def test_note_counts_are_bounded_and_consistent(self):
        data = build_sspm_v2()
        for offset, value, message in ((34, MAX_NOTES + 1, "note limit"), (38, 2, "differs")):
            with self.subTest(offset=offset), self.assertRaisesRegex(MapFormatError, message):
                parse_sspm(_mutate(data, offset, "I", value))
        data = _mutate(_mutate(data, 34, "I", 100), 38, "I", 100)
        with self.assertRaisesRegex(MapFormatError, "cannot fit"):
            parse_sspm(data)

    def test_unknown_marker_definitions_and_schemas_are_rejected(self):
        data = build_sspm_v2()
        offset = struct.unpack_from("<Q", data, 96)[0]
        for relative, value, message in ((0, 2, "one standard"), (3, ord("x"), "expected ssp_note"), (11, 2, "schema"), (12, 5, "schema"), (13, 1, "schema")):
            with self.subTest(relative=relative), self.assertRaisesRegex(MapFormatError, message):
                parse_sspm(_mutate(data, offset + relative, "B", value))

    def test_unknown_markers_and_coordinate_flags_are_rejected(self):
        data = build_sspm_v2()
        offset = struct.unpack_from("<Q", data, 112)[0]
        with self.assertRaisesRegex(MapFormatError, "marker type 1"):
            parse_sspm(_mutate(data, offset + 4, "B", 1))
        with self.assertRaisesRegex(MapFormatError, "coordinate format"):
            parse_sspm(_mutate(data, offset + 5, "B", 2))

    def test_nan_and_infinite_coordinates_are_rejected(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaisesRegex(MapFormatError, "finite"):
                parse_sspm(build_sspm_v2(notes=((0, value, 1.0),)))

    def test_marker_block_length_must_match_note_records(self):
        data = build_sspm_v2(footer=bytes(1))
        length = struct.unpack_from("<Q", data, 120)[0]
        with self.assertRaisesRegex(MapFormatError, "unread bytes"):
            parse_sspm(_mutate(data, 120, "Q", length + 1))

    def test_valid_custom_fields_are_bounded_and_inert(self):
        field = _text("difficulty_name") + b"\x09" + _text("Synthetic")
        array = b"\x03\x00" + struct.pack("<HHH", 1, 2, 3)
        fields = field + _text("numbers") + b"\x0c\x02" + struct.pack("<I", len(array)) + array
        parsed = parse_sspm(build_sspm_v2(custom_data=b"\x02\x00" + fields))
        self.assertEqual(len(parsed.notes), 3)

    def test_duplicate_unknown_and_truncated_custom_fields_rejected(self):
        field = _text("name") + b"\x09" + _text("value")
        cases = (
            (b"\x02\x00" + field + field, "Duplicate"),
            (b"\x01\x00" + _text("name") + b"\xff", "type 0xff"),
            (b"\x01\x00" + _text("name") + b"\x09\xff\xff", "Truncated"),
            (b"\x01\x00" + _text("name") + b"\x0c\x0c", "array type"),
            (b"\x00\x00\x00", "unread bytes"),
        )
        for custom, message in cases:
            with self.subTest(custom=custom), self.assertRaisesRegex(MapFormatError, message):
                parse_sspm(build_sspm_v2(custom_data=custom))

    def test_large_fixed_scalar_array_is_validated_by_byte_length(self):
        values = struct.pack("<H", 65535) + bytes(65535)
        custom = b"\x01\x00" + _text("fixed") + b"\x0c\x01" + struct.pack("<I", len(values)) + values
        parsed = parse_sspm(build_sspm_v2(custom_data=custom))
        self.assertEqual(len(parsed.notes), 3)
        with self.assertRaisesRegex(MapFormatError, "Truncated"):
            parse_sspm(build_sspm_v2(custom_data=custom[:-1]))

    def test_variable_custom_arrays_have_an_aggregate_work_limit(self):
        # Many empty strings are cheap in bytes but costly to interpret one by
        # one; the aggregate limit must apply across separate array fields.
        values = struct.pack("<H", 50001) + b"\x00\x00" * 50001
        array = b"\x0c\x09" + struct.pack("<I", len(values)) + values
        custom = b"\x02\x00" + _text("first") + array + _text("second") + array
        with self.assertRaisesRegex(MapFormatError, "work limit"):
            parse_sspm(build_sspm_v2(custom_data=custom))

    def test_file_size_limit_is_checked_before_parsing(self):
        with patch("rhythia_tool.sspm.MAX_FILE_BYTES", 4):
            with self.assertRaisesRegex(MapFormatError, "file limit"):
                parse_sspm(b"SS+m\x02")


if __name__ == "__main__":
    unittest.main()
