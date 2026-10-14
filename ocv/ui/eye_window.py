"""Eye diagram window."""
import matplotlib.pyplot as plt
import numpy as np
import tkinter as tk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from tkinter import ttk

from ocv.core.si_format import DEFAULT_UNIT, format_si, parse_si
from ocv.ui import theme
from ocv.ui.theme import THEMES
from ocv.ui.widgets import Tooltip


# =====================================================
# Eye Diagram
# =====================================================
class EyeDiagramWindow:
    """Diagrama de ojos: superpone N "slices" de la señal alineados por
    cruces del threshold para mostrar la apertura del ojo y permitir medir
    jitter, eye height, eye width, ISI…

    Parámetros expuestos en UI:
      - Source channel
      - UI duration (segundos por bit; sugerencia auto)
      - UIs per view (1, 2, 3 — 2 es el estándar)
      - Threshold (% entre min y max, o valor absoluto)
      - Slope (rising / falling / both)
      - Max traces (límite para no morir con señales de muchos millones de
        cruces)
      - Display mode (lines superpuestas / heatmap por densidad)

    Métricas reportadas (cuando hay slices suficientes):
      - Eye height (V): max bajo − min alto en el centro del UI
      - Eye width (s): horizontal del ojo a la altura del threshold
      - Jitter pk-pk (s): anchura del cruce a threshold
      - High level / Low level (V): top/base por histograma bimodal

    Notas de implementación:
    - "Cruce" = punto donde la señal pasa por el threshold con la pendiente
      pedida. Localizamos los cruces y usamos cada uno como t0 de un slice
      de duración UIs_per_view * UI.
    - Para mantener el plot fluido con muchos cruces, limitamos a
      `max_traces` (por defecto 500): tomamos una muestra uniforme.
    - Los slices se interpolan a una rejilla común de tiempo (relativa al
      cruce) para que las métricas sobre las trazas alineadas sean baratas.

    Filosofía: no se almacena un Line2D por slice (sería caro). En su lugar,
    creamos UN solo LineCollection con todos los segmentos — matplotlib lo
    renderiza mucho más rápido.
    """

    # Resolución temporal de la rejilla común (puntos por slice). 200 es un
    # buen compromiso entre fidelidad y velocidad: el ojo se ve bien y las
    # métricas tienen suficiente granularidad horizontal.
    SLICE_RESOLUTION = 200

    def __init__(self, scope):
        self.scope = scope
        self.time = scope.time

        # Canal de entrada (por defecto, el activo)
        self.source_idx = scope.active_channel_idx
        self.voltage = scope.channels[self.source_idx]["voltage"]

        # --- Parámetros (StringVar para ediciones SI) ---
        # Inicializamos UI con una estimación basada en el período medio
        # entre cruces por la mediana, si hay actividad.
        ui_guess = self._guess_ui()
        self.ui_duration = tk.DoubleVar(value=ui_guess)
        self.ui_display = tk.StringVar(value=format_si(ui_guess, "s"))

        self.uis_per_view = tk.IntVar(value=2)
        self.threshold_pct = tk.DoubleVar(value=50.0)   # 0..100
        self.slope = tk.StringVar(value="both")          # rising | falling | both
        self.max_traces = tk.IntVar(value=500)
        self.display_mode = tk.StringVar(value="Lines")  # Lines | Heatmap

        # Sources combo
        self.source_var = tk.StringVar(
            value=scope.channels[self.source_idx]["name"]
        )

        # Resultados computados
        self.slices = None              # array (n_slices, SLICE_RESOLUTION)
        self.slice_time = None          # array (SLICE_RESOLUTION,) relativo
        self.metrics = {}               # dict con eye_height, eye_width, jitter...

        # --- UI ---
        self.win = tk.Toplevel(scope.root)
        self.win.withdraw()  # anti-flash
        self.win.title("Eye diagram")
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)
        # Geometry PRIMERO (centra), chrome (transient) DESPUÉS (evita fantasma).
        # Centrado sobre la ventana principal (importante en multimonitor:
        # _center_dialog usa coords absolutas de root, no la pantalla
        # primaria). En lugar de geometry("1100x720") fijo.
        scope._center_dialog(self.win, 1100, 720)
        # Hereda icono de la ventana raíz y se marca transient para que en
        # Windows no aparezca como entrada propia en la taskbar.
        scope._apply_window_chrome(self.win)

        self._build_ui()
        self._recompute()

    # -----------------------------
    # Construcción de UI
    # -----------------------------
    def _build_ui(self):
        self.win.configure(bg=theme.COLOR_BG)

        ctrl = ttk.Frame(self.win, padding=10)
        ctrl.pack(side=tk.TOP, fill=tk.X)

        # Fila 1: Source, UI, UIs/view
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
        Tooltip(self.source_combo, "Channel to analyze")

        ttk.Label(row1, text="UI (bit time):").pack(side=tk.LEFT)
        self.ui_entry = ttk.Entry(
            row1, textvariable=self.ui_display, width=12
        )
        self.ui_entry.pack(side=tk.LEFT, padx=(4, 0))
        self.ui_entry.bind("<Return>",   lambda e: self._on_ui_typed())
        self.ui_entry.bind("<FocusOut>", lambda e: self._on_ui_typed())
        Tooltip(self.ui_entry,
                "Unit Interval — duration of one bit.\n"
                "Edit and press Enter. Examples: '10 us', '1 ms', '125 ns'.")

        # Botones de escala (1-2-5 series) — útil cuando no se sabe la UI
        # exacta y se quiere "barrer" escalas rápido hasta que el ojo se abra.
        ui_up = tk.Button(
            row1, text="⯅",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            activebackground=theme.COLOR_BG_HOVER,
            bd=1, relief="flat",
            font=("Segoe UI", 8, "bold"),
            padx=4, pady=0,
            highlightbackground=theme.COLOR_BORDER, highlightthickness=1,
            command=lambda: self._step_ui("up"),
        )
        ui_up.pack(side=tk.LEFT, padx=(2, 0))
        self.scope._add_hover_shadow(ui_up)
        Tooltip(ui_up, "Increase UI (1-2-5 series)")

        ui_dn = tk.Button(
            row1, text="⯆",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            activebackground=theme.COLOR_BG_HOVER,
            bd=1, relief="flat",
            font=("Segoe UI", 8, "bold"),
            padx=4, pady=0,
            highlightbackground=theme.COLOR_BORDER, highlightthickness=1,
            command=lambda: self._step_ui("down"),
        )
        ui_dn.pack(side=tk.LEFT, padx=(0, 4))
        self.scope._add_hover_shadow(ui_dn)
        Tooltip(ui_dn, "Decrease UI (1-2-5 series)")

        self.auto_btn = ttk.Button(row1, text="Auto", command=self._auto_ui)
        self.auto_btn.pack(side=tk.LEFT, padx=(0, 14))
        Tooltip(self.auto_btn,
                "Auto-estimate UI from the average period between threshold "
                "crossings of the signal")

        ttk.Label(row1, text="UIs/view:").pack(side=tk.LEFT)
        self.uis_combo = ttk.Combobox(
            row1, textvariable=self.uis_per_view,
            values=[1, 2, 3], state="readonly", width=4,
        )
        self.uis_combo.pack(side=tk.LEFT, padx=(4, 14))
        self.uis_combo.bind("<<ComboboxSelected>>",
                            lambda e: self._recompute())
        Tooltip(self.uis_combo,
                "How many UIs to show per slice. 2 is the standard\n"
                "(shows the full bit eye plus a transition either side).")

        # Fila 2: Threshold, Slope, Max traces, Display
        row2 = ttk.Frame(ctrl)
        row2.pack(fill=tk.X, pady=4)

        ttk.Label(row2, text="Threshold:").pack(side=tk.LEFT)
        self.thr_spin = ttk.Spinbox(
            row2, from_=5, to=95, increment=5,
            textvariable=self.threshold_pct, width=6,
            command=lambda: self._recompute(),
        )
        self.thr_spin.pack(side=tk.LEFT, padx=(4, 2))
        ttk.Label(row2, text="%").pack(side=tk.LEFT, padx=(0, 14))
        self.thr_spin.bind("<Return>",   lambda e: self._recompute())
        self.thr_spin.bind("<FocusOut>", lambda e: self._recompute())
        Tooltip(self.thr_spin,
                "Crossing threshold between low and high levels (in % of swing).\n"
                "50% is standard for symmetric signals.")

        ttk.Label(row2, text="Slope:").pack(side=tk.LEFT)
        self.slope_combo = ttk.Combobox(
            row2, textvariable=self.slope,
            values=["rising", "falling", "both"],
            state="readonly", width=8,
        )
        self.slope_combo.pack(side=tk.LEFT, padx=(4, 14))
        self.slope_combo.bind("<<ComboboxSelected>>",
                              lambda e: self._recompute())
        Tooltip(self.slope_combo,
                "Which crossings to use as t=0 alignment for each slice.\n"
                "'both' uses every crossing (most slices).")

        ttk.Label(row2, text="Max traces:").pack(side=tk.LEFT)
        self.max_combo = ttk.Combobox(
            row2, textvariable=self.max_traces,
            values=[100, 250, 500, 1000, 2500, 5000],
            state="readonly", width=6,
        )
        self.max_combo.pack(side=tk.LEFT, padx=(4, 14))
        self.max_combo.bind("<<ComboboxSelected>>",
                            lambda e: self._recompute())
        Tooltip(self.max_combo,
                "Cap on number of slices to overlay. If the signal has more\n"
                "crossings, a uniform sample is taken.")

        ttk.Label(row2, text="Display:").pack(side=tk.LEFT)
        self.disp_combo = ttk.Combobox(
            row2, textvariable=self.display_mode,
            values=["Lines", "Heatmap"],
            state="readonly", width=8,
        )
        self.disp_combo.pack(side=tk.LEFT, padx=(4, 14))
        self.disp_combo.bind("<<ComboboxSelected>>",
                             lambda e: self._redraw())
        Tooltip(self.disp_combo,
                "Lines — overlay transparent traces.\n"
                "Heatmap — 2D density (better with many slices, reveals\n"
                "rare excursions vs. typical paths).")

        # Fila 3: botones + métricas
        row3 = ttk.Frame(ctrl)
        row3.pack(fill=tk.X, pady=(4, 2))

        recompute_btn = ttk.Button(row3, text="Recompute",
                                   style="Accent.TButton",
                                   command=self._recompute)
        recompute_btn.pack(side=tk.LEFT, padx=2)
        Tooltip(recompute_btn,
                "Recompute the eye diagram with the current parameters")

        save_btn = ttk.Button(row3, text="Save image…",
                              command=self._save_image_dialog)
        save_btn.pack(side=tk.LEFT, padx=2)
        Tooltip(save_btn, "Save the current eye plot as PNG")

        # Métrics label a la derecha
        self.metrics_label = tk.Label(
            row3, text="",
            fg="#ffcc00", bg=theme.COLOR_BG,
            font=("Consolas", 10, "bold"),
            justify="right",
        )
        self.metrics_label.pack(side=tk.RIGHT, padx=10)

        # --- Plot ---
        plot_frame = ttk.Frame(self.win)
        plot_frame.pack(fill=tk.BOTH, expand=True)

        # Colores del plot según el tema activo. _redraw los relee en cada
        # pintado para que un cambio de tema en runtime se aplique al ojo.
        plot_theme = THEMES[theme.CURRENT_THEME_NAME]
        plot_bg = plot_theme["PLOT_BG"]

        self.fig, self.ax = plt.subplots(figsize=(10, 5))
        self.fig.patch.set_facecolor(plot_bg)
        self.ax.set_facecolor(plot_bg)

        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    # -----------------------------
    # Lógica de eventos
    # -----------------------------
    def _on_source_changed(self):
        name = self.source_var.get()
        for i, ch in enumerate(self.scope.channels):
            if ch["name"] == name:
                self.source_idx = i
                self.voltage = ch["voltage"]
                break
        self._recompute()

    def _on_ui_typed(self):
        """Parse del campo de UI con sufijos SI."""
        s = self.ui_display.get().strip()
        val = parse_si(s, "s")
        if val is None or val <= 0:
            # No válido → restaurar display al último válido
            self.ui_display.set(format_si(self.ui_duration.get(), "s"))
            return
        self.ui_duration.set(val)
        # Re-formatear con unidad SI estándar
        self.ui_display.set(format_si(val, "s"))
        self._recompute()

    def _auto_ui(self):
        """Re-estima UI a partir de los cruces y refresca."""
        v = self._guess_ui()
        self.ui_duration.set(v)
        self.ui_display.set(format_si(v, "s"))
        self._recompute()

    def _step_ui(self, direction):
        """Incrementa o decrementa el UI por la serie 1-2-5.
        Útil para barrer escalas cuando no se sabe el bit_time exacto.

        direction: "up" o "down".
        """
        # Serie canónica 1-2-5 con prefijos SI: 1ns, 2ns, 5ns, 10ns, ...
        SERIES = []
        for exp in range(-12, 4):  # 1 ps a 10 ks
            for m in (1, 2, 5):
                SERIES.append(m * (10 ** exp))
        cur = float(self.ui_duration.get())
        # Buscar el step más cercano al actual
        idx = min(range(len(SERIES)),
                  key=lambda i: abs(np.log10(SERIES[i]) - np.log10(max(cur, 1e-15))))
        if direction == "up" and idx + 1 < len(SERIES):
            new_v = SERIES[idx + 1]
        elif direction == "down" and idx > 0:
            new_v = SERIES[idx - 1]
        else:
            return
        self.ui_duration.set(new_v)
        self.ui_display.set(format_si(new_v, "s"))
        self._recompute()

    def _guess_ui(self):
        """Estima la duración de un UI a partir del tiempo entre cruces del
        threshold con histéresis.

        Estrategias en orden:
          1) Muestreo estratificado: 3 ventanas (inicio, medio, fin) de hasta
             50k muestras cada una. Buscamos cruces en todas y usamos la
             MEDIANA global. Esto es robusto si la señal empieza/termina
             en reposo o si la actividad está concentrada en el medio.
          2) Si no hay cruces en una ventana, se busca en otra (no abandonamos
             como antes).
          3) Histéresis del 20% del swing — evita contar glitches por ruido
             como cruces.
          4) Fallback final: t_total/100 — al menos algo razonable que muestre
             la traza en un eye en vez del valor fijo 1ms que podía estar
             muy lejos de la escala real.

        Devuelve un float > 0 siempre.
        """
        t = self.time
        v = self.voltage
        if v is None or len(v) < 10:
            return 1e-3

        # Vmin/Vmax sobre TODA la señal (más fiable para threshold global).
        # Quantiles para no pegarnos a outliers.
        vmin = float(np.quantile(v, 0.05))
        vmax = float(np.quantile(v, 0.95))
        if vmax - vmin < 1e-9:
            return self._fallback_ui()
        thr = (vmin + vmax) / 2.0
        hyst = (vmax - vmin) * 0.20

        # Schmitt trigger sobre 3 ventanas estratificadas
        n_total = len(v)
        win_size = min(50_000, n_total)
        all_cross_t = []
        offsets_to_try = [0,
                          max(0, (n_total - win_size) // 2),
                          max(0, n_total - win_size)]
        seen_offsets = set()
        for off in offsets_to_try:
            if off in seen_offsets:
                continue
            seen_offsets.add(off)
            end = min(off + win_size, n_total)
            vw = v[off:end]
            tw = t[off:end]
            # Schmitt simplificado: estado binario tipo histeresis
            hi = thr + hyst / 2.0
            lo = thr - hyst / 2.0
            state = 1 if vw[0] > thr else 0
            for i in range(1, len(vw)):
                new_state = state
                if state == 0 and vw[i] > hi:
                    new_state = 1
                elif state == 1 and vw[i] < lo:
                    new_state = 0
                if new_state != state:
                    # Interpolar cruce
                    v0, v1 = vw[i-1], vw[i]
                    if abs(v1 - v0) > 1e-15:
                        frac = (thr - v0) / (v1 - v0)
                        tc = tw[i-1] + frac * (tw[i] - tw[i-1])
                    else:
                        tc = tw[i]
                    all_cross_t.append(tc)
                    state = new_state
            if len(all_cross_t) >= 20:
                # Suficientes — no necesitamos seguir
                break

        if len(all_cross_t) < 4:
            return self._fallback_ui()

        diffs = np.diff(sorted(all_cross_t))
        # Filtrar diffs anómalos (mucho más grandes que la mediana — idle gaps)
        med = float(np.median(diffs))
        # Considerar solo diffs ≤ 3× mediana → activity-like
        clean = [d for d in diffs if d <= 3 * med]
        if clean:
            med = float(np.median(clean))
        return max(med, 1e-9)

    def _fallback_ui(self):
        """UI por defecto cuando no se puede estimar. Usa t_total/100 si está
        disponible — algo razonable para que el eye se vea aunque sea con
        slices "grandes". Si no, 1 ms."""
        if self.time is not None and len(self.time) > 1:
            dur = float(self.time[-1] - self.time[0])
            if dur > 0:
                return dur / 100.0
        return 1e-3

    # -----------------------------
    # Cálculo del eye
    # -----------------------------
    def _find_crossings(self, thr, slope):
        """Devuelve un array con los tiempos (interpolados) de los cruces
        del threshold según slope.

        slope: 'rising', 'falling', 'both'.
        """
        v = self.voltage
        t = self.time
        if len(v) < 2:
            return np.array([])
        diff_sign = np.diff(np.sign(v - thr))
        if slope == "rising":
            idx = np.where(diff_sign > 0)[0]
        elif slope == "falling":
            idx = np.where(diff_sign < 0)[0]
        else:
            idx = np.where(diff_sign != 0)[0]
        if len(idx) == 0:
            return np.array([])
        # Interpolación lineal de los cruces
        v0 = v[idx]
        v1 = v[idx + 1]
        t0 = t[idx]
        t1 = t[idx + 1]
        dv = v1 - v0
        # Evitar div por cero
        frac = np.where(np.abs(dv) > 1e-15, (thr - v0) / dv, 0.5)
        return t0 + frac * (t1 - t0)

    def _recompute(self):
        """Calcula los slices del eye diagram con los parámetros actuales."""
        if self.voltage is None or len(self.voltage) < 100:
            self.slices = None
            self._redraw()
            return

        ui = float(self.ui_duration.get())
        n_ui = max(1, int(self.uis_per_view.get()))
        win_dur = ui * n_ui
        # Centrar la ventana en t=0 del slice: medio UI antes, medio después
        # para que la transición caiga en el centro.
        half = win_dur / 2.0

        # Nivel del threshold
        vmin = float(np.min(self.voltage))
        vmax = float(np.max(self.voltage))
        if vmax - vmin < 1e-12:
            self.slices = None
            self._redraw()
            return
        thr = vmin + (vmax - vmin) * (float(self.threshold_pct.get()) / 100.0)

        # Cruces
        crossings = self._find_crossings(thr, self.slope.get())
        # Filtrar cruces demasiado cerca de los bordes (no cabe el slice)
        t_first = self.time[0]
        t_last = self.time[-1]
        crossings = crossings[
            (crossings >= t_first + half) & (crossings <= t_last - half)
        ]
        if len(crossings) < 2:
            self.slices = None
            self.metrics = {"n_slices": len(crossings), "thr": thr,
                            "vmin": vmin, "vmax": vmax}
            self._redraw()
            return

        # Muestreo uniforme si hay demasiados
        max_n = int(self.max_traces.get())
        if len(crossings) > max_n:
            idxs = np.linspace(0, len(crossings) - 1, max_n).astype(int)
            crossings = crossings[idxs]

        # Rejilla común
        rel_t = np.linspace(-half, +half, self.SLICE_RESOLUTION)

        # Para cada cruce, interpolar la voltaje en (cruce + rel_t)
        slices = np.empty((len(crossings), self.SLICE_RESOLUTION),
                          dtype=np.float64)
        for i, c in enumerate(crossings):
            sample_t = c + rel_t
            # np.interp es vectorizado y rápido; con un solo paso queda en O(N log N)
            slices[i] = np.interp(sample_t, self.time, self.voltage)

        self.slices = slices
        self.slice_time = rel_t
        self._compute_metrics(thr, vmin, vmax)
        self._redraw()

    def _compute_metrics(self, thr, vmin, vmax):
        """Calcula las métricas estándar del eye sobre self.slices."""
        s = self.slices
        if s is None or len(s) < 2:
            self.metrics = {}
            return

        n_slices, n_pts = s.shape
        rel_t = self.slice_time
        center_idx = n_pts // 2     # centro del UI (donde idealmente se decide)

        # === Top / Base por histograma bimodal sobre TODAS las muestras
        # del eye (clásico: dos picos en bajo y alto). Usamos quantiles
        # 5/95 como aproximación rápida y robusta.
        all_v = s.ravel()
        v_low  = float(np.quantile(all_v, 0.10))
        v_high = float(np.quantile(all_v, 0.90))

        # === Eye height: en el centro del UI, ¿cuán abierto está el ojo?
        # min de las muestras "altas" − max de las muestras "bajas" en el centro.
        center_vals = s[:, center_idx]
        # Clasificar centro por encima/debajo del threshold
        above = center_vals[center_vals > thr]
        below = center_vals[center_vals < thr]
        if len(above) > 0 and len(below) > 0:
            eye_height = float(np.min(above) - np.max(below))
        else:
            eye_height = 0.0

        # === Cruces por threshold por slice → jitter pk-pk
        # Cada slice está alineado con su cruce principal en rel_t=0. Por
        # construcción ese cruce no aporta información de jitter (siempre cae
        # cerca de 0). El jitter REAL aparece en los cruces SIGUIENTES dentro
        # del slice, que con UIs/view ≥ 2 deberían existir cerca de ±UI.
        #
        # Estrategia: para cada slice, buscar el cruce más cercano a +UI
        # (o -UI). Como UIs/view puede ser 1 (no hay otro cruce visible),
        # caemos a "no jitter computable" en ese caso.
        ui = float(self.ui_duration.get())
        n_ui = int(self.uis_per_view.get())
        crossings_t = []
        if n_ui >= 2:
            # Zona donde miramos: cualquier rel_t alejado >UI/4 del centro
            far_mask = np.abs(rel_t) > ui * 0.25
            for i in range(n_slices):
                diff_sign = np.diff(np.sign(s[i] - thr))
                idxs = np.where(diff_sign != 0)[0]
                if len(idxs) == 0:
                    continue
                # Filtrar a cruces lejos del centro
                idxs_far = [j for j in idxs if far_mask[j]]
                if not idxs_far:
                    continue
                # El cruce más cercano a +UI (el "siguiente bit")
                target = ui  # alternativa: -ui si quisiéramos el previo
                # Considerar también -UI si solo hay cruces a la izquierda
                # del centro (depende del patrón de bits)
                if any(rel_t[j] > 0 for j in idxs_far):
                    candidates = [j for j in idxs_far if rel_t[j] > 0]
                    best_idx = candidates[
                        int(np.argmin(np.abs(rel_t[candidates] - target)))
                    ]
                else:
                    candidates = [j for j in idxs_far if rel_t[j] < 0]
                    best_idx = candidates[
                        int(np.argmin(np.abs(rel_t[candidates] - (-target))))
                    ]
                v0 = s[i, best_idx]
                v1 = s[i, best_idx + 1]
                if abs(v1 - v0) > 1e-15:
                    frac = (thr - v0) / (v1 - v0)
                    tc = rel_t[best_idx] + frac * (rel_t[best_idx + 1] - rel_t[best_idx])
                else:
                    tc = rel_t[best_idx]
                # Normalizamos al valor absoluto del cruce-aproximado-en-UI
                # para que crossings a +UI y -UI se promedien juntos en
                # términos de desviación respecto al UI ideal.
                crossings_t.append(abs(tc) - ui)
        if crossings_t:
            crossings_t = np.array(crossings_t)
            jitter_pp = float(np.ptp(crossings_t))
            jitter_rms = float(np.std(crossings_t))
        else:
            jitter_pp = 0.0
            jitter_rms = 0.0

        # === Eye width: a la altura del threshold, hueco en el eje t entre
        # el cruce más tarde (rising side) y el cruce más temprano (falling).
        # Aproximación: para cada slice, encontrar primer y último cruce y
        # tomar el peor caso.
        # Más simple y robusto: eye width = UI - jitter_pp (aprox).
        ui = float(self.ui_duration.get())
        eye_width = max(0.0, ui - jitter_pp)

        # === SNR aproximado
        # ruido = std de las muestras altas o bajas alejadas del centro.
        # Usamos un cuarto-UI a cada lado del centro como zona "estable".
        stable_idx = int(n_pts * 0.10)
        if stable_idx >= 1:
            high_samples = s[:, :stable_idx][s[:, :stable_idx] > thr]
            low_samples  = s[:, :stable_idx][s[:, :stable_idx] < thr]
            noise_high = float(np.std(high_samples)) if len(high_samples) > 10 else 0.0
            noise_low  = float(np.std(low_samples))  if len(low_samples)  > 10 else 0.0
            noise = (noise_high + noise_low) / 2.0
            snr = (eye_height / noise) if noise > 1e-12 else float("inf")
        else:
            snr = float("inf")

        self.metrics = {
            "n_slices":    n_slices,
            "vmin":        vmin,
            "vmax":        vmax,
            "thr":         thr,
            "v_low":       v_low,
            "v_high":      v_high,
            "eye_height":  eye_height,
            "eye_width":   eye_width,
            "jitter_pp":   jitter_pp,
            "jitter_rms":  jitter_rms,
            "snr":         snr,
        }

    # -----------------------------
    # Render
    # -----------------------------
    def _redraw(self):
        """Pinta el eye con el modo actual + métricas en el label."""
        ax = self.ax
        ax.clear()

        # Colores del plot según el tema activo. Releídos en cada redraw
        # para que un cambio de tema en runtime se aplique al eye sin
        # tener que cerrarlo y reabrirlo.
        plot_theme = THEMES[theme.CURRENT_THEME_NAME]
        plot_bg   = plot_theme["PLOT_BG"]
        plot_grid = plot_theme["PLOT_GRID"]
        plot_text = plot_theme["PLOT_TEXT"]

        self.fig.patch.set_facecolor(plot_bg)
        ax.set_facecolor(plot_bg)

        if self.slices is None or len(self.slices) < 2:
            ax.text(0.5, 0.5,
                    "No slices computed.\n"
                    "Try increasing 'Max traces', adjusting UI, or check "
                    "that the signal actually crosses the threshold.",
                    color=theme.COLOR_TEXT_DIM, ha="center", va="center",
                    transform=ax.transAxes, fontsize=11)
            ax.set_xticks([])
            ax.set_yticks([])
            self.metrics_label.config(text="(no slices)")
            self.canvas.draw_idle()
            return

        rel_t = self.slice_time
        s = self.slices
        n_slices = s.shape[0]

        mode = self.display_mode.get()
        if mode == "Heatmap":
            # 2D histogram: cuenta cuántas trazas pasan por cada celda.
            # Es muy informativo con muchas trazas — el "ojo" se ve más claro.
            v_lo = self.metrics.get("vmin", float(np.min(s)))
            v_hi = self.metrics.get("vmax", float(np.max(s)))
            margin = 0.1 * (v_hi - v_lo + 1e-12)
            y_edges = np.linspace(v_lo - margin, v_hi + margin, 200)
            x_edges = np.linspace(rel_t[0], rel_t[-1], len(rel_t))

            # Flatten todo en xs/ys para hist2d
            xs = np.broadcast_to(rel_t, s.shape).ravel()
            ys = s.ravel()
            ax.hist2d(xs, ys, bins=[x_edges, y_edges], cmap="inferno",
                      cmin=1)
        else:
            # Lines: dibujamos con un LineCollection — UNA llamada para todas
            # las trazas. Esto es órdenes de magnitud más rápido que un
            # plot() por slice.
            from matplotlib.collections import LineCollection
            # segmentos por trazo: lista de N arrays (resolution, 2)
            segs = []
            for i in range(n_slices):
                segs.append(np.column_stack([rel_t, s[i]]))
            # Color del canal
            ch_color = "cyan"
            if 0 <= self.source_idx < len(self.scope.channels):
                ch_color = self.scope.channels[self.source_idx]["color"]
            alpha = max(0.03, min(0.25, 8.0 / n_slices))
            lc = LineCollection(segs, colors=ch_color, linewidths=0.7,
                                alpha=alpha)
            ax.add_collection(lc)
            ax.set_xlim(rel_t[0], rel_t[-1])
            v_lo = self.metrics.get("vmin", float(np.min(s)))
            v_hi = self.metrics.get("vmax", float(np.max(s)))
            margin = 0.1 * (v_hi - v_lo + 1e-12)
            ax.set_ylim(v_lo - margin, v_hi + margin)

        # Threshold horizontal
        thr = self.metrics.get("thr")
        if thr is not None:
            ax.axhline(thr, color=theme.COLOR_BORDER, linestyle=(0, (4, 3)),
                       linewidth=0.8, alpha=0.7)

        # Marcadores verticales en límites de UI
        ui = float(self.ui_duration.get())
        n_ui = int(self.uis_per_view.get())
        for k in range(-n_ui // 2, n_ui // 2 + 1):
            x = k * ui
            ax.axvline(x, color=theme.COLOR_BORDER, linestyle=":",
                       linewidth=0.6, alpha=0.6)

        # Estilo
        ax.grid(True, color=plot_grid, linewidth=0.4, alpha=0.5)
        for spine in ax.spines.values():
            spine.set_color(theme.COLOR_BORDER)
        ax.tick_params(colors=plot_text)

        # Eje X con formato SI (en segundos)
        xticks = ax.get_xticks()
        ax.set_xticklabels([format_si(x, "s", sig_figs=3) for x in xticks])

        ax.set_xlabel("Time (relative to crossing)", color=plot_text)
        ax.set_ylabel("Voltage", color=plot_text)
        for lbl in ax.get_xticklabels() + ax.get_yticklabels():
            lbl.set_color(plot_text)

        # Métricas en el label. Unit del canal source (v1.29).
        try:
            src_unit = self.scope.channels[self.source_idx].get("unit", DEFAULT_UNIT)
        except Exception:
            src_unit = DEFAULT_UNIT
        m = self.metrics
        if m and "eye_height" in m:
            lines = [
                f"Slices: {m['n_slices']}",
                f"Eye height: {format_si(m['eye_height'], src_unit)}",
                f"Eye width:  {format_si(m['eye_width'], 's')}",
                f"Jitter pp:  {format_si(m['jitter_pp'], 's')}",
                f"Jitter rms: {format_si(m['jitter_rms'], 's')}",
                (f"SNR: {m['snr']:.1f}" if np.isfinite(m['snr'])
                 else "SNR: ∞"),
                f"Vₕᵢ: {format_si(m['v_high'], src_unit)}   "
                f"Vₗₒ:  {format_si(m['v_low'], src_unit)}",
            ]
            self.metrics_label.config(text="   ".join(lines[:4]) + "\n" +
                                            "   ".join(lines[4:]))
        else:
            self.metrics_label.config(
                text=f"Slices: {m.get('n_slices', 0)} (need ≥2)")

        self.canvas.draw_idle()

    # -----------------------------
    # Save image
    # -----------------------------
    def _save_image_dialog(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            parent=self.win,
            title="Save eye diagram",
            defaultextension=".png",
            initialfile="eye_diagram.png",
            filetypes=[("PNG", "*.png"), ("SVG", "*.svg"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            self.fig.savefig(path, dpi=150,
                             facecolor=self.fig.get_facecolor(),
                             edgecolor="none")
            self.scope._show_toast(f"Saved: {path}")
        except Exception as e:
            self.scope._show_toast(f"Save failed: {e}")
