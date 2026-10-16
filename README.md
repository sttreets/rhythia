# Rhythia Tool

Python/Tkinter map preview and **Windows mouse autoplay** for local SSPM v2 beatmaps. The target is Windows 10/11 x64 and Steam/CAPO Games Rhythia, with game version **0.2.0 reported by the user**. Mouse playback uses normal Windows `SendInput`; it does not modify the game or submit scores itself.

**Live mouse playback is implemented, but compatibility with Steam Rhythia is experimental.** The map parser and input/timing logic have been tested with simulated input on Linux and in Windows x64 CI. Native Windows input, actual gameplay synchronization, normal score acceptance, and ranked RP have **not** been verified. The tool starts in **Preview** mode every time you open it.

## Easiest download: Windows app

[**Download RhythiaTool.exe**](https://github.com/sttreets/rhythia/releases/download/windows-build/RhythiaTool.exe) · [Portable ZIP](https://github.com/sttreets/rhythia/releases/download/windows-build/RhythiaTool-Windows.zip) · [Build status](https://github.com/sttreets/rhythia/actions/workflows/windows-build.yml)

Double-click **RhythiaTool.exe**. **No Python, installer, or administrator access is needed.** For the portable ZIP, choose **Extract All** first, then open the executable. These links are published by the Windows build workflow after its tests and executable smoke checks succeed. If the very first build is still running, check Build status and wait for it to finish.

The Windows app is unsigned and experimental. Download it from this repository's release. Windows may show an unknown publisher notice; code signing is not included.

## Quick start

The app opens on one page. Advanced settings are hidden until you need them. Steps scroll on smaller screens or larger display scaling; Stop and Emergency Stop stay visible.

1. **Choose your map:** select the `.sspm` for the song you will play.
2. **Set up playfield:** open Rhythia in windowed or borderless mode with its playfield visible. Click **Set up playfield…**, switch to Rhythia during the three-second countdown, then click the **top-left** and **bottom-right outer corners of the whole 3 × 3 playfield** in the overlay. The game window and calibration are captured together. Your settings save automatically. Escape cancels.
3. **Learn the song's start delay:** click **Learn timing…**, switch to Rhythia, and press **F6 as you start the song**. Press **F9 when the FIRST note should be hit**, not when it first appears. This step records timing and sends **no mouse movement**; you can do it before setting up the playfield. **F8** cancels.
4. **Ready to play:** restart the song from the beginning, click **Ready to play**, switch to Rhythia, and check the center using **Ctrl+Alt+F9**. Press **F6 at the same moment in starting the song that you used while learning**. **F8** stops; **Ctrl+Alt+F10** emergency stops.

Fresh settings use **0 seconds start delay**, **0 ms offset**, **1× speed**, and **smooth movement**. Saved settings retain your choices. Zero delay assumes F6 coincides with the map's timeline origin; the game's actual lead-in is unknown. Learning estimates the delay from your timing marks at song start and the first note's intended hit time. The first/next-note clock and speed are visible on Quick start. The tool does not start or detect audio, and manual timing still needs checking in the game. If the game cursor ignores Absolute screen, select **Relative calibrated**, open **Relative mode settings**, and follow the calibration instructions below.

**Rhythia must be running and focused.** Loading a map does not launch the game, select its song, or play audio. Do not click the centers of the corner cells during setup. After restarting the tool, repeat setup to select the correct game window. Stop and restart the song after losing focus, changing its window/display, or pausing live playback.

## Source launcher and advanced settings

If you prefer the source, [download its ZIP](https://github.com/sttreets/rhythia/archive/refs/heads/main.zip), choose **Extract All**, install **64-bit Windows Python 3.11–3.14** with **Tcl/Tk and IDLE** and the **Python Launcher** from [python.org](https://www.python.org/downloads/windows/), then double-click **run.bat** in **rhythia-main**. No third-party runtime packages are needed. The launcher creates/reuses `.venv`; run it in Command Prompt to retain any error message.

**Show advanced settings** reveals map preview, numeric calibration, custom hotkeys, playback speed, relative sensitivity and detailed instructions. Save edited settings, or use **Ready to play** to apply the current form. Changing settings during live playback stops the run. After editing, F6 refuses to start until the new settings are applied; click **Ready to play**, then restart the song. This prevents an edited delay from silently leaving F6 on an older saved value. The advanced Playback button **Apply live settings (then F6)** also applies fields without moving the cursor; use F6 from the focused game to begin.

Calibration uses physical desktop pixels and a captured foreground window. The guided selector places its overlay over that window, including monitors with negative coordinates. If an exclusive fullscreen game hides the field when the overlay appears, use windowed/borderless mode or Advanced → Calibration's delayed corner captures. The whole calibrated field must fit inside the captured window.

### Absolute screen calibration

Use **Absolute screen** only if a probe actually moves the **in-game** cursor to the corresponding screen position. Some cursor modes may ignore or reinterpret absolute Windows movement. A moving desktop pointer alone does not establish compatibility.

The SSPM format defines coordinates `0`, `1`, and `2` as the three cell centers. This tool maps the surrounding frame `[-0.5, 2.5]` to your calibrated screen rectangle. For example, in a 900-pixel-wide playfield, the three column centers are approximately 150, 450, and 750 pixels from its left edge. **Ctrl+Alt+F9** targets map coordinate `(1, 1)`, the center of the field. The invert options reverse the corresponding axis.

Bounds are physical desktop pixels, including negative coordinates on monitors left of or above the primary display. The Windows package requests Per-Monitor V2 DPI awareness. The recorded screen size and Windows scaling fields are reference information; the tool does not multiply the calibrated pixel bounds by that scale again. It also records the captured target's actual bounds and DPI. Recalibrate and recapture after changing window position, resolution, scaling, monitor, or layout. The complete calibrated field must fit inside the captured window.

### Relative calibrated input

Use **Relative calibrated** if Rhythia accepts normal relative mouse movement. This is not a guarantee of raw input support. In **Mouse control**, enter independently measured **mouse counts per grid unit X/Y**; one grid unit is the distance from one cell center to the next. The initial value of 300 is a placeholder, not a verified game sensitivity.

1. Keep the game's sensitivity, Windows pointer speed, and acceleration settings fixed. Acceleration can make a single counts-per-cell measurement unreliable at other movement speeds.
2. Set **Starting cursor map X/Y** to a location you can manually reproduce, normally `(1, 1)` for the center. Place the **in-game cursor** there yourself.
3. Select the X probe axis, save settings, focus the captured Rhythia window, and press **Ctrl+Alt+F9**. The probe requests one positive grid unit, or one negative unit when the configured anchor is greater than `1.5`, so it stays inside the field. Check whether the cursor moved exactly one cell. Adjust the X count and horizontal inversion if necessary.
4. Reset the cursor to the configured anchor, repeat for Y, and adjust its count and inversion. **Reset the in-game cursor to the anchor before every probe and every fresh run.**

There is no automatic homing, cursor-position feedback, or game sensitivity detection. The first movement is calculated from your configured anchor. Physical mouse movement during playback, uncalibrated acceleration, or game cursor clamping can invalidate subsequent relative positions. Stop and start again from the anchor if that happens.

## Synchronization

The tool does not start or pause Rhythia, monitor song audio, detect the gameplay state, or change the game's speed. You start the same map in the game and use the global start hotkey while Rhythia has focus. Choose an observable event you can repeat, such as clicking the game's song-start control. The **Start delay** field is the delay from that event to the map's timestamp origin. A game's own lead-in is not detected automatically.

### Learn timing without moving the mouse

1. Choose the map and match the visible **Speed** to the game's speed; normally both are **1×**. Keep the timing offset at zero while learning unless you have deliberately calibrated another value.
2. Click **Learn timing…**. No captured window or playfield calibration is needed for this measurement. Resolve any hotkey error first.
3. Focus Rhythia. Start the song and press **F6 at the same reference moment**. Learning records that time; it does not start mouse playback.
4. Press **F9 when the first note should be hit**. Mark the judgment time, **not the note's first appearance or an earlier approach animation**. The app computes and saves the start delay:

   ```text
   start delay = time from F6 to F9 − first map timestamp / speed − timing offset
   ```

5. **Restart the song from the beginning.** Click **Ready to play**, return to Rhythia, and press F6 at the **same reference moment** used in step 3. Complete playfield setup first if you have not done it yet. Reset the relative cursor anchor before playback when using relative input.

F8 or the emergency stop cancels learning. Learning sends no mouse input, including while waiting for F9. It is a manual measurement, so an inaccurate F9 press still produces an inaccurate alignment. Learn again if the song, game speed, timing offset, or start reference changes. F9 is reserved for the timing marker; **Ctrl+Alt+F9** remains the separate cursor calibration probe.

### Note clock and movement timing

Each note is scheduled using a monotonic high-resolution clock:

```text
note deadline = F6 time + countdown + timing offset + map timestamp / speed
```

A positive offset emits later; a negative offset emits earlier and may cause movement during countdown. Offset is in wall-clock milliseconds. The tool's speed must match the speed actually selected in the game; changing it here does not change game audio. For the supplied map, the first note is at **491 ms**, so with zero start delay/offset and speed 1.0 its original deadline is 491 ms after F6. Smooth movement can position the cursor at that endpoint up to **12 ms before** the deadline; it does not remove the map's lead-in. If playback is consistently early with zero start delay, learn the game's lead-in instead of assuming that every map starts when you press F6.

Test short runs, observe the timing, and adjust your chosen start event or offset. The first/next-note clock reports the scheduled map timeline; it does not observe the game's judgments. Windows scheduling and human hotkey timing introduce jitter. While live playback is active, the worker requests a **2 ms sleep** using Python's Windows high-resolution waitable timer ([CPython 3.11 documentation](https://github.com/python/cpython/blob/v3.11.0/Doc/library/time.rst)). This improves the wake-up mechanism, but the operating system may wake the thread late; there is no hard real-time guarantee. These controls provide manual alignment, not automatic audio synchronization; Steam-specific timestamp origin, song-start latency, and judging behavior remain unverified. A precise clock alone cannot establish synchronization with a separate song.

**Live pause cancels playback and disarms input.** Restart the song from the beginning, restore the relative anchor if needed, and press F6 for a fresh run. The same restart procedure applies after focus loss, an emergency stop, timing loss, or an error. Preview pause can resume its own simulated timeline; it does not pause the game's audio.

The live lateness cutoff defaults to **50 ms**, configurable from **1–250 ms**. If a due note is older than that limit, playback stops before sending it. Preview uses 250 ms. These cutoffs detect scheduling delays; they cannot detect audio drift or a manual start at the wrong moment.

## Controls and settings

| Action | Default global hotkey on Windows |
| --- | --- |
| Start live playback / start or resume preview | F6 |
| Pause preview / cancel live playback | F7 |
| Stop | F8 |
| Mark first note's hit time while learning | F9, fixed |
| Single calibration probe | Ctrl+Alt+F9, fixed |
| Emergency stop | Ctrl+Alt+F10, fixed |

Start/pause/stop bindings are configurable, for example `F6`, `Ctrl+Alt+S`, or `Shift+A`. F9 and the probe/emergency bindings stay reserved. If you customize the start key, use that key in place of F6 throughout the learning and playback instructions. [Windows reserves F12 for debuggers](https://github.com/MicrosoftDocs/sdk-api/blob/docs/sdk-api-src/content/winuser/nf-winuser-registerhotkey.md), so it cannot be used. If any hotkey registration fails, live playback and timing learning remain unavailable; choose unused bindings or close the conflicting program. On Linux, use the buttons for preview only.

Live input checks the captured foreground window, process identity, rectangle, and DPI before each movement and while running. It stops if those change, input fails, hotkeys fail, or timing falls behind. Restoring focus does not resume the old run. Foreground inspection and `SendInput` are separate Windows calls, so a focus switch in the tiny interval between them cannot be made atomic. Closing the tool stops and disarms its session.

Editing live settings or saving them stops playback. Apply changes with **Ready to play** before using F6 again. Settings live in `%LOCALAPPDATA%\RhythiaTool\settings.json` on Windows, or `$XDG_CONFIG_HOME/rhythia-tool/settings.json` / `~/.config/rhythia-tool/settings.json` on Linux. `--settings PATH` selects another file. No game credentials are stored. The captured target window and choice of live output are deliberately not saved across launches. Damaged settings are reported and preserved until you explicitly save a replacement. Older preview-only cursor-mode preferences reset to **Unverified** and need a fresh mode selection/calibration.

## Maps and compatibility evidence

The parser follows the public [SSPM v2 specification](https://github.com/basils-garden/types/blob/4095da95def637822acef7403b8f16278226e169/sspm/v2.md). It extracts title, mapper names, timestamps, and raw note coordinates for the standard `ssp_note` marker schema. Other container versions/custom marker schemas, malformed data, and oversized files produce clear errors. Empty maps cannot play.

The supplied **Sporty-O - Let Me Hit It (Audiostalkers Original Mix)(Cut Ver.)** map identifies mapper **dxprso** and has **220 notes**, from **491 ms to 31,897 ms**. It was parsed and simulated without moving the desktop cursor. Quantum/off-grid positions are retained. Preview expands to show them; live playback uses the fixed `[-0.5, 2.5]` frame and rejects any map with a note outside it before enabling input. Live coordinates are not rescaled to each map's extents.

### Smooth cursor movement

**Smooth movement is enabled by default.** Between notes, the cursor follows a straight path with minimum-jerk easing: it accelerates gradually and slows toward the destination. The default approach window is **120 ms**, with an arrival lead of **12 ms**, measured on the map timeline. At 1× speed those values are also wall-clock milliseconds; changing speed scales them with the map. The original note timestamps and note count stay unchanged. Quick start has the smooth-movement switch; advanced **Mouse control** contains the travel and early-arrival values.

The cursor holds the previous note through long gaps, approaches the next note near its deadline, then holds its endpoint through the note time. Closely spaced notes shorten the arrival lead and hold interval so they do not consume the gap. The tool does not know the physical cursor's starting location: before the first note it waits until that note's arrival lead, then sends its endpoint directly. It does not invent a starting position or move along a guessed first path. Relative mode still requires your manual starting anchor.

The motion follows deterministic paths and keeps note timestamps. It still requires correct start-delay calibration and compatible game input. Disable smooth movement to send only the scheduled note endpoints. Equal-time notes remain in file order, but one cursor cannot guarantee judgments at different positions simultaneously. Whether a movement is accepted and judged depends on the game's actual input and hit rules, which have not been established for this Steam build.

The sample contains embedded MP3 and PNG signatures. Media is not decoded, extracted, or played. Its all-zero marker SHA-1 produces an integrity warning; 24 footer bytes outside the declared blocks are ignored. A nonzero checksum mismatch is an error. Neither the uploaded map nor its media is included in the repository; tests generate their own fixtures.

The user-reported Steam game version `0.2.0`, this tool's version, and SSPM format version `2` are separate identifiers. The open-source [Rhythia/Client README](https://github.com/Rhythia/Client/blob/9200e16441c94f0cffca8ee10beb4e889235c56a/README.md) distinguishes that project from Steam/CAPO Games Rhythia; its input and eligibility rules do not establish Steam behavior. To record your exact installed Steam build, check the game's About/Settings and Steam's Build ID when available. **Library → Rhythia → Properties → Installed Files → Browse** opens the installation directory. No universal Steam map folder has been verified.

Windows API behavior follows the documented [`SendInput`](https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-sendinput), [`MOUSEINPUT`](https://learn.microsoft.com/windows/win32/api/winuser/ns-winuser-mouseinput), and [`RegisterHotKey`](https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-registerhotkey) contracts. Relative input may be affected by Windows pointer speed and acceleration. Windows can block injection into a program at a higher integrity level; keep normal privilege levels rather than using elevation as a workaround. No input hooks, game memory access, button/keyboard injection, or bypass of game input restrictions is implemented.

## Scores and ranked RP

**Normal Steam score acceptance and ranked RP eligibility remain unknown.** No authoritative eligibility rule for this installed build or accepted automated run has been verified. Successful movement, map completion, or a local results screen would not by itself prove ranked RP acceptance. There is no claim that this tool earns RP.

The game remains responsible for its normal gameplay and score submission flow. This tool only sends cursor movement; it does not forge scores, alter eligibility flags, call submission endpoints, or bypass input restrictions. Official Steam/CAPO documentation requests from the cloud environment still returned proxy HTTP 403 responses. Proposed network additions were saved in environment settings, but that alone does not establish access or eligibility. Remaining questions include which input modes are supported, automated-run rules, map/ranked eligibility, and server-side submission checks. Rules from the separate open-source client are not evidence for Steam eligibility.

## Command-line inspection and simulation

These commands never control the mouse or play audio:

```bat
.venv\Scripts\python.exe -m rhythia_tool --inspect-map "C:\Maps\example.sspm"
.venv\Scripts\python.exe -m rhythia_tool --simulate-map "C:\Maps\example.sspm"
.venv\Scripts\python.exe -m rhythia_tool --self-check
```

Inspection prints metadata, note ranges, warnings, and a SHA-256 file fingerprint. Simulation uses a fake clock and list sink to check that every note is emitted in order without waiting for real song time. Invalid files return a clear error and nonzero exit code. Steam/RP compatibility remains unverified. The self-check emits all 32 synthetic notes.

To open a map in the interface, initially in Preview mode:

```bat
run.bat --map "C:\Maps\example.sspm"
```

## Build a standalone Windows executable

On an actual Windows 10/11 x64 computer with the supported Python installation above, double-click **build.bat**, or run:

```bat
build.bat
```

The script creates/reuses `.venv`, installs `requirements-build.txt`, runs the tests without injecting real mouse input, and invokes PyInstaller with `RhythiaTool.spec`. It stops on failure. Build dependency **PyInstaller 6.16.0** is pinned; pip resolves its transitive dependencies. An uncached installation needs PyPI and its file host.

The result is **`dist\RhythiaTool.exe`**, a single GUI executable with Tkinter bundled, an `asInvoker` manifest, and Per-Monitor V2 DPI awareness. The recipient does not need Python. Test that exact executable on Windows before distributing it. It is unsigned; signing and SmartScreen reputation are separate release tasks. Administrator access is not required.

**The Linux cloud environment cannot build a native Windows executable locally.** The repository's Windows download workflow runs on Windows x64, tests the source with fake input, builds with PyInstaller, smoke-tests the exact executable without sending input, and publishes the dedicated experimental `windows-build` prerelease with SHA-256 checksums and source commit. The initial Windows build passed all source-test steps, built the executable, and passed its success/error smoke checks before publication ([build log](https://github.com/sttreets/rhythia/actions/runs/38908559532)). These checks establish packaging; actual game compatibility is still unverified. For local builds, use Windows Python on Windows; this repository does not use Wine or claim a Linux build produces a Windows executable. See the [PyInstaller operating-mode documentation](https://github.com/pyinstaller/pyinstaller/blob/v6.16.0/doc/operating-mode.rst).

## Verification

```bat
python -m unittest discover -s tests -v
python -m rhythia_tool --self-check
```

For full Linux cloud validation, including Tk functional checks:

```sh
python3 scripts/cloud_validate.py
```

The cloud validation starts and cleans up an isolated Xvfb display when Linux is headless. Without a display, ordinary unittest discovery skips Tk tests; a skipped run is not equivalent to full validation.

The full Linux validation passed **235 tests without skips**. Tests cover SSPM parsing and damaged-file rejection, quantum coordinates, preview transforms, timing/offset/speed/deadlines with fake clocks and simulated input, settings, and mock Windows API calls. Live-session tests cover fixed-frame calibration, absolute and relative movement, inversion, relative rounding, whole-map preflight, focus/error latching, and concurrent stopping without real input. GUI tests exercise playback controls, live-mode prerequisites, guided setup, two-corner selection, and emergency cancellation using fakes. Deterministic path tests check first-note timing, long-gap holds, close gaps, endpoints, and invalid input; release packaging tests use a fake API. The supplied 220-note map also completed both absolute and relative live-session paths with a fake clock and fake mouse in the earlier endpoint-only implementation (220 events each); no OS input was sent. Consult Build status for the exact revision's Windows test and packaged executable results.

**Native Windows input and hotkeys, physical display/DPI behavior, Windows batch execution, Steam gameplay, actual song synchronization, and RP acceptance have not been tested in this Linux environment.** The separate Windows workflow records its source-test, packaging and executable smoke-check results in Build status. Simulated tests establish the tool's logic, not the game's acceptance of input or scores.

## Troubleshooting

| Problem | Action |
| --- | --- |
| Python or Tkinter unavailable | Install supported x64 Windows Python with Tcl/Tk. Rename an incompatible `.venv`, then rerun `run.bat`. |
| Launcher closes too quickly | Open Command Prompt in this folder and run `run.bat` to retain the error. |
| Nothing moves | Confirm Windows mouse output, a real SSPM map, calibrated mode, registered hotkeys, and the captured Rhythia window focused. Start with F6; clicking Apply live settings only applies fields. |
| F6 says settings changed | Click Ready to play to apply the edited start delay, speed, or other settings, then restart the song and use F6 again. Editing during live playback stops the old run. |
| Probe moves desktop pointer but not game cursor | The selected game input mode may not accept that movement. Test a supported game cursor setting or relative calibration; desktop movement is not evidence of Steam compatibility. |
| Relative playback drifts | Reset the game cursor to the configured anchor before each run. Recheck sensitivity, acceleration, counts, and inversion; avoid physical mouse movement during playback. Stop if clamping or drift invalidates calibration. |
| Cursor is shifted after a display/window change | Recapture the window and recalibrate physical-pixel bounds at the final resolution, monitor, and scale. |
| Window capture fails | Click Capture foreground window, then switch to Rhythia within three seconds. The tool itself is not a valid target. |
| Live playback refuses a map | Read the error. The full field must fit inside the captured window; every note must lie inside `[-0.5, 2.5]`. Unsupported formats/custom marker schemas are rejected. |
| Hotkey registration error | Choose unused bindings or close the conflicting program. F9 and Ctrl+Alt+F9/F10 are reserved; F12 cannot be used. Live playback and timing learning require working hotkeys. |
| It stops after Alt+Tab, window movement, or pausing | Expected. Restart the song, recapture/recalibrate if needed, restore the relative anchor, and start fresh with F6. |
| Notes are consistently early | Click Learn timing… and mark the first note's intended hit time with F9. Restart the song, click Ready to play, and use F6 at the same start reference. Zero delay does not account for the game's unknown lead-in. Smooth motion's default arrival lead is only 12 ms at 1×. |
| Learning gives the wrong delay | Mark when the first note should be hit, not when it appears. Match map and speed, start F6 at a repeatable moment, and repeat learning. Do not continue the partly played learning run; restart the song. |
| Cursor jumps to the first note | Expected: the physical starting cursor position is unknown. The first endpoint is sent near its deadline; later notes use smooth paths when enabled. Relative mode still needs the configured starting anchor. |
| Timing is wrong or a lateness error appears | Stop and restart from the chosen song origin. Check start delay/offset/speed; the tool does not detect audio or pause the game. Learning addresses repeatable start alignment, not scheduling delays. Address those delays before increasing the cutoff. |
| Preview is silent despite embedded audio | Expected. Rhythia plays the song; the tool does not decode or play map audio. |
| Map checksum/trailing-bytes warning | All-zero marker SHA-1 means integrity is unverified; footer bytes outside declared blocks are ignored. A nonzero checksum mismatch is rejected. |
| Settings cannot be saved | Check permissions or choose a writable file with `--settings`; administrator access is not needed. |
| Build cannot download packages | Fix the reported network/TLS error, then retry. Preserve certificate verification. |
| Executable exits unexpectedly | Launch the source in Command Prompt to obtain diagnostics; record the error and Python/Windows versions. |
