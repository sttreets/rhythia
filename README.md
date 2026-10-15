# Rhythia Tool

A Python/Tkinter tool that reads SSPM v2 beatmaps and previews their notes, with timing controls, calibration settings, global Windows hotkeys, and standalone Windows packaging scripts. The target is **Steam/CAPO Games Rhythia on Windows**, with game version **0.2.0 reported by the user**.

**The interface provides a silent preview, not live autoplay.** Map parsing is implemented and tested. Steam cursor/input behavior, song synchronization, normal score acceptance, and ranked RP remain **unverified**, so live mouse output is disabled. No code submits scores or changes game eligibility.

## Supported maps and confirmed sample

Select a local `.sspm` file directly; locating the game's map folder is unnecessary. The parser follows the public [SSPM v2 specification](https://github.com/basils-garden/types/blob/4095da95def637822acef7403b8f16278226e169/sspm/v2.md). It extracts titles, mapper names, note timestamps, and raw note coordinates. It supports the standard `ssp_note` marker schema. Other container versions, additional/custom marker schemas, malformed data, and files exceeding parser limits produce explicit errors.

The supplied sample, **Sporty-O - Let Me Hit It (Audiostalkers Original Mix)(Cut Ver.)**, identifies mapper **dxprso** and contains **220 notes**, from **491 ms to 31,897 ms**. It was parsed and simulated without moving the mouse. Its off-grid/quantum coordinates are retained. The preview expands its viewport to contain them; this drawing transform is not a verified mapping to the Steam game's playfield.

The sample contains embedded MP3 and PNG data, identified by their signatures only. Audio and images are not decoded, extracted, or played. Its marker SHA-1 field is all zeros, so the parser warns that marker integrity is unverified. It also reports 24 trailing footer bytes outside the declared blocks, which are ignored. Neither the supplied map nor its media is included in the repository; tests generate their own fixtures.

