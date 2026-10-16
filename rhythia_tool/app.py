"""SSPM preview and manually calibrated Windows mouse playback interface."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .chart import display_text, preview_sspm
from .calibration_ui import PlayfieldSelector
from .demo import load_demo
from .live import ABSOLUTE_MODE, RELATIVE_MODE, LiveCalibration, LiveSession
from .mouse import WindowsMouse
from .playback import PlaybackEngine, PlaybackState
from .quickstart import QuickStartPanel
from .settings import Settings, SettingsError, default_settings_path, load_settings, save_settings
from .sspm import MapFormatError, MapNote, load_sspm
from .windows import GlobalHotkeys, IS_WINDOWS, cursor_position, foreground_window, set_dpi_awareness


ACTIVE = {PlaybackState.COUNTDOWN, PlaybackState.RUNNING, PlaybackState.PAUSED}


class Application:
    def __init__(self, root: tk.Tk, settings_path: Path, *, mouse_factory=WindowsMouse):
        self.root = root
        self.settings_path = settings_path
        self.settings_warning = ""
        try:
            self.settings = load_settings(settings_path)
        except SettingsError as exc:
            self.settings = Settings()
            self.settings_warning = f"{exc} Existing file preserved; Save settings explicitly to replace it."
        self.events: queue.SimpleQueue = queue.SimpleQueue()
        self.closing = threading.Event()
        self.target_lock = threading.Lock()
        self.control_lock = threading.RLock()
        self.control_generation = 0
        self.intent_lock = threading.Lock()
        self.cancel_generation = 0
        self.stop_signal = threading.Event()
        self.preview_generation = 0
        self.target = None
        self.guard_enabled = False
        self.mouse_factory = mouse_factory
        self.live_requested = False
        self.live_session = None
        self.pending_live_start = None
        self.live_preparing = False
        self.active_run_live = False
        self.run_was_live = False
        self.output_index = 0
        self.probe_axis = "X"
        self.calibration_pending = False
        self.playfield_selector = None
        self.quick_setup_intent = None
        self.hotkeys = None
        self.hotkey_signature = None
        self.pending_map: Path | None = None
        self.demo = load_demo()
        self.chart = self.demo
        self.loaded_map = None
        self.source_path = None
        self.engine = PlaybackEngine(self.demo.notes, self._emit_note)
        self.last_note = None
        self.after_id = None
        self.capture_after_id = None
        self.root.title(f"Rhythia Tool {__version__}")
        self.root.minsize(760, 650)
        width = min(860, max(760, self.root.winfo_screenwidth() - 60))
        height = min(800, max(650, self.root.winfo_screenheight() - 100))
        self.root.geometry(f"{width}x{height}")
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)
        self.vars = {name: (tk.BooleanVar(value=value) if type(value) is bool else tk.StringVar(value=str(value)))
                     for name, value in asdict(self.settings).items()}
        self.output_mode_var = tk.StringVar(value="Preview")
        self.probe_axis_var = tk.StringVar(value="X")
        self.banner_var = tk.StringVar(value="PREVIEW MODE - no desktop input. Steam compatibility and RP remain unverified.")
        self.source_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready for synthetic preview.")
        self.timing_var = tk.StringVar()
        self.position_var = tk.StringVar(value="No simulated note emitted yet.")
        self.hotkey_var = tk.StringVar()
        self.window_var = tk.StringVar(value="No foreground window captured.")
        self.guard_var = tk.BooleanVar(value=False)
        self.notice_var = tk.StringVar(value=self.settings_warning)
        self.quick_setup_var = tk.StringVar(value="Open Rhythia, then set up the playfield. Your calibration will be saved.")
        self.advanced_var = tk.BooleanVar(value=False)
        self._build_ui()
        self.load_synthetic()
        self._register_hotkeys()
        self.worker = threading.Thread(target=self._pump, name="preview-scheduler", daemon=True)
        self.worker.start()
        self._refresh()

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Rhythia Tool", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(outer, textvariable=self.banner_var,
                  foreground="#a14212", wraplength=800).pack(anchor="w", pady=(2, 10))
        tabs = self.tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True)
        quick = self.quick_panel = QuickStartPanel(tabs, self)
        tabs.add(quick, text="Quick start")
        playback = ttk.Frame(tabs, padding=12)
        calibration = ttk.Frame(tabs, padding=12)
        settings_tab = ttk.Frame(tabs, padding=12)
        mouse_tab = ttk.Frame(tabs, padding=12)
        help_tab = ttk.Frame(tabs, padding=12)
        tabs.add(playback, text="Playback")
        tabs.add(calibration, text="Calibration")
        tabs.add(settings_tab, text="Timing & hotkeys")
        tabs.add(mouse_tab, text="Mouse control")
        tabs.add(help_tab, text="Start guide")
        self.advanced_tabs = {"Playback": playback, "Calibration": calibration,
                              "Timing & hotkeys": settings_tab, "Mouse control": mouse_tab,
                              "Start guide": help_tab}

        row = ttk.Frame(playback)
        row.pack(fill="x")
        ttk.Button(row, text="Select local map…", command=self.pick_map).pack(side="left")
        ttk.Button(row, text="Load synthetic demo", command=self.load_synthetic).pack(side="left", padx=8)
        mode_row = ttk.Frame(playback)
        mode_row.pack(fill="x", pady=(8, 0))
        ttk.Label(mode_row, text="Output:").pack(side="left", padx=(0, 8))
        for mode in ("Preview", "Windows mouse"):
            ttk.Radiobutton(mode_row, text=mode, value=mode, variable=self.output_mode_var,
                            command=self.set_output_mode).pack(side="left", padx=(0, 14))
        ttk.Label(playback, textvariable=self.source_var, wraplength=760).pack(anchor="w", pady=8)
        self.canvas = tk.Canvas(playback, width=480, height=310, background="#172032", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, pady=(0, 8))
        self.canvas.bind("<Configure>", lambda _: self._draw_preview())
        ttk.Label(playback, textvariable=self.position_var).pack(anchor="w")
        controls = ttk.Frame(playback)
        controls.pack(fill="x", pady=10)
        self.start_button = ttk.Button(controls, text="Start / Resume preview", command=self.start)
        self.start_button.pack(side="left")
        self.pause_button = ttk.Button(controls, text="Pause preview", command=self.pause)
        self.pause_button.pack(side="left", padx=6)
        ttk.Button(controls, text="Stop", command=self.stop).pack(side="left")
        ttk.Button(controls, text="EMERGENCY STOP (Ctrl+Alt+F10)", command=self.emergency_stop).pack(side="right")
        ttk.Label(playback, text="Live: switch to Rhythia and use the Start hotkey at your chosen song origin.\n"
                  "Live pause stops input: restart the song and tool. Preview pause can resume.",
                  wraplength=760).pack(anchor="w")

        ttk.Label(calibration, text="Outer edges of the 3 by 3 playfield in physical desktop pixels",
                  font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
        fields = [("left", "Playfield left X"), ("top", "Playfield top Y"),
                  ("width", "Playfield width"), ("height", "Playfield height"),
                  ("screen_width", "Screen width"), ("screen_height", "Screen height"),
                  ("dpi_percent", "Windows scaling (%)")]
        for row_number, (key, label) in enumerate(fields, 1):
            ttk.Label(calibration, text=label).grid(row=row_number, column=0, sticky="w", pady=4)
            ttk.Entry(calibration, textvariable=self.vars[key], width=18).grid(row=row_number, column=1, sticky="w", padx=12)
        capture = ttk.Frame(calibration)
        capture.grid(row=8, column=0, columnspan=4, sticky="w", pady=10)
        ttk.Button(capture, text="Capture top-left in 3s", command=lambda: self.capture_corner(False)).pack(side="left")
        ttk.Button(capture, text="Capture bottom-right in 3s", command=lambda: self.capture_corner(True)).pack(side="left", padx=6)
        ttk.Label(calibration, text="Cursor input mode (verify using the calibration probe)").grid(row=9, column=0, sticky="w", pady=6)
        ttk.Combobox(calibration, state="readonly", textvariable=self.vars["input_mode"], width=30,
                     values=("Unverified", ABSOLUTE_MODE, RELATIVE_MODE)).grid(row=9, column=1, sticky="w")
        ttk.Button(calibration, text="Capture foreground window in 3s", command=self.capture_window).grid(row=10, column=0, columnspan=2, sticky="w", pady=10)
        ttk.Label(calibration, textvariable=self.window_var, wraplength=700).grid(row=11, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(calibration, text="Preview focus guard (live mode always checks focus)", variable=self.guard_var,
                        command=self._set_guard).grid(row=12, column=0, columnspan=3, sticky="w", pady=10)
        ttk.Label(calibration, text="Window capture does not infer the playfield. Capture the outer edges of all three rows/columns.\n"
                  "Negative X/Y values are valid for monitors to the left/above the primary display.\n"
                  "Use the global start hotkey with the captured window focused when the focus guard is enabled.",
                  wraplength=700).grid(row=13, column=0, columnspan=3, sticky="w")

        timing_fields = [("countdown_s", "Countdown (seconds, 0–3600)"),
                         ("offset_ms", "Timing offset (ms, −5000 to +5000)"),
                         ("speed", "Playback speed (0.1–4×)"),
                         ("live_lateness_ms", "Live lateness cutoff (ms, 1-250)"),
                         ("start_hotkey", "Start / Resume hotkey"),
                         ("pause_hotkey", "Pause hotkey"), ("stop_hotkey", "Stop hotkey")]
        for row_number, (key, label) in enumerate(timing_fields):
            ttk.Label(settings_tab, text=label).grid(row=row_number, column=0, sticky="w", pady=7)
            ttk.Entry(settings_tab, textvariable=self.vars[key], width=24).grid(row=row_number, column=1, sticky="w", padx=12)
        ttk.Label(settings_tab, text="Emergency stop: Ctrl+Alt+F10 (fixed). Examples: F6, Ctrl+Alt+S.\n"
                  "Positive offset emits later; negative offset may emit during countdown.\n"
                  "Speed scales note timestamps; offset stays in wall-clock milliseconds.\n"
                  "Save timing/calibration changes before starting via global hotkeys.\n"
                  "Saving settings stops input; restart the song and tool after changing timing.\n"
                  "Calibration probe: Ctrl+Alt+F9 (fixed). It moves the cursor once.",
                  wraplength=700).grid(row=7, column=0, columnspan=2, sticky="w", pady=16)
        ttk.Label(settings_tab, textvariable=self.hotkey_var, wraplength=700).grid(row=8, column=0, columnspan=2, sticky="w")
        ttk.Button(settings_tab, text="Save settings", command=self.save_form).grid(row=9, column=0, sticky="w", pady=14)
        ttk.Label(settings_tab, text=f"Settings: {self.settings_path}", wraplength=700).grid(row=10, column=0, columnspan=2, sticky="w")

        ttk.Label(mouse_tab, text="Relative-mode calibration", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        relative_fields = [("counts_per_unit_x", "Mouse counts per grid unit X"),
                           ("counts_per_unit_y", "Mouse counts per grid unit Y"),
                           ("anchor_x", "Starting cursor map X (center = 1)"),
                           ("anchor_y", "Starting cursor map Y (center = 1)")]
        for row_number, (key, label) in enumerate(relative_fields, 1):
            ttk.Label(mouse_tab, text=label).grid(row=row_number, column=0, sticky="w", pady=6)
            ttk.Entry(mouse_tab, textvariable=self.vars[key], width=18).grid(row=row_number, column=1, sticky="w", padx=12)
        ttk.Checkbutton(mouse_tab, text="Invert horizontal movement", variable=self.vars["invert_x"]).grid(row=5, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Checkbutton(mouse_tab, text="Invert vertical movement", variable=self.vars["invert_y"]).grid(row=6, column=0, columnspan=2, sticky="w", pady=5)
        probes = ttk.Frame(mouse_tab)
        probes.grid(row=7, column=0, columnspan=2, sticky="w", pady=12)
        ttk.Label(probes, text="Relative probe axis:").pack(side="left")
        for axis in ("X", "Y"):
            ttk.Radiobutton(probes, text=axis, value=axis, variable=self.probe_axis_var,
                            command=self.set_probe_axis).pack(side="left", padx=8)
        ttk.Label(mouse_tab, text="1. Select the cursor mode in Calibration and save settings.\n"
                  "2. Capture the Rhythia window, select Windows mouse output, then focus Rhythia.\n"
                  "3. Press Ctrl+Alt+F9 for one calibration movement. It never starts a song.\n"
                  "Absolute mode: the probe targets the center of your calibrated playfield.\n"
                  "Relative mode: manually put the game cursor at your anchor first; the probe moves\n"
                  "one grid unit along the chosen axis. Adjust counts until it moves exactly one cell.\n"
                  "Reset the game cursor to the configured anchor BEFORE EACH relative start or probe.\n"
                  "Windows pointer speed/acceleration and game sensitivity affect relative calibration.\n"
                  "This tool does not auto-home the game cursor or detect song playback.\n"
                  "Synthetic demo can only preview. Live runs require a loaded SSPM map.\n"
                  "Steam input compatibility and score/RP acceptance are still unverified.",
                  wraplength=740).grid(row=8, column=0, columnspan=2, sticky="w", pady=8)

        help_text = (
            "Target: Steam/CAPO Games Rhythia, reported game version 0.2.0. "
            "Normal Windows mouse playback is implemented, but this machine has not tested it in Windows or Rhythia. "
            "Steam input compatibility and score/RP acceptance remain unverified.\n\n"
            "1. Select your local SSPM v2 map. Use Preview first to check its notes and timing. "
            "No game map folder is needed. Embedded audio is not played by this tool.\n\n"
            "2. In Calibration, capture the OUTER edges of the entire 3 by 3 playfield in physical pixels. "
            "Choose Absolute screen for a screen-position cursor, or Relative calibrated for a captured cursor. "
            "Use Mouse control to set relative counts, starting anchor, and inversion. Save settings.\n\n"
            "3. Capture foreground window in 3s, then switch to Rhythia before capture finishes. "
            "Live input requires that same window to stay focused, unmoved, and at the same DPI.\n\n"
            "4. Select Windows mouse output. Focus Rhythia and use Ctrl+Alt+F9 to test calibration. "
            "Absolute mode targets the calibrated center. Relative mode moves one grid unit along the chosen axis; "
            "manually reset the game cursor to the configured anchor before every probe and run. "
            "Adjust counts until one probe matches one grid cell.\n\n"
            "5. Set countdown to the delay from your Start hotkey to song timeline zero. "
            "Match playback speed to the game. Positive offset delays notes; negative offset advances them. "
            "Start the game/song yourself and press the Start hotkey (default F6) at your chosen reference point. "
            "The tool neither detects nor starts song playback. Check synchronization on a short run first.\n\n"
            "6. F8 stops; Ctrl+Alt+F10 is emergency stop. Live pause (F7) stops input and loses song sync: "
            "restart the song, reset the relative anchor, and start a fresh run. Only Preview can pause/resume.\n\n"
            "The live Apply settings button prepares the run; F6 from the focused game begins it. "
            "Capture the window each launch. Output always defaults to Preview. Focus, window geometry/DPI, "
            "input errors, or excessive scheduling lateness stop live output. No clicking, keyboard injection, "
            "game memory access, or alternative score submission is used.\n\n"
            "The SSPM specification establishes map timestamps and coordinates. It does not establish Steam's "
            "cursor sensitivity, accepted input mode, song origin, or ranked-score rules. The separate open-source "
            "Rhythia/Client is not evidence for those Steam behaviors. See README for test evidence and limitations."
        )
        text = tk.Text(help_tab, wrap="word", height=24, font=("Segoe UI", 10), relief="flat")
        scroll = ttk.Scrollbar(help_tab, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.insert("1.0", help_text)
        text.configure(state="disabled")
        for tab in self.advanced_tabs.values():
            tabs.hide(tab)
        ttk.Checkbutton(outer, text="Show advanced settings", variable=self.advanced_var,
                        command=self._toggle_advanced).pack(anchor="w", pady=(8, 0))
        ttk.Separator(outer).pack(fill="x", pady=10)
        ttk.Label(outer, textvariable=self.status_var, wraplength=800).pack(anchor="w")
        ttk.Label(outer, textvariable=self.timing_var).pack(anchor="w", pady=(3, 0))
        ttk.Label(outer, textvariable=self.notice_var, foreground="#a14212", wraplength=800).pack(anchor="w", pady=(3, 0))

    def _toggle_advanced(self):
        if self.advanced_var.get():
            for tab in self.advanced_tabs.values():
                self.tabs.add(tab)
        else:
            self.tabs.select(self.quick_panel)
            for tab in self.advanced_tabs.values():
                self.tabs.hide(tab)

    def show_advanced(self, tab_name="Playback"):
        self.advanced_var.set(True)
        self._toggle_advanced()
        self.tabs.select(self.advanced_tabs[tab_name])

    def quick_setup(self):
        if not IS_WINDOWS:
            self._error("Playfield setup requires Windows. You can still preview a map here.")
            return
        if self.playfield_selector is not None:
            self.playfield_selector.cancel()
        # Keep output off throughout setup. The final Ready button explicitly
        # enables it; selecting corners never starts gameplay or sends input.
        self.output_mode_var.set("Preview")
        self.set_output_mode()
        if self.vars["input_mode"].get() == "Unverified":
            self.vars["input_mode"].set(ABSOLUTE_MODE)
        self.quick_setup_var.set("Switch to Rhythia within 3 seconds. Then click the TOP-LEFT and BOTTOM-RIGHT outer corners of the whole playfield.")

        def capture(intent):
            target = foreground_window()
            if intent != self._current_intent():
                self.quick_setup_var.set("Setup cancelled by Stop. Try again when ready.")
                return
            if target is None or target.pid == os.getpid():
                raise ValueError("Switch to Rhythia during the countdown, then try Set up playfield again.")
            self.quick_setup_intent = intent

            def cancelled():
                self.playfield_selector = None
                self.quick_setup_intent = None
                self.calibration_pending = False
                self.quick_setup_var.set("Setup cancelled. Click Set up playfield to try again.")

            def selected(left, top, width, height):
                self.playfield_selector = None
                self.quick_setup_intent = None
                self.calibration_pending = False
                if self.closing.is_set() or self._current_intent() != intent:
                    self.quick_setup_var.set("Setup cancelled by Stop. Try again when ready.")
                    return
                for name, value in (("left", left), ("top", top), ("width", width),
                                    ("height", height), ("dpi_percent", round(target.dpi / 96 * 100, 2))):
                    self.vars[name].set(str(value))
                if self.save_form():
                    with self.target_lock:
                        self.target = target
                    self.window_var.set(f"{display_text(target.title) or 'Rhythia window'} · playfield saved")
                    self.quick_setup_var.set("Playfield saved. Click Ready to play, then switch to Rhythia. Check the center with Ctrl+Alt+F9.")
                    self.root.lift()

            self.playfield_selector = PlayfieldSelector(self.root, target, selected, cancelled)

        self._delayed_capture(capture, self.quick_setup_var.get())

    def quick_ready(self):
        if self.loaded_map is None or self.pending_map is not None:
            self._error("Choose your .sspm map first.")
            return
        if self.calibration_pending:
            self._error("Finish setting up the playfield first.")
            return
        if not self.save_form():
            return
        try:
            with self.control_lock:
                self._live_prerequisites_locked()
            self.output_mode_var.set("Windows mouse")
            self.set_output_mode()
            key = self.settings.start_hotkey
            self.quick_setup_var.set(f"Ready. Switch to Rhythia and check the center with Ctrl+Alt+F9. Press {key} at your song start reference; F8 stops.")
            self.notice_var.set(f"Ready for {key}. The tool does not start the song. Countdown: {self.settings.countdown_s:g}s.")
        except (ValueError, RuntimeError, OSError) as exc:
            self._error(str(exc))

    def _form_settings(self) -> Settings:
        values = {name: variable.get() for name, variable in self.vars.items()}
        for key in ("countdown_s", "offset_ms", "speed", "dpi_percent", "counts_per_unit_x",
                    "counts_per_unit_y", "anchor_x", "anchor_y", "live_lateness_ms"):
            try:
                values[key] = float(values[key])
            except ValueError as exc:
                raise SettingsError(f"{key} must be a number.") from exc
        for key in ("left", "top", "width", "height", "screen_width", "screen_height"):
            try:
                values[key] = int(values[key])
            except ValueError as exc:
                raise SettingsError(f"{key} must be a whole number.") from exc
        result = Settings(**values).validate()
        # Validate syntax and duplicates even on Linux; constructing does not register.
        GlobalHotkeys(self._hotkey_mapping(result), lambda action: None)
        return result

    @staticmethod
    def _hotkey_mapping(settings):
        return {"start": settings.start_hotkey, "pause": settings.pause_hotkey,
                "stop": settings.stop_hotkey, "emergency": "Ctrl+Alt+F10", "probe": "Ctrl+Alt+F9"}

    def _emit_note(self, note):
        if self.stop_signal.is_set():
            raise RuntimeError("Playback was cancelled before the next input event.")
        if self.active_run_live:
            if self.live_session is None or self.loaded_map is None or self.output_index >= len(self.loaded_map.notes):
                raise RuntimeError("Live note stream lost its map/session; restart required.")
            raw = self.loaded_map.notes[self.output_index]
            if raw.time_ms != note.time_ms:
                raise RuntimeError("Live note timing no longer matches the selected map.")
            self.live_session.emit(raw)
        self.output_index += 1
        self.events.put(("note", (self.preview_generation, note)))

    def _close_live_locked(self):
        session, self.live_session = self.live_session, None
        self.active_run_live = False
        if session is not None:
            try:
                session.close()
            except Exception as exc:
                self.events.put(("error", f"Mouse session cleanup failed: {exc}"))

    def _cancel_locked(self, reason):
        self.stop_signal.set()
        self.pending_live_start = None
        self.live_preparing = False
        self._close_live_locked()
        self.control_generation += 1
        self.engine.stop(reason)

    def _signal_stop(self):
        # This lock never encloses preparation, a note callback, or a native API
        # call. Stop intent remains responsive while those operations run.
        with self.intent_lock:
            self.cancel_generation += 1
            self.stop_signal.set()

    def _current_intent(self):
        with self.intent_lock:
            return self.cancel_generation

    def _clear_stop_for_start(self, expected_generation):
        with self.intent_lock:
            if expected_generation != self.cancel_generation:
                return False
            self.stop_signal.clear()
            return True

    def set_output_mode(self):
        self._signal_stop()
        with self.control_lock:
            self._cancel_locked("Output mode changed; restart required.")
            self.preview_generation += 1
            self.live_requested = self.output_mode_var.get() == "Windows mouse"
        if self.live_requested:
            self.banner_var.set("WINDOWS MOUSE MODE - manual calibration/sync. Steam compatibility and RP unverified.")
            self.start_button.configure(text="Apply live settings (then F6)")
            self.pause_button.configure(text="Pause / stop live")
            self.notice_var.set("Capture and calibrate Rhythia, save settings, focus the game, then use the Start hotkey.")
        else:
            self.banner_var.set("PREVIEW MODE - no desktop input. Steam compatibility and RP remain unverified.")
            self.start_button.configure(text="Start / Resume preview")
            self.pause_button.configure(text="Pause preview")

    def set_probe_axis(self):
        with self.control_lock:
            self.probe_axis = self.probe_axis_var.get()

    def _live_prerequisites_locked(self, *, require_map=True):
        if not IS_WINDOWS:
            raise RuntimeError("Live mouse output requires Windows 10/11. Preview is available on this platform.")
        if self.calibration_pending:
            raise RuntimeError("Wait for the pending calibration capture to finish before live input.")
        if require_map and (self.loaded_map is None or self.pending_map is not None):
            raise RuntimeError("Load a valid SSPM map for live playback. The synthetic demo is preview only.")
        if (self.hotkeys is None or self.hotkeys.last_error or
                self.hotkey_signature != tuple(self._hotkey_mapping(self.settings).items())):
            raise RuntimeError("Save settings and resolve all global hotkey errors before live output; emergency stop must be registered.")
        with self.target_lock:
            target = self.target
        if target is None:
            raise RuntimeError("Capture the Rhythia foreground window in Calibration before live output.")
        if target.pid == os.getpid():
            raise RuntimeError("The captured target is this tool. Capture the Rhythia window instead.")
        calibration = LiveCalibration.from_settings(self.settings).validate()
        return calibration, target

    def _start_locked(self, *, reference_ns=None, intent_generation=None):
        if intent_generation is None:
            intent_generation = self._current_intent()
        if reference_ns is None:
            reference_ns = time.perf_counter_ns()
        if self.calibration_pending:
            raise RuntimeError("Wait for the calibration capture to finish before starting playback.")
        if self.pending_map is not None:
            raise RuntimeError("Load a valid SSPM map or the synthetic preview first.")
        state = self.engine.snapshot().state
        if state in (PlaybackState.COUNTDOWN, PlaybackState.RUNNING):
            return
        if self.live_preparing:
            return
        if state == PlaybackState.PAUSED and not self.live_requested:
            if self._clear_stop_for_start(intent_generation):
                self.engine.resume()
            return
        if self.live_requested:
            calibration, target = self._live_prerequisites_locked()
            if not self._clear_stop_for_start(intent_generation):
                return
            self._close_live_locked()
            self.control_generation += 1
            self.preview_generation += 1
            self.live_preparing = True
            self.run_was_live = True
            self.engine.stop("Preparing live input; song origin is the Start hotkey time plus countdown.")
            self.pending_live_start = {
                "generation": self.control_generation, "intent": intent_generation,
                "reference_ns": reference_ns, "settings": self.settings,
                "calibration": calibration, "target": target,
                "map": self.loaded_map, "chart": self.chart,
            }
            return
        else:
            with self.target_lock:
                if self.guard_enabled and self.target is None:
                    raise RuntimeError("Capture a foreground window before enabling the preview focus guard.")
        self._close_live_locked()
        if not self._clear_stop_for_start(intent_generation):
            return
        self.run_was_live = False
        self.preview_generation += 1
        self.output_index = 0
        self.engine = PlaybackEngine(self.chart.notes, self._emit_note)
        try:
            self.engine.start(countdown_s=self.settings.countdown_s,
                              offset_ms=self.settings.offset_ms, speed=self.settings.speed)
        except Exception:
            self._close_live_locked()
            raise

    def _live_request_current_locked(self, request):
        return (self.live_preparing and self.control_generation == request["generation"]
                and self._current_intent() == request["intent"] and not self.stop_signal.is_set()
                and self.live_requested and not self.closing.is_set()
                and self.loaded_map is request["map"] and self.chart is request["chart"])

    def _prepare_live_request(self, request):
        # Full-chart validation and deadline construction must not block the
        # hotkey message thread or the control lock. This local engine cannot
        # emit until the still-current request is committed below.
        session = None
        try:
            with self.control_lock:
                if not self._live_request_current_locked(request):
                    return
            session = LiveSession(request["calibration"], request["target"], request["map"].notes,
                                  mouse_factory=self.mouse_factory)
            settings = request["settings"]
            engine = PlaybackEngine(request["chart"].notes, self._emit_note,
                                    max_lateness_ms=settings.live_lateness_ms)
            engine.start(countdown_s=settings.countdown_s, offset_ms=settings.offset_ms,
                         speed=settings.speed, reference_ns=request["reference_ns"])
            with self.control_lock:
                if not self._live_request_current_locked(request):
                    session.close()
                    return
                session.check_target()
                self.live_session = session
                self.active_run_live = True
                self.run_was_live = True
                self.output_index = 0
                self.engine = engine
                self.live_preparing = False
                session = None  # Ownership transferred to the active run.
        except Exception as exc:
            if session is not None:
                session.close()
            with self.control_lock:
                if self._live_request_current_locked(request):
                    self._cancel_locked(f"Live preparation stopped: {exc}")
                    self.events.put(("error", str(exc)))

    def _pause_locked(self):
        if self.live_requested or self.active_run_live:
            self._cancel_locked("Live pause stopped input. Restart the song, reset the cursor anchor, and start a fresh run.")
        else:
            self.engine.pause()

    def _probe_locked(self, intent_generation=None):
        if intent_generation is None:
            intent_generation = self._current_intent()
        if not self.live_requested:
            raise RuntimeError("Select Windows mouse output before using the calibration probe.")
        calibration, target = self._live_prerequisites_locked(require_map=False)
        self._cancel_locked("Calibration probe: restart the song/tool before playback.")
        if calibration.input_mode == ABSOLUTE_MODE:
            point = MapNote(0, 1, 1)
            description = "Absolute probe targeted the calibrated playfield center."
        elif self.probe_axis == "X":
            delta = 1 if calibration.anchor_x <= 1.5 else -1
            point = MapNote(0, calibration.anchor_x + delta, calibration.anchor_y)
            description = f"Relative X probe sent {delta:+d} grid unit; manually reset the cursor to its anchor."
        else:
            delta = 1 if calibration.anchor_y <= 1.5 else -1
            point = MapNote(0, calibration.anchor_x, calibration.anchor_y + delta)
            description = f"Relative Y probe sent {delta:+d} grid unit; manually reset the cursor to its anchor."
        session = LiveSession(calibration, target, (point,), mouse_factory=self.mouse_factory)
        try:
            if not self._clear_stop_for_start(intent_generation):
                return
            session.emit(point)
        finally:
            session.close()
            self.stop_signal.set()
        self.events.put(("notice", description))

    def save_form(self):
        self._signal_stop()
        try:
            updated = self._form_settings()
            save_settings(self.settings_path, updated)
            with self.control_lock:
                self._cancel_locked("Settings changed; restart playback and the song for live output.")
                self.control_generation += 1
                self.preview_generation += 1
                self.settings = updated
            self._register_hotkeys()
            if self.hotkeys is not None and not self.hotkeys.last_error:
                self.notice_var.set("Settings saved. For live playback, focus Rhythia and use the Start hotkey after manual synchronization.")
            elif not IS_WINDOWS:
                self.notice_var.set("Settings saved. Preview is available here; live mouse output requires Windows.")
            return True
        except (ValueError, RuntimeError) as exc:
            self._error(str(exc))
            return False

    def _register_hotkeys(self):
        signature = tuple(self._hotkey_mapping(self.settings).items())
        if signature == self.hotkey_signature and self.hotkeys and not self.hotkeys.last_error:
            return
        if self.hotkeys:
            self.hotkeys.stop()
            self.hotkeys = None
        self.hotkey_signature = None
        if not IS_WINDOWS:
            self.hotkey_var.set("Global hotkeys require Windows. Use the buttons on this platform.")
            return
        try:
            hotkeys = GlobalHotkeys(dict(signature), self._hotkey_action)
            hotkeys.start()
            self.hotkeys = hotkeys
            self.hotkey_signature = signature
            self.hotkey_var.set("Global hotkeys registered: " + ", ".join(f"{action}={key}" for action, key in signature))
        except (ValueError, RuntimeError) as exc:
            self.hotkey_var.set(f"Global hotkeys unavailable: {exc} Use buttons or change the bindings.")
            self.notice_var.set(f"Global hotkeys unavailable: {exc} Buttons remain available.")

    def _hotkey_action(self, action):
        if self.closing.is_set():
            return
        reference_ns = time.perf_counter_ns()
        generation = self.control_generation
        intent = self._current_intent()
        if action in ("stop", "emergency") or (action == "pause" and self.live_requested):
            self._signal_stop()
        # Handle controls immediately, without a queued Start surviving a later
        # emergency stop. Never read Tk variables on this message thread.
        with self.control_lock:
            if action in ("start", "probe") and (generation != self.control_generation or intent != self._current_intent()):
                return
            try:
                if action in ("stop", "emergency"):
                    self._cancel_locked("Emergency stop; restart required." if action == "emergency" else "Stopped; restart required.")
                elif action == "pause":
                    self._pause_locked()
                elif action == "start":
                    self._start_locked(reference_ns=reference_ns, intent_generation=intent)
                elif action == "probe":
                    self._probe_locked(intent_generation=intent)
            except (ValueError, RuntimeError, OSError) as exc:
                self._cancel_locked(f"Input stopped: {exc}")
                self.events.put(("error", str(exc)))

    def _error(self, message):
        self.notice_var.set(message)
        messagebox.showerror("Rhythia Tool", message, parent=self.root)

    def pick_map(self):
        chosen = filedialog.askopenfilename(parent=self.root, title="Select an SSPM v2 beatmap",
                                            filetypes=[("SSPM beatmaps", "*.sspm"), ("All files", "*.*")])
        if chosen:
            self.select_map(Path(chosen))

    def select_map(self, path: Path):
        self._signal_stop()
        with self.control_lock:
            self._cancel_locked("Loading SSPM map.")
            self.control_generation += 1
            self.preview_generation += 1
            self.pending_map = path
        self.start_button.configure(state="disabled")
        self.last_note = None
        try:
            parsed = load_sspm(path)
            chart = preview_sspm(parsed)
        except (OSError, ValueError) as exc:
            self.source_var.set(f"Could not load {display_text(path.name)}. Playback disabled.\n{display_text(str(exc))}")
            self._error(str(exc))
            self._draw_preview()
            return
        with self.control_lock:
            self._cancel_locked("SSPM map loaded.")
            self.control_generation += 1
            self.preview_generation += 1
            self.pending_map = None
            self.chart = chart
            self.loaded_map = parsed
            self.source_path = path
            self.engine = PlaybackEngine(chart.notes, self._emit_note)
            self.output_index = 0
            self.run_was_live = False
            self.settings = replace(self.settings, last_map_path=str(path))
        self.vars["last_map_path"].set(str(path))
        authors = ", ".join(display_text(author, 50) for author in parsed.authors[:3]) or "Unknown mapper"
        self.source_var.set(f"{display_text(parsed.title)} · {len(parsed.notes):,} notes · {parsed.duration_ms / 1000:.3f}s\n"
                            f"SSPM v{parsed.format_version} · {authors} · audio {'present' if parsed.has_audio else 'absent'} · manual song synchronization")
        warnings = " ".join(parsed.warnings)
        self.notice_var.set("SSPM decoded; Steam live compatibility remains unverified. " + display_text(warnings, 250))
        self.start_button.configure(state="normal")
        self.last_note = None
        self._draw_preview()

    def load_synthetic(self):
        self._signal_stop()
        with self.control_lock:
            self._cancel_locked("Synthetic demo loaded; ready to preview.")
            self.control_generation += 1
            self.preview_generation += 1
            self.pending_map = None
            self.chart = self.demo
            self.loaded_map = None
            self.source_path = None
            self.engine = PlaybackEngine(self.demo.notes, self._emit_note)
            self.output_index = 0
            self.run_was_live = False
        self.output_mode_var.set("Preview")
        self.set_output_mode()
        self.source_var.set(f"{self.demo.title} · {len(self.demo.notes)} synthetic notes · {self.demo.duration_ms / 1000:.1f}s\n"
                            "Generated in this tool; not a Rhythia map and no song audio is played.")
        self.start_button.configure(state="normal")
        self.last_note = None
        self._draw_preview()

    def start(self):
        if self.pending_map is not None:
            self.notice_var.set("No valid map loaded. Select a supported SSPM v2 map or load the synthetic demo.")
            return
        try:
            generation = self.control_generation
            intent = self._current_intent()
            state = self.engine.snapshot().state
            if self.live_requested:
                self._signal_stop()
                updated = self._form_settings()
                with self.control_lock:
                    if generation != self.control_generation:
                        return
                    self.settings = updated
                    self._live_prerequisites_locked()
                    self._cancel_locked("Live settings applied. Focus Rhythia and use the Start hotkey to begin.")
                self.notice_var.set(f"Ready: focus Rhythia, manually synchronize the song, and press {self.settings.start_hotkey}. "
                                    "Relative mode requires resetting the game cursor to the configured anchor first.")
            elif state == PlaybackState.PAUSED:
                with self.control_lock:
                    if generation == self.control_generation:
                        self._start_locked(intent_generation=intent)
            elif state not in ACTIVE:
                updated = self._form_settings()
                with self.control_lock:
                    if generation != self.control_generation:
                        return
                    self.settings = updated
                    self.last_note = None
                    self._start_locked(intent_generation=intent)
        except (ValueError, RuntimeError) as exc:
            self._error(str(exc))

    def pause(self):
        if self.live_requested:
            self._signal_stop()
        with self.control_lock:
            self._pause_locked()

    def stop(self):
        self._hotkey_action("stop")

    def emergency_stop(self):
        self._hotkey_action("emergency")

    def _set_guard(self):
        self._signal_stop()
        with self.target_lock:
            self.guard_enabled = self.guard_var.get()
        with self.control_lock:
            self._cancel_locked("Preview focus guard changed; restart required. Live focus checking is always enabled.")

    def _delayed_capture(self, callback, message):
        if not IS_WINDOWS:
            self._error("Desktop capture is available on Windows. Enter calibration values manually here.")
            return
        if self.capture_after_id:
            self.root.after_cancel(self.capture_after_id)
        self._signal_stop()
        capture_intent = self._current_intent()
        with self.control_lock:
            self._cancel_locked("Calibration started; restart required.")
            self.calibration_pending = True
        self.notice_var.set(message)
        def finish():
            self.capture_after_id = None
            if not self.closing.is_set():
                try:
                    with self.control_lock:
                        if capture_intent != self._current_intent():
                            self.quick_setup_var.set("Setup cancelled by Stop. Try again when ready.")
                            return
                        self._cancel_locked("Calibration capture finished; restart required.")
                    callback(capture_intent)
                except (OSError, ValueError, RuntimeError) as exc:
                    self._error(str(exc))
                finally:
                    with self.control_lock:
                        self.calibration_pending = self.playfield_selector is not None
        self.capture_after_id = self.root.after(3000, finish)

    def capture_corner(self, bottom_right):
        def capture(intent):
            x, y = cursor_position()
            if intent != self._current_intent():
                return
            if bottom_right:
                left, top = int(self.vars["left"].get()), int(self.vars["top"].get())
                if x <= left or y <= top:
                    raise ValueError("Bottom-right must be below and to the right of the top-left corner.")
                self.vars["width"].set(str(x - left + 1))
                self.vars["height"].set(str(y - top + 1))
            else:
                self.vars["left"].set(str(x))
                self.vars["top"].set(str(y))
            self.notice_var.set(f"Captured physical pixel ({x}, {y}). Save settings to persist calibration.")
        self._delayed_capture(capture, "Move the desktop cursor to the chosen playfield corner within 3 seconds.")

    def capture_window(self):
        def capture(intent):
            target = foreground_window()
            if intent != self._current_intent():
                return
            if target is None:
                raise ValueError("No foreground window found. Try capturing again.")
            if target.pid == os.getpid():
                raise ValueError("The tool itself is focused. Switch to the intended game window during the countdown.")
            with self.target_lock:
                self.target = target
            self.window_var.set(f"{target.title or '(untitled)'} · PID {target.pid} · DPI {target.dpi}\n"
                                f"Window bounds {target.rect}; playfield must still be calibrated separately.")
            self.vars["dpi_percent"].set(str(round(target.dpi / 96 * 100, 2)))
            self.notice_var.set("Window captured. Live input requires it to stay focused at the same size/position/DPI. Capture again each launch.")
        self._delayed_capture(capture, "Switch to the intended game window within 3 seconds.")

    def _pump(self):
        while not self.closing.is_set():
            try:
                with self.control_lock:
                    request, self.pending_live_start = self.pending_live_start, None
                if request is not None:
                    self._prepare_live_request(request)
                # Starting and checking focus share one lock so even a zero-
                # countdown global Start cannot emit before its focus check.
                with self.control_lock:
                    state = self.engine.snapshot().state
                    with self.target_lock:
                        target, guarded = self.target, self.guard_enabled
                    if self.active_run_live and self.live_session is not None and state in ACTIVE:
                        self.live_session.check_target()
                    elif guarded and target is not None and state in ACTIVE:
                        focused = foreground_window()
                        if focused is None or (focused.handle, focused.pid) != (target.handle, target.pid):
                            self.engine.stop("Captured window lost focus; restart required.")
                    self.engine.pump()
                    if self.active_run_live and self.engine.snapshot().state not in ACTIVE:
                        self._close_live_locked()
            except Exception as exc:
                with self.control_lock:
                    self._cancel_locked(f"Playback stopped after error: {exc}; restart the song and tool.")
                self.events.put(("error", str(exc)))
            self.closing.wait(0.002)

    def _refresh(self):
        if self.closing.is_set():
            return
        if (self.playfield_selector is not None and
                self.quick_setup_intent != self._current_intent()):
            self.playfield_selector.cancel()
        for _ in range(1000):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "note":
                generation, note = value
                if generation == self.preview_generation and self.pending_map is None:
                    self.last_note = note
            elif kind == "error":
                self.notice_var.set(value)
            elif kind == "notice":
                self.notice_var.set(value)
        if self.hotkeys and self.hotkeys.last_error:
            with self.control_lock:
                self._cancel_locked("Global hotkeys failed; input stopped. Resolve hotkey errors before restarting.")
            self.hotkey_var.set("Global hotkeys failed: " + self.hotkeys.last_error)
        snapshot = self.engine.snapshot()
        output = "Windows mouse" if self.run_was_live else "Preview"
        self.status_var.set(f"{output} · {snapshot.state.value.capitalize()} · {snapshot.message}")
        self.timing_var.set(f"Map timeline {snapshot.elapsed_ms / 1000:.3f}s · countdown {snapshot.countdown_remaining_s:.2f}s · "
                            f"emitted {snapshot.emitted_count}/{len(self.chart.notes)} · max lateness {snapshot.max_lateness_ms:.2f}ms")
        self._draw_preview()
        self.quick_panel.refresh()
        self.after_id = self.root.after(30, self._refresh)

    def _draw_preview(self):
        if not hasattr(self, "canvas"):
            return
        self.canvas.delete("all")
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        edge = max(10, min(width - 48, height - 36))
        left, top = (width - edge) / 2, (height - edge) / 2
        for i in range(4):
            if self.loaded_map is not None:
                min_x, min_y, max_x, max_y = self.chart.bounds
                px = (-0.5 + i - min_x) / (max_x - min_x) * edge
                py = (-0.5 + i - min_y) / (max_y - min_y) * edge
            else:
                px = py = i * edge / 3
            self.canvas.create_line(left + px, top, left + px, top + edge, fill="#43516c")
            self.canvas.create_line(left, top + py, left + edge, top + py, fill="#43516c")
        if self.pending_map:
            self.canvas.create_text(width / 2, height / 2, text="Unsupported or damaged map\nPlayback disabled",
                                    fill="#ffffff", font=("Segoe UI", 13), justify="center")
        elif self.last_note is not None:
            note = self.last_note
            x, y = left + note.x * edge, top + note.y * edge
            self.canvas.create_oval(x - 9, y - 9, x + 9, y + 9, fill="#67e8cb", outline="")
            if self.loaded_map is not None:
                min_x, min_y, max_x, max_y = self.chart.bounds
                raw_x = min_x + note.x * (max_x - min_x)
                raw_y = min_y + note.y * (max_y - min_y)
                self.position_var.set(f"SSPM point ({raw_x:.3f}, {raw_y:.3f}) at {note.time_ms:.0f}ms · " +
                                      ("Windows mouse output" if self.run_was_live else "preview, no desktop input"))
            else:
                px, py = self.settings.physical_position(note.x, note.y)
                self.position_var.set(f"Preview target: normalized ({note.x:.3f}, {note.y:.3f}) → physical pixels ({px}, {py})")
        else:
            self.position_var.set("No note emitted yet. Live mode starts from the focused game using its Start hotkey."
                                      if self.live_requested else "Preview only. No desktop input is generated.")

    def shutdown(self):
        if self.closing.is_set():
            return
        self.closing.set()
        self._signal_stop()
        if self.playfield_selector is not None:
            self.playfield_selector.cancel()
        with self.control_lock:
            self._cancel_locked("Application closed.")
        if self.after_id:
            self.root.after_cancel(self.after_id)
        if self.capture_after_id:
            self.root.after_cancel(self.capture_after_id)
        try:
            if self.hotkeys:
                self.hotkeys.stop()
        finally:
            self.worker.join(timeout=1)
            self.root.destroy()


def self_check():
    demo = load_demo()
    now = [0]
    emitted = []
    engine = PlaybackEngine(demo.notes, emitted.append, clock_ns=lambda: now[0])
    engine.start(countdown_s=0, offset_ms=0, speed=1)
    for ms in range(0, int(demo.duration_ms) + 501, 10):
        now[0] = ms * 1_000_000
        engine.pump()
    snapshot = engine.snapshot()
    if tuple(emitted) != demo.notes or snapshot.state != PlaybackState.COMPLETED:
        raise RuntimeError(f"Simulation failed: {snapshot}")
    print(json.dumps({"mode": "synthetic simulation", "notes_emitted": len(emitted),
                      "state": snapshot.state.value, "max_lateness_ms": snapshot.max_lateness_ms,
                      "live_input": False, "game_map_parsing": False}))
    return 0


def inspect_map(path: Path):
    chart = load_sspm(path)
    print(json.dumps({"file": path.name, "format": "SSPM", "format_version": chart.format_version,
                      "title": chart.title, "authors": chart.authors, "note_count": len(chart.notes),
                      "first_note_ms": chart.notes[0].time_ms if chart.notes else None,
                      "last_note_ms": chart.notes[-1].time_ms if chart.notes else None,
                      "duration_ms": chart.duration_ms, "has_audio": chart.has_audio,
                      "audio_format": chart.audio_format, "has_cover": chart.has_cover,
                      "requires_mod": chart.requires_mod, "difficulty_group": chart.difficulty_group,
                      "map_rating": chart.map_rating, "marker_hash_verified": chart.marker_hash_verified,
                      "x_range": [min(n.x for n in chart.notes), max(n.x for n in chart.notes)] if chart.notes else None,
                      "y_range": [min(n.y for n in chart.notes), max(n.y for n in chart.notes)] if chart.notes else None,
                      "warnings": chart.warnings, "sha256": chart.sha256,
                      "steam_compatibility": "unverified", "rp_eligibility": "unverified"}))
    return 0


def simulate_map(path: Path):
    parsed = load_sspm(path)
    chart = preview_sspm(parsed)
    now = [0]
    emitted = []
    engine = PlaybackEngine(chart.notes, emitted.append, clock_ns=lambda: now[0])
    engine.start(countdown_s=0, offset_ms=0, speed=1)
    for note in chart.notes:
        now[0] = round(note.time_ms * 1_000_000)
        engine.pump()
    snapshot = engine.snapshot()
    if tuple(emitted) != chart.notes or snapshot.state != PlaybackState.COMPLETED:
        raise RuntimeError(f"Map simulation failed: {snapshot}")
    print(json.dumps({"mode": "SSPM map simulation", "file": path.name,
                      "format_version": parsed.format_version, "notes_emitted": len(emitted),
                      "first_note_ms": chart.notes[0].time_ms, "last_note_ms": chart.notes[-1].time_ms,
                      "state": snapshot.state.value, "max_lateness_ms": snapshot.max_lateness_ms,
                      "live_input": False, "audio_played": False,
                      "steam_compatibility": "unverified", "rp_eligibility": "unverified"}))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rhythia Tool: SSPM preview and calibrated Windows mouse playback (GUI live mode).")
    parser.add_argument("--settings", type=Path, default=None, help="Use a specific local settings JSON file.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-check", action="store_true", help="Run a headless deterministic demo simulation.")
    mode.add_argument("--inspect-map", type=Path, help="Read SSPM metadata without a display or live input.")
    mode.add_argument("--simulate-map", type=Path, help="Test every SSPM note with a fake clock and list sink.")
    parser.add_argument("--map", type=Path, help="Open a local SSPM map in the GUI for silent preview.")
    args = parser.parse_args(argv)
    if args.map and (args.self_check or args.inspect_map or args.simulate_map):
        parser.error("--map is for the GUI; use only one headless operation.")
    try:
        if args.self_check:
            return self_check()
        if args.inspect_map:
            return inspect_map(args.inspect_map)
        if args.simulate_map:
            return simulate_map(args.simulate_map)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Map/preview error: {exc}", file=sys.stderr)
        return 2
    set_dpi_awareness()  # Must precede creation of ANY Tk HWND.
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Cannot start the interface: {exc}. A desktop display and Python Tk support are required.", file=sys.stderr)
        return 1
    app = Application(root, args.settings or default_settings_path())
    if args.map:
        app.select_map(args.map)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
