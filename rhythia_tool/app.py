"""SSPM preview interface. Steam live integration remains disabled."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .chart import display_text, preview_sspm
from .demo import load_demo
from .playback import PlaybackEngine, PlaybackState
from .settings import Settings, SettingsError, default_settings_path, load_settings, save_settings
from .sspm import MapFormatError, load_sspm
from .windows import GlobalHotkeys, IS_WINDOWS, cursor_position, foreground_window, set_dpi_awareness


ACTIVE = {PlaybackState.COUNTDOWN, PlaybackState.RUNNING, PlaybackState.PAUSED}


class Application:
    def __init__(self, root: tk.Tk, settings_path: Path):
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
        self.preview_generation = 0
        self.target = None
        self.guard_enabled = False
        self.hotkeys = None
        self.hotkey_signature = None
        self.pending_map: Path | None = None
        self.demo = load_demo()
        self.chart = self.demo
        self.loaded_map = None
        self.source_path = None
        self.engine = PlaybackEngine(self.demo.notes,
                                     lambda note: self.events.put(("note", (self.preview_generation, note))))
        self.last_note = None
        self.after_id = None
        self.capture_after_id = None
        self.root.title(f"Rhythia Tool {__version__} · Simulation")
        self.root.minsize(760, 650)
        self.root.geometry("860x800")
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)
        self.vars = {name: tk.StringVar(value=str(value)) for name, value in asdict(self.settings).items()}
        self.source_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready for synthetic preview.")
        self.timing_var = tk.StringVar()
        self.position_var = tk.StringVar(value="No simulated note emitted yet.")
        self.hotkey_var = tk.StringVar()
        self.window_var = tk.StringVar(value="No foreground window captured.")
        self.guard_var = tk.BooleanVar(value=False)
        self.notice_var = tk.StringVar(value=self.settings_warning)
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
        ttk.Label(outer, text="PREVIEW ONLY · Steam/CAPO live compatibility unverified · Desktop cursor is never moved",
                  foreground="#a14212", wraplength=800).pack(anchor="w", pady=(2, 10))
        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True)
        playback = ttk.Frame(tabs, padding=12)
        calibration = ttk.Frame(tabs, padding=12)
        settings_tab = ttk.Frame(tabs, padding=12)
        help_tab = ttk.Frame(tabs, padding=12)
        tabs.add(playback, text="Playback preview")
        tabs.add(calibration, text="Calibration")
        tabs.add(settings_tab, text="Timing & hotkeys")
        tabs.add(help_tab, text="Steam compatibility")

        row = ttk.Frame(playback)
        row.pack(fill="x")
        ttk.Button(row, text="Select local map…", command=self.pick_map).pack(side="left")
        ttk.Button(row, text="Load synthetic demo", command=self.load_synthetic).pack(side="left", padx=8)
        ttk.Label(playback, textvariable=self.source_var, wraplength=760).pack(anchor="w", pady=8)
        self.canvas = tk.Canvas(playback, width=480, height=310, background="#172032", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, pady=(0, 8))
        self.canvas.bind("<Configure>", lambda _: self._draw_preview())
        ttk.Label(playback, textvariable=self.position_var).pack(anchor="w")
        controls = ttk.Frame(playback)
        controls.pack(fill="x", pady=10)
        self.start_button = ttk.Button(controls, text="Start / Resume preview", command=self.start)
        self.start_button.pack(side="left")
        ttk.Button(controls, text="Pause preview", command=self.pause).pack(side="left", padx=6)
        ttk.Button(controls, text="Stop", command=self.stop).pack(side="left")
        ttk.Button(controls, text="EMERGENCY STOP (Ctrl+Alt+F10)", command=self.emergency_stop).pack(side="right")
        ttk.Label(playback, text="Pause freezes only this simulation. It does not pause game audio.\n"
                  "A focus change, emergency stop, or missed deadline requires a fresh start.",
                  wraplength=760).pack(anchor="w")

        ttk.Label(calibration, text="Physical desktop pixels (preview mapping only)",
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
        ttk.Label(calibration, text="Cursor input mode (recorded preference; not verified)").grid(row=9, column=0, sticky="w", pady=6)
        ttk.Combobox(calibration, state="readonly", textvariable=self.vars["input_mode"], width=30,
                     values=("Unverified", "Absolute desktop (unverified)", "Relative/raw (unverified)")).grid(row=9, column=1, sticky="w")
        ttk.Button(calibration, text="Capture foreground window in 3s", command=self.capture_window).grid(row=10, column=0, columnspan=2, sticky="w", pady=10)
        ttk.Label(calibration, textvariable=self.window_var, wraplength=700).grid(row=11, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(calibration, text="Stop preview when captured window loses focus", variable=self.guard_var,
                        command=self._set_guard).grid(row=12, column=0, columnspan=3, sticky="w", pady=10)
        ttk.Label(calibration, text="Window capture does not infer the game's playfield. Calibrate its inner boundaries manually.\n"
                  "Negative X/Y values are valid for monitors to the left/above the primary display.\n"
                  "Use the global start hotkey with the captured window focused when the focus guard is enabled.",
                  wraplength=700).grid(row=13, column=0, columnspan=3, sticky="w")

        timing_fields = [("countdown_s", "Countdown (seconds, 0–3600)"),
                         ("offset_ms", "Timing offset (ms, −5000 to +5000)"),
                         ("speed", "Playback speed (0.1–4×)"),
                         ("start_hotkey", "Start / Resume hotkey"),
                         ("pause_hotkey", "Pause hotkey"), ("stop_hotkey", "Stop hotkey")]
        for row_number, (key, label) in enumerate(timing_fields):
            ttk.Label(settings_tab, text=label).grid(row=row_number, column=0, sticky="w", pady=7)
            ttk.Entry(settings_tab, textvariable=self.vars[key], width=24).grid(row=row_number, column=1, sticky="w", padx=12)
        ttk.Label(settings_tab, text="Emergency stop: Ctrl+Alt+F10 (fixed). Examples: F6, Ctrl+Alt+S.\n"
                  "Positive offset emits later; negative offset may emit during countdown.\n"
                  "Speed scales note timestamps; offset stays in wall-clock milliseconds.\n"
                  "Save timing/calibration changes before starting via global hotkeys.\n"
                  "Saving settings stops the current preview; restart it to apply changes.",
                  wraplength=700).grid(row=6, column=0, columnspan=2, sticky="w", pady=16)
        ttk.Label(settings_tab, textvariable=self.hotkey_var, wraplength=700).grid(row=7, column=0, columnspan=2, sticky="w")
        ttk.Button(settings_tab, text="Save settings", command=self.save_form).grid(row=8, column=0, sticky="w", pady=14)
        ttk.Label(settings_tab, text=f"Settings: {self.settings_path}", wraplength=700).grid(row=9, column=0, columnspan=2, sticky="w")

        help_text = (
            "Target: Steam/CAPO Games Rhythia. Reported game version: 0.2.0. "
            "SSPM v2 parsing is supported against the public SSPM specification and supplied sample. "
            "This does not verify Steam gameplay/input/scoring compatibility.\n\n"
            "No map folder is needed: select a local .sspm file directly. The tool reads its notes and metadata "
            "for a silent preview. Embedded audio and images are not decoded or executed. "
            "The preview preserves quantum/off-grid positions and fits them into its own viewport.\n\n"
            "Steam live input, actual song synchronization, and RP acceptance remain unverified. "
            "Live cursor output is disabled until authoritative Steam-specific evidence is available. "
            "The open-source Rhythia/Client is a different project; its input and eligibility rules "
            "are not evidence for Steam/CAPO.\n\n"
            "To identify the exact installed Steam build, check the game title/About/Settings, and "
            "Steam Properties for a Build ID when available.\n\n"
            "2. If installed through Steam: Library → Rhythia → gear → Properties → Installed Files → Browse. "
            "For a desktop shortcut: right-click → Properties → Open File Location. "
            "Record the download/store URL and the executable's exact name. EXE Properties → Details may show a version.\n\n"
            "3. Use the game's own map import/export or open-map-folder command if one exists. Otherwise inspect "
            "only that installation's maps/songs/user-data folders. Enable View → Show → File name extensions in Explorer. "
            "There is no verified universal map folder.\n\n"
            "4. Send the edition/version or store URL and ONE known playable map. If it is a directory or archive "
            "with metadata/audio, keep its structure and names. Do not send a whole game profile, credentials, or personal files.\n\n"
            "The open-source Rhythia/Client and Steam/CAPO Games edition are separate projects. File extensions "
            "such as .sspm/.phxm/.txt/.rhm are candidates for the open-source client only.\n\n"
            "The parser supports SSPM v2 notes. Other formats and marker types produce clear errors. "
            "No live mouse output, audio synchronization, score submission, or RP acceptance is claimed.\n\n"
            "For now: load the synthetic demo, set countdown/offset/speed, and start the preview. Only the dot in this "
            "window moves. The displayed pixel positions let you inspect calibration without controlling the mouse."
        )
        text = tk.Text(help_tab, wrap="word", height=24, font=("Segoe UI", 10), relief="flat")
        scroll = ttk.Scrollbar(help_tab, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.insert("1.0", help_text)
        text.configure(state="disabled")
        ttk.Separator(outer).pack(fill="x", pady=10)
        ttk.Label(outer, textvariable=self.status_var, wraplength=800).pack(anchor="w")
        ttk.Label(outer, textvariable=self.timing_var).pack(anchor="w", pady=(3, 0))
        ttk.Label(outer, textvariable=self.notice_var, foreground="#a14212", wraplength=800).pack(anchor="w", pady=(3, 0))

    def _form_settings(self) -> Settings:
        values = {name: variable.get() for name, variable in self.vars.items()}
        for key in ("countdown_s", "offset_ms", "speed", "dpi_percent"):
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
                "stop": settings.stop_hotkey, "emergency": "Ctrl+Alt+F10"}

    def save_form(self):
        try:
            updated = self._form_settings()
            save_settings(self.settings_path, updated)
            with self.control_lock:
                self.engine.stop("Settings changed; restart the preview.")
                self.control_generation += 1
                self.preview_generation += 1
                self.settings = updated
            self._register_hotkeys()
            self.notice_var.set("Settings saved. Live game integration remains disabled.")
        except (ValueError, RuntimeError) as exc:
            self._error(str(exc))

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
        # Handle controls immediately, without a queued Start surviving a later
        # emergency stop. Never read Tk variables on this message thread.
        with self.control_lock:
            if action in ("stop", "emergency"):
                self.control_generation += 1
                self.engine.stop("Emergency stop; restart required." if action == "emergency" else "Stopped; restart required.")
            elif action == "pause":
                self.engine.pause()
            elif action == "start" and self.pending_map is None:
                state = self.engine.snapshot().state
                if state == PlaybackState.PAUSED:
                    self.engine.resume()
                elif state not in ACTIVE:
                    with self.target_lock:
                        if self.guard_enabled and self.target is None:
                            self.events.put(("error", "Capture a foreground window before enabling the focus guard."))
                            return
                    try:
                        self.preview_generation += 1
                        self.engine.start(countdown_s=self.settings.countdown_s,
                                          offset_ms=self.settings.offset_ms, speed=self.settings.speed)
                    except (ValueError, RuntimeError) as exc:
                        self.events.put(("error", str(exc)))

    def _error(self, message):
        self.notice_var.set(message)
        messagebox.showerror("Rhythia Tool", message, parent=self.root)

    def pick_map(self):
        chosen = filedialog.askopenfilename(parent=self.root, title="Select an SSPM v2 map for silent preview",
                                            filetypes=[("SSPM beatmaps", "*.sspm"), ("All files", "*.*")])
        if chosen:
            self.select_map(Path(chosen))

    def select_map(self, path: Path):
        with self.control_lock:
            self.engine.stop("Loading SSPM map.")
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
            self.engine.stop("SSPM map loaded for silent preview.")
            self.control_generation += 1
            self.preview_generation += 1
            self.pending_map = None
            self.chart = chart
            self.loaded_map = parsed
            self.source_path = path
            self.engine = PlaybackEngine(chart.notes,
                lambda note: self.events.put(("note", (self.preview_generation, note))))
            self.settings = replace(self.settings, last_map_path=str(path))
        self.vars["last_map_path"].set(str(path))
        authors = ", ".join(display_text(author, 50) for author in parsed.authors[:3]) or "Unknown mapper"
        self.source_var.set(f"{display_text(parsed.title)} · {len(parsed.notes):,} notes · {parsed.duration_ms / 1000:.3f}s\n"
                            f"SSPM v{parsed.format_version} · {authors} · audio {'present' if parsed.has_audio else 'absent'} · silent preview")
        warnings = " ".join(parsed.warnings)
        self.notice_var.set("SSPM decoded; Steam live compatibility remains unverified. " + display_text(warnings, 250))
        self.start_button.configure(state="normal")
        self.last_note = None
        self._draw_preview()

    def load_synthetic(self):
        with self.control_lock:
            self.engine.stop("Synthetic demo loaded; ready to start.")
            self.control_generation += 1
            self.preview_generation += 1
            self.pending_map = None
            self.chart = self.demo
            self.loaded_map = None
            self.source_path = None
            self.engine = PlaybackEngine(self.demo.notes,
                lambda note: self.events.put(("note", (self.preview_generation, note))))
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
            state = self.engine.snapshot().state
            if state == PlaybackState.PAUSED:
                with self.control_lock:
                    if generation == self.control_generation:
                        self.engine.resume()
            elif state not in ACTIVE:
                updated = self._form_settings()
                with self.target_lock:
                    if self.guard_enabled and self.target is None:
                        raise ValueError("Capture a foreground window before enabling the focus guard.")
                with self.control_lock:
                    if generation != self.control_generation:
                        return
                    self.settings = updated
                    self.last_note = None
                    self.preview_generation += 1
                    self.engine.start(countdown_s=updated.countdown_s,
                                      offset_ms=updated.offset_ms, speed=updated.speed)
        except (ValueError, RuntimeError) as exc:
            self._error(str(exc))

    def pause(self):
        self.engine.pause()

    def stop(self):
        self._hotkey_action("stop")

    def emergency_stop(self):
        self._hotkey_action("emergency")

    def _set_guard(self):
        with self.target_lock:
            self.guard_enabled = self.guard_var.get()
        self.engine.stop("Focus guard changed; restart required.")

    def _delayed_capture(self, callback, message):
        if not IS_WINDOWS:
            self._error("Desktop capture is available on Windows. Enter calibration values manually here.")
            return
        if self.capture_after_id:
            self.root.after_cancel(self.capture_after_id)
        self.engine.stop("Calibration started; restart required.")
        self.notice_var.set(message)
        def finish():
            self.capture_after_id = None
            if not self.closing.is_set():
                try:
                    callback()
                except (OSError, ValueError, RuntimeError) as exc:
                    self._error(str(exc))
        self.capture_after_id = self.root.after(3000, finish)

    def capture_corner(self, bottom_right):
        def capture():
            x, y = cursor_position()
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
        def capture():
            target = foreground_window()
            if target is None:
                raise ValueError("No foreground window found. Try capturing again.")
            if target.pid == os.getpid():
                raise ValueError("The tool itself is focused. Switch to the intended game window during the countdown.")
            with self.target_lock:
                self.target = target
            self.window_var.set(f"{target.title or '(untitled)'} · PID {target.pid} · DPI {target.dpi}\n"
                                f"Window bounds {target.rect}; playfield must still be calibrated separately.")
            self.vars["dpi_percent"].set(str(round(target.dpi / 96 * 100, 2)))
            self.notice_var.set("Window captured for optional preview focus checking. Target identity is not saved across launches.")
        self._delayed_capture(capture, "Switch to the intended game window within 3 seconds.")

    def _pump(self):
        while not self.closing.is_set():
            try:
                # Starting and checking focus share one lock so even a zero-
                # countdown global Start cannot emit before its focus check.
                with self.control_lock:
                    state = self.engine.snapshot().state
                    with self.target_lock:
                        target, guarded = self.target, self.guard_enabled
                    if guarded and target is not None and state in ACTIVE:
                        focused = foreground_window()
                        if focused is None or (focused.handle, focused.pid) != (target.handle, target.pid):
                            self.engine.stop("Captured window lost focus; restart required.")
                    self.engine.pump()
            except Exception as exc:
                self.engine.stop(f"Preview stopped after error: {exc}; restart required.")
                self.events.put(("error", str(exc)))
            self.closing.wait(0.002)

    def _refresh(self):
        if self.closing.is_set():
            return
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
        if self.hotkeys and self.hotkeys.last_error:
            self.engine.stop("Global hotkeys failed; restart required.")
            self.hotkey_var.set("Global hotkeys failed: " + self.hotkeys.last_error)
        snapshot = self.engine.snapshot()
        self.status_var.set(f"{snapshot.state.value.capitalize()} · {snapshot.message}")
        self.timing_var.set(f"Simulated song {snapshot.elapsed_ms / 1000:.3f}s · countdown {snapshot.countdown_remaining_s:.2f}s · "
                            f"emitted {snapshot.emitted_count}/{len(self.chart.notes)} · max lateness {snapshot.max_lateness_ms:.2f}ms")
        self._draw_preview()
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
                self.position_var.set(f"SSPM point ({raw_x:.3f}, {raw_y:.3f}) at {note.time_ms:.0f}ms · preview only, no game input")
            else:
                px, py = self.settings.physical_position(note.x, note.y)
                self.position_var.set(f"Preview target: normalized ({note.x:.3f}, {note.y:.3f}) → physical pixels ({px}, {py})")
        else:
            self.position_var.set("Silent preview only. No desktop input is generated.")

    def shutdown(self):
        if self.closing.is_set():
            return
        self.closing.set()
        self.engine.stop("Application closed.")
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
    parser = argparse.ArgumentParser(description="Rhythia Tool: SSPM preview; Steam live integration pending.")
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
