# Oscilloscope CSV Viewer

Desktop application that brings the oscilloscope workflow to CSV
captures exported by real instruments. It does not talk to hardware:
it works offline on the files the oscilloscope already saved, for lab
analysis, test documentation and protocol debugging.

## Features

- **CSV import** with automatic detection of the time column,
  separator and metadata. Captures saved in peak-detect mode
  (repeated min/max timestamps) are supported. The instrument brand is
  shown when the header identifies it (Tektronix, Keysight,
  Rohde & Schwarz, Rigol, Siglent, Hantek, LeCroy, PicoScope, OWON).
- **Up to 12 channels** on a 12 × 8 division grid, each with its own
  V/div, position and display unit (V, A, W, °C, dB and others).
- **23 parametric measurements**: amplitude and level, overshoot, RMS,
  period, frequency, rise and fall time, pulse widths, duty cycle,
  slew rate and phase between channels. Up to 16 active at once.
- **Cursors** in time and amplitude, movable with mouse or keyboard.
- **Math channels** (up to 8) from expressions such as `CH1+CH2`,
  `ABS(CH1-CH2)`, `INT(CH1*CH2)`, `DIFF(CH1)` or `SQRT(M1)`.
- **Post-capture trigger** on edge or pulse width, with hysteresis and
  navigation between events.
- **FFT** with Rectangular, Hann, Hamming and Blackman windows,
  averaging, max hold, harmonic detection and THD.
- **Eye diagram** with eye height, eye width and jitter.
- **Protocol decoders**: UART/RS-232/RS-422/RS-485, I²C, SPI, I²S,
  SDIO, LIN, CAN 2.0, CAN FD, J1939, ARINC 429 and MIL-STD-1553.
  Frames are overlaid on the plot and can be exported to CSV.
- **XY mode**, **annotations** (freehand and text, with undo/redo) and
  **phosphor** intensity grading.
- **Sessions** (`.ocv`): the capture and the full view state in one
  file.
- **Export** of the plot as an image, or copy to the clipboard
  (Windows).
- **Four themes**: Dark, Light, Blueprint and High Contrast.

## Installation

### Windows executable

Download `OCV-vX.Y.Z-win64.zip` from the Releases page, extract it to
a writable folder (for example inside Documents) and run
`Oscilloscope CSV Viewer.exe`.

The application is portable: preferences are stored in `config.json`
next to the executable. A read-only location such as
`C:\Program Files` prevents saving them.

The executable is not code-signed, so Windows SmartScreen may show a
warning on first launch.

### From source

Requires Python 3.10 or later.

```
pip install -r requirements.txt
python main.py
```

## Keyboard shortcuts

| Keys | Action |
|---|---|
| `1`…`9` | Select the active channel |
| `Ctrl+1`…`9` | Show or hide a channel |
| `↑` `↓` / `←` `→` | Move the active channel / pan the time axis |
| `Shift+↑` `↓` | Step V/div |
| `Ctrl+Shift+↑` `↓` | Step T/div |
| `C` | Toggle cursors |
| `Ctrl+←` `→`, `Ctrl+↑` `↓` | Move cursor 1 (add `Alt` for cursor 2) |
| `G` / `0` | Snap ground to the grid / center the trace |
| `U` | Unit of the active channel |
| `Ctrl+S` / `Ctrl+Shift+S` | Save session / Save session as |
| `Ctrl+Z` / `Ctrl+Y` | Undo / redo annotations |

The full list is under *Settings → Commands legend*.

## Development

```
pip install -r requirements-dev.txt
python tests/run.py            # all tests
python tests/run.py csv        # tests whose name matches "csv"
python build.py --smoke        # build, smoke-test and zip the .exe
```

Publishing a GitHub release builds the executable on a Windows runner
and attaches the zip to the release.

## License

MIT. See [LICENSE](LICENSE).
