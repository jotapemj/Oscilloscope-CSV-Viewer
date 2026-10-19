# Changelog

All notable changes to this project.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/) from
2.0.0 on.

> **Versions up to 1.30** were a single-file script
> (`scope_v<MM>_<mm>.py`); the *Filename reference* lines and the
> appendix at the bottom refer to those snapshots.
>
> **Versions ≤ 1.14** are reconstructed from the historical source
> archive (file names + function signature diffs between consecutive
> snapshots) since no contemporary changelog was kept. Approximate.

---

## [2.0.0] — 2026-10-19 — Modular package, tests and release builds

The 1.30 single-file script (~16 000 lines) is split into the `ocv`
package. Behaviour is kept; the bugs found while moving the code are
fixed in separate commits, each with a test that reproduces it first.

### Changed
- **Package layout.** `ocv/core/` (CSV import, measurements, MATH
  evaluator, DSP, SI formatting, sessions, config), `ocv/decoders/`
  (one module per protocol), `ocv/ui/` (theme, FFT, Eye and Decoder
  windows) and `ocv/ui/app/` (`OscilloscopeApp` state and layout plus
  15 mixins). `main.py` and `python -m ocv` launch the app.
- **Single version source**: `ocv.__version__`, shown in About and on
  the splash screen.
- **Session button** moved under *Open CSV*; the channel panel legend
  is replaced by tooltips on each control.
- **Measurements panel** appears with the first measurement and hides
  when the last one is removed.
- **Minimum window width** equal to the top bar width (capped to the
  virtual desktop), so its groups are never clipped.
