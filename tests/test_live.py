"""Calibrated movement tests use fakes only and never invoke real mouse APIs."""

from dataclasses import FrozenInstanceError, asdict, replace
import random
import threading
from types import SimpleNamespace
import unittest

from rhythia_tool.live import ABSOLUTE_MODE, RELATIVE_MODE, LiveCalibration, LiveSession
from rhythia_tool.sspm import MapNote
from rhythia_tool.windows import WindowInfo


TARGET = WindowInfo(123, 456, "Target application", (-500, -300, 1000, 900), 144)
CALIBRATION = LiveCalibration(-200, -100, 901, 601, ABSOLUTE_MODE)
NOTES = (MapNote(0, -0.5, -0.5), MapNote(500, 1, 1), MapNote(1000, 2.5, 2.5))


class FakeMouse:
    def __init__(self, target):
        self.target = target
        self.events = []
        self.checks = 0
        self.closed = 0
        self.focus_error = None
        self.send_error = None
        self.close_error = None

    def check_target(self):
        self.checks += 1
        if self.focus_error:
            raise self.focus_error

    def _move(self, kind, x, y):
        # The real WindowsMouse repeats its foreground check immediately before
        # the native send. The fake preserves that contract without using APIs.
        self.check_target()
        if self.send_error:
            raise self.send_error
        self.events.append((kind, x, y))

    def move_absolute(self, x, y):
        self._move("absolute", x, y)

    def move_relative(self, x, y):
        self._move("relative", x, y)

    def close(self):
        self.closed += 1
        if self.close_error:
            raise self.close_error


