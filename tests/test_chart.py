import unittest
from types import SimpleNamespace

from rhythia_tool.chart import display_text, preview_sspm
from rhythia_tool.sspm import MapFormatError, MapNote


class ChartPreviewTests(unittest.TestCase):
    def test_quantum_coordinates_expand_preview_without_clamping_or_reordering(self):
        raw = (MapNote(50, -2.0, 0.0), MapNote(100, 1.0, 4.0), MapNote(150, 2.5, -0.5))
        source = SimpleNamespace(notes=raw, title="Quantum fixture", duration_ms=150)
        preview = preview_sspm(source)
        self.assertEqual(preview.bounds, (-2.0, -0.5, 2.5, 4.0))
        self.assertEqual(preview.notes[0].x, 0)
        self.assertEqual(preview.notes[1].y, 1)
        self.assertEqual(tuple(n.time_ms for n in preview.notes), (50, 100, 150))
        # The input remains the raw SSPM data; normalizing is a drawing operation.
        self.assertEqual(source.notes[0].x, -2)

    def test_grid_centers_remain_in_middle_of_three_cells(self):
        raw = tuple(MapNote(i * 100, i, i) for i in range(3))
        preview = preview_sspm(SimpleNamespace(notes=raw, title="Grid", duration_ms=200))
        for index, note in enumerate(preview.notes):
            self.assertAlmostEqual(note.x, (index + 0.5) / 3)
            self.assertAlmostEqual(note.y, (index + 0.5) / 3)

    def test_empty_map_is_not_playable(self):
        with self.assertRaises(MapFormatError):
            preview_sspm(SimpleNamespace(notes=(), title="Empty", duration_ms=0))

    def test_metadata_is_bounded_inert_text(self):
        self.assertEqual(display_text("Map\n\x00\u202efile", 30), "Map   file")
        self.assertEqual(display_text("a" * 50, 10), "a" * 10 + "...")


if __name__ == "__main__":
    unittest.main()
