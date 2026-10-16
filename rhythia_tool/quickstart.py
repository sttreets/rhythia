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
        self.rowconfigure(1, weight=1)
        self._wrap_labels = []

        ttk.Label(self, text="Get ready in three steps", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8))

        scroll_area = ttk.Frame(self)
        scroll_area.grid(row=1, column=0, sticky="nsew")
        scroll_area.columnconfigure(0, weight=1)
        scroll_area.rowconfigure(0, weight=1)
        self.scroll_canvas = tk.Canvas(scroll_area, width=1, height=1, highlightthickness=0, borderwidth=0,
                                       takefocus=True, background=self.winfo_toplevel().cget("background"))
        self.scroll_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(scroll_area, orient="vertical", command=self.scroll_canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.scroll_canvas.configure(yscrollcommand=scrollbar.set)
        self.scroll_content = ttk.Frame(self.scroll_canvas)
        self.scroll_content.columnconfigure(0, weight=1)
        self._content_window = self.scroll_canvas.create_window((0, 0), window=self.scroll_content, anchor="nw")
        self.scroll_canvas.bind("<Configure>", self._resize_content)
        self.scroll_content.bind("<Configure>", self._update_scroll_region)

        map_step = ttk.LabelFrame(self.scroll_content, text="1. Choose your map", padding=10)
        map_step.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        map_step.columnconfigure(0, weight=1)
        map_buttons = ttk.Frame(map_step)
        map_buttons.grid(row=0, column=0, sticky="w")
        self.map_button = ttk.Button(map_buttons, text="Choose .sspm map…", command=app.pick_map)
        self.map_button.pack(side="left")
        ttk.Button(map_buttons, text="Preview / map details", command=lambda: app.show_advanced("Playback")).pack(
            side="left", padx=(8, 0))
        self._wrapped_label(map_step, self.map_var).grid(row=1, column=0, sticky="w", pady=(7, 0))

        setup_step = ttk.LabelFrame(self.scroll_content, text="2. Show the tool your play area", padding=10)
        setup_step.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        setup_step.columnconfigure(0, weight=1)
        self.setup_button = ttk.Button(setup_step, text="Set up playfield…", command=app.quick_setup)
        self.setup_button.grid(row=0, column=0, sticky="w")
        self._wrapped_label(setup_step, app.quick_setup_var).grid(row=1, column=0, sticky="w", pady=(7, 0))
        self._wrapped_label(setup_step, self.target_var).grid(row=2, column=0, sticky="w", pady=(4, 0))
        mode_row = ttk.Frame(setup_step)
        mode_row.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(mode_row, text="Mouse mode:").pack(side="left", padx=(0, 8))
        self.mode_selector = ttk.Combobox(mode_row, state="readonly", width=23,
                                         textvariable=app.vars["input_mode"],
                                         values=(ABSOLUTE_MODE, RELATIVE_MODE))
        self.mode_selector.pack(side="left")
        ttk.Button(mode_row, text="Relative mode settings…",
                   command=lambda: app.show_advanced("Mouse control")).pack(side="left", padx=(8, 0))
        self._wrapped_label(setup_step, self.mode_hint_var).grid(row=4, column=0, sticky="w", pady=(6, 0))

        play_step = ttk.LabelFrame(self.scroll_content, text="3. Get ready, then start from Rhythia", padding=10)
        play_step.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        play_step.columnconfigure(0, weight=1)
        timing = ttk.Frame(play_step)
        timing.grid(row=0, column=0, sticky="w")
        ttk.Label(timing, text="Countdown (s)").pack(side="left")
        ttk.Entry(timing, textvariable=app.vars["countdown_s"], width=7).pack(side="left", padx=(6, 18))
        ttk.Label(timing, text="Timing offset (ms)").pack(side="left")
        ttk.Entry(timing, textvariable=app.vars["offset_ms"], width=8).pack(side="left", padx=(6, 0))
        self.ready_button = ttk.Button(play_step, text="Ready to play", command=app.quick_ready)
        self.ready_button.grid(row=1, column=0, sticky="w", pady=(9, 0))
        self._wrapped_label(play_step, self.readiness_var).grid(row=2, column=0, sticky="w", pady=(7, 0))
        self._wrapped_label(play_step, self.guide_var).grid(row=3, column=0, sticky="w", pady=(6, 0))

        stops = ttk.Frame(self)
        stops.grid(row=2, column=0, sticky="ew", pady=(8, 7))
        self.stop_button = ttk.Button(stops, text="Stop", command=app.stop)
        self.stop_button.pack(side="left")
        self.emergency_button = ttk.Button(stops, text="EMERGENCY STOP · Ctrl+Alt+F10", command=app.emergency_stop)
        self.emergency_button.pack(side="left", padx=(8, 0))
        self._status_label = ttk.Label(self, textvariable=app.status_var, wraplength=760)
        self._status_label.grid(row=3, column=0, sticky="w")
        self.bind("<Configure>", self._resize_status)
        # A private bindtag reaches only this panel's widgets. It never adds a
        # process-wide wheel binding or affects the advanced pages/combobox list.
        self._scroll_bindtag = f"QuickStartScroll{id(self)}"
        self.bind_class(self._scroll_bindtag, "<MouseWheel>", self._scroll_wheel)
        self.bind_class(self._scroll_bindtag, "<Button-4>", self._scroll_wheel)
        self.bind_class(self._scroll_bindtag, "<Button-5>", self._scroll_wheel)
        self.bind_class(self._scroll_bindtag, "<Prior>", lambda event: self._scroll_page(event, -1))
        self.bind_class(self._scroll_bindtag, "<Next>", lambda event: self._scroll_page(event, 1))
        self.bind_class(self._scroll_bindtag, "<FocusIn>", self._scroll_focus)
        self._add_scroll_bindtag(self.scroll_canvas)
        self.bind("<Destroy>", self._clear_scroll_bindings)
        self.refresh()

    def _wrapped_label(self, parent, variable):
        label = ttk.Label(parent, textvariable=variable, wraplength=730)
        self._wrap_labels.append(label)
        return label

    def _resize_content(self, event):
        self.scroll_canvas.itemconfigure(self._content_window, width=event.width)
        for label in self._wrap_labels:
            label.configure(wraplength=max(120, event.width - 28))

    def _resize_status(self, event):
        if event.widget is self:
            self._status_label.configure(wraplength=max(120, event.width - 24))

    def _update_scroll_region(self, _event=None):
        self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))

    def _add_scroll_bindtag(self, widget):
        tags = widget.bindtags()
        widget.bindtags((tags[0], self._scroll_bindtag, *tags[1:]))
        for child in widget.winfo_children():
            self._add_scroll_bindtag(child)

    def _scroll_wheel(self, event):
        if isinstance(event.widget, ttk.Combobox):
            return None  # Keep the selector's own wheel behavior.
        if getattr(event, "num", None) in (4, 5):
            units = -3 if event.num == 4 else 3
        else:
            delta = getattr(event, "delta", 0)
            if not delta:
                return None
            units = -max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)
        self.scroll_canvas.yview_scroll(units, "units")
        return "break"

    def _scroll_page(self, event, direction):
        if isinstance(event.widget, ttk.Combobox):
            return None
        self.scroll_canvas.yview_scroll(direction, "pages")
        return "break"

    def _scroll_focus(self, event):
        """Tabbing to an off-screen control brings that control into view."""
        if event.widget is self.scroll_canvas:
            return
        top = self.scroll_canvas.canvasy(0)
        height = self.scroll_canvas.winfo_height()
        widget_top = event.widget.winfo_rooty() - self.scroll_content.winfo_rooty()
        widget_bottom = widget_top + event.widget.winfo_height()
        content_height = max(1, self.scroll_content.winfo_height())
        if widget_top < top:
            self.scroll_canvas.yview_moveto(widget_top / content_height)
        elif widget_bottom > top + height:
            self.scroll_canvas.yview_moveto((widget_bottom - height) / content_height)

    def _clear_scroll_bindings(self, event):
        if event.widget is self:
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>", "<Prior>", "<Next>", "<FocusIn>"):
                self.unbind_class(self._scroll_bindtag, sequence)

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