class CalibrationTests(unittest.TestCase):
    def test_settings_are_copied_and_frozen_without_dpi_rescaling(self):
        settings = SimpleNamespace(**asdict(CALIBRATION), dpi_percent=150, screen_width=2560)
        calibration = LiveCalibration.from_settings(settings)
        self.assertEqual(calibration, CALIBRATION)
        self.assertIs(calibration.validate(), calibration)
        with self.assertRaises(FrozenInstanceError):
            calibration.left = 0
        with self.assertRaisesRegex(ValueError, "missing.*field"):
            LiveCalibration.from_settings(SimpleNamespace(left=0))

    def test_invalid_geometry_modes_and_numerical_fields_are_rejected(self):
        invalid = {
            "left": (True, 0.0, -100001, 100001),
            "top": (None, "0", -100001, 100001),
            "width": (True, 2, 100001, 100.0),
            "height": (False, 0, 100001, -3),
            "input_mode": ("Unverified", "Relative/raw (unverified)", None),
            "counts_per_unit_x": (True, 0, -1, 100001, float("nan"), float("inf"), "300"),
            "counts_per_unit_y": (False, 0, float("-inf"), 10 ** 1000),
            "anchor_x": (-0.5001, 2.5001, float("nan"), True),
            "anchor_y": (-1, 3, float("inf"), "1"),
            "invert_x": (1, 0, None, "false"),
            "invert_y": (1, 0, None),
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=str(value)[:80]):
                    with self.assertRaises(ValueError):
                        replace(CALIBRATION, **{field: value}).validate()

    def test_all_supported_boundary_values_validate(self):
        replace(
            CALIBRATION, left=-100000, top=100000, width=3, height=100000,
            input_mode=RELATIVE_MODE, counts_per_unit_x=0.01,
            counts_per_unit_y=100000, anchor_x=-0.5, anchor_y=2.5,
            invert_x=True, invert_y=True,
        ).validate()


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.devices = []

    def factory(self, target):
        device = FakeMouse(target)
        self.devices.append(device)
        return device

    def session(self, calibration=CALIBRATION, target=TARGET, notes=NOTES):
        return LiveSession(calibration, target, notes, mouse_factory=self.factory)

    def test_preflight_checks_focus_once_without_moving_or_homing(self):
        for mode in (ABSOLUTE_MODE, RELATIVE_MODE):
            with self.subTest(mode=mode):
                session = self.session(replace(CALIBRATION, input_mode=mode))
                device = self.devices[-1]
                self.assertEqual(device.target, TARGET)
                self.assertEqual(device.checks, 1)
                self.assertEqual(device.events, [])
                session.check_target()
                self.assertEqual(device.checks, 2)
                self.assertEqual(device.events, [])
                session.close()

    def test_entire_chart_is_checked_before_constructing_mouse(self):
        for notes in (
            (),
            (*NOTES, MapNote(2000, 2.50001, 1)),
            (*NOTES, MapNote(2000, 1, -0.50001)),
            (*NOTES, MapNote(float("nan"), 1, 1)),
            (*NOTES, MapNote(float("inf"), 1, 1)),
            (*NOTES, MapNote(-1, 1, 1)),
            (*NOTES, MapNote(2000, float("inf"), 1)),
            (*NOTES, MapNote(2000, 1, float("nan"))),
            (*NOTES, MapNote(2000, True, 1)),
            (*NOTES, MapNote(999, 1, 1)),
            (*NOTES, object()),
        ):
            with self.subTest(notes=notes):
                with self.assertRaises(ValueError):
                    self.session(notes=notes)
                self.assertEqual(self.devices, [])

    def test_equal_timestamps_are_preserved(self):
        notes = (MapNote(100, 0, 0), MapNote(100, 2, 2))
        session = self.session(notes=notes)
        for note in notes:
            session.emit(note)
        self.assertEqual(len(self.devices[0].events), 2)

    def test_playfield_must_be_inside_target_including_last_pixel(self):
        for calibration in (
            replace(CALIBRATION, left=-501),
            replace(CALIBRATION, top=-301),
            replace(CALIBRATION, left=100),  # right = 1001, outside target
            replace(CALIBRATION, top=300),  # bottom = 901
        ):
            with self.subTest(calibration=calibration):
                with self.assertRaisesRegex(ValueError, "entire.*inside"):
                    self.session(calibration)
                self.assertEqual(self.devices, [])
        self.session(replace(CALIBRATION, left=-500, top=-300, width=1500, height=1200))

    def test_invalid_target_is_rejected_before_factory(self):
        for target in (
            None, replace(TARGET, handle=0), replace(TARGET, handle=True),
            replace(TARGET, pid=0), replace(TARGET, dpi=0),
            replace(TARGET, rect=(-500, -300, -500, 900)),
            replace(TARGET, rect=(-500, -300, 1000)),
            replace(TARGET, rect=(-500, -300, 1000.0, 900)),
            replace(TARGET, rect=(-500, -300, 2 ** 31, 900)),
        ):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    self.session(target=target)
                self.assertEqual(self.devices, [])

    def test_absolute_raw_frame_edges_and_centres_use_physical_pixels(self):
        session = self.session()
        for note in NOTES:
            session.emit(note)
        self.assertEqual(self.devices[0].events, [
            ("absolute", -200, -100),
            ("absolute", 250, 200),
            ("absolute", 700, 500),
        ])
        self.assertEqual(self.devices[0].checks, 1 + len(NOTES))

    def test_absolute_inverse_axes_and_small_rect_keep_inclusive_endpoints(self):
        calibration = replace(CALIBRATION, width=4, height=3, invert_x=True, invert_y=True)
        session = self.session(calibration)
        for note in NOTES:
            session.emit(note)
        self.assertEqual(self.devices[0].events, [
            ("absolute", -197, -98),
            ("absolute", -198, -99),
            ("absolute", -200, -100),
        ])

    def test_absolute_centres_do_not_expand_to_chart_content(self):
        notes = (MapNote(0, 0, 0), MapNote(100, 2, 2))
        session = self.session(notes=notes)
        for note in notes:
            session.emit(note)
        self.assertEqual(self.devices[0].events, [("absolute", -50, 0), ("absolute", 550, 400)])

    def test_relative_starts_from_manual_anchor_with_axis_specific_counts(self):
        calibration = replace(
            CALIBRATION, input_mode=RELATIVE_MODE, counts_per_unit_x=100,
            counts_per_unit_y=200, anchor_x=0, anchor_y=2, invert_y=True,
        )
        session = self.session(calibration)
        for note in NOTES:
            session.emit(note)
        self.assertEqual(self.devices[0].events, [
            ("relative", -50, 500),
            ("relative", 150, -300),
            ("relative", 150, -300),
        ])

    def test_relative_subcount_rounding_does_not_accumulate_drift(self):
        rng = random.Random(3828)
        notes = tuple(MapNote(i, rng.uniform(-0.5, 2.5), rng.uniform(-0.5, 2.5)) for i in range(1000))
        calibration = replace(
            CALIBRATION, input_mode=RELATIVE_MODE, counts_per_unit_x=17.125,
            counts_per_unit_y=23.375, anchor_x=-0.25, anchor_y=2.25,
            invert_x=True,
        )
        session = self.session(calibration, notes=notes)
        total_x = total_y = 0
        for note in notes:
            session.emit(note)
            _, dx, dy = self.devices[0].events[-1]
            self.assertIs(type(dx), int)
            self.assertIs(type(dy), int)
            total_x += dx
            total_y += dy
            self.assertLessEqual(abs(total_x + (note.x - calibration.anchor_x) * calibration.counts_per_unit_x), 0.500001)
            self.assertLessEqual(abs(total_y - (note.y - calibration.anchor_y) * calibration.counts_per_unit_y), 0.500001)

    def test_relative_unchanged_note_has_no_artificial_homing(self):
        calibration = replace(CALIBRATION, input_mode=RELATIVE_MODE)
        note = MapNote(0, calibration.anchor_x, calibration.anchor_y)
        session = self.session(calibration, notes=(note,))
        session.emit(note)
        self.assertEqual(self.devices[0].events, [("relative", 0, 0)])

    def test_preflight_focus_error_closes_device_without_movement(self):
        device = FakeMouse(TARGET)
        device.focus_error = RuntimeError("Target lost focus")
        with self.assertRaisesRegex(RuntimeError, "lost focus.*Restart"):
            LiveSession(CALIBRATION, TARGET, NOTES, mouse_factory=lambda _: device)
        self.assertEqual(device.closed, 1)
        self.assertEqual(device.events, [])

    def test_focus_error_is_latched_in_every_mode_even_if_focus_returns(self):
        for mode in (ABSOLUTE_MODE, RELATIVE_MODE):
            for operation in ("emit", "check_target"):
                with self.subTest(mode=mode, operation=operation):
                    session = self.session(replace(CALIBRATION, input_mode=mode))
                    device = self.devices[-1]
                    device.focus_error = RuntimeError("Target lost focus")
                    with self.assertRaisesRegex(RuntimeError, "lost focus"):
                        session.emit(NOTES[0]) if operation == "emit" else session.check_target()
                    device.focus_error = None
                    for action in (lambda: session.emit(NOTES[1]), session.check_target):
                        with self.assertRaisesRegex(RuntimeError, "lost focus.*Restart"):
                            action()
                    self.assertEqual(device.closed, 1)
                    self.assertEqual(device.events, [])

    def test_native_and_runtime_validation_errors_latch_and_never_send_again(self):
        for error in (OSError("SendInput failed"), ValueError("Pixel outside desktop")):
            with self.subTest(error=error):
                session = self.session()
                device = self.devices[-1]
                device.send_error = error
                device.close_error = OSError("close failed")
                with self.assertRaisesRegex(RuntimeError, str(error)):
                    session.emit(NOTES[0])
                device.send_error = None
                with self.assertRaisesRegex(RuntimeError, "Restart playback"):
                    session.emit(NOTES[1])
                self.assertEqual(device.events, [])
        session = self.session()
        with self.assertRaisesRegex(RuntimeError, "outside.*frame"):
            session.emit(MapNote(0, 99, 0))
        with self.assertRaisesRegex(RuntimeError, "Restart playback"):
            session.emit(NOTES[0])
        self.assertEqual(self.devices[-1].events, [])

    def test_close_is_permanent_idempotent_and_robust_to_backend_close_error(self):
        session = self.session()
        device = self.devices[0]
        device.close_error = OSError("close failed")
        session.close()
        session.close()
        self.assertEqual(device.closed, 1)
        for action in (lambda: session.emit(NOTES[0]), session.check_target):
            with self.assertRaisesRegex(RuntimeError, "stopped.*Restart playback"):
                action()
        self.assertEqual(device.events, [])

    def test_close_serializes_with_running_movement_and_blocks_all_later_events(self):
        entered = threading.Event()
        release = threading.Event()
        close_done = threading.Event()

        class BlockingMouse(FakeMouse):
            def move_absolute(self, x, y):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("Test timed out waiting for release")
                super().move_absolute(x, y)

        device = BlockingMouse(TARGET)
        session = LiveSession(CALIBRATION, TARGET, NOTES, mouse_factory=lambda _: device)
        emitter = threading.Thread(target=lambda: session.emit(NOTES[0]))
        closer = threading.Thread(target=lambda: (session.close(), close_done.set()))
        emitter.start()
        try:
            self.assertTrue(entered.wait(1))
            closer.start()
            self.assertFalse(close_done.wait(0.01))
        finally:
            release.set()
            emitter.join(2)
            if closer.ident is not None:
                closer.join(2)
        self.assertFalse(emitter.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertTrue(close_done.is_set())
        self.assertEqual(len(device.events), 1)
        with self.assertRaisesRegex(RuntimeError, "Restart playback"):
            session.emit(NOTES[1])
        self.assertEqual(len(device.events), 1)


if __name__ == "__main__":
    unittest.main()
