# What's New

User-facing changelog for **Oscilloscope CSV Viewer**.
Plain language, no internals. For the technical version see
[`CHANGELOG.md`](CHANGELOG.md).

---

## 2.0.0 — Rebuilt inside, polished outside

- **Same app, new foundations.** The program was reorganised and is
  now covered by automated tests. Everything you used in 1.30 is still
  there.
- **Ready-made Windows download.** Each release includes a zip with
  the executable; extract it and run it.
- **Session button** now sits under *Open CSV*.
- **Measurements panel** only appears when you add a measurement, so
  the plot gets more room the rest of the time.
- **The unsaved mark (`*`) goes away** if you undo your changes back
  to the saved state.
- **Tooltips** on every channel control replace the old legend text.
- **Cleaner axes.** Auto-fit picks "1 ms" instead of "1.00001 ms", and
  the tick at zero reads "0 s".
- **Light theme fixed**: yellow channels, cursor lines and trigger
  markers are now readable on white.
- **Peak-detect captures** (some Rohde & Schwarz files) open directly,
  and FFT, `DIFF` and phosphor work correctly with them.
- **Trigger ignores noise**: no more false edges on noisy signals.
- **CAN FD decoding** works; SPI without chip select no longer loses
  the first bit; CAN no longer stops at a damaged frame.
- **Exported images** keep annotations on the right scale and no
  longer overlap channel labels.
- **Multi-monitor**: the window reopens where you closed it, also on a
  secondary monitor.
- **Smaller fixes**: loading a session no longer duplicates MATH
  channels, annotations are cleared when you open another CSV, the
  top bar is never cut off, dialogs put the main button first, and
  About shows the real version.

---

## 1.30 — Keyboard shortcuts + Settings

- **New: keyboard shortcuts** for the most common actions. Press
  `C` to toggle cursors, `U` to open the unit picker for the
  active channel, `G` to snap its ground to the nearest grid
  line, `0` to recenter it on the origin. `1…9` still picks the
  active channel; `Ctrl+1…9` now toggles channel visibility.
- **Pan and zoom with the arrows.** `← / →` pan the time axis
  in 0.1-division steps; `↑ / ↓` pan the active channel
  vertically. Hold `Shift` to step the V/div setting; hold
  `Ctrl+Shift` to step the T/div setting.
- **Move cursors from the keyboard.** Hold `Ctrl` and the
  arrows to move cursor 1 (`Ctrl+←/→` for the time cursor,
  `Ctrl+↑/↓` for the voltage cursor). Add `Alt` for cursor 2
  (`Ctrl+Alt+←/→` and `Ctrl+Alt+↑/↓`). If cursors are off,
  pressing any of these turns them on automatically.
- **Shortcuts respect typing**: they are ignored while the
  caret is inside any text field (V/div, channel name, MATH
  expression, trigger level, etc.), so editing is never
  interrupted.
- **New dialog: *Commands legend***. Open *Settings* (the gear
  icon, top-right) and click *Commands legend* at the bottom to
  see the full list of shortcuts grouped by category.
- **Renamed: *Grid view* → *Settings***. The sun icon in the top
  bar is now a gear. Same window, same options (theme, plot
  colours, phosphor) plus the new commands button.
- **Polish.** Vertical arrow keys now match intuition (↑ moves
  the trace up). The active channel is highlighted in the
  sidebar as soon as you change it with `1–9`. The gear icon
  recolours with the chosen theme. The bottom bar of the
  *Settings* window has more breathing room above the buttons.

---

## 1.29 — Pick a unit for each channel

- **New: per-channel unit.** Until now everything was assumed to
  be Volts. Now each channel can be tagged with its own unit, so
  a current signal reads `A`, a power signal reads `W`, a
  temperature reads `°C`, etc. The unit only affects display
  (V/div label, ticks, cursors, measurements, FFT, Eye, image
  export) — it does **not** change what MATH does.
- **How to set it.** Each channel row in the sidebar has a small
  bordered box with the current unit symbol between the channel
  name and the `⟲` snap button. Click it → a 5×5 picker opens.
  Selected unit is shown sunken/highlighted. Confirm with **OK**
  (Enter) or back out with **Cancel** (Esc).
- **Units available** (24, layout 5×5 with one empty cell):
  ```
  V    A    W    VA   var
  J    Ω    Hz   %    dB
  N    kgf  N·m  RPM  T
  °C   °F   K    Pa   PSI
  bar  lux  lm   —
  ```
  `—` is "no unit / dimensionless".
