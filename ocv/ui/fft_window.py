"""FFT analysis window and its persistent display preferences."""
import matplotlib.pyplot as plt
import numpy as np
import tkinter as tk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.colors import to_hex
from tkinter import ttk

from ocv.core.config import load_user_config, save_user_config
from ocv.core.dsp import (
    apply_window,
    FFT_WINDOWS,
    FFT_Y_SCALES,
    merge_repeated_times,
    scale_unit,
    vrms_to_scale,
)
from ocv.core.scope import H_DIVS
from ocv.core.si_format import DEFAULT_UNIT, format_si, parse_si
from ocv.resources import _resource_path
from ocv.ui import theme
from ocv.ui.theme import THEMES
from ocv.ui.widgets import Tooltip


# -----------------------------
# Configuración persistente FFT
# -----------------------------
# Defaults usados:
#   - como base al crear una ventana FFT por primera vez (sin config guardada)
#   - como destino del botón "Reset configuration" del usuario
#
# IMPORTANTE: aquí solo van las preferencias "estables" de display. Los
# parámetros que dependen del archivo cargado (W, RBW, Start/Stop/Center/
# Span) NO se persisten: se recalculan al abrir la ventana porque dependen
# de fs y duración del trace. Persistirlos provocaría valores fuera de
# rango al cambiar de archivo.
FFT_DEFAULT_CONFIG = {
    "window_name":     "Hann",
    "y_scale":         "Linear (Vrms)",
    "trace_mode":      "Live",
    "avg_n":           8,
    "show_harmonics":  False,
    "n_harmonics":     5,
    "cursor_enabled":  False,
}


def load_fft_config():
    """Devuelve dict de config FFT del user config (key 'fft_config'),
    completado con FFT_DEFAULT_CONFIG para claves ausentes."""
    user = load_user_config().get("fft_config", {})
    out = dict(FFT_DEFAULT_CONFIG)
    if isinstance(user, dict):
        # Filtrar solo claves conocidas para no propagar basura
        for k in FFT_DEFAULT_CONFIG:
            if k in user:
                out[k] = user[k]
    return out


def save_fft_config(cfg):
    """Guarda dict completo de config FFT bajo la key 'fft_config' del
    user config. Filtra solo claves conocidas."""
    clean = {k: cfg[k] for k in FFT_DEFAULT_CONFIG if k in cfg}
    save_user_config({"fft_config": clean})


