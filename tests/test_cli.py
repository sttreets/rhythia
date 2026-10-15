import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

from test_sspm import build_sspm_v2


PROJECT = Path(__file__).resolve().parent.parent


class CommandLineTests(unittest.TestCase):
    def run_tool(self, *arguments):
        # No display is needed or initialized for these real entry-point calls.
        return subprocess.run([sys.executable, str(PROJECT / "launch.py"), *map(str, arguments)],
                              capture_output=True, text=True, timeout=10)

    def test_inspect_reports_raw_quantum_positions_and_no_game_compatibility_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chart with spaces.sspm"
            path.write_bytes(build_sspm_v2(notes=((491, -0.5, 0), (31897, 2.5, 2.33))))
            result = self.run_tool("--inspect-map", path)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["note_count"], 2)
            self.assertEqual(data["first_note_ms"], 491)
            self.assertEqual(data["last_note_ms"], 31897)
            self.assertEqual(data["x_range"], [-0.5, 2.5])
            self.assertTrue(data["marker_hash_verified"])
            self.assertEqual(data["steam_compatibility"], "unverified")
            self.assertEqual(data["rp_eligibility"], "unverified")

    def test_simulate_completes_long_timeline_with_same_time_notes_without_sleeping(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "long.sspm"
            path.write_bytes(build_sspm_v2(notes=((0, 0, 0), (0, 1, 1), (4_000_000_000, 2, 2))))
            result = self.run_tool("--simulate-map", path)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["notes_emitted"], 3)
            self.assertEqual(data["state"], "completed")
            self.assertEqual(data["max_lateness_ms"], 0)
            self.assertFalse(data["live_input"])
            self.assertFalse(data["audio_played"])

    def test_invalid_or_unsupported_files_exit_nonzero_without_traceback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "damaged.sspm"
            for content in (b"bad data", b"SS+m" + struct.pack("<H", 1), build_sspm_v2()[:-1]):
                path.write_bytes(content)
                result = self.run_tool("--inspect-map", path)
                self.assertEqual(result.returncode, 2)
                self.assertIn("error:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_empty_container_can_be_inspected_but_not_played(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "empty.sspm"
            path.write_bytes(build_sspm_v2(notes=()))
            inspected = self.run_tool("--inspect-map", path)
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            self.assertEqual(json.loads(inspected.stdout)["note_count"], 0)
            played = self.run_tool("--simulate-map", path)
            self.assertEqual(played.returncode, 2)
            self.assertIn("no playable notes", played.stderr)

    def test_headless_self_check_and_incompatible_modes(self):
        result = self.run_tool("--self-check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["notes_emitted"], 32)
        mixed = self.run_tool("--map", "file.sspm", "--self-check")
        self.assertEqual(mixed.returncode, 2)
        self.assertIn("GUI", mixed.stderr)


if __name__ == "__main__":
    unittest.main()
