import unittest

from rhythia_tool.demo import load_demo


class DemoTests(unittest.TestCase):
    def test_demo_is_deterministic_normalized_and_explicitly_synthetic(self):
        first = load_demo()
        self.assertEqual(first, load_demo())
        self.assertIn("Synthetic", first.title)
        self.assertIn("not a Rhythia beatmap", first.title)
        self.assertEqual(len(first.notes), 32)
        self.assertEqual(first.notes[0].time_ms, 0)
        self.assertEqual(first.notes[-1].time_ms, 15_500)
        self.assertGreaterEqual(first.duration_ms, first.notes[-1].time_ms)
        self.assertEqual(list(first.notes), sorted(first.notes, key=lambda note: note.time_ms))
        for note in first.notes:
            self.assertTrue(0 <= note.x <= 1)
            self.assertTrue(0 <= note.y <= 1)


if __name__ == "__main__":
    unittest.main()
