from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from rhythia_tool.settings import Settings, SettingsError, load_settings, save_settings


class SettingsTests(unittest.TestCase):
    def test_round_trip_with_unicode_path_and_negative_monitor_origin(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nested" / "settings.json"
            expected = replace(Settings(), left=-1920, last_map_path="C:/maps/音楽.sspm", speed=1.5)
            save_settings(path, expected)
            self.assertEqual(load_settings(path), expected)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_corrupt_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(SettingsError):
                load_settings(path)
            self.assertEqual(path.read_text(), "{broken")

    def test_missing_file_uses_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(load_settings(Path(folder) / "missing.json"), Settings())

    def test_rejects_nonfinite_invalid_types_and_unknown_fields(self):
        for changed in ({"speed": float("nan")}, {"width": 0}, {"countdown_s": True},
                        {"left": 1.5}, {"input_mode": "made-up"}, {"offset_ms": -6000}):
            with self.subTest(changed=changed), self.assertRaises(SettingsError):
                replace(Settings(), **changed).validate()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({"unsupported": 1}))
            with self.assertRaises(SettingsError):
                load_settings(path)

    def test_calibration_uses_physical_pixel_edges(self):
        settings = replace(Settings(), left=-1920, top=40, width=1920, height=1080)
        self.assertEqual(settings.physical_position(0, 0), (-1920, 40))
        self.assertEqual(settings.physical_position(1, 1), (-1, 1119))
        with self.assertRaises(SettingsError):
            settings.physical_position(2, 0)

    def test_invalid_save_does_not_replace_existing_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            save_settings(path, Settings())
            before = path.read_bytes()
            with self.assertRaises(SettingsError):
                save_settings(path, replace(Settings(), speed=0))
            self.assertEqual(path.read_bytes(), before)

    def test_hostile_numeric_and_nested_settings_fail_cleanly(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            for text in ('{"speed":' + '9' * 400 + '}', '[' * 10000 + ']' * 10000):
                with self.subTest(text=text[:20]):
                    path.write_text(text)
                    with self.assertRaises(SettingsError):
                        load_settings(path)
                    self.assertEqual(path.read_text(), text)


if __name__ == "__main__":
    unittest.main()