The Steam game version `0.2.0` and SSPM format version `2` are separate version numbers. The open-source [Rhythia/Client README](https://github.com/Rhythia/Client/blob/9200e16441c94f0cffca8ee10beb4e889235c56a/README.md) explicitly distinguishes its project from Steam/CAPO Games Rhythia. Its similarly numbered releases, input implementation, and score eligibility rules do not establish Steam compatibility.

To identify an exact installed Steam build later, check the game's About/Settings/title screen and Steam's Build ID when available. **Library → Rhythia → Properties → Installed Files → Browse** opens the installation directory; the game executable's **Properties → Details** may show another version string. For other maps, use the game's own import/export or open-folder command if available and enable File Explorer's file extensions. No universal Steam Rhythia map path has been verified.

## Run from source on Windows

Use Windows 10/11, 64-bit, and **64-bit Windows Python 3.11–3.14** from [python.org](https://www.python.org/downloads/windows/). Include the **Tcl/Tk and IDLE** component and Python Launcher when installing. Python 3.13 or 3.14 is a suitable choice. The packaging version pinned here does not support Python 3.15.

Extract/clone this repository and double-click `run.bat`, or open Command Prompt in the repository and run:

```bat
run.bat
```

The launcher uses an existing `.venv\Scripts\python.exe`, or creates `.venv` using `py -3` and then a `python` fallback. It checks Windows, x64 interpreter architecture, Python version, and Tkinter. If your default interpreter is unsupported but Python 3.14 is also installed, create `.venv` explicitly with `py -3.14 -m venv .venv` before launching. It does not require administrator access. There are no third-party runtime packages; Tkinter and Windows API access through `ctypes` come with Python.

For a direct launch after creating a suitable environment:

```bat
.venv\Scripts\python.exe -m rhythia_tool
```

## Preview, calibration, and controls

1. Choose **Select local map…** and open an SSPM v2 file. The interface shows its title, mapper, note count, timeline length, embedded-audio presence, and warnings. Alternatively, choose **Load synthetic demo** to use generated notes. The preview moves a marker inside the interface; your desktop cursor stays under your control.
2. Enter the playfield's left/top corner and width/height in **physical screen pixels**. Negative screen coordinates are valid for monitors left of or above the primary screen. Optional delayed corner capture gives you time to position the cursor before recording a corner. Capturing calibration reads the cursor position; it does not move it.
3. Record the relevant display scale/resolution and selected input mode. Calibration is used for preview pixel positions only; no verified mapping to Steam's absolute/relative cursor mode exists yet. The Windows package requests Per-Monitor V2 DPI awareness. Recalibrate after changing resolution, DPI scale, game window position, or playfield layout.
4. Set countdown, timing offset, and playback speed. A positive offset delays notes; a negative offset brings them earlier and may emit notes during countdown. Speed scales the note timeline, so `2.0` plays it twice as fast; offset remains measured in wall-clock milliseconds. These controls change the simulated schedule, not game audio.
5. Optionally capture a foreground window for the focus guard. With that guard enabled, the simulation stops when a different window becomes foreground; it does not automatically resume. If no target is captured, the UI makes that state visible.
6. Start, pause/resume, or stop the silent preview. Status shows the current state, elapsed timeline, lateness, and errors. Invalid/unsupported maps disable playback until a valid map or the synthetic demo is loaded. When the focus guard is enabled, start with its captured window focused using the global start hotkey.

| Action | Default Windows global hotkey |
| --- | --- |
| Start/resume simulation | F6 |
| Pause simulation | F7 |
| Stop/reset | F8 |
| Emergency stop | Ctrl+Alt+F10, fixed |

Start/pause/stop hotkeys are configurable; Ctrl+Alt+F10 stays reserved for emergency stop. Supported syntax includes `F6`, `Ctrl+Alt+F6`, or `Shift+A`. Global hotkeys require Windows and can fail when another program owns a binding. The interface reports registration failures; choose another available binding. Buttons remain available. On other platforms, use the buttons for simulation. F12 is rejected for global bindings because [Windows reserves it for debuggers](https://github.com/MicrosoftDocs/sdk-api/blob/docs/sdk-api-src/content/winuser/nf-winuser-registerhotkey.md).

Choose **Timing & hotkeys → Save settings** to persist your changes. Global hotkeys use the last applied settings; editing a field alone does not apply it to an active run. Saving stops the current preview, so restart afterward. Settings are saved to `%LOCALAPPDATA%\RhythiaTool\settings.json` on Windows. On Linux, they use `$XDG_CONFIG_HOME/rhythia-tool/settings.json` or `~/.config/rhythia-tool/settings.json`. Use `--settings PATH` to select another file. Settings are not written into the game installation. Damaged settings are reported and preserved until you explicitly save a replacement.

## Synchronization and game integration

The simulator uses a monotonic high-resolution clock and schedules against elapsed map time. Pausing freezes its own timeline and resuming continues from that time. **It cannot pause or synchronize a song playing in Rhythia.** Do not use this pause/resume behavior as evidence of synchronization with a real run.

A reliable gameplay start procedure still requires authoritative Steam-specific evidence for its audio start event/countdown, timestamp origin, pause behavior, cursor mode, coordinate mapping, focus behavior, and speed modifiers. Parsing the supplied map does not verify those behaviors. There is no documented live autoplay start sequence and live input remains disabled. If game/audio synchronization is lost in a future verified integration, stop and restart the song and tool from the beginning instead of guessing a new offset.

The scheduler uses absolute deadlines and stops before emitting a note more than 250 ms late; a fresh start is required afterward. Windows scheduling can introduce jitter, so the high-resolution clock does not guarantee exact delivery times.

`rhythia_tool/mouse.py` contains a generic guarded Windows `SendInput` adapter with relative/absolute movement and focus/window/DPI checks. It is tested using fakes only and is intentionally not wired to the interface or CLI. Its presence does not establish native Windows or Steam input compatibility. Any future live integration must use normal Windows input and the game's normal submission flow, retain emergency stop, and stop on focus loss or errors.

## Score eligibility evidence

**Steam/CAPO score and ranked RP acceptance are unverified.** No authoritative rule for this installed build has been established. A parsed map, successful cursor movement, or a local results screen would not by itself prove accepted ranked RP. No bypasses, score forging, or alternate submission flow are implemented.

The cloud environment returned proxy HTTP 403 responses for official Steam/CAPO pages. Proposed network additions were saved in environment settings for `store.steampowered.com`, `steamcommunity.com`, `rhythia.com`, `www.rhythia.com`, and `capo.games`. Review/save those settings and publish/update the environment as prompted, then retry the official documentation. Saved configuration alone does not establish access or eligibility. Remaining questions include accepted input modes, map/ranked eligibility, submission checks, and the treatment of automated runs. Rules from the separate open-source client are not Steam eligibility evidence.

## Command-line inspection and simulation

These commands read local files and never control the mouse or play audio:

```bat
.venv\Scripts\python.exe -m rhythia_tool --inspect-map "C:\Maps\example.sspm"
.venv\Scripts\python.exe -m rhythia_tool --simulate-map "C:\Maps\example.sspm"
```

`--inspect-map` prints JSON metadata, note timing/coordinate ranges, warnings, and a SHA-256 file fingerprint. `--simulate-map` uses a fake clock and list sink to verify that every parsed note is emitted in order; it runs without waiting for real song time and reports completion/lateness. Both report Steam and RP compatibility as unverified. Invalid files produce a clear error and nonzero exit code.

To open the same map in the interface:

```bat
run.bat --map "C:\Maps\example.sspm"
```

## Build a standalone Windows executable

On an actual Windows 10/11 x64 computer with the Python installation above:

```bat
build.bat
```

The script creates/reuses `.venv`, installs `requirements-build.txt` noninteractively, runs the unit tests, and invokes PyInstaller with `RhythiaTool.spec`. It stops on a failed command and preserves its error code. The build dependency is pinned to **PyInstaller 6.16.0**; its transitive dependencies are resolved by pip. Network access to PyPI and its file host is needed for an uncached install.

The result is `dist\RhythiaTool.exe`: a single GUI executable with Tkinter bundled, an `asInvoker` manifest, and Per-Monitor V2 DPI awareness. The recipient does not need Python. Test that exact executable on Windows before distributing it. It is unsigned; signing and SmartScreen reputation are separate release tasks. Administrator access is not required.

Build on Windows using Windows Python. This repository does not use Wine or claim that a Linux build produces a Windows executable. See the [PyInstaller 6.16 operating-mode documentation](https://github.com/pyinstaller/pyinstaller/blob/v6.16.0/doc/operating-mode.rst) and [Windows manifest documentation](https://github.com/MicrosoftDocs/win32/blob/docs/desktop-src/SbsCs/application-manifests.md).

## Verification

Run tests without touching the desktop cursor:

```bat
python -m unittest discover -s tests -v
python -m rhythia_tool --self-check
```

The tests exercise SSPM v2 parsing and malformed-file rejection, quantum coordinates and preview transforms, timing with simulated input, pause/resume, offsets, speed, stop/focus guards, settings validation, and Windows API behavior through mocks. The `--self-check` command independently emits all 32 synthetic notes against a fake clock; use `--simulate-map` to check a local map.

Cloud validation: `python3 scripts/cloud_validate.py` last passed **107 tests**, including **eight Tk functional tests**, plus the headless self-check on Linux. On a headless Linux host it starts and cleans up an isolated Xvfb display; the saved environment setup prepares Xvfb outside the checkout. GUI checks covered valid-map loading/playback, invalid-map gating, a complete threaded 32-note demo, controls, and settings. CLI checks exercised the actual entry point, malformed-file exit codes, inspection, and full-map simulation. The 16 mouse-adapter tests used fake native calls; hotkey registration/error cleanup was also simulated. The supplied 220-note map was parsed and fully simulated separately. These checks never injected desktop input. Without a desktop/display, direct unittest discovery skips the eight Tk tests; a skipped run is not equivalent to full cloud validation.

Development verification in the cloud environment uses Linux. Native Windows hotkey registration, physical display/DPI behavior, Windows batch scripts, packaging, and gameplay have **not** been run there. No Windows `.exe` is included until a suitable Windows build environment is available. Passing simulation tests does not establish live autoplay, audio synchronization, or RP eligibility.

## Troubleshooting

| Problem | Action |
| --- | --- |
| Python or Tkinter unavailable | Install a supported x64 Python with Tcl/Tk. If the existing `.venv` uses another interpreter, rename it and rerun the launcher. |
| Launcher closes before you can read an error | Open Command Prompt in this folder and run `run.bat`; the shell retains the error text. |
| Hotkey registration error | Choose an unused binding, apply settings, and retry. Ctrl+Alt+F10 is the fixed emergency binding; close a conflicting application if necessary. F12 cannot be a global binding. |
| Local map cannot start | Read the parser error. Only SSPM v2 with the standard note schema is supported; older versions/custom markers are rejected. Empty maps cannot play. Load the synthetic demo to check the interface independently. |
| Preview is silent despite embedded audio | Expected. Embedded audio is identified but not decoded or played; game/song synchronization is unverified. |
| Missing marker checksum or trailing-bytes warning | Review the warning. All-zero SHA-1 means marker integrity was not verified; trailing data outside declared blocks is ignored. A nonzero checksum mismatch is an error. |
| Calibration is shifted after a display change | Re-enter or recapture physical-pixel bounds on the intended display; record the new scaling and resolution. |
| Simulation stops when switching windows or falling behind | The focus/lateness guard requires a fresh start. Capture the intended window again if necessary, then start deliberately. |
| Settings cannot be saved | Check directory permissions or launch with `--settings` pointing to a writable location. Do not run as administrator as a workaround. |
| Build fails downloading packages | Check the reported PyPI/network/TLS error and retry after fixing connectivity. Do not disable certificate verification. |
| Executable exits unexpectedly | Run from source in Command Prompt to see startup diagnostics; report the error and Python/Windows versions. |
