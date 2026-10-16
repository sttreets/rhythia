"""Validated, atomic, per-user settings. No game credentials are stored."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import math
import os
from pathlib import Path
import tempfile


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    countdown_s: float = 0.0
    offset_ms: float = 0.0
    speed: float = 1.0
    left: int = 0
    top: int = 0
    width: int = 900
    height: int = 900
    screen_width: int = 1920
    screen_height: int = 1080
    dpi_percent: float = 100.0
    input_mode: str = "Unverified"
    counts_per_unit_x: float = 300.0
    counts_per_unit_y: float = 300.0
    anchor_x: float = 1.0
    anchor_y: float = 1.0
    invert_x: bool = False
    invert_y: bool = False
    live_lateness_ms: float = 50.0
    smooth_motion: bool = True
    approach_ms: float = 120.0
    arrival_lead_ms: float = 12.0
    start_hotkey: str = "F6"
    pause_hotkey: str = "F7"
    stop_hotkey: str = "F8"
    last_map_path: str = ""

    def validate(self) -> Settings:
        for name, low, high in (
            ("countdown_s", 0, 3600), ("offset_ms", -5000, 5000),
            ("speed", 0.1, 4), ("dpi_percent", 25, 800),
            ("counts_per_unit_x", 0.001, 100000), ("counts_per_unit_y", 0.001, 100000),
            ("anchor_x", -0.5, 2.5), ("anchor_y", -0.5, 2.5), ("live_lateness_ms", 1, 250),
            ("approach_ms", 10, 1000), ("arrival_lead_ms", 0, 100),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SettingsError(f"{name} must be a number.")
            if not low <= value <= high or not math.isfinite(value):
                raise SettingsError(f"{name} must be between {low} and {high}.")
        for name in ("left", "top", "width", "height", "screen_width", "screen_height"):
            value = getattr(self, name)
            low = -100000 if name in ("left", "top") else 1
            if type(value) is not int or not low <= value <= 100000:
                raise SettingsError(f"{name} must be a whole number between {low} and 100000.")
        if self.input_mode not in ("Unverified", "Absolute screen", "Relative calibrated"):
            raise SettingsError("Unknown cursor input mode.")
        for name in ("invert_x", "invert_y", "smooth_motion"):
            if type(getattr(self, name)) is not bool:
                raise SettingsError(f"{name} must be true or false.")
        for name in ("start_hotkey", "pause_hotkey", "stop_hotkey"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise SettingsError(f"{name} must not be empty.")
        if not isinstance(self.last_map_path, str):
            raise SettingsError("last_map_path must be text.")
        return self

    def physical_position(self, x: float, y: float) -> tuple[int, int]:
        """Convert normalized DEMO coordinates to calibrated physical pixels."""
        if not (math.isfinite(x) and math.isfinite(y) and 0 <= x <= 1 and 0 <= y <= 1):
            raise SettingsError("Demo coordinates must be finite and between 0 and 1.")
        return self.left + round(x * (self.width - 1)), self.top + round(y * (self.height - 1))


def default_settings_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "RhythiaTool" / "settings.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "rhythia-tool" / "settings.json"


def load_settings(path: Path) -> Settings:
    try:
        if not path.exists():
            return Settings()
        if path.stat().st_size > 65536:
            raise SettingsError("Settings file is too large (maximum 64 KiB).")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SettingsError("Settings must contain a JSON object.")
        unknown = set(data) - {field.name for field in fields(Settings)}
        if unknown:
            raise SettingsError("Unknown settings fields: " + ", ".join(sorted(unknown)))
        if data.get("input_mode") in ("Absolute desktop (unverified)", "Relative/raw (unverified)"):
            data["input_mode"] = "Unverified"
        return Settings(**data).validate()
    except SettingsError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise SettingsError(f"Cannot load settings: {exc}") from exc


def save_settings(path: Path, settings: Settings) -> None:
    settings.validate()
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".settings-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(asdict(settings), stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except OSError as exc:
        raise SettingsError(f"Cannot save settings: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)
