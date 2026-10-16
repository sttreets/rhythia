"""A small, guided front page. All refreshes run on Tk's owning thread."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .chart import display_text
from .live import ABSOLUTE_MODE, RELATIVE_MODE
from .playback import PlaybackState


class QuickStartPanel(ttk.Frame):
    """Beginner controls over the application's existing, guarded actions.

    This view never starts playback, captures a window, saves settings, or sends
    input during construction/refresh. The explicit buttons delegate to app.
    """

    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.map_var = tk.StringVar(self)
        self.target_var = tk.StringVar(self)
        self.readiness_var = tk.StringVar(self)
        self.mode_hint_var = tk.StringVar(self)
        self.guide_var = tk.StringVar(self)
        self.columnconfigure(0, weight=1)

        ttk.Label(self, text="Get ready in three steps", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8))

        map_step = ttk.LabelFrame(self, text="1. Choose your map", padding=10)
        map_step.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        map_step.columnconfigure(0, weight=1)
        map_buttons = ttk.Frame(map_step)
        map_buttons.grid(row=0, column=0, sticky="w")
        self.map_button = ttk.Button(map_buttons, text="Choose .sspm map…", command=app.pick_map)
        self.map_button.pack(side="left")
        ttk.Button(map_buttons, text="Preview / map details", command=lambda: app.show_advanced("Playback")).pack(
            side="left", padx=(8, 0))
        ttk.Label(map_step, textvariable=self.map_var, wraplength=730).grid(
            row=1, column=0, sticky="w", pady=(7, 0))

        setup_step = ttk.LabelFrame(self, text="2. Show the tool your play area", padding=10)
        setup_step.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        setup_step.columnconfigure(0, weight=1)
        self.setup_button = ttk.Button(setup_step, text="Set up playfield…", command=app.quick_setup)
        self.setup_button.grid(row=0, column=0, sticky="w")
        ttk.Label(setup_step, textvariable=app.quick_setup_var, wraplength=730).grid(
            row=1, column=0, sticky="w", pady=(7, 0))
        ttk.Label(setup_step, textvariable=self.target_var, wraplength=730).grid(
            row=2, column=0, sticky="w", pady=(4, 0))
        mode_row = ttk.Frame(setup_step)
        mode_row.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(mode_row, text="Mouse mode:").pack(side="left", padx=(0, 8))
        self.mode_selector = ttk.Combobox(mode_row, state="readonly", width=23,
                                         textvariable=app.vars["input_mode"],
                                         values=(ABSOLUTE_MODE, RELATIVE_MODE))
        self.mode_selector.pack(side="left")
        ttk.Button(mode_row, text="Relative mode settings…",
                   command=lambda: app.show_advanced("Mouse control")).pack(side="left", padx=(8, 0))
        ttk.Label(setup_step, textvariable=self.mode_hint_var, wraplength=730).grid(
            row=4, column=0, sticky="w", pady=(6, 0))

        play_step = ttk.LabelFrame(self, text="3. Get ready, then start from Rhythia", padding=10)
        play_step.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        play_step.columnconfigure(0, weight=1)
        timing = ttk.Frame(play_step)
        timing.grid(row=0, column=0, sticky="w")
        ttk.Label(timing, text="Countdown (s)").pack(side="left")
        ttk.Entry(timing, textvariable=app.vars["countdown_s"], width=7).pack(side="left", padx=(6, 18))
        ttk.Label(timing, text="Timing offset (ms)").pack(side="left")
        ttk.Entry(timing, textvariable=app.vars["offset_ms"], width=8).pack(side="left", padx=(6, 0))
        self.ready_button = ttk.Button(play_step, text="Ready to play", command=app.quick_ready)
        self.ready_button.grid(row=1, column=0, sticky="w", pady=(9, 0))
        ttk.Label(play_step, textvariable=self.readiness_var, wraplength=730).grid(
            row=2, column=0, sticky="w", pady=(7, 0))
        ttk.Label(play_step, textvariable=self.guide_var, wraplength=730).grid(
            row=3, column=0, sticky="w", pady=(6, 0))

        stops = ttk.Frame(self)
        stops.grid(row=4, column=0, sticky="ew", pady=(2, 7))
        self.stop_button = ttk.Button(stops, text="Stop", command=app.stop)
        self.stop_button.pack(side="left")
        self.emergency_button = ttk.Button(stops, text="EMERGENCY STOP · Ctrl+Alt+F10", command=app.emergency_stop)
        self.emergency_button.pack(side="left", padx=(8, 0))
        ttk.Label(self, textvariable=app.status_var, wraplength=760).grid(row=5, column=0, sticky="w")
        self.rowconfigure(6, weight=1)
        self.refresh()

    def refresh(self):
        """Reflect current map, setup and scheduler state without side effects."""
        app = self.app
        has_map = app.loaded_map is not None and app.pending_map is None
        if app.pending_map is not None:
            self.map_var.set("This map could not be loaded. Choose a supported .sspm map.")
        elif has_map:
            duration_s = max(0, round(app.chart.duration_ms / 1000))
            self.map_var.set(f"{display_text(app.chart.title, 110)} · {len(app.chart.notes)} notes · "
                             f"{duration_s // 60}:{duration_s % 60:02d}")
        else:
            self.map_var.set("Choose the .sspm file for the song you will play in Rhythia.")

        captured = app.target is not None
        self.target_var.set(f"Game window: {display_text(app.target.title, 100)}" if captured else
                            "No game window selected yet. Guided setup will select it.")
        mode = app.vars["input_mode"].get()
        if mode == ABSOLUTE_MODE:
            self.mode_hint_var.set("Test the center once: focus Rhythia and press Ctrl+Alt+F9. "
                                   "If the cursor does not reach the center, try Relative calibrated and follow its settings.")
        elif mode == RELATIVE_MODE:
            self.mode_hint_var.set("Set relative sensitivity in Relative mode settings. Reset the game cursor to your "
                                   "chosen starting point before every start or Ctrl+Alt+F9 test.")
        else:
            self.mode_hint_var.set("Guided setup selects Absolute screen for a first test. Verify it with Ctrl+Alt+F9; "
                                   "the game's supported mouse mode is still unverified.")

        start_key = app.settings.start_hotkey
        stop_key = app.settings.stop_hotkey
        try:
            zero_countdown = float(app.vars["countdown_s"].get()) == 0
        except ValueError:
            zero_countdown = False
        timeline_guide = (f"{start_key} at song timeline start." if zero_countdown else
                          f"Press {start_key} one countdown before song timeline start.")
        self.guide_var.set(f"Open Rhythia, select the same song. {timeline_guide} "
                           f"Tool does not start audio. {stop_key} stops; pausing requires restarting the song.")

        snapshot = app.engine.snapshot()
        active = snapshot.state in {PlaybackState.COUNTDOWN, PlaybackState.RUNNING, PlaybackState.PAUSED}
        pending = app.calibration_pending or app.live_preparing
        hotkeys_ready = (app.hotkeys is not None and not app.hotkeys.last_error and
                         app.hotkey_signature == tuple(app._hotkey_mapping(app.settings).items()))
        if app.live_preparing:
            message = "Preparing mouse playback. Stop cancels immediately."
        elif app.calibration_pending:
            message = "Finish the guided capture before getting ready."
        elif active and app.active_run_live:
            message = f"Mouse playback is active. {stop_key} stops; Ctrl+Alt+F10 is the emergency stop."
        elif active:
            message = "Preview is active. Stop it before getting ready for mouse playback."
        elif not has_map:
            message = "First choose your map."
        elif not captured:
            message = "Next, complete Guided setup."
        elif mode not in (ABSOLUTE_MODE, RELATIVE_MODE):
            message = "Choose a mouse mode or complete Guided setup."
        elif not hotkeys_ready:
            message = "Global hotkeys are unavailable. Ready to play will apply settings; resolve any error shown below."
        elif app.live_requested and self._quick_choices_applied():
            message = f"Ready for a manual start. Focus Rhythia, test Ctrl+Alt+F9, then use {start_key} at your chosen start time."
        else:
            message = "Click Ready to play to apply these choices. It will not move the mouse or start the song."
        self.readiness_var.set(message)
        can_ready = has_map and captured and mode in (ABSOLUTE_MODE, RELATIVE_MODE) and not pending and not active
        self.ready_button.state(["!disabled"] if can_ready else ["disabled"])
        self.setup_button.state(["disabled"] if pending else ["!disabled"])

    def _quick_choices_applied(self):
        """F6 uses committed settings, so edits must not look already applied."""
        app = self.app
        if app.vars["input_mode"].get() != app.settings.input_mode:
            return False
        try:
            return all(float(app.vars[key].get()) == getattr(app.settings, key)
                       for key in ("countdown_s", "offset_ms"))
        except ValueError:
            return False