- **MATH dialog** has a *Unit:* dropdown next to *Name:* so you
  can pick the unit for derived channels (e.g. `V*A` → set unit
  to `W`). Default stays at `V`.
- **Sensible scaling.** The display still picks the right SI
  prefix automatically (`5 mA`, `120 kHz`, `2.4 µJ`...) for units
  where that makes sense. For temperatures, decibels, percents,
  PSI, RPM, lux, lumens and the dimensionless `—`, the scaling is
  skipped — `15 °C` stays `15 °C`, not `1.5 da°C`.
- **Saved with the session.** Open an `.ocv` and the units come
  back exactly as you left them. Sessions from older versions
  open fine — channels just default to `V` until you change them.
- **Fixed: changing the theme or the plot colours marked the
  session as modified.** Theme and plot colours are personal
  preferences, not part of a project — they're remembered across
  app launches but no longer dirty the session or get saved into
  `.ocv` files.
- **Fixed: background colour box in *Appearance → Custom* stayed
  with the previous colour after switching themes.** Switching
  from High Contrast to Dark used to leave that swatch grey
  until you re-clicked the theme. Now it updates in sync with
  the rest of the UI.
- **Fixed: Decoder window cut off at the bottom.** The frames
  table header was hidden below the visible area on some
  monitors. It now sizes correctly and stays within the screen
  height on smaller displays.
- **Channel row layout.** The per-channel mini-buttons in the
  sidebar are now in the order *unit · ⟲ snap · ✕ delete* and
  the help line below is more compact, so the sidebar no longer
  widens unnecessarily.

---

## 1.28 — Number keys + copy plot to clipboard

- **New: keys 1 to 9 switch the active channel.** Press `3` and
  channel 3 becomes the active one (drives the V/div display,
  cursors, GND marker, etc.). A small toast confirms the switch.
- Keys are ignored while you are typing in any text field, so
  entering numbers in V/div, t/div, math expressions or rename
  dialogs keeps working as before.
- Channels 10–12 stay mouse-only.
- **New: copy plot to clipboard** (Windows). Right-click on the
  plot → *Copy image to clipboard*. Paste straight into Paint,
  Word, Slack, Discord, etc. The image matches exactly what you
  see on screen — current theme, zoom, phosphor, everything.
- *Save image as...* (file) still uses the printable white-paper
  look with optional cursors / measurements / annotations; for a
  fast paste, the clipboard copy is now one click away.
- **New: `requirements.txt`** at the repo root, so a fresh Python
  install or virtualenv can reproduce the working environment with
  `pip install -r requirements.txt`.
- **Fixed: transparent flash before dialogs.** Opening dialogs
  (Trigger, XY, Eye, Decoder, MATH, Add Measurements, About,
  Appearance, Annotation text, Rename, Save image, etc.) briefly
  showed a small semi-transparent "ghost" window in a corner
  before the real dialog appeared. Now every dialog stays hidden
  until it has its final size and position, so it pops into view
  cleanly and centred over the main window.
- **New: splash screen on launch (packaged `.exe` only).** When
  the standalone Windows build is used, a splash image appears
  while the app loads and disappears as soon as the main window
  is ready. Running from source is unchanged.
- **Decoder window taller.** The decoded-frames table at the
  bottom of the Decoder window was cut off by default and you
  had to resize the window to read it. Default height is now
  large enough to show the table without resizing.
- **Fixed: harmless `AttributeError` in the console at startup.**
  Running the app from a terminal printed a Tk traceback about a
  missing `ax` attribute during the first few milliseconds of
  startup. The app worked fine despite it, but the noise is gone
  now.
- **Fixed: re-saving a loaded session produced a broken file.**
  Opening an `.ocv` and saving it again wrote a file with no CSV
  inside, so re-opening it failed with "session does not contain
  embedded CSV data". Now Save / Save As after loading a session
  keep the embedded CSV intact, and the file round-trips fine.
- **Fixed: tiny window on first launch.** The very first time you
  opened the app (before any preferences had been saved) the
  window came up at the minimum content size and you had to
  maximize it by hand. The app now opens maximized on first run
  and remembers your size/position from then on.

---

## 1.27 — Phosphor mode (CRT-style intensity grading)