# =============================
# FFT Window
# =============================
class FFTWindow:
    """Ventana de análisis de espectro (FFT) tipo analizador.

    Parámetros de control:
      - W (ms)      : longitud del tramo temporal a transformar.
      - RBW (Hz)    : resolución espectral. Editable. Al cambiarla se recalcula W.
      - Window      : Rectangular / Hann / Hamming / Blackman.
      - Y scale     : Linear (Vrms), dBV, dBm(50Ω), dBµV.
      - Start/Stop  : visualización (xlim). Editar uno actualiza Center/Span.
      - Center/Span : visualización (xlim). Editar uno actualiza Start/Stop.

    Drag vertical: desplaza solo el ylim (offset visual).
    """

    def __init__(self, scope):
        self.scope = scope
        # Canal seleccionado (por defecto, el activo del scope)
        self.source_idx = scope.active_channel_idx
        voltage = scope.channels[self.source_idx]["voltage"] \
            if scope.channels else scope.voltage
        # Peak detect: un valor por instante; si no, dt sale a la mitad,
        # fs al doble y las N muestras cubren la mitad del tiempo
        self.time, self.voltage = merge_repeated_times(scope.time, voltage)

        # ==============================================================
        # Configuración persistente del usuario
        # ==============================================================
        # Cargamos las preferencias "estables" (window, y_scale, trace
        # mode, etc.) desde el config de usuario. Si no hay nada guardado,
        # esto devuelve FFT_DEFAULT_CONFIG. Los parámetros que dependen
        # del archivo (W, RBW, frecuencias) NO se persisten — se calculan
        # al vuelo más abajo a partir de fs/duración.
        #
        # Cualquier cambio de la UI llama a self._save_fft_config() para
        # que la próxima sesión arranque donde la dejamos. El botón
        # "Reset configuration" restablece los defaults y borra de disco.
        self._fft_config = load_fft_config()
        # Flag para evitar que _save_fft_config se dispare durante la
        # carga inicial — algunos handlers se ejecutan al construir la UI.
        self._suppress_config_save = True

        # Fuente de la FFT: canal seleccionado (nombre visible)
        if scope.channels:
            self.source_var = tk.StringVar(
                value=scope.channels[self.source_idx]["name"]
            )
        else:
            self.source_var = tk.StringVar(value="")

        # --- Estado ---
        # Tramo actualmente visible en el osciloscopio → valores por defecto
        t_start_scope = scope.t_offset.get()
        window_scope  = scope.t_div.get() * H_DIVS

        mask = (self.time >= t_start_scope) & (self.time <= t_start_scope + window_scope)
        if np.count_nonzero(mask) < 2:
            return

        self.t_segment_start = t_start_scope
        self.dt = float(np.mean(np.diff(self.time[mask])))
        self.fs = 1.0 / self.dt

        # W en milisegundos (por defecto = ventana visible del scope)
        self.W = tk.DoubleVar(value=window_scope * 1000.0)

        # Ventana FFT — leer del config persistente (con fallback al default)
        self.window_name = tk.StringVar(value=self._fft_config["window_name"])

        # RBW se calcula al vuelo desde W y la ventana
        self.RBW = tk.DoubleVar(value=self._compute_rbw())

        # Escala Y — del config persistente
        self.y_scale = tk.StringVar(value=self._fft_config["y_scale"])

        # Zoom frecuencia (se inicializan al hacer el primer cálculo)
        self.f_start  = tk.DoubleVar(value=0.0)
        self.f_stop   = tk.DoubleVar(value=self.fs / 2.0)
        self.f_center = tk.DoubleVar(value=self.fs / 4.0)
        self.f_span   = tk.DoubleVar(value=self.fs / 2.0)

        # StringVars de display (formato SI "240 kHz")
        self.W_display       = tk.StringVar(value=format_si(self.W.get() / 1000.0, "s"))
        self.RBW_display     = tk.StringVar(value=format_si(self.RBW.get(), "Hz"))
        self.f_start_display  = tk.StringVar(value=format_si(self.f_start.get(),  "Hz"))
        self.f_stop_display   = tk.StringVar(value=format_si(self.f_stop.get(),   "Hz"))
        self.f_center_display = tk.StringVar(value=format_si(self.f_center.get(), "Hz"))
        self.f_span_display   = tk.StringVar(value=format_si(self.f_span.get(),   "Hz"))

        # Flag para evitar recursión al actualizar pares redundantes
        self._updating_freq = False

        # Arrays del último cálculo
        self.freq = None
        self.mag_vrms = None

        # Offset vertical para drag
        self.y_offset = 0.0

        # Drag state
        self._dragging = False
        self._drag_start_y = None
        self._drag_start_offset = 0.0

        # --- Cursor vertical ---
        self.cursor_enabled = tk.BooleanVar(
            value=self._fft_config["cursor_enabled"])
        self.cursor_freq = None          # frecuencia actual del cursor (Hz)
        self._cursor_dragging = False    # True mientras se arrastra el cursor
        self._cursor_line = None         # artist matplotlib (axvline)
        self._cursor_marker = None       # punto sobre el pico (Line2D)

        # ===============================================
        # Features avanzadas (paso 5)
        # ===============================================
        # --- Trace mode: Live | Average | Max-hold | Min-hold ---
        # Live: el espectro recién calculado. Avg/Max/Min: agregados.
        self.trace_mode = tk.StringVar(
            value=self._fft_config["trace_mode"])

        # Buffer para averaging (RMS averaging: media de |X|^2 luego sqrt).
        # Se reinicia al cambiar parámetros estructurales (W, RBW, ventana,
        # canal fuente) — porque entonces los bins no son comparables.
        self._avg_count = 0           # nº de espectros acumulados
        self._avg_sum_sq = None       # array sum(|X|^2) bin a bin (Vrms^2)
        self.avg_n = tk.IntVar(
            value=int(self._fft_config["avg_n"]))   # nº deseado de promedios

        # Max/min hold acumuladores (en Vrms — la unidad nativa interna).
        self._max_hold = None
        self._min_hold = None

        # --- Detección de armónicos ---
        self.show_harmonics = tk.BooleanVar(
            value=self._fft_config["show_harmonics"])
        self.n_harmonics = tk.IntVar(
            value=int(self._fft_config["n_harmonics"]))   # cuántos mostrar incluyendo f0
        # Estos artists matplotlib se gestionan en _draw_harmonics; los
        # guardamos en una lista para poder limpiarlos sin tocar el resto.
        self._harmonic_artists = []
        # Última frecuencia fundamental detectada (None si no hay)
        self._f0_detected = None
        # Última THD calculada (porcentaje, None si no disponible)
        self._thd_pct = None
        self._thd_db = None

        # --- UI ---
        self.win = tk.Toplevel(scope.root)
        self.win.withdraw()  # anti-flash
        self.win.title("FFT")
        # Permitir maximizar: declaramos resizable explícito y eliminamos
        # transient() de la cadena de chrome (transient hace que Windows
        # trate la ventana como diálogo modal sin botón de maximizar).
        # Aplicamos solo el icono + withdraw/deiconify del chrome para
        # evitar el flash de ventana en blanco.
        self.win.resizable(True, True)
        self.win.minsize(700, 420)
        # Centrado sobre la ventana principal (importante en multimonitor:
        # _center_dialog usa coords absolutas de root, no la pantalla
        # primaria). En lugar de geometry("1100x650") fijo.
        scope._center_dialog(self.win, 1100, 650)
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)
        # Chrome sin transient: icono y withdraw/deiconify igual que las
        # otras ventanas, pero conservando los botones de la barra de
        # título (minimize/maximize/close).
        self._apply_chrome_maximizable(scope)

        self._build_ui()
        self._recompute()
        # UI construida — desactivar la supresión de save y a partir de aquí
        # cualquier cambio del usuario disparará la persistencia automática.
        self._suppress_config_save = False

    def _save_fft_config(self):
        """Persiste las preferencias actuales de la FFT en el user config.

        Se llama automáticamente desde los handlers de cambios en la UI
        (sin preguntar al usuario, igual que el resto de prefs de la app).
        El flag `_suppress_config_save` evita escrituras durante la
        construcción inicial de la ventana.

        IMPORTANTE: aquí solo van las preferencias estables. W, RBW y los
        rangos de frecuencia NO se persisten porque dependen del archivo
        cargado (fs y duración). Persistirlos provocaría valores fuera de
        rango al cambiar a otro trace."""
        if getattr(self, "_suppress_config_save", True):
            return
        cfg = {
            "window_name":    self.window_name.get(),
            "y_scale":        self.y_scale.get(),
            "trace_mode":     self.trace_mode.get(),
            "avg_n":          int(self.avg_n.get()),
            "show_harmonics": bool(self.show_harmonics.get()),
            "n_harmonics":    int(self.n_harmonics.get()),
            "cursor_enabled": bool(self.cursor_enabled.get()),
        }
        self._fft_config = cfg
        # Guardado silencioso (igual que grid_theme / app_theme): si falla
        # no molestamos al usuario en mitad de un workflow.
        save_fft_config(cfg)

    def _reset_fft_config(self):
        """Restablece la config FFT a sus defaults y la persiste.

        Equivalente a 'olvidar las preferencias guardadas' — útil cuando
        el usuario ha hecho experimentos raros y quiere volver al estado
        inicial sin tener que tocar cada control."""
        # Suprimir save mientras aplicamos los cambios uno a uno
        self._suppress_config_save = True
        try:
            self.window_name.set(FFT_DEFAULT_CONFIG["window_name"])
            self.y_scale.set(FFT_DEFAULT_CONFIG["y_scale"])
            self.trace_mode.set(FFT_DEFAULT_CONFIG["trace_mode"])
            self.avg_n.set(int(FFT_DEFAULT_CONFIG["avg_n"]))
            self.show_harmonics.set(
                bool(FFT_DEFAULT_CONFIG["show_harmonics"]))
            self.n_harmonics.set(int(FFT_DEFAULT_CONFIG["n_harmonics"]))
            self.cursor_enabled.set(
                bool(FFT_DEFAULT_CONFIG["cursor_enabled"]))
            # Aplicar efectos visuales de los cambios
            self.RBW.set(round(self._compute_rbw(), 6))
            self._sync_fft_displays()
            self._reset_trace_buffers(silent=True)
            # Cerrar cursor si quedó activo
            self._on_cursor_toggle()
        finally:
            self._suppress_config_save = False
        # Persistir el reset (escribe los defaults a disco)
        self._save_fft_config()
        # Refresco final
        self._recompute()

    def _apply_chrome_maximizable(self, scope):
        """Variante de _apply_window_chrome para ventanas que necesitan
        ser maximizables (FFT). transient(root) en Windows inhibe el
        botón de maximizar, así que NO lo aplicamos aquí. La contrapartida
        es que la ventana aparece como entrada propia en la taskbar de
        Windows (en lugar de agruparse con la principal).

        Sí aplicamos icono y withdraw/deiconify para mitigar el flash de
        ventana en blanco mientras se construye la UI."""
        try:
            self.win.withdraw()
        except Exception:
            pass
        try:
            self.win.iconbitmap(_resource_path("assets/icon.ico"))
        except Exception:
            pass

        def _show_when_ready():
            try:
                self.win.update_idletasks()
                self.win.deiconify()
            except Exception:
                pass
        try:
            self.win.after_idle(_show_when_ready)
        except Exception:
            try:
                self.win.deiconify()
            except Exception:
                pass

    # -----------------------------
    def _build_ui(self):
        self.win.configure(bg=theme.COLOR_BG)

        # Panel de controles (arriba)
        ctrl = ttk.Frame(self.win, padding=10)
        ctrl.pack(side=tk.TOP, fill=tk.X)

        # --- Fila 1: Source, W, RBW, Window, Y scale ---
        row1 = ttk.Frame(ctrl)
        row1.pack(fill=tk.X, pady=4)

        ttk.Label(row1, text="Source:").pack(side=tk.LEFT)
        ch_names = [ch["name"] for ch in self.scope.channels]
        self.source_combo = ttk.Combobox(
            row1, textvariable=self.source_var,
            values=ch_names, state="readonly", width=10,
        )
        self.source_combo.pack(side=tk.LEFT, padx=(4, 14))
        self.source_combo.bind("<<ComboboxSelected>>",
                               lambda e: self._on_source_changed())

        ttk.Label(row1, text="W:").pack(side=tk.LEFT)
        self.w_spin = ttk.Spinbox(
            row1, from_=0, to=1e9, increment=1,
            textvariable=self.W_display, width=12,
            command=lambda: self._on_W_spin_arrow(),
        )
        self.w_spin.pack(side=tk.LEFT, padx=(4, 14))
        self.w_spin.bind("<Return>",   lambda e: self._on_W_typed())
        self.w_spin.bind("<FocusOut>", lambda e: self._on_W_typed())

        ttk.Label(row1, text="RBW:").pack(side=tk.LEFT)
        self.rbw_spin = ttk.Spinbox(
            row1, from_=0, to=1e12, increment=1,
            textvariable=self.RBW_display, width=12,
            command=lambda: self._on_RBW_spin_arrow(),
        )
        self.rbw_spin.pack(side=tk.LEFT, padx=(4, 14))
        self.rbw_spin.bind("<Return>",   lambda e: self._on_RBW_typed())
        self.rbw_spin.bind("<FocusOut>", lambda e: self._on_RBW_typed())

        ttk.Label(row1, text="Window:").pack(side=tk.LEFT)
        self.win_combo = ttk.Combobox(
            row1, textvariable=self.window_name,
            values=list(FFT_WINDOWS.keys()),
            state="readonly", width=12,
        )
        self.win_combo.pack(side=tk.LEFT, padx=(4, 14))
        self.win_combo.bind("<<ComboboxSelected>>", lambda e: self._on_window_changed())

        ttk.Label(row1, text="Y scale:").pack(side=tk.LEFT)
        self.yscale_combo = ttk.Combobox(
            row1, textvariable=self.y_scale,
            values=FFT_Y_SCALES,
            state="readonly", width=14,
        )
        self.yscale_combo.pack(side=tk.LEFT, padx=(4, 14))
        self.yscale_combo.bind("<<ComboboxSelected>>",
                               lambda e: self._on_yscale_changed())

        # Botón "Reset configuration" — a la derecha de la fila principal.
        # Restablece todas las preferencias persistidas a sus defaults
        # (window, y_scale, trace mode, harmonics, cursor, etc.) y las
        # vuelve a guardar en disco.
        self.reset_config_btn = ttk.Button(
            row1, text="Reset configuration",
            command=self._reset_fft_config,
        )
        self.reset_config_btn.pack(side=tk.RIGHT, padx=(4, 0))
        Tooltip(self.reset_config_btn,
                "Reset all FFT preferences to defaults\n"
                "(window, Y scale, trace mode, harmonics, cursor)\n"
                "Note: W, RBW and frequency ranges are not persisted —\n"
                "they depend on the loaded file's sample rate and duration.")

        # --- Fila 2: Start, Stop, Center, Span ---
        row2 = ttk.Frame(ctrl)
        row2.pack(fill=tk.X, pady=4)

        ttk.Label(row2, text="Start:").pack(side=tk.LEFT)
        self.start_spin = ttk.Spinbox(
            row2, from_=0, to=1e12, increment=10,
            textvariable=self.f_start_display, width=12,
            command=lambda: self._on_freq_spin_arrow("start"),
        )
        self.start_spin.pack(side=tk.LEFT, padx=(4, 14))
        self.start_spin.bind("<Return>",   lambda e: self._on_freq_typed("start"))
        self.start_spin.bind("<FocusOut>", lambda e: self._on_freq_typed("start"))

        ttk.Label(row2, text="Stop:").pack(side=tk.LEFT)
        self.stop_spin = ttk.Spinbox(
            row2, from_=0, to=1e12, increment=10,
            textvariable=self.f_stop_display, width=12,
            command=lambda: self._on_freq_spin_arrow("stop"),
        )
        self.stop_spin.pack(side=tk.LEFT, padx=(4, 14))
        self.stop_spin.bind("<Return>",   lambda e: self._on_freq_typed("stop"))
        self.stop_spin.bind("<FocusOut>", lambda e: self._on_freq_typed("stop"))

        ttk.Label(row2, text="Center:").pack(side=tk.LEFT)
        self.center_spin = ttk.Spinbox(
            row2, from_=0, to=1e12, increment=10,
            textvariable=self.f_center_display, width=12,
            command=lambda: self._on_freq_spin_arrow("center"),
        )
        self.center_spin.pack(side=tk.LEFT, padx=(4, 14))
        self.center_spin.bind("<Return>",   lambda e: self._on_freq_typed("center"))
        self.center_spin.bind("<FocusOut>", lambda e: self._on_freq_typed("center"))

        ttk.Label(row2, text="Span:").pack(side=tk.LEFT)
        self.span_spin = ttk.Spinbox(
            row2, from_=0, to=1e12, increment=10,
            textvariable=self.f_span_display, width=12,
            command=lambda: self._on_freq_spin_arrow("span"),
        )
        self.span_spin.pack(side=tk.LEFT, padx=(4, 14))
        self.span_spin.bind("<Return>",   lambda e: self._on_freq_typed("span"))
        self.span_spin.bind("<FocusOut>", lambda e: self._on_freq_typed("span"))

        # --- Fila 3: botones + cursor ---
        row3 = ttk.Frame(ctrl)
        row3.pack(fill=tk.X, pady=(4, 2))

        ttk.Button(row3, text="Recompute", command=self._recompute,
                   style="Accent.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(row3, text="Auto-scale Y", command=self._auto_scale_y).pack(side=tk.LEFT, padx=2)
        ttk.Button(row3, text="Reset zoom", command=self._reset_zoom).pack(side=tk.LEFT, padx=2)
        ttk.Button(row3, text="Full span", command=self._full_span).pack(side=tk.LEFT, padx=2)

        ttk.Separator(row3, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Checkbutton(
            row3, text="Cursor",
            variable=self.cursor_enabled,
            command=self._on_cursor_toggle
        ).pack(side=tk.LEFT, padx=(4, 4))

        ttk.Button(row3, text="Snap to max", command=self._cursor_snap_to_max).pack(side=tk.LEFT, padx=2)

        self.info_label = tk.Label(
            row3, text="",
            fg=theme.COLOR_TEXT_DIM, bg=theme.COLOR_BG,
            font=("Consolas", 9)
        )
        self.info_label.pack(side=tk.RIGHT, padx=10)

        # --- Fila 4: Trace mode (Live/Avg/Max-hold/Min-hold) + Harmonics + THD ---
        row4 = ttk.Frame(ctrl)
        row4.pack(fill=tk.X, pady=(4, 2))

        ttk.Label(row4, text="Trace:").pack(side=tk.LEFT)
        self.trace_mode_combo = ttk.Combobox(
            row4, textvariable=self.trace_mode,
            values=["Live", "Average", "Max-hold", "Min-hold"],
            state="readonly", width=10,
        )
        self.trace_mode_combo.pack(side=tk.LEFT, padx=(4, 8))
        self.trace_mode_combo.bind(
            "<<ComboboxSelected>>", lambda e: self._on_trace_mode_changed()
        )
        Tooltip(self.trace_mode_combo,
                "Trace accumulation:\n"
                "  Live — show every recompute\n"
                "  Average — RMS average of N spectra (lower noise)\n"
                "  Max-hold — keep maximum per bin (catches transients)\n"
                "  Min-hold — keep minimum per bin (reveals constant tones)")

        ttk.Label(row4, text="N avg:").pack(side=tk.LEFT)
        self.avg_n_combo = ttk.Combobox(
            row4, textvariable=self.avg_n,
            values=[2, 4, 8, 16, 32, 64, 128],
            state="readonly", width=5,
        )
        self.avg_n_combo.pack(side=tk.LEFT, padx=(4, 8))
        self.avg_n_combo.bind(
            "<<ComboboxSelected>>", lambda e: self._on_avg_n_changed()
        )
        Tooltip(self.avg_n_combo,
                "Number of spectra to average (RMS averaging)\n"
                "Larger N → less noise but slower response")

        self.reset_trace_btn = ttk.Button(
            row4, text="Reset trace", command=self._reset_trace_buffers
        )
        self.reset_trace_btn.pack(side=tk.LEFT, padx=(0, 14))
        Tooltip(self.reset_trace_btn,
                "Clear the averaging / max-hold / min-hold accumulators")

        ttk.Separator(row4, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=4)

        self.harmonics_check = ttk.Checkbutton(
            row4, text="Show harmonics",
            variable=self.show_harmonics,
            command=lambda: self._on_harmonics_toggle()
        )
        self.harmonics_check.pack(side=tk.LEFT, padx=(8, 4))
        Tooltip(self.harmonics_check,
                "Detect the fundamental (largest peak) and mark its harmonics.\n"
                "THD is also computed and displayed on the right.")

        ttk.Label(row4, text="N:").pack(side=tk.LEFT)
        self.n_harm_combo = ttk.Combobox(
            row4, textvariable=self.n_harmonics,
            values=[3, 5, 7, 9, 11, 15, 21],
            state="readonly", width=4,
        )
        self.n_harm_combo.pack(side=tk.LEFT, padx=(4, 14))
        self.n_harm_combo.bind(
            "<<ComboboxSelected>>",
            lambda e: self._on_n_harmonics_changed()
        )
        Tooltip(self.n_harm_combo,
                "How many harmonics to mark (including fundamental).\n"
                "Standard THD uses 5-9.")

        self.thd_label = tk.Label(
            row4, text="",
            fg="#ffcc00", bg=theme.COLOR_BG,
            font=("Consolas", 10, "bold")
        )
        self.thd_label.pack(side=tk.RIGHT, padx=10)
        Tooltip(self.thd_label,
                "THD: Total Harmonic Distortion\n"
                "Computed as sqrt(Σ V_h²) / V_1 across the marked harmonics.\n"
                "Lower is better (clean sine ≈ -60 dB or 0.1%).")

        # --- Plot ---
        plot_frame = ttk.Frame(self.win)
        plot_frame.pack(fill=tk.BOTH, expand=True)

        # Colores del plot según el tema activo (PLOT_BG, PLOT_GRID,
        # PLOT_TEXT). En vez de hardcodear "black"/"white" leemos del tema.
        plot_theme = THEMES[theme.CURRENT_THEME_NAME]
        plot_bg   = plot_theme["PLOT_BG"]
        plot_text = plot_theme["PLOT_TEXT"]

        self.fig, self.ax = plt.subplots(figsize=(10, 5))
        self.fig.patch.set_facecolor(plot_bg)
        self.ax.set_facecolor(plot_bg)

        # Label del cursor: texto sobre la figura (esquina inferior izquierda)
        self.cursor_label = self.fig.text(
            0.02, 0.02,
            "",
            ha="left",
            va="bottom",
            fontsize=10,
            color="yellow",
            family="monospace",
            bbox=dict(
                facecolor=plot_bg,
                edgecolor=theme.COLOR_BORDER,
                alpha=0.9,
                boxstyle="round,pad=0.3"
            )
        )

        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Conectar eventos de ratón para drag vertical
        self.canvas.mpl_connect("button_press_event",   self._on_mouse_press)
        self.canvas.mpl_connect("button_release_event", self._on_mouse_release)
        self.canvas.mpl_connect("motion_notify_event",  self._on_mouse_move)

    # -----------------------------
    # Lógica de parámetros
    # -----------------------------
    def _compute_rbw(self):
        """RBW = ENBW * fs / N  donde N = W * fs."""
        W_s = self.W.get() / 1000.0
        N = max(2, int(round(W_s * self.fs)))
        enbw = FFT_WINDOWS[self.window_name.get()]["enbw"]
        return enbw * self.fs / N

    def _compute_W_from_RBW(self):
        """W (ms) = ENBW / RBW * 1000."""
        rbw = max(self.RBW.get(), 1e-9)
        enbw = FFT_WINDOWS[self.window_name.get()]["enbw"]
        N = enbw * self.fs / rbw
        W_s = N / self.fs
        return W_s * 1000.0

    # -----------------------------
    # Sincronización displays <-> valores numéricos
    # -----------------------------
    def _sync_fft_displays(self):
        """Actualiza todos los StringVar de display desde los numéricos."""
        self.W_display.set(format_si(self.W.get() / 1000.0, "s"))
        self.RBW_display.set(format_si(self.RBW.get(), "Hz"))
        self.f_start_display.set(format_si(self.f_start.get(), "Hz"))
        self.f_stop_display.set(format_si(self.f_stop.get(), "Hz"))
        self.f_center_display.set(format_si(self.f_center.get(), "Hz"))
        self.f_span_display.set(format_si(self.f_span.get(), "Hz"))

    # -----------------------------
    # Handlers de spinbox: flechas (incrementar/decrementar) + tipear
    # -----------------------------
    def _spin_direction(self, display_var, current_value, unit):
        """Deduce si el spinbox fue pulsado ↑ o ↓ comparando el texto nuevo
        con el valor numérico actual. Devuelve 'up', 'down' o None."""
        parsed = parse_si(display_var.get(), unit)
        if parsed is None:
            return None
        if parsed > current_value * 1.0000001:
            return "up"
        if parsed < current_value * 0.9999999:
            return "down"
        return None

    def _step_value_decade(self, value, direction):
        """Avanza un valor por los pasos 1-2-5 × 10^n (estilo osciloscopio).
        Si el valor está fuera de la escalera, se acopla al más cercano primero."""
        if value <= 0:
            return max(1e-12, 1.0 if direction == "up" else value / 2)
        exp = int(np.floor(np.log10(value)))
        base = 10 ** exp
        mant = value / base
        # Elegir siguiente mantisa según dirección
        ladder = [1, 2, 5, 10]
        if direction == "up":
            # Buscar el primer escalón estrictamente mayor
            for m in ladder:
                if m > mant * 1.0001:
                    return m * base
            return 10 * base  # si ya estaba en 10, sube una década
        else:
            for m in reversed(ladder):
                if m < mant * 0.9999:
                    return m * base
            # Estaba en 1, bajar una década al 5
            return 5 * base / 10

    def _on_W_spin_arrow(self):
        """↑/↓ del spinbox W. Paso 1-2-5 en segundos sobre W en ms."""
        current_W_s = self.W.get() / 1000.0
        direction = self._spin_direction(self.W_display, current_W_s, "s")
        if direction is None:
            # Texto inválido: restaurar
            self.W_display.set(format_si(current_W_s, "s"))
            return
        new_W_s = self._step_value_decade(current_W_s, direction)
        # Limitar al tramo disponible
        max_W_s = self.time[-1] - self.t_segment_start
        new_W_s = min(new_W_s, max_W_s)
        if new_W_s <= 0:
            new_W_s = current_W_s
        self.W.set(new_W_s * 1000.0)
        self.RBW.set(self._compute_rbw())
        self._sync_fft_displays()
        self._recompute()

    def _on_W_typed(self):
        val_s = parse_si(self.W_display.get(), "s")
        if val_s is None or val_s <= 0:
            self.W_display.set(format_si(self.W.get() / 1000.0, "s"))
            return
        max_W_s = self.time[-1] - self.t_segment_start
        val_s = min(val_s, max_W_s)
        self.W.set(val_s * 1000.0)
        self.RBW.set(self._compute_rbw())
        self._sync_fft_displays()
        self._recompute()

    def _on_RBW_spin_arrow(self):
        current = self.RBW.get()
        direction = self._spin_direction(self.RBW_display, current, "Hz")
        if direction is None:
            self.RBW_display.set(format_si(current, "Hz"))
            return
        # Intuición: ↑ pedir MENOS resolución (RBW mayor) NO es lo típico
        # en un SA. Aquí respetamos la semántica matemática: ↑ = RBW mayor.
        new_rbw = self._step_value_decade(current, direction)
        self.RBW.set(new_rbw)
        # Recalcular W coherente
        new_W_ms = self._compute_W_from_RBW()
        max_W_ms = (self.time[-1] - self.t_segment_start) * 1000.0
        new_W_ms = min(new_W_ms, max_W_ms)
        self.W.set(new_W_ms)
        # RBW real tras la limitación
        self.RBW.set(self._compute_rbw())
        self._sync_fft_displays()
        self._recompute()

    def _on_RBW_typed(self):
        val = parse_si(self.RBW_display.get(), "Hz")
        if val is None or val <= 0:
            self.RBW_display.set(format_si(self.RBW.get(), "Hz"))
            return
        self.RBW.set(val)
        new_W_ms = self._compute_W_from_RBW()
        max_W_ms = (self.time[-1] - self.t_segment_start) * 1000.0
        new_W_ms = min(new_W_ms, max_W_ms)
        self.W.set(new_W_ms)
        self.RBW.set(self._compute_rbw())
        self._sync_fft_displays()
        self._recompute()

    def _on_freq_spin_arrow(self, which):
        """which ∈ {'start','stop','center','span'}."""
        mapping = {
            "start":  (self.f_start,  self.f_start_display),
            "stop":   (self.f_stop,   self.f_stop_display),
            "center": (self.f_center, self.f_center_display),
            "span":   (self.f_span,   self.f_span_display),
        }
        var, disp = mapping[which]
        current = var.get()
        direction = self._spin_direction(disp, current, "Hz")
        if direction is None:
            disp.set(format_si(current, "Hz"))
            return
        new_val = self._step_value_decade(max(current, 1e-12), direction)
        if which == "start":
            new_val = max(0.0, new_val)
        var.set(new_val)

        # Propagar el cambio a la lógica existente
        if which in ("start", "stop"):
            self._on_start_stop_changed()
        else:
            self._on_center_span_changed()
        self._sync_fft_displays()

    def _on_freq_typed(self, which):
        mapping = {
            "start":  (self.f_start,  self.f_start_display),
            "stop":   (self.f_stop,   self.f_stop_display),
            "center": (self.f_center, self.f_center_display),
            "span":   (self.f_span,   self.f_span_display),
        }
        var, disp = mapping[which]
        val = parse_si(disp.get(), "Hz")
        if val is None:
            disp.set(format_si(var.get(), "Hz"))
            return
        if which == "start":
            val = max(0.0, val)
        var.set(val)
        if which in ("start", "stop"):
            self._on_start_stop_changed()
        else:
            self._on_center_span_changed()
        self._sync_fft_displays()

    def _on_W_changed(self):
        # Reajustar el RBW para que sea coherente
        try:
            self.RBW.set(round(self._compute_rbw(), 6))
        except Exception:
            return
        # Cambia el tamaño de los bins → invalida acumuladores
        self._reset_trace_buffers(silent=True)
        self._recompute()

    def _on_RBW_changed(self):
        # Recalcular W para que coincida con el RBW pedido
        try:
            new_W = self._compute_W_from_RBW()
        except Exception:
            return
        # Limitar W al tramo disponible del CSV
        max_W_ms = (self.time[-1] - self.t_segment_start) * 1000.0
        new_W = min(new_W, max_W_ms)
        self.W.set(round(new_W, 6))
        # Reajustar RBW al valor real que se va a aplicar (puede diferir por la limitación)
        self.RBW.set(round(self._compute_rbw(), 6))
        self._sync_fft_displays()
        self._reset_trace_buffers(silent=True)
        self._recompute()

    def _on_source_changed(self):
        """Cambio de canal fuente → recomputa la FFT sobre el nuevo canal."""
        name = self.source_var.get()
        for i, ch in enumerate(self.scope.channels):
            if ch["name"] == name:
                self.source_idx = i
                _, self.voltage = merge_repeated_times(self.scope.time,
                                                       ch["voltage"])
                break
        # Canal distinto → acumuladores ya no son válidos
        self._reset_trace_buffers(silent=True)
        self._recompute()

    def _on_window_changed(self):
        # Al cambiar la ventana, RBW cambia aunque W sea el mismo
        self.RBW.set(round(self._compute_rbw(), 6))
        self._sync_fft_displays()
        # Cambia la corrección ENBW → bins no comparables con los previos
        self._reset_trace_buffers(silent=True)
        self._save_fft_config()
        self._recompute()

    # -----------------------------
    # Lógica de zoom de frecuencia
    # -----------------------------
    def _on_start_stop_changed(self):
        if self._updating_freq:
            return
        self._updating_freq = True
        try:
            start = max(0.0, self.f_start.get())
            stop  = max(start + 1e-9, self.f_stop.get())
            self.f_start.set(start)
            self.f_stop.set(stop)
            self.f_center.set((start + stop) / 2.0)
            self.f_span.set(stop - start)
        finally:
            self._updating_freq = False
        self._apply_zoom()

    def _on_center_span_changed(self):
        if self._updating_freq:
            return
        self._updating_freq = True
        try:
            center = max(0.0, self.f_center.get())
            span   = max(1e-9, self.f_span.get())
            start = max(0.0, center - span / 2.0)
            stop  = center + span / 2.0
            self.f_start.set(start)
            self.f_stop.set(stop)
            # Si center era menor que span/2, se ha recortado; ajustamos
            self.f_center.set((start + stop) / 2.0)
        finally:
            self._updating_freq = False
        self._apply_zoom()

    def _apply_zoom(self):
        """Aplica el zoom de frecuencia (solo cambia xlim, no recalcula)."""
        if self.freq is None:
            return
        self.ax.set_xlim(self.f_start.get(), self.f_stop.get())
        self._update_xticks()
        self.canvas.draw_idle()

    def _reset_zoom(self):
        # Reset X + Y offset
        self.y_offset = 0.0
        self._updating_freq = True
        self.f_start.set(0.0)
        self.f_stop.set(self.fs / 2.0)
        self.f_center.set(self.fs / 4.0)
        self.f_span.set(self.fs / 2.0)
        self._updating_freq = False
        self._sync_fft_displays()
        self._redraw()

    def _full_span(self):
        """Mantiene el Y actual, pero vuelve a todo el rango en X."""
        self._updating_freq = True
        self.f_start.set(0.0)
        self.f_stop.set(self.fs / 2.0)
        self.f_center.set(self.fs / 4.0)
        self.f_span.set(self.fs / 2.0)
        self._updating_freq = False
        self._sync_fft_displays()
        self._apply_zoom()

    # -----------------------------
    # Cálculo y dibujo
    # -----------------------------
    def _recompute(self):
        """Extrae el segmento de datos, aplica ventana, calcula FFT en Vrms."""
        # Longitud en muestras
        W_s = self.W.get() / 1000.0
        N = max(2, int(round(W_s * self.fs)))

        # Tomar N muestras empezando en t_segment_start
        i0 = int(np.searchsorted(self.time, self.t_segment_start))
        i1 = i0 + N
        if i1 > len(self.voltage):
            i1 = len(self.voltage)
            N = i1 - i0
            if N < 2:
                return

        v = self.voltage[i0:i1].astype(float)
        v = v - np.mean(v)   # quitar DC

        # Aplicar ventana
        v_win, cg = apply_window(v, self.window_name.get())

        # FFT
        spectrum = np.fft.rfft(v_win)
        freq = np.fft.rfftfreq(N, d=self.dt)

        # Magnitud → Vrms, compensando la ganancia coherente de la ventana
        mag = np.abs(spectrum) * 2.0 / (N * np.sqrt(2)) / cg
        # Corrección DC y Nyquist
        mag[0] /= 2.0
        if N % 2 == 0:
            mag[-1] /= 2.0

        self.freq = freq
        self.mag_vrms = mag

        # Actualizar acumuladores para Average / Max-hold / Min-hold.
        # Si el tamaño no coincide (cambió N, ventana, etc.), reseteamos.
        self._update_trace_buffers(mag)

        # Si start/stop no se han tocado, poner por defecto todo el rango
        if self.f_stop.get() > freq[-1]:
            self._updating_freq = True
            self.f_stop.set(freq[-1])
            self.f_span.set(freq[-1] - self.f_start.get())
            self.f_center.set((self.f_start.get() + self.f_stop.get()) / 2.0)
            self._updating_freq = False

        self._sync_fft_displays()
        self._redraw()

    def _update_trace_buffers(self, mag):
        """Actualiza los acumuladores Avg / Max-hold / Min-hold con el
        espectro recién calculado `mag` (en Vrms).

        Si el tamaño de mag difiere del previo (cambio de N, RBW, ventana...),
        los buffers se reinician — no tendría sentido promediar bins distintos.
        """
        if self._avg_sum_sq is not None and self._avg_sum_sq.shape != mag.shape:
            self._reset_trace_buffers(silent=True)

        # Average (RMS): acumular |X|^2, después al usar dividimos por N y sqrt.
        if self._avg_sum_sq is None:
            self._avg_sum_sq = mag.astype(np.float64) ** 2
            self._avg_count = 1
        else:
            target_n = max(1, int(self.avg_n.get()))
            if self._avg_count < target_n:
                self._avg_sum_sq += mag.astype(np.float64) ** 2
                self._avg_count += 1
            else:
                # Media exponencial ligera para que siga respondiendo si la
                # señal cambia tras llegar a N. Pongo alpha = 1/N.
                alpha = 1.0 / target_n
                self._avg_sum_sq = (1 - alpha) * self._avg_sum_sq + alpha * (mag.astype(np.float64) ** 2) * target_n
                # OJO: aquí mantengo la "energía" como si fueran N muestras
                # para que el `/ count` luego dé media razonable.

        # Max-hold
        if self._max_hold is None:
            self._max_hold = mag.copy()
        else:
            np.maximum(self._max_hold, mag, out=self._max_hold)

        # Min-hold (ignora ceros para no quedar pegado a cero por overflow numérico)
        if self._min_hold is None:
            self._min_hold = mag.copy()
        else:
            np.minimum(self._min_hold, mag, out=self._min_hold)

    def _reset_trace_buffers(self, silent=False):
        """Limpia los acumuladores. Se llama:
        - Al hacer click en "Reset trace".
        - Automáticamente cuando cambia algo estructural (W, RBW, ventana,
          canal fuente) que invalida la comparabilidad de los espectros.
        - Por el menú contextual (botón derecho)."""
        self._avg_sum_sq = None
        self._avg_count = 0
        self._max_hold = None
        self._min_hold = None
        if not silent:
            # Solo redraw si la app está construida (evita problemas en init)
            if hasattr(self, "canvas"):
                self._redraw()

    def _on_trace_mode_changed(self):
        """Llamado al cambiar el combo Trace. Solo redibuja — los buffers
        siguen llenándose en background."""
        self._save_fft_config()
        self._redraw()

    def _on_avg_n_changed(self):
        """Cambiar N reinicia el acumulador de average (los promedios actuales
        no son válidos para el nuevo N)."""
        self._avg_sum_sq = None
        self._avg_count = 0
        self._save_fft_config()
        self._redraw()

    def _on_yscale_changed(self):
        """Cambia la escala vertical (Linear, dBV, dBm, dBµV) y persiste."""
        self._save_fft_config()
        self._redraw()

    def _on_harmonics_toggle(self):
        """Activa/desactiva el marcado de armónicos y persiste."""
        self._save_fft_config()
        self._redraw()

    def _on_n_harmonics_changed(self):
        """Cambia el número de armónicos a mostrar y persiste."""
        self._save_fft_config()
        self._redraw()

    def _get_display_trace(self):
        """Devuelve el array Vrms que toca dibujar según trace_mode.
        Cae a Live si el acumulador correspondiente no está listo."""
        mode = self.trace_mode.get()
        if mode == "Average" and self._avg_sum_sq is not None and self._avg_count > 0:
            return np.sqrt(self._avg_sum_sq / self._avg_count)
        if mode == "Max-hold" and self._max_hold is not None:
            return self._max_hold
        if mode == "Min-hold" and self._min_hold is not None:
            return self._min_hold
        return self.mag_vrms

    # -----------------------------
    # Detección de armónicos y THD
    # -----------------------------
    def _detect_fundamental(self, freq, mag):
        """Encuentra el bin de máxima magnitud dentro del rango visible
        (excluyendo DC). Devuelve (f0, idx) o (None, None).

        Restringimos a la ventana visible para no detectar armónicos como
        fundamental si el usuario hace zoom alrededor del armónico real.
        """
        if freq is None or len(freq) < 3:
            return None, None
        f_lo = max(self.f_start.get(), freq[1])  # excluir DC
        f_hi = self.f_stop.get()
        if f_hi <= f_lo:
            return None, None
        mask = (freq >= f_lo) & (freq <= f_hi)
        if not np.any(mask):
            return None, None
        idxs = np.where(mask)[0]
        sub = mag[idxs]
        i_local = int(np.argmax(sub))
        idx_global = int(idxs[i_local])
        return float(freq[idx_global]), idx_global

    def _amplitude_at(self, freq, mag, f_target, search_bins=2):
        """Devuelve la magnitud (Vrms) del pico cerca de f_target. Busca el
        máximo local en una ventana de ±search_bins alrededor del bin más
        cercano — robustece frente a leakage / pequeñas desviaciones del
        múltiplo exacto."""
        if freq is None or f_target <= 0 or f_target > freq[-1]:
            return 0.0
        # Encontrar el bin más cercano
        idx_center = int(np.argmin(np.abs(freq - f_target)))
        lo = max(0, idx_center - search_bins)
        hi = min(len(mag), idx_center + search_bins + 1)
        if hi <= lo:
            return 0.0
        return float(np.max(mag[lo:hi]))

    def _compute_thd(self, freq, mag, f0, n_harmonics):
        """Calcula THD a partir del fundamental f0 y los siguientes
        n_harmonics-1 armónicos (2f0, 3f0, ...).

        Devuelve dict {pct, db, amplitudes} con:
          pct: THD en porcentaje
          db:  THD en dB (20·log10(THD_ratio))
          amplitudes: lista [(k, freq_k, vrms_k)] para todos los k=1..N

        Si V_1 ≈ 0 o no hay armónicos válidos, devuelve None en pct/db.
        """
        amplitudes = []
        f_nyquist = freq[-1]
        for k in range(1, n_harmonics + 1):
            fk = f0 * k
            if fk > f_nyquist:
                break
            vk = self._amplitude_at(freq, mag, fk)
            amplitudes.append((k, fk, vk))

        if not amplitudes or amplitudes[0][2] <= 1e-15:
            return {"pct": None, "db": None, "amplitudes": amplitudes}

        v1 = amplitudes[0][2]
        sum_sq = sum(v ** 2 for _k, _f, v in amplitudes[1:])
        if sum_sq <= 0:
            return {"pct": 0.0, "db": -np.inf, "amplitudes": amplitudes}
        thd_ratio = np.sqrt(sum_sq) / v1
        pct = thd_ratio * 100.0
        db = 20.0 * np.log10(thd_ratio)
        return {"pct": pct, "db": db, "amplitudes": amplitudes}

    def _draw_harmonics(self, freq, mag_display):
        """Dibuja líneas verticales y etiquetas para los armónicos detectados.

        Solo dibuja si `show_harmonics` está activo y se ha podido detectar
        un fundamental. Actualiza self._f0_detected, self._thd_pct, self._thd_db.

        Args:
            freq: array de frecuencias
            mag_display: array Vrms del modo de traza visible (para que los
                         armónicos se midan sobre la traza realmente mostrada,
                         no sobre el último Live).
        """
        # Limpiar artists previos
        for a in self._harmonic_artists:
            try:
                a.remove()
            except Exception:
                pass
        self._harmonic_artists = []
        self._f0_detected = None
        self._thd_pct = None
        self._thd_db = None

        if not self.show_harmonics.get():
            self.thd_label.config(text="")
            return

        f0, idx0 = self._detect_fundamental(freq, mag_display)
        if f0 is None:
            self.thd_label.config(text="THD: —")
            return
        self._f0_detected = f0

        n_h = max(2, int(self.n_harmonics.get()))
        thd = self._compute_thd(freq, mag_display, f0, n_h)
        self._thd_pct = thd["pct"]
        self._thd_db = thd["db"]
        amplitudes = thd["amplitudes"]

        # Color de las líneas: contrasta tanto en dark como en light
        line_color = "#ff9933"
        text_color = "#ffcc66"
        # Convertir a unidad de pantalla actual
        y_unit_func = lambda v_arr: vrms_to_scale(v_arr, self.y_scale.get())

        # Para colocar etiquetas: usar el límite Y actual
        y_lo, y_hi = self.ax.get_ylim()
        y_top = y_hi - 0.04 * (y_hi - y_lo)

        for k, fk, vk in amplitudes:
            if vk <= 1e-15:
                continue
            # Línea vertical
            ln = self.ax.axvline(
                fk, color=line_color,
                linestyle=(0, (4, 3)),
                linewidth=1.0 if k == 1 else 0.7,
                alpha=0.6 if k > 1 else 0.85,
                zorder=2,
            )
            self._harmonic_artists.append(ln)

            # Etiqueta arriba: "f0" para el primero, "2H", "3H"... para el resto
            tag = "f₀" if k == 1 else f"{k}H"
            txt = self.ax.text(
                fk, y_top, tag,
                color=text_color,
                fontsize=8 if k > 1 else 9,
                fontweight="bold" if k == 1 else "normal",
                ha="center", va="top",
                zorder=6,
                bbox=dict(facecolor="#000000", edgecolor="none",
                          alpha=0.5, boxstyle="round,pad=0.15"),
            )
            self._harmonic_artists.append(txt)

        # THD label arriba a la derecha
        if self._thd_pct is None:
            self.thd_label.config(text=f"f₀={format_si(f0, 'Hz')}  THD: —")
        else:
            self.thd_label.config(
                text=f"f₀={format_si(f0, 'Hz')}  "
                     f"THD: {self._thd_pct:.3f}%  ({self._thd_db:+.1f} dB)"
            )

    def _redraw(self):
        """Dibuja la traza con el estilo y escala actuales."""
        if self.freq is None:
            return

        self.ax.clear()
        # ax.clear() elimina los artists → invalidamos las referencias
        self._cursor_line = None
        self._cursor_marker = None
        # Los armónicos también dependen del ax — al limpiarse el ax sus
        # artists quedan inválidos. Reseteamos la lista (re-poblamos abajo).
        self._harmonic_artists = []

        # Colores del plot según el tema activo. Se releen en cada redraw
        # para que un cambio de tema en runtime se refleje al siguiente
        # repaint del FFT.
        plot_theme = THEMES[theme.CURRENT_THEME_NAME]
        plot_bg   = plot_theme["PLOT_BG"]
        plot_grid = plot_theme["PLOT_GRID"]
        plot_text = plot_theme["PLOT_TEXT"]

        self.fig.patch.set_facecolor(plot_bg)
        self.ax.set_facecolor(plot_bg)

        # Trace: live, average, max-hold, min-hold
        mag_display = self._get_display_trace()
        y = vrms_to_scale(mag_display, self.y_scale.get())
        # Color del canal seleccionado
        trace_color = "cyan"
        if 0 <= self.source_idx < len(self.scope.channels):
            trace_color = theme.readable_on(
                self.scope.channels[self.source_idx]["color"],
                to_hex(self.ax.get_facecolor()), theme.GRAPHIC_MIN)
        self.ax.plot(self.freq, y, color=trace_color, linewidth=0.9)

        # En Average / Max-hold / Min-hold también superponemos el Live
        # en gris tenue como referencia (útil para ver dónde "vive" cada bin).
        if self.trace_mode.get() != "Live":
            y_live = vrms_to_scale(self.mag_vrms, self.y_scale.get())
            self.ax.plot(self.freq, y_live, color="#666666",
                         linewidth=0.6, alpha=0.5, zorder=1)

        self.ax.grid(True, color=plot_grid, linewidth=0.6)
        for spine in self.ax.spines.values():
            spine.set_color(theme.COLOR_BORDER)
        self.ax.tick_params(colors=plot_text)

        self.ax.set_xlabel("Frequency", color=plot_text)
        self.ax.set_ylabel(scale_unit(self.y_scale.get()), color=plot_text)
        for lbl in self.ax.get_xticklabels() + self.ax.get_yticklabels():
            lbl.set_color(plot_text)

        # Rango X
        self.ax.set_xlim(self.f_start.get(), self.f_stop.get())
        self._update_xticks()

        # Rango Y: autoscale y luego aplicar offset
        self._autoscale_y_axis()
        y_lo, y_hi = self.ax.get_ylim()
        self.ax.set_ylim(y_lo + self.y_offset, y_hi + self.y_offset)

        # Título informativo
        mode_tag = ""
        if self.trace_mode.get() == "Average":
            mode_tag = f"   [Avg {self._avg_count}/{self.avg_n.get()}]"
        elif self.trace_mode.get() in ("Max-hold", "Min-hold"):
            mode_tag = f"   [{self.trace_mode.get()}]"
        self.info_label.config(
            text=(
                f"fs = {format_si(self.fs, 'Hz')}   "
                f"N = {len(self.mag_vrms)*2 - 2}   "
                f"ENBW = {FFT_WINDOWS[self.window_name.get()]['enbw']:.4f} bins"
                f"{mode_tag}"
            )
        )

        # Armónicos y THD (sobre el trace mostrado)
        self._draw_harmonics(self.freq, mag_display)

        # Repintar el cursor si estaba activo
        if self.cursor_enabled.get() and self.cursor_freq is not None:
            self._update_cursor_artists()

        self.canvas.draw_idle()

    def _update_xticks(self):
        """Ticks con unidades SI, calculados sobre el rango visible.
        Usa valores 'limpios' (1, 2, 5 · 10^n) y ~6 divisiones para no aglomerar."""
        xlo, xhi = self.ax.get_xlim()
        span = xhi - xlo
        if span <= 0:
            return

        # Paso "nice": potencia de 10 × (1, 2 o 5), apuntando a ~6 divisiones
        target_divs = 6
        raw = span / target_divs
        exp = np.floor(np.log10(raw))
        base = 10 ** exp
        frac = raw / base
        if frac <= 1.5:
            nice = 1 * base
        elif frac <= 3.5:
            nice = 2 * base
        elif frac <= 7.5:
            nice = 5 * base
        else:
            nice = 10 * base

        start = np.ceil(xlo / nice) * nice
        xticks = np.arange(start, xhi + nice * 0.5, nice)
        # Incluir xlo al principio si no está, para que el eje arranque en el valor exacto
        if len(xticks) == 0 or xticks[0] > xlo + nice * 0.01:
            xticks = np.concatenate(([xlo], xticks))

        self.ax.set_xticks(xticks)
        self.ax.set_xticklabels([format_si(x, "Hz") for x in xticks])
        plot_text = THEMES[theme.CURRENT_THEME_NAME]["PLOT_TEXT"]
        for lbl in self.ax.get_xticklabels():
            lbl.set_color(plot_text)

    def _autoscale_y_axis(self):
        """Autoscale Y en la banda visible."""
        if self.freq is None:
            return
        xlo, xhi = self.f_start.get(), self.f_stop.get()
        mask = (self.freq >= xlo) & (self.freq <= xhi)
        if not np.any(mask):
            return
        y = vrms_to_scale(self.mag_vrms[mask], self.y_scale.get())
        y_min, y_max = float(np.min(y)), float(np.max(y))
        if y_min == y_max:
            y_min -= 1.0
            y_max += 1.0
        margin = (y_max - y_min) * 0.08
        self.ax.set_ylim(y_min - margin, y_max + margin)

    def _auto_scale_y(self):
        """Botón explícito: resetea el offset y hace autoscale."""
        self.y_offset = 0.0
        self._redraw()

    # -----------------------------
    # Drag vertical (solo offset) + cursor vertical
    # -----------------------------
    def _on_mouse_press(self, event):
        if event.inaxes != self.ax:
            return

        # --- Cursor vertical (click izquierdo cuando está activo) ---
        if self.cursor_enabled.get() and event.button == 1:
            if event.xdata is None:
                return
            # Al hacer click, enganchar al pico local cercano
            f = self._snap_to_local_peak(event.xdata)
            self.cursor_freq = f
            self._cursor_dragging = True
            self._update_cursor_artists()
            return

        # --- Botón derecho: menú contextual (cuando cursor NO está activo) ---
        # Con cursor activo, el derecho sigue siendo el botón de pan vertical
        # (mantiene la convención previa). Sin cursor, el derecho abre menú
        # con Save image / Reset trace / ... — patrón estándar.
        if event.button == 3 and not self.cursor_enabled.get():
            self._show_context_menu(event)
            return

        # --- Pan vertical ---
        # - Si el cursor está activo, pan con botón derecho (3)
        # - Si no, pan con botón izquierdo (1) como siempre
        pan_button = 3 if self.cursor_enabled.get() else 1
        if event.button != pan_button:
            return
        self._dragging = True
        self._drag_start_y = event.ydata
        self._drag_start_offset = self.y_offset

    def _show_context_menu(self, event):
        """Menú contextual del plot de la FFT. Acciones: guardar imagen,
        resetear acumuladores."""
        menu = tk.Menu(self.win, tearoff=0, bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
                       activebackground=theme.COLOR_ACCENT, activeforeground=theme.COLOR_TEXT_ON_ACCENT,
                       bd=0)

        menu.add_command(label="Save image as PNG…",
                         command=self._save_image_dialog)
        menu.add_command(label="Save image as SVG…",
                         command=lambda: self._save_image_dialog(fmt="svg"))
        menu.add_separator()
        menu.add_command(label="Reset trace accumulators",
                         command=self._reset_trace_buffers,
                         state="normal" if (
                             self._avg_sum_sq is not None or
                             self._max_hold is not None
                         ) else "disabled")
        menu.add_separator()
        menu.add_command(label="Copy peak data to clipboard",
                         command=self._copy_peak_to_clipboard,
                         state="normal" if self._f0_detected is not None else "disabled")

        # Posicionar el menú donde está el cursor del ratón
        try:
            x_root = self.win.winfo_pointerx()
            y_root = self.win.winfo_pointery()
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    def _save_image_dialog(self, fmt="png"):
        """Guarda la imagen actual del plot FFT. fmt: 'png' o 'svg'."""
        from tkinter import filedialog
        defext = f".{fmt}"
        filetypes = [(f"{fmt.upper()} image", f"*{defext}"), ("All files", "*.*")]
        # Sugerencia de nombre: incluir canal y unidad
        ch_name = ""
        if 0 <= self.source_idx < len(self.scope.channels):
            ch_name = self.scope.channels[self.source_idx]["name"]
        default = f"fft_{ch_name}_{self.window_name.get()}{defext}"
        path = filedialog.asksaveasfilename(
            parent=self.win,
            title=f"Save FFT image as {fmt.upper()}",
            defaultextension=defext,
            initialfile=default,
            filetypes=filetypes,
        )
        if not path:
            return
        try:
            # Mantener el fondo negro al guardar (por defecto matplotlib usa
            # facecolor del rcparam, no de la fig).
            self.fig.savefig(path, dpi=150,
                             facecolor=self.fig.get_facecolor(),
                             edgecolor="none")
        except Exception as e:
            try:
                self.scope._show_toast(f"Save failed: {e}")
            except Exception:
                pass
            return
        try:
            self.scope._show_toast(f"Saved: {path}")
        except Exception:
            pass

    def _copy_peak_to_clipboard(self):
        """Copia al portapapeles la información de la fundamental y armónicos."""
        if self._f0_detected is None:
            return
        lines = [f"FFT peak data — {self.window_name.get()} window"]
        lines.append(f"Fundamental f0 = {format_si(self._f0_detected, 'Hz')}")
        if self._thd_pct is not None:
            lines.append(f"THD = {self._thd_pct:.4f} %  ({self._thd_db:+.2f} dB)")
        # Las amplitudes están en _draw_harmonics; las recalculo aquí para
        # asegurar coherencia con lo dibujado.
        if self.freq is not None:
            mag_display = self._get_display_trace()
            thd = self._compute_thd(self.freq, mag_display,
                                    self._f0_detected,
                                    max(2, int(self.n_harmonics.get())))
            # Unit del canal source (v1.29): refleja la unidad seleccionada.
            try:
                src_unit = self.scope.channels[self.source_idx].get("unit", DEFAULT_UNIT)
            except Exception:
                src_unit = DEFAULT_UNIT
            lines.append(f"Harmonic    Frequency     {src_unit}rms")
            for k, fk, vk in thd["amplitudes"]:
                tag = "f0" if k == 1 else f"{k}H"
                lines.append(f"  {tag:>3}    {format_si(fk, 'Hz'):>12}    "
                             f"{format_si(vk, src_unit):>10}")
        text = "\n".join(lines)
        try:
            self.win.clipboard_clear()
            self.win.clipboard_append(text)
            self.win.update()  # forzar que el clipboard se mantenga tras cerrar
            self.scope._show_toast("Peak data copied to clipboard")
        except Exception:
            pass

    def _on_mouse_release(self, event):
        # Si estábamos arrastrando el cursor, al soltar hacemos snap al pico
        if self._cursor_dragging and self.cursor_freq is not None:
            self.cursor_freq = self._snap_to_local_peak(self.cursor_freq)
            self._update_cursor_artists()
        self._dragging = False
        self._cursor_dragging = False

    def _on_mouse_move(self, event):
        if event.inaxes != self.ax:
            return

        # --- Cursor siendo arrastrado (sin snap, seguimiento libre) ---
        if self._cursor_dragging and event.xdata is not None:
            # Durante el drag, seguir al ratón; el snap se aplica solo al soltar
            self.cursor_freq = max(0.0, event.xdata)
            self._update_cursor_artists()
            return

        # --- Pan vertical ---
        if not self._dragging or event.ydata is None:
            return
        dy = event.ydata - self._drag_start_y
        self.y_offset = self._drag_start_offset - dy

        self._autoscale_y_axis()
        y_lo, y_hi = self.ax.get_ylim()
        self.ax.set_ylim(y_lo + self.y_offset, y_hi + self.y_offset)
        self.canvas.draw_idle()

    # -----------------------------
    # Cursor vertical: lógica
    # -----------------------------
    def _on_cursor_toggle(self):
        if self.cursor_enabled.get():
            # Primera activación: colocar en el pico máximo visible
            if self.cursor_freq is None:
                self._cursor_snap_to_max()
            else:
                self._update_cursor_artists()
        else:
            # Desactivar: borrar artists
            if self._cursor_line is not None:
                self._cursor_line.remove()
                self._cursor_line = None
            if self._cursor_marker is not None:
                self._cursor_marker.remove()
                self._cursor_marker = None
            self.cursor_label.set_text("")
            self.canvas.draw_idle()
        # Persistir estado del cursor (on/off) tras el toggle
        self._save_fft_config()

    def _snap_to_local_peak(self, f_click):
        """Devuelve la frecuencia del máximo local alrededor de f_click.
        Busca en una ventana de ±2% del span visible o, como mínimo, varios bins."""
        if self.freq is None or len(self.freq) < 2:
            return f_click

        xlo, xhi = self.f_start.get(), self.f_stop.get()
        span = max(xhi - xlo, self.freq[1] - self.freq[0])
        df = max(span * 0.02, (self.freq[1] - self.freq[0]) * 5)

        f_lo = max(0.0, f_click - df)
        f_hi = f_click + df
        mask = (self.freq >= f_lo) & (self.freq <= f_hi)
        if not np.any(mask):
            return f_click

        # Trabajamos en la escala Y seleccionada (así el "pico" coincide con el visual)
        y_region = vrms_to_scale(self.mag_vrms[mask], self.y_scale.get())
        freq_region = self.freq[mask]
        i_max = int(np.argmax(y_region))
        return float(freq_region[i_max])

    def _cursor_snap_to_max(self):
        """Salta el cursor al máximo global de la banda visible."""
        if self.freq is None or self.mag_vrms is None:
            return
        xlo, xhi = self.f_start.get(), self.f_stop.get()
        mask = (self.freq >= xlo) & (self.freq <= xhi)
        if not np.any(mask):
            return

        y_region = vrms_to_scale(self.mag_vrms[mask], self.y_scale.get())
        freq_region = self.freq[mask]
        i_max = int(np.argmax(y_region))
        self.cursor_freq = float(freq_region[i_max])

        # Si estaba desactivado, lo activamos para que se vea
        if not self.cursor_enabled.get():
            self.cursor_enabled.set(True)
        self._update_cursor_artists()

    def _cursor_value_at(self, f):
        """Devuelve (freq_real, vrms, y_scaled) del bin más próximo a f.
        None si no hay datos o f cae fuera."""
        if self.freq is None or self.mag_vrms is None or f is None:
            return None
        if f < self.freq[0] or f > self.freq[-1]:
            return None
        i = int(np.argmin(np.abs(self.freq - f)))
        f_real = float(self.freq[i])
        vrms = float(self.mag_vrms[i])
        y_sc = float(vrms_to_scale(np.array([vrms]), self.y_scale.get())[0])
        return f_real, vrms, y_sc

    def _update_cursor_artists(self):
        """Redibuja (o crea) la línea vertical y el marcador del cursor, y actualiza el label."""
        if not self.cursor_enabled.get() or self.cursor_freq is None:
            return

        data = self._cursor_value_at(self.cursor_freq)
        if data is None:
            return
        f_real, vrms, y_sc = data

        # Línea vertical
        if self._cursor_line is None:
            self._cursor_line = self.ax.axvline(
                f_real, color="yellow", linestyle=(0, (5, 5)),
                linewidth=1.0, alpha=0.9
            )
        else:
            self._cursor_line.set_xdata([f_real, f_real])

        # Marcador sobre la traza: X amarilla mediana, line width fino
        if self._cursor_marker is None:
            (self._cursor_marker,) = self.ax.plot(
                [f_real], [y_sc],
                marker="x", color="yellow",
                markersize=10, markeredgewidth=1.2
            )
        else:
            self._cursor_marker.set_data([f_real], [y_sc])

        unit = scale_unit(self.y_scale.get())
        self.cursor_label.set_text(
            f"f = {format_si(f_real, 'Hz')}    Level = {y_sc:.3f} {unit}    ({format_si(vrms, 'Vrms')})"
        )
        self.canvas.draw_idle()