- **Dialog button order**: primary action first (Add | Close,
  Save | Don't save | Cancel).
- **Top bar tools** are ttk buttons with a visible border; channel
  visibility uses the themed ttk checkbox.
- **Auto-fit T/div** snaps to the standard step when the exact
  `t_total / H_DIVS` is within 0.1 % of it.
- **Time tick labels** are rounded to 1/1000 of a division
  (`format_tick`).

### Added
- **Unsaved-changes mark** clears when the state returns to the saved
  one (debounced signature comparison).
- **Test suite** (`python tests/run.py [filter]`): unit tests for the
  core and decoders, golden master for decoders, and a smoke test of
  the full Tk application with measured contrast checks.
- **Build**: `ocv.spec` (PyInstaller `--onedir`, no UPX),
  `build.py` (build, `--smoke` launch test, zip) and
  `requirements-dev.txt`. The splash version label is generated from
  `__version__`.
- **CI**: publishing a GitHub release builds the executable on a
  Windows runner and attaches `OCV-vX.Y.Z-win64.zip`.

### Fixed
- CSV: brand detection from header substrings (Rigol MSO read as
  Tektronix, "rte"/"rto" as R&S); short metadata aliases matched
  inside longer words; peak-detect captures (repeated timestamps)
  asked for the time column.
- Peak-detect data downstream: FFT used half the real sample period
  (frequencies ×2), `DIFF` divided by zero, phosphor slope lost.
- SI formatting: `parse_si("1.")` returned None; ticks showed
  "1e+03 µs" instead of "1 ms"; a tick next to zero showed "26.1 ns".
- SPI without CS lost the first bit of every frame.
- CAN: truncated headers aborted the whole decode; CAN FD rewritten to
  ISO 11898-1:2015 (timing on stuffed bits, stuff bit count, fixed
  stuff bits, CRC, 29-bit headers).
- Trigger: no hysteresis, so noise produced false edges; markers and
  the event counter colours ignored the theme.
- Theme: unreadable accent/hover states, checkbox tick and Detail/Fast
  toggle in some themes; colours re-mapped twice on theme change;
  custom plot colours kept after switching theme; channel colours,
  cursor lines, trigger markers, Eye and FFT readouts unreadable on
  Light.
- Window: saved position with negative coordinates was not parsed;
  windows closed on a secondary monitor reopened on the primary one;
  About and Settings collapsed in a narrow window.
- Session: loading a session with MATH channels asked to keep them and
  duplicated them.
- Annotations survived loading another CSV; exported annotations used
  the wrong vertical scale and stretched the image.
- GND labels of channels with the same 0 V overlapped, on screen and
  in exported images.
- `Ctrl+Shift+↓` zoomed out past the full record.
- About showed a stale hardcoded version.
- Plot left unused margins on the top and right.

---

## [1.30] — Keyboard shortcuts, Settings + Commands legend

Adds a layered keyboard shortcut system covering the most common
actions (channel selection / visibility, unit picker, snap-to-grid,
panning, V/div and T/div stepping, cursor toggle) and centralises
their documentation in a new modal under *Settings → Commands
legend*. The previous "Grid view" entry in the top bar is now
*Settings* with a gear icon — the underlying window remains the
existing themed Appearance + grid-colour panel, plus the new
commands-legend button at the bottom.

### Added
- **Keyboard shortcuts** registered via `bind_all` on the root,
  each routed through a new central `_hotkey_guard()` helper that
  ignores presses while a text-input widget (Entry, Spinbox, Text
  — both `tk` and `ttk` variants) holds focus. This keeps editing
  V/div, channel names, MATH expressions, trigger fields, etc.
  fully usable without stealing keys. New bindings:

  | Combo            | Action                                          | Handler                              |
  |------------------|-------------------------------------------------|--------------------------------------|
  | `C`              | Toggle cursors                                  | `_hotkey_toggle_cursors`             |
  | `U`              | Open unit picker for the active channel         | `_hotkey_open_unit_picker`           |
  | `G`              | Snap active channel ground to nearest grid line | `_hotkey_snap_gnd`                   |
  | `0`              | Center active channel (`v_offset = 0`)          | `_hotkey_center_channel`             |
  | `1` – `9`        | Set active channel by index (existed)           | `_on_active_channel_hotkey`          |
  | `Ctrl+1` – `Ctrl+9` | Toggle channel visibility                    | `_hotkey_toggle_channel_visibility`  |
  | `← / →`          | Pan T offset (±0.1 div per press)               | `_hotkey_pan_h`                      |
  | `↑ / ↓`          | Pan active channel V offset (±0.1 div)          | `_hotkey_pan_v`                      |
  | `Shift+↑ / ↓`    | Step V/div discretely (Fine: ±0.1 step)         | `_hotkey_step_v_div`                 |
  | `Ctrl+Shift+↑ / ↓` | Step T/div discretely (next `TIME_DIV_VALUES`) | `_hotkey_step_t_div`                 |
  | `Ctrl+← / →`     | Move cursor X1 (time, vertical left) ±0.05 div  | `_hotkey_move_cursor("x", 0, ±1)`    |
  | `Ctrl+↑ / ↓`     | Move cursor Y1 (voltage, horizontal lower)      | `_hotkey_move_cursor("y", 0, ±1)`    |
  | `Ctrl+Alt+← / →` | Move cursor X2 (vertical right)                 | `_hotkey_move_cursor("x", 1, ±1)`    |
  | `Ctrl+Alt+↑ / ↓` | Move cursor Y2 (horizontal upper)               | `_hotkey_move_cursor("y", 1, ±1)`    |

- **`_hotkey_guard()`**: single source of truth for the focus
  check. Returns `True` for `tk.Entry`/`ttk.Entry`/`tk.Spinbox`/
  `ttk.Spinbox`/`tk.Text`; callers early-return on `True`. Old
  hotkey 1–9 refactored to use it.
- **`_open_commands_legend_dialog`**: modal that lists every
  shortcut grouped by category (Channel / View / Cursors /
  Session / Misc), monospace key column + dim description column.
  Closes with *Close*, Esc, or the X. Geometry → 560×680 via
  `_center_dialog` before `_apply_window_chrome` (anti-flash
  pattern).
- **`_hotkey_move_cursor(axis, idx, direction)`**: handler único
  para las 8 combinaciones de cursor (X1/X2/Y1/Y2 × ±). Step =
  0.05 div (más fino que el pan general). Si los cursores están
  apagados, los activa automáticamente — el usuario que pulsa el
  atajo claramente quiere usarlos.
- **"Commands legend" button** in the Settings window bottom bar
  (left side; *Close* stays on the right).

### Changed
- **Top-bar entry renamed** *Grid view* → *Settings*. The hand-
  drawn sun icon is replaced by a hand-drawn gear (8 teeth +
  centred hollow ring) rendered on the same `tk.Canvas`. Tooltip
  updated to "Settings: app theme, plot colors, phosphor mode and
  keyboard shortcuts legend". Variable names kept as `gear_btn` /
  `gear_canvas` / `draw_gear` for clarity. The window title is now
  *Settings* (was *Appearance*).
- **`_on_active_channel_hotkey`** now delegates the focus check
  to `_hotkey_guard()` (same logic, factored out).

### Fixed (within v1.30 cycle)
- **Vertical pan inverted**: pressing `↑` was lowering the trace
  (it incremented `v_offset`, and the screen mapping
  `y_screen = (v_real - v_offset)/v_div` makes the trace go
  **down** when offset goes up). Convention is now `↑` = trace
  up, so the handler subtracts the step instead of adding it.
- **Active-channel highlight lagged**: the bold-name highlight in
  the sidebar was only refreshed when the user clicked a channel
  row; firing the `1–9` hotkey moved the controls (V/div, slider,
  etc.) but the sidebar kept the old channel bold. `set_active_channel`
  now also calls `_rebuild_channels_panel()` so the highlight
  follows.
- **Gear icon keeps the old accent after a theme change**: a
  `tk.Canvas` stores items with absolute colour strings, so a
  theme switch left the gear painted in the previous accent
  until the next hover. Registered a redraw closure as
  `self._gear_redraw` when the top bar is built; `_apply_theme_immediate`
  fires it (outside the `sync_plot` branch so it runs on every
  theme change) and the gear repaints in the new accent. Also
  re-applies `bg` / `highlightbackground` to the surrounding
  `gear_btn` / `gear_canvas` / `gear_label` widgets, which the
  generic `_retheme_tk_widgets` pass would otherwise miss for
  Canvas items.
- **Settings bottom bar too tight against the separator**: the
  Close / Commands-legend buttons were almost touching the
  horizontal separator above them. Bumped the `bottom` frame's
  top `pady` from `0` to `12` in `_make_scrollable_dialog`,
  giving consistent breathing room across all dialogs that use
  the helper (Settings is the most visible one).

### Notes
- Pan / stepping handlers respect existing scope behaviour:
  - V offset writes go through `ch["v_offset"]` and sync
    `self.v_offset` + `v_slider`.
  - V/div stepping uses Fine mode (continuous 0.1 step) or
    `V_DIV_VALUES` discrete jump, matching the spinbox arrows.
  - T/div stepping reuses the up/down search through
    `TIME_DIV_VALUES` and then calls `on_time_div_change()` so
    the existing pivot logic kicks in.
- All hotkey-fired channel writes mark the session dirty through
  the existing channel `visible` traces / `set_active_channel`
  paths — no new dirty hooks.

---

## [1.29] — Per-channel unit

Display-only unit per channel. The historical assumption "every
channel is Volts" no longer holds for users measuring current with
a shunt, power with a multiplier, temperature with a thermocouple,
etc. Each channel now carries its own unit label; MATH does the
arithmetic exactly as before — combinations are never rejected on
physical grounds.

### Added
- **`CHANNEL_UNITS`** constant — 24 units laid out for a 5×5 picker
  (one cell stays empty):
  ```
  V    A    W    VA   var
  J    Ω    Hz   %    dB
  N    kgf  N·m  RPM  T
  °C   °F   K    Pa   PSI
  bar  lux  lm   —
  ```
  `—` is the explicit "dimensionless" symbol. `DEFAULT_UNIT = "V"`.
- **`_NO_SI_PREFIX_UNITS`** = `{"°C","°F","dB","%","PSI","RPM","lux","lm","—"}`:
  `format_si` skips the k/M/m/µ scaling for these (no sense in
  "k°C" or "MdB"). `—` produces just the bare number without a
  trailing symbol. `%` keeps the legacy 2-decimal format.
- **`channel["unit"]`** field on every channel dict (real and MATH).
  Initialised to `DEFAULT_UNIT` on CSV load / append / MATH create.
- **Sidebar unit-box** per channel: bordered `tk.Label` showing the
  current unit symbol, placed between the swatch/name and the `⟲`
  snap-to-grid button. Hover accents the border; click opens the
  picker.
- **`_open_unit_picker_dialog(ch_idx)`** — modal 5×5 grid of
  themed cells. Current unit shown sunken/accent; other cells get a
  hover bg. Pending selection only applies on OK; Cancel/Esc keeps
  the previous unit. Picker uses the same anti-flash pattern as the
  rest (`_center_dialog` before `_apply_window_chrome` /
  `transient`).
- **New MATH dialog** gets a *Unit:* combobox (readonly) next to
  *Name:*, defaulting to `V`. `_create_math_channel(...)` now takes
  a `unit=DEFAULT_UNIT` kwarg.
- **Helpers**: `_ch_unit(idx)`, `_active_unit()` (with safe
  fallback to `DEFAULT_UNIT` so sessions saved before v1.29 keep
  working), `_update_v_div_display()`.

### Changed
- **V/div spinbox** display and parsing use the active channel's
  unit (`format_si` for show, `parse_si(text, active_unit)` for
  edit). The Fine-mode list and the discrete `V_DIV_VALUES` list
  are formatted with the active unit too — switching the active
  channel re-renders the spinbox in the new symbol.
- **Y-tick labels** (both real-value-aligned and screen-aligned
  modes) and the GND `0V` line label render with the active
  channel's unit.
- **Cursors**: vertical Y readout (`V1: … V2: … ΔV: …`) uses the
  active channel's unit; per-channel cursor labels (when "show
  all" is on) use each channel's own unit, so a single capture
  with V + A traces shows correctly-labelled values side by side.
- **Export figure** (`_build_export_figure`): the legend
  `chN: 5 V/div` line picks each channel's unit; the `ΔV` cursor
  annotation uses the active channel's unit; the Y-tick labels
  match the channel selected for Y scale.
- **Measurements** (`compute_measurements`): post-processes the
  unit returned by `ALL_MEASUREMENTS[name]` — base `"V"` becomes
  the channel unit, `"V/s"` becomes `<unit>/s`. Structural units
  (s, Hz, %, °) are untouched.
- **FFT** THD copy header and amplitude column use the source
  channel's unit (e.g. *"Arms"* for a current channel). **Eye**
  diagram metrics (`Eye height`, `Vₕᵢ`, `Vₗₒ`) render with the
  source channel's unit.
- **Structure fingerprint** (`_structure_fingerprint`) now includes
  each channel's unit. Switching units forces a full redraw so
  ticks/labels rebuild — without it a fast-path update would keep
  the old labels stale.

### Persistence
- **Sessions (`.ocv`)**: `_build_session_dict` includes
  `"unit": ch.get("unit", DEFAULT_UNIT)` in every channel entry
  (real and MATH). `_apply_session` reads `unit` back with a
  `DEFAULT_UNIT` fallback for sessions from earlier versions, and
  validates it against `CHANNEL_UNITS` before assigning (so a
  corrupted/foreign value can't break the picker). MATH channels
  recreated from session use their saved unit through the new
  `unit=` parameter of `_create_math_channel`.
- **`SESSION_VERSION` not bumped**: the addition is purely additive
  and reads gracefully from older payloads, so a v2 session opens
  fine in v1.29 and a v1.29 session opens fine in older code
  (which ignores the unknown key).

### Notes
- MATH is unaffected by units — arithmetic over channels with
  different symbols (`V * A → W`) is allowed and the result takes
  whatever unit the user chose in the MATH dialog. The viewer does
  not validate dimensional consistency.

### Fixed (within v1.29 cycle)
- **App theme dirties the session**: changing the app theme from
  Appearance was marking the session dirty and being serialised as
  `"app_theme"` in `.ocv`. Theme is a personal preference, not
  project state. Removed `_mark_dirty()` from `_apply_theme_fade`,
  dropped `app_theme` from `_build_session_dict`, and stopped
  reading `app_theme` from `_apply_session`. `user_config.json`
  remains the single source of truth for the active theme.
- **Grid theme dirties the session**: same problem for plot grid
  presets and custom colour pickers (`apply_preset` /
  `pick_color`). Removed `_mark_dirty()` from both. Dropped
  `grid_theme` from `_build_session_dict` and `_apply_session`.
  Personalised plot colours still persist via `save_user_config`.
  Old `.ocv` files carrying these keys are ignored silently.
- **Custom-colour swatch shows residual colour after theme fade**:
  switching theme (e.g. High Contrast → Dark) left the
  Appearance → Custom background swatch with the old colour until
  the user re-clicked the same theme. Cause: `_apply_theme_fade`
  is asynchronous (Tk `after` chain), but `choose_theme` was
  calling `refresh_swatches()` inline immediately after
  `apply_theme_runtime` — before `grid_theme` had been updated
  inside the deferred `_apply_theme_immediate`. Fix: register
  `refresh_swatches` as `self._appearance_swatch_refresh` when
  the Appearance window opens; `_apply_theme_immediate` calls it
  right after updating `grid_theme`. Cleared on window close
  (WM_DELETE_WINDOW and Esc) so the closure doesn't outlive its
  widgets.
- **Decoder window cut off at the bottom**: the `Protocol decoder`
  window's `_build_ui()` ran `self.win.geometry("780x720")` after
  the `__init__` had already centred it with
  `_center_dialog(700, 960)`. The inner geometry call overrode the
  centred one, clipping the frames table header below the visible
  area. Removed the stray `geometry()`. Also added a defensive
  clamp inside `_center_dialog` so a requested size never exceeds
  96 %×92 % of the screen (covers monitors smaller than the
  requested height, e.g. 1366×768).
- **Sidebar widens unnecessarily**: the channel-row legend was a
  single long line ("click swatch → color | dbl-click name →
  rename | unit box → change unit | ⟲ snap to grid | ✕ remove
  channel") that forced the whole sidebar to expand. Rewritten as
  a more compact `swatch = color · dbl-click = rename · box =
  unit · ⟲ = snap · ✕ = delete` with `wraplength=240` so it
  flows onto multiple lines when the column is narrow.
- **Per-channel button order rearranged**: was `[✕] [unit] [⟲]`
  (delete on the left, snap on the right). User-preferred order
  is `[unit] [⟲] [✕]` (settings → action → destructive). Pack
  order in `_rebuild_channels_panel` reordered accordingly
  (rightmost packed first when using `side=tk.RIGHT`). Legend
  updated.

---

## [1.28] — Numeric hotkeys + copy plot to clipboard

Small ergonomics release. Adds keyboard shortcuts to switch the
active channel, and right-click "Copy image to clipboard" on the
plot. Both target the workflow of "grab a snapshot fast and paste
it in a note / chat".

### Added
- **Hotkeys `1`..`9`** select the active channel by index
  (`1` → channel 0, …, `9` → channel 8). Channels 10–12 remain
  mouse-only; no obvious extra digit available and the use case is
  rare. Implemented via `root.bind_all("<Key-N>", ...)` so it works
  regardless of which top-level widget has focus.
- **Focus guard**: handler `_on_active_channel_hotkey(idx)` ignores
  the keypress if focus is on a `tk.Entry`, `ttk.Entry`,
  `tk.Spinbox`, `ttk.Spinbox` or `tk.Text`. This protects digit
  input in V/div, t/div, channel rename, math expression, trigger
  level, etc.
- **Feedback**: a 900 ms toast `"Active: <ch name>"` confirms the
  switch. Skipped when the hotkey targets the channel that is
  already active (no-op short-circuit, also avoids a needless
  `update_plot` + dirty flip).
- **Copy image to clipboard** entry in the plot right-click menu
  (`_show_plot_context_menu`). New helper
  `_copy_plot_to_clipboard()` renders `self.fig` via the current
  Agg backend, converts the RGBA buffer to BGRA, builds a top-down
  DIB and pushes it to the Windows clipboard as `CF_DIB`. Toasts
  `"Copied to clipboard"` on success.

### Internal
- Hotkey helper added next to `set_active_channel`. Uses the
  existing `_show_toast` so no new visual primitives.
- Clipboard helper uses **only stdlib + numpy + matplotlib** — no
  Pillow, no pywin32, no new dependency. `ctypes` is already used
  elsewhere (AppUserModelID), so the import is local.
- DIB layout: 40-byte `BITMAPINFOHEADER` followed by 32-bit BGRA
  pixels. `biHeight` is set negative for top-down rows so we
  don't have to flip the matplotlib buffer.
- Windows handle ownership: on `SetClipboardData` success Windows
  takes the `GlobalAlloc` handle and frees it on next
  `EmptyClipboard`. The helper sets `h_mem = None` after success
  so the failure-path `GlobalFree` doesn't double-free. On any
  earlier failure the handle is explicitly `GlobalFree`'d.
- Menu entry is only added when `sys.platform.startswith("win")`,
  so non-Windows platforms don't see a button that wouldn't work.

### Tooling
- **`requirements.txt`** added at repo root. Pins the three runtime
  dependencies (`pandas`, `numpy`, `matplotlib`) with major-version
  upper bounds so a fresh venv / Python upgrade reproduces a known-
  good environment. `pyinstaller` is listed commented-out as a
  build-only extra. Stdlib modules (`tkinter`, `ctypes`, `json`,
  `csv`, `re`, `struct`, `threading`) are noted in the header for
  reference but not pip-installable.

### Fixed
- **Transparent ghost flash before dialogs**: opening Toplevels
  (Rename, Color, MATH new/delete/manager, About, Appearance,
  Trigger, XY, Save image, Add Measurements, Annotation text, Eye,
  Decoder) previously showed a small semi-transparent ghost
  window before the real dialog appeared. Two-part root cause:
  (1) `tk.Toplevel(parent)` maps the window immediately in
  Windows, and any subsequent configuration before `withdraw()`
  let the WM paint a default-sized frame; (2) more importantly,
  applying `transient(root)` to a withdrawn Toplevel before its
  geometry is fixed triggers an internal Tk/Windows
  `deiconify→transient→withdraw` cycle whose transient deiconify
  is the ghost — at default position/size, since geometry hasn't
  been applied yet. Why FFT and Decoder were unaffected from the
  start: FFT uses `_apply_chrome_maximizable` (no transient),
  Decoder happened to call `_center_dialog` before chrome. Fix:
  (a) inline `win.withdraw()` immediately after every
  `tk.Toplevel(...)` (so configure/title/bg don't show); (b)
  reorder every dialog so geometry helpers (`_center_dialog` or
  `_make_resizable_dialog`) run **before** `_apply_window_chrome`
  / `win.transient(self.root)`. The transient call then operates
  on a Toplevel that already has its final size and position, so
  the transient-cycle deiconify lands invisibly on the right
  spot. Decoder was switched from a fixed `geometry("700x600")`
  with WM-chosen position to `_center_dialog(700, 820)` to match
  the rest. Color picker (which never had geometry) got an
  explicit `_center_dialog(320, 360)` for consistency.

### Added
- **PyInstaller splash screen close**: when the .exe is built with
  PyInstaller's `--splash splash.png`, the bootloader injects a
  runtime-only `pyi_splash` module that shows the image and keeps
  it on top until the app explicitly closes it. Added a guarded
  `pyi_splash.close()` at the end of the main block (after the
  root window has been deiconified, maximized and its icon re-
  applied) so the splash hands off cleanly to the main window
  with no flicker through the desktop in between.
  `import pyi_splash` is wrapped in try/except — in dev runs from
  source the module doesn't exist and the import fails silently,
  so no behavioural change outside the packaged build.

### Changed
- **Decoder window taller** (600 → 820 px). The decoded-frames
  table at the bottom of the window was being cut off on the
  initial size and required manual resize to read. 820 px shows
  the table fully on common 1080p layouts.
- **Spurious `AttributeError: ... has no attribute 'ax'` at startup**:
  during `__init__`, tk.Var traces on V/div / sliders / etc. could
  fire `update_plot` (→ `_full_redraw_plot` → `configure_grid` →
  `self.ax.clear()`) before `_build_ui` had created `self.ax`. The
  error was caught by Tk and printed to the console without
  affecting runtime (the real first redraw happened later at the
  end of `_build_ui`), but it polluted the log. Added an early
  guard `if not hasattr(self, "ax"): return` at the top of
  `update_plot`. No behavioural change.
- **Re-saving a loaded session produced an empty `.ocv`**: opening a
  `.ocv`, then saving it again (Ctrl+S or Save As) wrote a file with
  `csv_content=""`, so re-opening it failed with "session does not
  contain embedded CSV data". Root cause: `load_session` extracts
  the embedded CSV to a temp file, loads it through the normal CSV
  path, then deletes the temp and sets `current_filepath = None`
  (so the UI shows the embedded CSV name, not the system temp path).
  The next `_build_session_dict` had no source to read from. Fix:
  cache the embedded CSV text in `self._embedded_csv_content` after
  `load_session`; `_build_session_dict` falls back to that cache
  when `current_filepath` is None or unreadable. The cache is
  invalidated whenever a real CSV is loaded from disk
  (`_load_csv_from_path` and `_load_csv_replace`), so opening a new
  CSV on top of a previously loaded session still re-embeds the
  fresh CSV correctly.
- **First launch window size**: opening the app for the very first
  time (no `config.json` yet) used to leave the main window at the
  natural requested size of its contents — visibly tiny, forcing
  the user to maximize manually. `_position_main_window` now treats
  the absence of `main_window_geom` as "first run" and returns
  `zoomed = True` by default. Subsequent launches still honour the
  user's last choice (`main_window_zoomed` from `config.json`),
  so explicitly restoring a non-maximized window keeps working.

### Notes
- `set_active_channel` already calls `_mark_dirty()`, so the hotkey
  marks the session dirty just like clicking the channel selector.
  Consistent with prior behaviour.
- Clipboard copy reflects the **current on-screen render** at
  whatever theme / phosphor / zoom is active. It does not go
  through `_build_export_figure` (no white-paper recolouring,
  no cursors / measurements options dialog). Saving to file via
  "Save image as..." still uses the export figure for that.
- Output is a true DIB, so paste works in Paint, Word, Slack,
  Discord, Greenshot, Photoshop, etc. Apps that only accept
  `CF_BITMAP` would need a separate format added; not bothering
  unless that comes up.

### Filename
- `scope_v01_28.py`

---

## [1.27] — Phosphor intensity grading (dV/dt style)

Adds an optional CRT-style intensity grading to the trace render:
segments with high |dV/dt| fade, flat segments stay bright. Toggle
and intensity slider live in *Appearance*, both persisted across
launches.

### Added
- **Phosphor mode** on the main time-domain plot. When enabled, each
  channel renders as a `LineCollection` instead of a `Line2D`, with
  per-segment alpha computed as
  `alpha = lerp(base · (1 − intensity), base, 1 − |dV/dt|/max)`.
  Intensity = 0 collapses the lerp to `alpha = base` (no effect, the
  toggle is a no-op). Intensity = 1 drives `alpha_min = 0`, so the
  fastest edges fade to invisible.
- **State**: `self.phosphor_enabled: BooleanVar`,
  `self.phosphor_intensity: DoubleVar (0..1, default 0.6)`. Both
  load from `user_config.json` on launch (`phosphor_enabled`,
  `phosphor_intensity`).
- **Helpers**: `_build_phosphor_collection(t, y, base_color,
  base_alpha, linewidth, intensity)` and `_hex_to_rgb01(hex_color)`.
  The collection helper handles degenerate inputs gracefully
  (`n<2` → empty collection; all-flat → uniform alpha; `dt=0` →
  protected division).
- **Appearance UI**: new "Phosphor (intensity grading)" section
  with an enable checkbox and a 0–100 % intensity slider (tk.Scale
  with the dialog's themed colour set). Tooltips explain the effect
  and the slider extremes.
- **Persistence**: both vars are `trace_add`-ed to a debounced
  callback (250 ms after-id) that writes through `save_user_config`
  and triggers a redraw. The debounce prevents one config write
  per slider tick while dragging.

### Changed
- **`update_plot`**: phosphor mode now goes through the same
  fingerprint-based dispatcher as everything else. The fingerprint
  includes `phosphor_enabled` so toggling forces a full rebuild
  (Line2D ↔ LineCollection), but slider drags / pan / cursor moves
  with phosphor on stay on the fast path. This is the key
  performance fix — on a busy CSV the previous full-redraw-every-
  frame approach made the app borderline unusable when phosphor
  was on; with the fast path it stays interactive.
- **`_fast_update_plot`**: now branches on `hasattr(line,
  "set_segments")` (= LineCollection) vs Line2D. The LineCollection
  branch recomputes segments + per-segment RGBA via
  `_compute_phosphor_rgba` and updates the existing artist with
  `set_segments(...) + set_color(...)` instead of rebuilding from
  scratch.
- **`_full_redraw_plot`**: the per-channel render branches on
  `phosphor_enabled`. The else-branch is the original
  `ax.plot(...)` call. The phosphor branch builds the collection
  and calls `self.ax.add_collection(line)`. The `_channel_lines`
  cache stores whichever artist was created; the fast path picks
  the right update method per artist.
- **Phosphor normalisation tightened**: previous version used the
  raw `max(|dV/dt|)` as the divisor. A single sharp edge dominated
  the scale and the rest of the trace ended up with `norm ≈ 0`
  (visually no effect). New model uses the 95th percentile as the
  "max" (`_PHOSPHOR_PERCENTILE = 95`), and applies a gamma curve
  with `gamma = 0.5` (`_PHOSPHOR_GAMMA`) to the normalised slope
  so mid-range slopes also fade visibly, not just outliers.
- **Appearance window**: preferred height bumped from 560 → 680 px
  to fit the new section without scrolling on default open. The
  window remains scrollable, so this is a comfort tweak rather
  than a requirement.

### Internal
- **`_compute_phosphor_rgba(t, y, base_color, base_alpha,
  intensity)`** split out from `_build_phosphor_collection`.
  Returns the `(segments, rgba)` tuple so both the full-redraw path
  (constructing a fresh LineCollection) and the fast path
  (refreshing an existing one) share the same maths.
- **Hard cap on per-channel segments in phosphor mode**:
  `_PHOSPHOR_MAX_SEGMENTS = 1500` (tuned against real usage; an
  initial 2000 was still noticeably slower than the non-phosphor
  path). With LineCollection each segment is a draw call; on a
  50k+ samples-per-channel CSV the no-cap version was effectively
  unusable (one slider tick = seconds of freeze). Both
  `_full_redraw_plot` and `_fast_update_plot` pre-decimate with
  `minmax_decimate(tp, yp, cap // 2)` when the visible window
  exceeds the cap. minmax_decimate preserves peaks (it doesn't
  average), so the alpha-by-slope effect on edges survives the
  downsampling.
- **Split phosphor trace handlers**: enable toggle is immediate
  (invalidates fingerprint, triggers full rebuild); intensity slider
  is debounced 80 ms (slider drags fire writes per pixel, the
  debounce coalesces them into one update without losing the live
  feel).

### Notes
- Phosphor settings are *not* part of the session document — they
  do not mark the session dirty and they are not saved to `.ocv`.
  They are user preferences, like `app_theme` or `grid_theme`.
- CPU cost: each phosphor redraw is ~O(n) for slope computation
  plus the LineCollection construction. On the fast-decimated
  render mode the cost is bounded to ~`n_pixels` segments per
  channel, so it stays usable.
- The toggle invalidates `_last_structure_fingerprint` on every
  phosphor-var write to guarantee that toggling back off goes
  through a full rebuild and doesn't try to reuse a stale cache
  populated by a `LineCollection` artist.

**Filename**: `scope_v01_27.py`

---

## [1.26] — 12 channels, larger MATH and measurement pools

Capacity bump release. Bigger captures with more probes per shot
were starting to butt up against the hard-coded slot limits.

### Changed
- **`MAX_TOTAL_CHANNELS`: 8 → 12.** Indirect change: the constant is
  derived from `len(CHANNEL_COLORS)`, so the new value follows
  automatically once four new colour entries are added. Tooltips and
  error messages already used the constant by interpolation
  (`f"... ({self.MAX_TOTAL_CHANNELS}) ..."`), so no string changes
  needed.
- **`MAX_MATH`: 4 → 8.** Per user request — keeps room for more
  derived signals (sum/diff/integrals/derivatives) on the larger
  channel count.
- **`MAX_MEASUREMENTS`: 10 → 16.** Per user request — covers the
  common case of "one measurement per channel" on 12 channels with
  some slack left over for combinations.

### Added
- **CH9–CH12 colours.** Picked for distinct hues from the original
  Tek-style 8 and from each other, with good contrast on dark
  backgrounds:
  - CH9  — `#b388ff` (violet)
  - CH10 — `#76ff03` (lime)
  - CH11 — `#ff4081` (pink)
  - CH12 — `#00bfa5` (teal)

### Notes
- No structural change to the channels panel: rows are built
  dynamically with `for i, ch in enumerate(self.channels)`. The
  sidebar Frame does not scroll, so on very short windows the lower
  rows could overflow — but this was already true with 8 channels
  in tight layouts, and the user has not asked for a scroll yet.
  Worth a follow-up if it becomes a problem.
- Session payload format unchanged (`SESSION_VERSION = 2`). A v1.25
  session with up to 8 channels loads as-is; a v1.26 session with
  9–12 channels saved would error on a v1.25 build at apply time
  (the loader would still try to map by index and silently drop
  channels past 8). Not formalised as a breaking change — the
  format itself is forward-compatible.

**Filename**: `scope_v01_26.py`

---

## [1.25] — Sessions overhaul: Untitled, Save, dirty state

The session subsystem stops being a one-shot "Save current state to a
file" operation and starts behaving like a document editor: every
session has an identity (Untitled or a path), a clean/dirty flag, and
the usual save / save-as / prompt-on-discard flow.

### Added
- **Untitled session model.** On launch the app is in a session
  called *Untitled* with no backing path. The title bar reads
  `Untitled — Oscilloscope CSV Viewer`. As soon as the user modifies
  anything that is part of the session state, a leading `*` is added
  (`*Untitled — Oscilloscope CSV Viewer`). The mark disappears after
  a successful save.
- **`save_session_as` (Ctrl+Shift+S).** Always prompts for a path.
  On success, updates `session_path` so subsequent `save_session`
  calls go directly to that file. Initial dialog directory uses, in
  this order: the directory of the current session, the directory of
  the loaded CSV, or the OS default.
- **`_write_session_to(path)` helper.** Shared by Save and Save As.
  Writes the JSON payload, updates `session_path`, clears dirty,
  refreshes the title. Returns True/False so the caller can react.
- **`_prompt_save_if_dirty(context)`** — Save/Don't save/Cancel
  dialog used by `on_close`, `load_session` and `_load_csv_replace`.
  Returns True if the caller can proceed (saved OK or discard),
  False if the user cancelled or the save failed.
- **Themed save-prompt dialog** (`_ask_save_changes_dialog`).
  Replaces the native `messagebox.askyesnocancel` so the prompt
  follows the app theme (same chrome as Load CSV / Trigger / XY:
  COLOR_BG_ALT header with COLOR_ACCENT title, body text in
  COLOR_TEXT/COLOR_TEXT_DIM, ttk.Button row with `Accent.TButton`
  on Save). Keyboard: Enter = Save, Escape = Cancel, WM close =
  Cancel.
- **Title bar shows the session name.** Format:
  `[*]<name> — Oscilloscope CSV Viewer`. `<name>` is the basename
  without extension of the current `session_path`, or `Untitled`
  if none.
- **Dirty hooks** on the relevant tk.Vars (t_div, v_div, t_offset,
  v_offset, fine_mode, render_mode, cursors_enabled, cursor_mode,
  cursor_show_all) via `trace_add("write", _dirty_trace)`. A single
  `_mark_dirty()` call costs nothing past the first flip per save
  cycle (idempotent guard), so dragging a slider doesn't re-run
  `_update_title()` on every tick.
- **Dirty hooks on programmatic state changes** that don't go
  through a tk.Var: channel rename, color picker, channel delete,
  MATH create, annotation undo/redo and push, measurement
  add/remove, active channel switch, trigger config (via
  `_refresh_trigger`), XY config callbacks, grid_theme preset and
  pick_color, cursor drag, theme change. Channel `visible`
  BooleanVar traces now also mark dirty (previously only redrew).

### Changed
- **`save_session` (Ctrl+S)** now writes directly to the current
  `session_path` if one exists, no dialog. If the session is still
  Untitled it delegates to `save_session_as`. Returns True/False.
- **Ctrl+Shift+S binding** added (`<Control-S>`, which Tk interprets
  as Ctrl+Shift+s). Previously this fired the same handler as
  Ctrl+S.
- **Session menu** entries updated:
  `Save  Ctrl+S` / `Save as...  Ctrl+Shift+S` / separator /
  `Load session...  Ctrl+O`.
- **`load_session`** now sets `session_path = path` on successful
  load and clears dirty (the loaded session is the new clean
  baseline). The `_apply_session` phase runs under
  `_suppress_dirty = True` so the tk.Var writes it does don't
  immediately re-flag the loaded session as dirty.
- **`_finalize_csv_load`** (replace path) clears the previous
  session: after `_load_channels`, `session_path` is reset to None
  and dirty to False (per user request — a freshly loaded CSV is
  treated as a clean Untitled session, not as dirty). The
  `_load_channels` call itself is wrapped in `_suppress_dirty` for
  the same reason as load_session.
- **`on_close`** now goes through `_prompt_save_if_dirty("closing")`.
  Cancel aborts the close.
- **`load_session` and `_load_csv_replace`** prompt-to-save if the
  current session is dirty before discarding it. Cancel aborts the
  whole operation.

### Internal
- New state on `OscilloscopeApp`:
  `session_path: str|None`, `session_dirty: bool`,
  `_suppress_dirty: bool`.
- New helpers: `_session_display_name`, `_update_title`,
  `_mark_dirty`, `_clear_dirty`, `_prompt_save_if_dirty`,
  `_write_session_to`, `save_session_as`.
- `__init__` ends with an explicit `_update_title()` so the initial
  `Untitled` label replaces the static `self.root.title(...)` set at
  the very top of the constructor.

### Notes
- `_mark_dirty()` short-circuits when `self.session_dirty` is already
  True, so high-frequency event sources (slider drags, mouse drags,
  cursor moves) only re-trigger a single title refresh per dirty
  cycle. Measured cost on the slider drag path is negligible.
- The mark is purely UI-side. The `.ocv` file format is unchanged
  from v1.23 (`SESSION_VERSION = 2`); a v1.25 session loaded in v1.24
  would just ignore the new in-memory flags.

**Filename**: `scope_v01_25.py`

---

## [1.24] — Bug round: Eye, Fine V/div, Annot

Bug-fix release. Four issues reported by the user — three are clean
fixes, one is a small architectural change to how annotations encode
their vertical coordinate.

### Fixed
- **Eye diagram window did not centre on the main window.** Same root
  cause as the FFT window in v1.21: it used a hard-coded
  `geometry("1100x720")`. Now routes through `_center_dialog` so it
  follows the main window across monitors.
- **Fine V/div mode collapsed values > 50 V on the first arrow click.**
  `_step_v_div` clamped the target to `min(target, 50.0)` when Fine
  was on — so being at 100 V/div and pressing ↑ jumped to 50 V/div
  immediately. The ceiling was a cosmetic artefact (the Fine spinbox
  list stops at 50) and had no technical justification. Removed.
  Fine now only changes the *step size*, not the admissible range.
- **Y-axis tick labels rounded to integers in Fine mode for values
  > 100 V/div.** The label formatter used `sig_figs=3`, which turns
  100.1 into "100" — so the decimal step from Fine was visible in
  the scale value but not in the tick labels. Raised to `sig_figs=4`
  in both `configure_grid` (full redraw) and `_fast_update_plot`,
  so Fine steps actually show up on the axis. Thanks to `{:.4g}`,
  integer values still render without trailing zeros ("1 V", not
  "1.000 V").
- **V/div zoom now pivots from the active channel's GND in *grid
  divisions*, not in pixels.** Symptom: with a non-zero V offset,
  changing V/div left the trace at the same pixel position but the
  "0 V" line of the channel slid relative to the grid lines — e.g.
  on 5 V/div the channel zero sat 2 divs below centre, on 6 V/div
  it sat ≈1.67 divs below centre. Real scopes (Tek/Keysight/R&S)
  keep the GND reference fixed *to its grid line*, scaling
  amplitude around it.

  Model: position of the channel zero in screen pixels is
  `-v_offset` (the v_div terms cancel for the active channel).
  Position in divisions is `-v_offset / v_div`. To keep the latter
  constant when V/div changes we must scale v_offset
  proportionally:
  `v_offset_new = v_offset_old · (v_div_new / v_div_old)`.

  Implementation: at the top of `on_v_div_change`, before
  reconfiguring the slider or persisting, read `ch_active["v_div"]`
  (still the old value at that point — `_persist_active_channel_vars`
  updates it later) and scale `self.v_offset` proportionally.
  Tk callbacks gated by `_syncing_active=True`. The original
  range clamp still applies and prevails when the scaled offset
  would land outside ±4 divs of the new scale.

  Also keeps the previous safeguard from earlier in this release:
  `_syncing_active=True` wraps the slider reconfiguration so a
  thumb auto-clamp does not fire `on_v_slider_move` and rewrite
  `v_offset` behind our back.
- **Annotations toolbar had a duplicate ✕ button.** The toplevel
  already has the OS close button (X in the title bar) wired to the
  same handler. The extra button only added noise. Removed; `Escape`
  and the title bar X remain.
- **Annotations disappeared / reappeared as you switched between
  channels with different `v_div`.** Root cause: annotation
  coordinates were stored as `(t_real, y_screen)`, i.e. the second
  element was matplotlib's axes coordinate at the moment of creation
  — but `y_screen` is tied to the *active* channel's `v_div`
  (everything is rescaled by `v_div_active`). When the active
  channel changed, the axes Y range changed too, and the stored
  `y_screen` no longer mapped to the same point on screen — often
  ending up clipped outside the viewport. The visible pattern was an
  apparent toggle between "two annotation sets" because channels
  alternated between two `v_div` values.

  Fix: annotations now store `(t_real, v_real)` plus a `ch_idx`
  identifying the channel they were created on. On draw, the stored
  `v_real` is transformed to the current `y_screen` using that
  channel's scale, so the annotation stays attached to the trace it
  was drawn on regardless of which channel is active.

  Helpers `_annot_v_real_from_screen` / `_annot_screen_from_v_real`
  added. `_data_point_from_event` unchanged in signature but its
  docstring now flags the y_screen-vs-v_real distinction.

  Touched: `_on_annotation_press`, `_on_annotation_move`,
  `_prompt_text_annotation` (new `ch_idx` parameter), `_erase_at`
  (compares in `y_screen` after transforming each annotation),
  `_draw_annotations` (transforms `v_real` → `y_screen` per
  annotation), `_build_session_dict` / `_apply_session` (persist
  and reload `ch_idx`).

  **Backward compatibility**: annotations from sessions older than
  v1.24 (no `ch_idx` field) are treated as legacy: their stored Y
  is taken as `y_screen` and drawn unchanged. Such annotations will
  still exhibit the original bug when channels with different
  `v_div` are alternated — they need to be re-created in v1.24+ to
  benefit from the fix.
- **Y-axis "0 V" rendered as `100 fV` / `1 fV` in Fine mode after
  V/div changes.** Side effect of the proportional GND-pivot
  rescaling above: `v_offset_new = v_offset_old · ratio` accumulates
  floating-point residue, so the tick that should land at exactly
  0 V ended up at ~1e-15 V — well below any real measurement but
  enough for `format_si` to print it as "100 fV". Added a snap-to-
  zero step before formatting: values whose magnitude is below
  `v_div · 1e-9` are coerced to `0.0` prior to `format_si`. Applied
  in both `configure_grid` (full redraw) and `_fast_update_plot`.
  The threshold scales with V/div, so it stays a billionth of a
  division regardless of scale — never swallows a real reading.

**Filename**: `scope_v01_24.py`

---

## [1.23] — Session format: `.ocv` with embedded CSV

Session files become self-contained: the CSV payload travels inside
the `.ocv`, so a session can be shared (email, USB, network drive)
without having to also send the original CSV. Breaking format change.

### Changed
- **Session extension `.oscsession` → `.ocv`** (Oscilloscope CSV
  Viewer). The Save / Load dialogs only show `*.ocv`; the legacy
  extension is no longer offered.
- **Session payload now embeds the CSV** under a new `csv_content`
  key (UTF-8 text). Removed `csv_path` — sessions no longer depend
  on the CSV staying at a specific filesystem location.
- App key in the payload: `"OscilloscopeTraceViewer"` →
  `"OscilloscopeCSVViewer"`.
- `SESSION_VERSION` bumped: **1 → 2**.

### Added
- Load path writes the embedded CSV to a `tempfile.NamedTemporaryFile`
  with the original extension, reuses the existing `_load_csv_from_path`
  pipeline, then deletes the temp on the post-load callback. After
  loading, `self.current_filename` is restored to the original CSV
  name (the UI shows the original file, not the OS temp path).

### Removed
- The whole "CSV not found → ask the user to locate it" fallback
  branch of `load_session` — irrelevant with embedded content.

### Breaking
- **Legacy `.oscsession` files (SESSION_VERSION = 1) no longer load.**
  Clean break per design: opening a v1 session shows a dedicated
  error explaining that sessions must be re-created with v1.23+.
  There is no migration path.

### Notes
- Session file size grows by approximately the CSV size (a 5 MB CSV
  becomes a ~5 MB `.ocv`, plus a few KB of metadata). No compression
  in this release; if it becomes a problem we'll switch to gzip or
  a ZIP container in a follow-up release.

**Filename**: `scope_v01_23.py`

---

## [1.22] — Theme fade rework (overlay alpha)

Single-purpose release. Replaces the per-widget theme fade with an
overlay-based transition whose cost is independent of how many
widgets are on screen, so modest PCs no longer struggle during a
theme switch.

### Changed
- **Theme fade now uses an alpha overlay instead of per-widget
  interpolation.** A frameless `Toplevel` of the size of the main
  window, coloured with the new theme's `BG`, fades in over its own
  `-alpha` attribute (~80 ms), the theme is applied in one shot
  while the overlay is opaque (invisible flash), and the overlay
  fades back out (~80 ms). Total ~160 ms regardless of widget count.
  The previous implementation interpolated colours across hundreds
  of `ttk.Style` configurations and tk widgets every frame, which
  was visually nice but unusably slow on lower-spec machines.
- `_apply_theme_fade(name, sync_plot)` keeps the same signature so
  the rest of the app (`apply_theme_runtime`) doesn't change.
- Fallback to instant switch if the WM doesn't honour `-alpha`
  (degradación elegante).

### Removed
- `_apply_intermediate_theme(theme_dict)` — dead with the new fade.
- `_retheme_tk_widgets_light(widget, ...)` — dead with the new fade.
  Net ~265 lines removed.

### Notes
- The overlay covers only the main window (root). Secondary windows
  open at the time of the switch (FFT, Trigger, XY, etc.) retheme
  in one shot through `_apply_theme_immediate`. Accepted trade-off:
  building a per-Toplevel overlay would double the moving parts.
- Snapshot of v1.21 in `old/scope_v01_21.py` was taken AFTER the
  v1.22 edits started, so byte-by-byte it matches the v1.22 working
  file. Use the [1.21] changelog entry to reconstruct what the v1.21
  state actually contained.

**Filename**: `scope_v01_22.py`

---

## [1.21] — Multi-monitor fixes · rebrand · plot wheel

UX-focused release. Solves the long-standing bug of dialogs jumping
back to the primary monitor when the main window lives on a secondary
one, and adds Ctrl+wheel zoom on the plot.

### Added
- **Ctrl + mouse wheel on the plot → horizontal zoom (T/div)**. Same
  behaviour as the mouse wheel over the minimap but available without
  moving the cursor outside the grid. Up = zoom in, Down = zoom out.
  Linux Button-4/Button-5 also bound.

### Changed
- **Rebrand to "Oscilloscope CSV Viewer"**. Removed the "Offline"
  prefix in the window title, About window (and its description), and
  in the Windows AppUserModelID
  (`jotapeveloper.OfflineScopeCSVViewer` → `jotapeveloper.OscilloscopeCSVViewer`).
  Side effect on Windows: any taskbar pin will detach once after the
  upgrade since the AppID is treated as a new application.
- **FFT window now centres on the main window** instead of opening at
  a hard-coded `+0+0`-ish position. Same multi-monitor logic as the
  rest of the dialogs.
- About window description updated to reflect the full decoder list
  (UART / I²C / SPI / LIN / I²S / CAN / CAN FD / J1939 / SDIO /
  MIL-STD-1553 / ARINC 429).

### Fixed
- **Dialogs jumping to the primary monitor in multi-monitor setups**.
  Root cause: `_make_resizable_dialog` clamped the dialog position
  against `winfo_screenwidth/height`, which always returns the
  *primary* screen, so when the main window was on a secondary
  monitor every dialog was pushed back to the primary. Fixed by
  delegating positioning to `_center_dialog` (which uses absolute
  root coordinates and does not clamp against the primary screen).
  Affects: About, Appearance / Grid view, Math, Trigger, XY, Save
  image, Add channels picker, etc.
- **Tooltips appearing on the primary monitor when the cursor was on
  a secondary one**. Same bug: tooltip clamped against
  `winfo_screenwidth/height`. Replaced with
  `winfo_vrootx/y/width/height` (virtual desktop) so the tooltip
  stays on whichever monitor the cursor is currently on, even with
  monitors arranged to the left of the primary (negative coords).
- **XY mode: disabling the checkbox left the plot blank until the
  next structural change**. `_draw_xy()` calls `ax.clear()` but the
  `_channel_lines` dict still held references to the now-dead Line2D
  artists, so the fast-update path on the way back to time-domain
  would `set_data()` on artists that were no longer in the axes.
  Reset `_channel_lines = {}` and `_last_structure_fingerprint = None`
  on entry to `_draw_xy()` to force a full redraw when XY is turned off.

**Filename**: `scope_v01_21.py`

---

## [1.20] — Bigger UX pass (post-debug)

This release rolls up everything done during the "debug" phase as a
single labelled cut. Internally we iterated several behaviours that
never received a version bump — they're all collected here.

### Added

- **Theme transitions now fade smoothly (Level 2)**. Switching between
  themes (Dark, Light, Blueprint, High Contrast) interpolates the
  colours over ~180 ms (6 frames × 30 ms, ease-out) instead of
  switching abruptly. Animated each frame: window backgrounds (root,
  Toplevels), plot background, spines, gridlines, tick labels and
  axis labels, ttk.Style for the most visible widgets (TFrame,
  TLabel, TButton, TCombobox, TSpinbox, TEntry, TCheckbutton,
  TRadiobutton, TLabelframe, TSeparator) including check/radio
  indicator colours, and a lightweight tk widget pass (bg/fg of
  Frame, Label, Button, Canvas, Toplevel). Final frame: full retheme
  barrier guarantees consistency. Cancels cleanly if another theme
  is picked mid-animation.
- **Colour interpolation helpers** (`_hex_to_rgb`, `_rgb_to_hex`,
  `lerp_color`, `lerp_theme`) at module scope.
- **`_theme_protected` widget attribute**. Marks a widget as
  "don't retheme me". Applied to the per-theme preview buttons in
  *Appearance* and to the per-preset preview buttons of the grid
  theme picker — both are visual samples and must stay as their
  native colours.
- **Persistent FFT configuration**. Window type, Y scale, trace mode,
  N avg, harmonic markers (on/off + count) and cursor enabled state
  are saved on every change to `user_config.json` under the
  `fft_config` key. A **Reset configuration** button restores
  defaults. RBW, W and frequency ranges are *not* persisted.
- **Main window position persistence**. Geometry (`WxH+X+Y`) and
  maximised state remembered between sessions. First launch: centred
  on the primary screen. Validation: if the saved geometry leaves
  <80 px visible, it is discarded and re-centred.
- **Windows AppUserModelID registration** via `ctypes`. Sets the
  AppID to `jotapeveloper.OfflineScopeCSVViewer`. Decouples from
  `python.exe` in the taskbar.
- **Decoder window: double-click selects time column**.
- **Cursor "Show on all channels" checkbox**. Voltage marker (X) on
  every visible channel at the same time instant. Channel name
  prefixed in the label when multiple markers visible. Persisted.
- **Load CSV: add channels to existing data**. Dialog asks replace
  vs append. Append flow includes channel picker with live counter,
  slot limit (8 channels total), and linear interpolation onto the
  existing timebase. Out-of-range samples NaN-padded.
- **Channel deletion from the channels panel**. Every channel shows
  an ✕ button. Cascade deletion of dependent MATH channels offered
  when applicable. Removing the last channel returns to "No CSV
  loaded".
- **Dialogs now open centred** over the main window via
  `_center_dialog(win, w, h)`. Applied to load-mode picker, time
  column dialogs, add-channels picker, CSV manual fallback,
  annotation editor.

### Fixed

- **Theme preview buttons resizing on theme change**. Fixed
  `highlightthickness`; only colour changes now.
- **Plot grid not fading in Dark↔Light transitions**. `self.grid_theme`
  is now interpolated and `update_plot()` is forced per frame.
- **Window opening at random positions / tiny sizes**. Removed the
  fragile `state("normal")` trick; validation ≥400×300 before
  writing to disk.
- **Taskbar icon shown as generic Python icon**. State applied after
  `deiconify`; icon re-applied at end.
- **Add-channels mode loaded the CSV as a full replace** + **two
  time column dialogs on every CSV load**. Root cause: duplicated
  `check_done` closure running the legacy replace path. Removed.

### Changed
- `apply_theme_runtime(name, sync_plot=True)` →
  `apply_theme_runtime(name, sync_plot=True, fade=None)`. Legacy
  immediate behaviour now in `_apply_theme_immediate`.
- `_position_main_window` no longer applies `state("zoomed")`; the
  caller does it after `deiconify`.
- `_delete_math_channel` is now a thin wrapper around the new
  `_delete_channel`, which handles data + MATH and the empty state.

**Filename**: `scope_v01_20.py`

---

## [1.18] — SPI without CS · LIN versions

### Added
- **SPI without CS line**. New parameter `Idle time (µs, no CS)`
  (default 100 µs). Frames delimited by SCK gaps longer than this
  threshold. R&S-style "SPI no CS" mode.
- **LIN J2602 and "Any" versions**.
- **LIN checksum modes** `Auto` / `Classic` / `Enhanced`. Auto
  respects version + reserved-ID exceptions. Any tries both.

### Changed
- LIN frame diagram now advertises supported versions.
- SPI / LIN docstrings updated.

**Filename**: `scope_v01_18.py`

---

## [1.17] — *(skipped; no published cut)*

---

## [1.16] — Decoder diagrams (rest of protocols) · avionics decoders

### Added
- **Frame diagrams for all protocols**: I2C, SPI, LIN, I2S, CAN,
  CAN FD, J1939, SDIO. Two new helpers: `_dgm_field_row` (row of
  boxes — CAN/LIN/MIL-1553/ARINC style) and `_dgm_rail_label`
  (left-side rail labels for SCL/SDA/CLK/MOSI…).
- **MIL-STD-1553B decoder**. 1 Mbit/s Manchester II. Sync detection
  by pulse duration (~1.5 bit-times), 16 data bits + odd parity.
  CMD/STAT vs DATA differentiated by sync polarity.
- **ARINC 429 decoder**. 32-bit word avionics bus. Low (12.5 kbit/s)
  / High (100 kbit/s) / Custom rates. Decomposes Label · SDI · Data ·
  SSM · Parity. Label format octal / hex / decimal.
- New category **AVIONICS / MILITARY** in the decoder accordion.

### Notes
- MIL-STD-1553 and ARINC 429 decoders are basic: word extraction +
  parity. They do not deeply verify protocol-level fields. Adequate
  for visualisation, not certification-grade.

**Filename**: `scope_v01_16.py`

---

## [1.15] — Decoder frame diagram infrastructure

### Added
- **Frame structure panel** in the decoder window — `tk.Canvas`
  between the protocol accordion and the channel mapping. Renders a
  diagram explaining the on-the-wire format of the currently
  selected protocol. Inspired by Rohde & Schwarz scope decoder
  dialogs.
- **`draw_diagram(canvas, x, y, w, h, theme)`** classmethod on
  `ProtocolDecoder` (default returns False; subclasses override).
- **Drawing helpers**: `_dgm_box`, `_dgm_signal_step`, `_dgm_label`,
  `_dgm_arrow_h`, `_dgm_tick`. Shared primitives.
- `_refresh_diagram()` on `DecoderWindow`. Bound to `<Configure>` so
  the diagram rescales with the window.
- **UART diagram** as the proof of concept.
- `_bind_escape_to_close()` helper to bind Esc to close any dialog.
- **Horizontal cursor snap to t=0** (`_snap_horizontal_to_zero` +
  `_update_snap_t0_state`).
- **Maximisable secondary windows** via `_apply_chrome_maximizable`
  (used by FFT, which needs to be resizable beyond the chrome).

### Changed
- `_show_toast` default duration bumped from 1500 ms to 2000 ms.

**Filename**: `scope_v01_15.py`

---

## [1.14] — Time display origin

### Added
- `_t_display_origin`: configurable time-axis origin. Lets the X
  axis show times relative to a chosen reference (trigger event,
  t=0, file start) without re-sampling the data.

*Small release. ~226 lines added.*

**Filename**: `scope_v01_14.py`

---

## [1.13] — Embedded & automotive decoders

### Added
- **LIN decoder** with break, sync, PID, data, classic/enhanced
  checksum.
- **I²S decoder** (BCLK + WS + SDATA, Philips / left-justified /
  right-justified alignments).
- **CAN 2.0A/B decoder**. Logical / differential input. SOF, 11/29-bit
  ID, control, data, CRC15, ACK, EOF + bit-unstuffing.
- **CAN FD decoder**. Up to 64-byte payload, BRS / ESI flags,
  CRC17/21 by length.
- **SAE J1939 decoder**. Layer on top of CAN 29-bit. Decomposes the
  identifier into Priority / EDP / DP / PF / PS / SA. PGN derivation.
- **SDIO decoder**. CMD line + DAT0 (1-bit) / DAT0..3 (4-bit).
  Command / response packets.
- **CAN helpers** (shared between CAN, CAN FD, J1939):
  `_can_extract_logical`, `_can_binarize_invert_if_differential`,
  `_can_unstuff_bits`, `_can_crc15`, `_can_crc17`, `_can_crc21`,
  `_can_detect_bit_time`.
- **Smart time-column dialog**. `_open_time_column_dialog`: when
  auto-detection is not confident enough, show the user the
  candidate columns with stats and let them confirm + pick the unit.
  `_score_time_column`, `_guess_time_unit`, `_finalize_csv_load`
  introduced.
- `_resource_path` for PyInstaller-friendly asset loading.
- `_apply_window_chrome` for consistent secondary-window decoration.

*Major release. +2,400 lines, +6 classes.*

**Filename**: `scope_v01_13.py`

---

## [1.12] — Polish & internals refactor

Mostly a reformat / docstring / comment pass with four small
functional additions.

### Added
- **About window** (`_open_about_window`).
- **Shift+wheel pans the plot horizontally** (`_on_plot_shift_wheel`).
- UI step / fallback helpers (`_step_ui`, `_fallback_ui`) used by
  the FFT spinbox controls.

### Changed
- `format_si(value, unit)` → `format_si(value, unit, sig_figs=6)`:
  significant figures now configurable.
- Sweeping reformat / docstring / comment pass. ~12,400 lines touched
  for ~12,100 reverted-equivalent (net +300 lines). The file mostly
  stayed the same functionally; everything was re-wrapped, re-spaced
  and re-commented.

**Filename**: `scope_v01_12.py`

---

## [1.11] — Hover fix

### Fixed
- Minor hover regression introduced by the portable-mode work.

*Very small. +19 / -5 lines. No new functions.*

**Filename**: `scope_v01_11.py`

---

## [1.10] — Portable mode

### Added
- **Portable user config**. The config file now lives next to the
  executable (when frozen) or next to the script (in dev). Auto-
  detection via `_app_base_dir`.
- `_is_writable(directory)` and `check_config_writable()` to detect
  read-only installs early and warn the user.
- `_make_scrollable_dialog()` helper.

**Filename**: `scope_v01_10.py`

---

## [1.9] — Persistent user config + bug fixes

### Added
- **User config system** (`_user_config_path`, `load_user_config`,
  `save_user_config`). JSON file storing app preferences (theme,
  grid style, time-display options, etc.) that survives restarts.
- `_force_refresh_ttk_in()` helper — workaround for ttk.Treeview not
  picking up Style changes on its own.

### Fixed
- General fixes batch (small but multi-area).

**Filename**: `scope_v01_09.py`

---

## [1.8] — First protocol decoders

This is the release where the decoder subsystem appears. Goes from
3 to 10 top-level classes.

### Added
- **`ProtocolDecoder` base class** + **`DecoderWindow`** UI.
- **UART decoder** (RX + optional TX, start / N-bit data / parity /
  stop).
- **I²C decoder** (SCL + SDA, START / ADDR + R/W / ACK / DATA /
  STOP).
- **SPI decoder** (SCK + MOSI + MISO + optional CS, CPOL/CPHA
  configurable, MSB/LSB first).
- Channel-role mapping UI (assign which scope channel plays which
  role in the selected protocol).
- Frame table with `_on_row_double_click`, `_clear`, `_export_csv`.

*Big jump. +2,850 lines, +7 classes.*

**Filename**: `scope_v01_08.py`

---

## [1.4 – 1.7] — *(not preserved as standalone snapshots)*

The decoder system landed in v1.8 but its scaffolding (channel
roles, frame data structure, threshold detection helpers,
binarisation) was iterated in unversioned intermediate states
between v1.3 and v1.8.

---

## [1.3] — Data loading improvements

### Added
- Improved CSV ingestion path. ~400 lines.

*Small release. Mostly internal.*

**Filename**: `scope_v01_03.py`

---

## [1.2] — Theme system generalisation + dead-code purge

The single-dark-theme scaffolding already existed (`apply_theme`,
`_retheme_tk_widgets`, `COLOR_*` globals from v0.6). This release
generalises it to **multiple selectable themes** and removes a
large amount of code that had accumulated during the
design-2 / minimap / optimized iterations and was no longer used.

### Added
- `THEMES` dict with multiple selectable themes (foundation for
  Dark / Light / Blueprint / High Contrast that ship later).
- Cleaner separation between theme data and theme application.

### Changed
- Heavy cleanup pass. Net **−1,920 lines** (1,962 deleted, 42 added).
  Mostly dead code removal and reorganisation around the new
  `THEMES` abstraction.

**Filename**: `scope_v01_02.py`

---

## [0.6] — Performance pass v3 (the "big merge")

This is a much larger release than the name suggests. Beyond the
plot optimisations, several subsystems land here for the first
time.

### Added

- **Multi-theme infrastructure** (early form). `apply_theme()`,
  `_refresh_color_globals()`, `_retheme_tk_widgets()`,
  `_rebuild_legacy_reverse()`, `_map_legacy_color()`. The
  `COLOR_*` globals approach used by the rest of the codebase is
  introduced here. v1.2 later cleans this up and generalises it to
  four themes.
- **`apply_theme_runtime`** on the main app. Switch theme without
  restarting.
- **`Tooltip` class**. Hover tooltips used pervasively from now on.
- **Annotation undo / redo**: `_push_undo_annotation`,
  `_undo_annotation_action`, `_redo_annotation_action`.
- **Math channel cycle detection** (`_math_would_create_cycle`).
  Prevents the user from defining `M1 = M2 + 1` while
  `M2 = M1 * 2`.
- **Fast/slow plot redraw paths** — the key optimisation of this
  release:
  - `_structure_fingerprint` to detect when only data values
    changed vs the whole layout,
  - `_fast_update_plot` (data-only path),
  - `_fast_update_vertical_cursors`, `_fast_update_horizontal_cursors`,
  - `_clear_gnd_markers`, `_clear_cursor_v_markers`,
  - `_full_redraw_plot` (slow path, when structure changed),
  - `_legacy_update_plot` kept as fallback.
- **Eye Diagram window** (`EyeDiagramWindow`). Full new subsystem:
  UI build, source change handling, manual / auto UI estimation,
  crossings detection, metric computation (eye opening, jitter,
  rise/fall), redraw, save-as-image.
- **FFT trace modes**: Live / Average / Max-hold / Min-hold.
  `_update_trace_buffers`, `_reset_trace_buffers`, mode change
  handler, N-avg change handler, `_get_display_trace`.
- **FFT harmonics detection** and **THD computation**:
  `_detect_fundamental`, `_amplitude_at`, `_compute_thd`,
  `_draw_harmonics`.
- **FFT extras**: right-click context menu (`_show_context_menu`),
  save-as-image dialog, copy peak frequency / amplitude to clipboard.
- `open_eye_window` entry point on the main app.

### Changed
- Plot redraws now go through a fingerprint-based dispatcher; only
  layout-changing actions trigger the full redraw, everything else
  uses the fast path. Major perceived latency improvement.

*+2,385 / -63 lines. Two new top-level classes (Tooltip,
EyeDiagramWindow).*

**Filename**: `scope_v00_06.py`

---

## [0.5] — First performance pass

### Changed
- Optimisations to plot rendering. +685 lines, +11 functions.
  Foundation for the fast/slow split that fully lands in v0.6.

**Filename**: `scope_v00_05.py`

---

## [0.4] — Minimap

### Added
- Minimap panel (an overview strip showing the full trace with a
  movable window indicating the visible region of the main plot).

**Filename**: `scope_v00_04.py`

---

## [0.3] — Visual redesign (design 2)

### Changed
- Top bar refactor (`build_topbar`) and broader layout cleanup.
  ~349 lines added.

**Filename**: `scope_v00_03.py`

---

## [0.2] — Major rewrite + dark theme

This is the release where the app becomes recognisable. From 1,970
lines / 81 functions / 2 classes to 7,511 / 219 / 3.

### Added

- **Dark theme** (`apply_dark_theme`), `channel_color` palette.
- **Comprehensive measurements**: pulse widths (positive / negative),
  slew rates (rising / falling), duty cycles, high/low time, time
  outside level, phase via Hilbert and via zero-crossings, plus the
  inventory of geometry helpers (`_meas_fig_base`,
  `_draw_square_wave`, `_draw_sine`, `_hline`, `_vline`,
  `_arrow_v`, `_arrow_h`) that drive the measurement preview
  thumbnails (`make_measurement_preview`).
- **SI parser** (`parse_si`) for "10 m", "1k", "2.5 µ" → float.
- **Smart CSV reader** (`_read_csv_smart`) with separator detection,
  numeric-row heuristic, metadata extraction, scope brand
  fingerprint (`_detect_scope_brand`).
- **Math channel parser**: `_MathParser` class, `math_tokenize`,
  `math_parse`, `math_deps`, `math_eval`. No `eval()`; custom AST.
- **Channel panel** (rename, color picker, GND snap to grid,
  active-channel selection, hover/active visual feedback).
- **Measurement panel** (build / rebuild, per-row drawing, removal).
- **Annotations toolbar**: text annotations, draw, erase tools.
- **Trigger window**: configurable level / hysteresis, event
  navigation, marker rendering on the main plot.
- **XY mode** window.
- **Cursors**: vertical, horizontal, with measurement markers
  (`_draw_vertical_cursor_marker`).
- **Export image** dialog and `_build_export_figure`.
- **Plot context menu**.
- **Toast** notifications.
- **CSV import dialog** (`_open_csv_import_dialog`) with manual
  fallback.
- **Auto-fit** for V/div and T/div with snapping (`_snap_to_list`,
  `_snap_t_div_nearest`).
- **FFT window** subsystem (W / RBW / freq controls with spin
  arrows and SI-typed inputs).
- **Loading overlay**.

Everything else in the modern app builds on top of this release.

**Filename**: `scope_v00_02.py`

---

## [0.1] — Initial prototype

First commit. ~1,970 lines, 81 functions, 2 classes.

Bare CSV viewer with a matplotlib plot, basic V/div and T/div
controls, file open. No themes, no measurements, no decoders,
no FFT.

**Filename**: `scope_v00_01.py`

---

## Appendix — File rename mapping

For reference, here is the mapping from the historical (chaotic)
filenames to the new convention:

| New filename          | Original filename                                                  |
|-----------------------|--------------------------------------------------------------------|
| `scope_v00_01.py`     | `OscilloscopeTraceViewer.py`                                       |
| `scope_v00_02.py`     | `OscilloscopeTraceViewer_dark.py`                                  |
| `scope_v00_03.py`     | `OscilloscopeTraceViewer_dark_design2.py`                          |
| `scope_v00_04.py`     | `OscilloscopeTraceViewer_dark_design2_minimap.py`                  |
| `scope_v00_05.py`     | `OscilloscopeTraceViewer_dark_design2_minimap_optimized.py`        |
| `scope_v00_06.py`     | `OscilloscopeTraceViewer_dark_design2_minimap_optimizedv3.py`      |
| `scope_v01_02.py`     | `scope_v2_themes (1).py`                                           |
| `scope_v01_03.py`     | `scope_v3_setdata.py`                                              |
| `scope_v01_08.py`     | `scope_v8_decoder.py`                                              |
| `scope_v01_09.py`     | `scope_v9_fixes.py`                                                |
| `scope_v01_10.py`     | `scope_v10_portable.py`                                            |
| `scope_v01_11.py`     | `scope_v11_hover_fix.py`                                           |
| `scope_v01_12.py`     | `scope_v12_pulido.py`                                              |
| `scope_v01_13.py`     | `scope_v13.py`                                                     |
| `scope_v01_14.py`     | `scope_v14.py`                                                     |
| `scope_v01_15.py`     | `scope_v15.py`                                                     |
| `scope_v01_16.py`     | `scope_v16.py`                                                     |
| `scope_v01_18.py`     | `scope_v18.py`                                                     |
| `scope_v01_19.py`     | `scope_v19.py`                                                     |
| `scope_v01_20.py`     | `scope_v20.py`                                                     |
| `scope_v01_21.py`     | (no historical alias; snapshot impuro — see [1.22] notes)          |
| `scope_v01_22.py`     | (no historical alias; clean snapshot saved before v1.23)           |
| `scope_v01_23.py`     | (no historical alias; clean snapshot saved before v1.24)           |
| `scope_v01_24.py`     | (working file, no historical alias)                                |