- **New: phosphor mode.** Optional CRT-style look where flat
  segments of the trace stay bright and fast edges fade out, just
  like the unequal brightness of a real analogue oscilloscope
  phosphor. Toggle in *Appearance → Phosphor (intensity grading)*.
- **Intensity slider** (0–100 %). 0 % is "off", 100 % makes the
  fastest edges almost invisible.
- **Both settings persist** across launches (saved in your user
  config like the theme and grid colours). They are *not* part of
  the session file — they're a personal preference.

> Phosphor mode is heavier than the normal render — the per-segment
> alpha is recomputed on every redraw — but it now reuses the
> fast-update path on pan / drag instead of rebuilding the whole
> plot, so it should stay responsive on most CSVs. If you notice
> sluggishness on very long captures, turn it off while interacting
> and re-enable it for inspection.

---

## 1.26 — More room: 12 channels, more MATH, more measurements

- **Up to 12 channels** (was 8). Four new colours: violet, lime,
  pink, teal.
- **Up to 8 MATH channels** (was 4). More room for derived signals
  on the larger channel count.
- **Up to 16 active measurements** (was 10). Enough for one per
  channel with a few combinations left over.

> If you keep the app window short, the bottom channels in the
> sidebar may run off-screen on dense layouts. Resize the window
> or hide some channels. Proper scrolling for the sidebar may come
> in a future release if it turns out to be common.

---

## 1.25 — Sessions like a document editor

- **Untitled on launch.** The app starts in a session called
  *Untitled*. The window title shows the session name.
- **Unsaved changes marker.** The title gets a leading `*` as soon
  as you change anything (channels, view, cursors, annotations,
  trigger, theme...). The `*` disappears after you save.
- **Save (Ctrl+S)** now writes straight to the current session file
  if you have one. No more dialog every time. If the session is
  still Untitled, it falls back to *Save As*.
- **Save As (Ctrl+Shift+S)** always asks where to save and remembers
  the new path.
- **Close / open something else asks to save.** Closing the app,
  loading another `.ocv` session, or loading a CSV that would
  replace your data now prompts you with *Save / Don't save /
  Cancel* if there are unsaved changes. The prompt uses the app's
  current theme — no more out-of-place native dialog. Enter saves,
  Escape cancels.
- Session menu reorganised: *Save*, *Save as...*, *Load session...*.

> Loading a CSV from scratch counts as a new *Untitled* session —
> the `*` only appears once you start modifying it.

---

## 1.24 — Bug round: Eye, Fine V/div, annotations

- Fixed: the **Eye Diagram** window no longer opens on the primary
  monitor — it now centres on the main window like every other
  dialog.
- Fixed: in **Fine V/div** mode, pressing ↑ at 100 V/div no longer
  jumps down to 50 V/div. Fine now only changes the step size, not
  the maximum scale.
- Fixed: Y-axis tick labels in **Fine V/div** mode now show the
  decimal you actually set (e.g. `100.1 V` instead of being rounded
  to `100 V`).
- Fixed: changing **V/div** with a non-zero vertical offset no longer
  makes the active channel's `0 V` line slide relative to the grid.
  The trace now zooms around its own GND mark like on a real scope.
- Fixed: the `0 V` tick label no longer shows up as `100 fV` /
  `1 fV` after changing scales in Fine mode.
- Fixed: **annotations** no longer appear and disappear when you
  switch between channels with different V/div. They now stay
  attached to the trace they were drawn on. (Annotations from
  older sessions will still show the old behaviour — re-create
  them to benefit from the fix.)
- Cleanup: the **Annotations** toolbar no longer has a duplicate
  close (✕) button. Use the title-bar X or `Escape`.

---

## 1.23 — Session files are now self-contained (`.ocv`)

- **New session extension: `.ocv`** (Oscilloscope CSV Viewer).
  Replaces the old `.oscsession`.
- **Sessions now contain the CSV inside the file.** You can email
  or copy a single `.ocv` to share a complete session — no need
  to attach the original CSV.
- ⚠️ **Old `.oscsession` files no longer load.** They need to be
  re-created in 1.23+.

---

## 1.22 — Faster theme switching on modest hardware

- The smooth theme transition introduced in 1.20 has been rebuilt
  with a lightweight overlay. Switching themes now takes the same
  time regardless of how many widgets are on screen, so older /
  lower-spec PCs no longer stutter.

---

## 1.21 — Multi-monitor & polish

- Renamed app to **Oscilloscope CSV Viewer** (was *Offline
  Oscilloscope CSV Viewer*).
- **Ctrl + mouse wheel on the plot** → horizontal zoom (T/div).
  Same as the minimap wheel, but without leaving the grid.
- Fixed: **dialogs jumping to the primary monitor** when the main
  window lived on a second monitor. About, Appearance, Grid view,
  Math, Trigger, XY, Save image, Add channels — all of them now
  open on the correct monitor.
- Fixed: **tooltips appearing on the wrong monitor** in
  multi-monitor setups.
- Fixed: the **FFT** window now centres on the main window
  instead of opening at a corner.
- Fixed: turning **XY mode** off no longer leaves the plot blank
  until the next change.
- About window description updated with the full decoder list
  (UART / I²C / SPI / LIN / I²S / CAN / CAN FD / J1939 / SDIO /
  MIL-STD-1553 / ARINC 429).
- Heads-up: if you had the app pinned to the Windows taskbar, the
  pin will detach once after this upgrade.

---

## 1.20 — UX pass

- **Smooth theme transitions.** Switching between Dark / Light /
  Blueprint / High Contrast now fades the colours over ~180 ms
  instead of switching abruptly.
- **FFT settings are now remembered** between sessions (window
  type, Y scale, trace mode, averaging, harmonic markers, cursor).
  A *Reset configuration* button restores defaults.
- **Window position is remembered** between sessions, including
  maximised state. First launch centres on the primary screen.
- **Load CSV → add channels to existing data.** When you open a
  new CSV the app asks whether to *replace* or *append*. Append
  shows a channel picker with a live slot counter (8 channels
  max) and interpolates the new data onto the current timebase.
- **Delete channels from the channels panel.** Each channel has
  an ✕ button. If you delete a channel that a MATH expression
  depends on, the app offers to remove the dependent MATH too.
- **Cursors: "Show on all channels"** checkbox. The voltage
  marker (X) appears on every visible channel at the same time
  instant. Persisted.
- **Decoder window:** double-click a column to set it as the
  time column.
- Dialogs (load mode, time column, add channels, manual CSV
  fallback, annotation editor) now open centred over the main
  window.
- Fixed: theme preview buttons resizing on theme change.
- Fixed: plot grid not fading along with Dark ↔ Light.
- Fixed: window opening at random positions or tiny sizes.
- Fixed: taskbar icon shown as the generic Python icon on Windows.
- Fixed: add-channels mode used to load the CSV as a full
  replace; also fixed two time-column dialogs showing up on every
  load.

---

## 1.18 — More SPI / LIN options

- **SPI without CS line.** Frames are delimited by SCK idle gaps
  longer than a configurable threshold (default 100 µs).
- **LIN J2602** and **Any** versions added.
- **LIN checksum:** Auto / Classic / Enhanced.

---

## 1.16 — Frame diagrams + avionics decoders

- **Frame structure diagram** for every protocol: I²C, SPI, LIN,
  I²S, CAN, CAN FD, J1939, SDIO (UART had landed in 1.15). Shown
  in the decoder window between the protocol list and the channel
  mapping.
- **MIL-STD-1553B decoder** (1 Mbit/s, Manchester II).
- **ARINC 429 decoder** (32-bit avionics bus, 12.5 / 100 kbit/s
  or custom).
- New **AVIONICS / MILITARY** category in the decoder list.

> Note: the 1553 and ARINC decoders do word extraction + parity.
> They don't deeply validate protocol-level fields — fine for
> visualisation, not for certification.

---

## 1.15 — Decoder frame diagrams (start)

- **Frame structure panel** added to the decoder window — a
  diagram that shows the on-the-wire layout of the selected
  protocol (Rohde & Schwarz style). UART diagram included as the
  first one; the rest land in 1.16.
- **Cursor snap to t=0** for the horizontal cursor.
- Secondary windows (e.g. FFT) can now be maximised.
- Toast notifications are now shown for 2 s (was 1.5 s).

---

## 1.14 — Time display origin

- The time axis can now display times relative to a chosen
  reference (trigger event, t=0, file start) without re-loading
  the data.

---

## 1.13 — Embedded + automotive decoders

- **LIN** decoder (break, sync, PID, data, classic / enhanced
  checksum).
- **I²S** decoder (BCLK + WS + SDATA; Philips, left-justified,
  right-justified).
- **CAN 2.0 A/B** decoder. Logical or differential input. SOF,
  11/29-bit ID, control, data, CRC15, ACK, EOF.
- **CAN FD** decoder. Up to 64-byte payload, BRS / ESI flags.
- **SAE J1939** decoder. Decomposes the CAN 29-bit identifier
  into Priority / EDP / DP / PF / PS / SA. PGN derivation.
- **SDIO** decoder. CMD line + DAT0 (1-bit) or DAT0..3 (4-bit).
- **Smart time-column picker.** When auto-detection isn't
  confident, you now get a dialog showing the candidate columns
  with stats — pick one and confirm the unit.

---

## 1.12 — Polish

- **About window** added.
- **Shift + mouse wheel** pans the plot horizontally.

*(Mostly an internal pass: re-formatting, comments, docstrings.)*

---

## 1.11 — Hover fix

- Fixed a small hover regression introduced by the portable-mode
  changes.

---

## 1.10 — Portable mode

- **App config now lives next to the executable** (or next to the
  script during development), so the app is portable: copy the
  folder, copy the settings. Auto-detected.
- Warns you on launch if the install folder is read-only.

---

## 1.9 — Settings persistence + fixes

- **User config system.** Theme, grid style, time-display options
  and other preferences now survive restarts (saved as a JSON file).
- General fixes batch.

---

## 1.8 — Protocol decoders arrive

This is the release where the decoder subsystem appears.

- **UART** decoder (RX + optional TX; start, N data bits, parity,
  stop).
- **I²C** decoder (SCL + SDA; START, ADDR + R/W, ACK, DATA,
  STOP).
- **SPI** decoder (SCK + MOSI + MISO + optional CS; CPOL/CPHA
  configurable; MSB/LSB first).
- **Decoder window** with a channel-role mapping UI (assign
  which scope channel plays which role) and a frame table you
  can double-click to navigate to, and export to CSV.

---

## 1.3 — Loading improvements

- Better CSV ingestion.

---

## 1.2 — Theme system + cleanup

- The theme system now supports **multiple selectable themes**
  (foundation for Dark / Light / Blueprint / High Contrast).
- Internal cleanup pass (~1,900 lines of dead code removed).

---

## 0.6 — The "big merge"

A much larger release than the name suggests.

- **Multiple themes** (early form). Switch the theme without
  restarting.
- **Hover tooltips** across the UI.
- **Undo / redo** for annotations.
- **Math channel cycle detection** — the app refuses to define
  `M1 = M2 + 1` while `M2 = M1 * 2`.
- **Eye Diagram window**: opening, jitter, rise/fall metrics;
  manual or automatic UI estimation; save-as-image.
- **FFT trace modes**: Live, Average, Max-hold, Min-hold.
- **FFT harmonic detection** and **THD** computation.
- **FFT extras**: right-click context menu, save as image, copy
  peak frequency / amplitude to clipboard.
- Plot redraws are now significantly faster (perceptible
  improvement on dense traces).

---

## 0.5 — First performance pass

- Plot rendering optimisations. Foundation for the fast / slow
  split that fully lands in 0.6.

---

## 0.4 — Minimap

- **Minimap panel** added: overview strip showing the full trace
  with a movable window indicating the visible region of the
  main plot.

---

## 0.3 — Visual redesign

- Top-bar refactor and broader layout cleanup.

---

## 0.2 — The release that defines the app

The release where the app becomes recognisable.

- **Dark theme.**
- **Comprehensive measurements**: pulse widths (positive /
  negative), slew rates, duty cycle, high/low time, time outside
  level, phase via Hilbert and via zero-crossings. Each
  measurement has a small preview thumbnail.
- **Math channels** with a safe expression parser (no `eval`).
- **Channel panel**: rename, colour picker, GND snap to grid,
  active-channel selection, hover/active feedback.
- **Annotations toolbar**: text annotations, draw, erase.
- **Trigger window**: configurable level / hysteresis, event
  navigation, markers on the main plot.
- **XY mode** window.
- **Cursors**: vertical, horizontal, with measurement markers.
- **Export image** dialog.
- **Plot context menu.**
- **Toast notifications.**
- **CSV import dialog** with manual fallback.
- **Auto-fit** for V/div and T/div.
- **FFT window** with W / RBW / frequency controls (SI-typed
  inputs).
- **Loading overlay.**

---

## 0.1 — Initial prototype

First release. Bare CSV viewer with a matplotlib plot, basic
V/div and T/div controls, file open. No themes, no
measurements, no decoders, no FFT.
