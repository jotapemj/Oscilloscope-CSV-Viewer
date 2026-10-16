"""Software trigger: Edge/Width event search, navigation, markers and window."""
import tkinter as tk

from tkinter import ttk

from ocv.core.measurements import _find_trigger_crossings
from ocv.core.scope import H_DIVS, V_DIVS
from ocv.ui import theme
from ocv.ui.widgets import Tooltip


class TriggerMixin:
    """Mixin de OscilloscopeApp: usa su estado vía self."""

    # =============================
    # Trigger
    # =============================
    def _compute_trigger_events(self):
        """Recorre la señal actual y genera self.trigger_events según la
        configuración. Eventos son tiempos reales (en segundos absolutos)."""
        self.trigger_events = []
        cfg = self.trigger_config
        if not cfg["enabled"] or self.time is None or not self.channels:
            return
        src_idx = cfg["source"]
        if not (0 <= src_idx < len(self.channels)):
            return
        v = self.channels[src_idx]["voltage"]
        t = self.time
        if len(v) < 2:
            return

        if cfg["type"] == "Edge":
            lvl = float(cfg["level"])
            edge = "rising" if cfg["slope"] == "Rising" else "falling"
            crossings = _find_trigger_crossings(t, v, lvl, edge=edge)
            for c in crossings:
                self.trigger_events.append({
                    "t_trigger": float(c),
                    "t_start":   float(c),
                    "t_end":     None,
                })
            return

        if cfg["type"] == "Width":
            lvl = float(cfg["level"])
            if cfg["polarity"] == "Positive":
                # Pulso positivo: rising -> falling
                rises = _find_trigger_crossings(t, v, lvl, edge="rising")
                falls = _find_trigger_crossings(t, v, lvl, edge="falling")
                pulses = []
                for r in rises:
                    nxt = falls[falls > r]
                    if len(nxt):
                        pulses.append((r, nxt[0]))
            else:
                # Pulso negativo: falling -> rising
                rises = _find_trigger_crossings(t, v, lvl, edge="rising")
                falls = _find_trigger_crossings(t, v, lvl, edge="falling")
                pulses = []
                for f in falls:
                    nxt = rises[rises > f]
                    if len(nxt):
                        pulses.append((f, nxt[0]))

            w1 = float(cfg["width1"])
            w2 = float(cfg["width2"])
            cond = cfg["condition"]
            TOL = max(w1 * 0.01, 1e-12)   # 1% para igualdad

            for (ts, te) in pulses:
                w = te - ts
                ok = False
                if cond == "Width >":       ok = w > w1
                elif cond == "Width <":     ok = w < w1
                elif cond == "Width =":     ok = abs(w - w1) <= TOL
                elif cond == "Width ≠":     ok = abs(w - w1) > TOL
                elif cond == "Inside":      ok = w1 <= w <= w2
                elif cond == "Outside":     ok = w < w1 or w > w2
                if ok:
                    self.trigger_events.append({
                        "t_trigger": float(ts),
                        "t_start":   float(ts),
                        "t_end":     float(te),
                    })

    def _refresh_trigger(self):
        """Recalcula y redibuja. Actualiza también la ventana si está abierta."""
        self._compute_trigger_events()
        # Ajustar índice actual si cambió la lista
        if self.trigger_events:
            if self.trigger_current_idx < 0 or self.trigger_current_idx >= len(self.trigger_events):
                self.trigger_current_idx = 0
        else:
            self.trigger_current_idx = -1
        self._update_trigger_count_label()
        self.update_plot()
        # Cualquier cambio de trigger (enable, src, type, level, etc.) pasa
        # por aquí — un único punto para marcar dirty.
        self._mark_dirty()

    def _update_trigger_count_label(self):
        """Actualiza el label de la ventana (si está abierta)."""
        if self._trigger_window is None:
            return
        try:
            lbl = self._trigger_window._count_label
            n = len(self.trigger_events)
            if n == 0:
                lbl.config(text="0 trigger events found", fg=theme.COLOR_DANGER)
            else:
                idx_str = (f"{self.trigger_current_idx + 1}/{n}"
                           if self.trigger_current_idx >= 0 else f"—/{n}")
                lbl.config(text=f"{n} trigger events found   ({idx_str})",
                           fg=theme.COLOR_ACCENT)
        except (AttributeError, tk.TclError):
            pass

    def _trigger_goto(self, delta):
        """Mueve el índice y centra la ventana visible en ese evento."""
        if not self.trigger_events:
            return
        n = len(self.trigger_events)
        self.trigger_current_idx = (self.trigger_current_idx + delta) % n
        ev = self.trigger_events[self.trigger_current_idx]
        t_ev = ev["t_trigger"]
        window = self.t_div.get() * H_DIVS
        # Centrar el evento en la ventana visible
        new_offset = max(0.0, t_ev - window / 2.0)
        if self.t_total is not None:
            new_offset = min(new_offset, max(0.0, self.t_total - window))
        self.t_offset.set(new_offset)
        self._update_trigger_count_label()
        self.update_plot()

    def _draw_trigger_markers(self):
        """Dibuja markers en la ventana temporal visible.

        Limpia artists previos antes de redibujar — necesario en fast path
        donde no se hace ax.clear() entre frames.
        """
        # Limpiar previos
        for a in getattr(self, "_trigger_marker_artists", []):
            try:
                a.remove()
            except Exception:
                pass
        self._trigger_marker_artists = []

        if not self.trigger_events or not self.trigger_config["show_markers"]:
            return
        t_start = self.t_offset.get()
        window = self.t_div.get() * H_DIVS
        t_end = t_start + window
        v_range = self.v_div.get() * V_DIVS

        cur = self.trigger_current_idx
        for i, ev in enumerate(self.trigger_events):
            t_ev = ev["t_trigger"]
            # Filtrar: ignorar los que queden fuera de la ventana visible
            if not (t_start <= t_ev <= t_end) and not (
                ev["t_end"] is not None
                and ev["t_start"] <= t_end and ev["t_end"] >= t_start
            ):
                continue

            is_current = (i == cur)
            color = "#ffeb3b"
            lw = 1.8 if is_current else 1.0
            alpha = 1.0 if is_current else 0.7

            # Para Width: sombreado del pulso
            if ev["t_end"] is not None:
                x1 = max(ev["t_start"] - t_start, 0)
                x2 = min(ev["t_end"] - t_start, window)
                span = self.ax.axvspan(x1, x2, color=color,
                                alpha=0.12 if is_current else 0.06,
                                zorder=1)
                self._trigger_marker_artists.append(span)

            # Línea vertical en el instante de trigger
            if t_start <= t_ev <= t_end:
                x = t_ev - t_start
                ln = self.ax.axvline(x, color=color, linewidth=lw, alpha=alpha,
                                linestyle=(0, (4, 3)), zorder=4)
                self._trigger_marker_artists.append(ln)
                # Marker "T" arriba
                txt = self.ax.text(x, v_range / 2 * 0.95, "T",
                             color=color,
                             fontsize=10 if is_current else 8,
                             fontweight="bold",
                             ha="center", va="top",
                             zorder=6)
                self._trigger_marker_artists.append(txt)

    def open_trigger_window(self):
        """Ventana de configuración del trigger (viva)."""
        if self._trigger_window is not None and self._trigger_window.winfo_exists():
            self._trigger_window.lift()
            return
        if self.time is None or not self.channels:
            self._show_toast("Load a CSV first")
            return

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("Trigger")
        win.configure(bg=theme.COLOR_BG)
        # Geometry PRIMERO (centra), chrome (transient) DESPUÉS. Si
        # transient se aplica antes de tener geometry, Tk en Windows
        # hace un deiconify transitorio que se percibe como fantasma.
        self._make_resizable_dialog(win, 420, 540)
        self._apply_window_chrome(win)

        # Bottom bar reservada PRIMERO (ver Math): asegura que los botones
        # Close/Clear queden siempre visibles aunque la ventana se reduzca.
        btns = tk.Frame(win, bg=theme.COLOR_BG)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=12)

        # ---- Enable + source ----
        header = tk.Frame(win, bg=theme.COLOR_BG)
        header.pack(fill=tk.X, padx=14, pady=(14, 6))

        enable_var = tk.BooleanVar(value=self.trigger_config["enabled"])
        # find_btn se define más abajo (en la zona de botones inferiores) y
        # lo referenciamos desde on_enable para habilitarlo/deshabilitarlo.
        # Usamos una "celda" mutable (lista) para captura por closure ya que
        # find_btn aún no existe cuando definimos on_enable.
        _find_btn_ref = []

        def on_enable():
            is_on = bool(enable_var.get())
            self.trigger_config["enabled"] = is_on
            # Habilitar/deshabilitar el botón Find acorde al estado
            if _find_btn_ref:
                try:
                    _find_btn_ref[0].config(
                        state=("normal" if is_on else "disabled"))
                except Exception:
                    pass
            # Si se desactiva, limpiar los eventos y el plot; si se activa,
            # NO disparar búsqueda — el usuario lo hará con Find. Así puede
            # ajustar todos los parámetros antes de evaluar.
            if not is_on:
                self.trigger_events = []
                self.trigger_current_idx = -1
                self._update_trigger_count_label()
                self.update_plot()

        ttk.Checkbutton(header, text="Enable trigger",
                        variable=enable_var, command=on_enable
                        ).pack(side=tk.LEFT)

        # Source
        src_row = tk.Frame(win, bg=theme.COLOR_BG)
        src_row.pack(fill=tk.X, padx=14, pady=(4, 6))
        tk.Label(src_row, text="Source:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        ch_names = [ch["name"] for ch in self.channels]
        src_idx = self.trigger_config["source"]
        if not (0 <= src_idx < len(ch_names)):
            src_idx = 0
        src_var = tk.StringVar(value=ch_names[src_idx])
        src_combo = ttk.Combobox(src_row, textvariable=src_var,
                                 values=ch_names, state="readonly", width=18)
        src_combo.pack(side=tk.LEFT)

        def on_src_change(_e=None):
            n = src_var.get()
            for i, ch in enumerate(self.channels):
                if ch["name"] == n:
                    self.trigger_config["source"] = i
                    break
            self._refresh_trigger()
        src_combo.bind("<<ComboboxSelected>>", on_src_change)

        # Type
        type_row = tk.Frame(win, bg=theme.COLOR_BG)
        type_row.pack(fill=tk.X, padx=14, pady=(4, 6))
        tk.Label(type_row, text="Type:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        type_var = tk.StringVar(value=self.trigger_config["type"])
        type_combo = ttk.Combobox(type_row, textvariable=type_var,
                                  values=["Edge", "Width"],
                                  state="readonly", width=12)
        type_combo.pack(side=tk.LEFT)

        # ---- Edge frame ----
        edge_frame = tk.LabelFrame(win, text=" Edge ", bg=theme.COLOR_BG,
                                   fg=theme.COLOR_ACCENT, bd=1,
                                   font=("Segoe UI", 9, "bold"))
        slope_var = tk.StringVar(value=self.trigger_config["slope"])
        slope_row = tk.Frame(edge_frame, bg=theme.COLOR_BG)
        slope_row.pack(fill=tk.X, padx=8, pady=(4, 4))
        tk.Label(slope_row, text="Slope:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Radiobutton(slope_row, text="Rising", variable=slope_var,
                        value="Rising").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(slope_row, text="Falling", variable=slope_var,
                        value="Falling").pack(side=tk.LEFT)

        edge_level_row = tk.Frame(edge_frame, bg=theme.COLOR_BG)
        edge_level_row.pack(fill=tk.X, padx=8, pady=(2, 6))
        tk.Label(edge_level_row, text="Level (V):", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        edge_level_var = tk.StringVar(value=f"{self.trigger_config['level']:g}")
        edge_level_entry = ttk.Entry(edge_level_row, textvariable=edge_level_var,
                                     width=12)
        edge_level_entry.pack(side=tk.LEFT)

        # ---- Width frame ----
        width_frame = tk.LabelFrame(win, text=" Width ", bg=theme.COLOR_BG,
                                    fg=theme.COLOR_ACCENT, bd=1,
                                    font=("Segoe UI", 9, "bold"))

        polarity_var = tk.StringVar(value=self.trigger_config["polarity"])
        pol_row = tk.Frame(width_frame, bg=theme.COLOR_BG)
        pol_row.pack(fill=tk.X, padx=8, pady=(4, 4))
        tk.Label(pol_row, text="Polarity:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Radiobutton(pol_row, text="Positive", variable=polarity_var,
                        value="Positive").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(pol_row, text="Negative", variable=polarity_var,
                        value="Negative").pack(side=tk.LEFT)

        cond_row = tk.Frame(width_frame, bg=theme.COLOR_BG)
        cond_row.pack(fill=tk.X, padx=8, pady=(2, 4))
        tk.Label(cond_row, text="Condition:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        cond_var = tk.StringVar(value=self.trigger_config["condition"])
        cond_combo = ttk.Combobox(cond_row, textvariable=cond_var,
                                  values=["Width >", "Width <", "Width =",
                                          "Width ≠", "Inside", "Outside"],
                                  state="readonly", width=12)
        cond_combo.pack(side=tk.LEFT)

        w1_row = tk.Frame(width_frame, bg=theme.COLOR_BG)
        w1_row.pack(fill=tk.X, padx=8, pady=(2, 2))
        tk.Label(w1_row, text="Width 1 (s):", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        w1_var = tk.StringVar(value=f"{self.trigger_config['width1']:g}")
        w1_entry = ttk.Entry(w1_row, textvariable=w1_var, width=12)
        w1_entry.pack(side=tk.LEFT)

        w2_row = tk.Frame(width_frame, bg=theme.COLOR_BG)
        w2_row.pack(fill=tk.X, padx=8, pady=(2, 2))
        tk.Label(w2_row, text="Width 2 (s):", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        w2_var = tk.StringVar(value=f"{self.trigger_config['width2']:g}")
        w2_entry = ttk.Entry(w2_row, textvariable=w2_var, width=12)
        w2_entry.pack(side=tk.LEFT)

        w_level_row = tk.Frame(width_frame, bg=theme.COLOR_BG)
        w_level_row.pack(fill=tk.X, padx=8, pady=(2, 6))
        tk.Label(w_level_row, text="Level (V):", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        w_level_var = tk.StringVar(value=f"{self.trigger_config['level']:g}")
        w_level_entry = ttk.Entry(w_level_row, textvariable=w_level_var,
                                  width=12)
        w_level_entry.pack(side=tk.LEFT)

        # Empaquetar: mostrar/ocultar según type
        def refresh_type_visibility():
            tp = type_var.get()
            if tp == "Edge":
                edge_frame.pack(fill=tk.X, padx=14, pady=(6, 4))
                width_frame.pack_forget()
            else:
                width_frame.pack(fill=tk.X, padx=14, pady=(6, 4))
                edge_frame.pack_forget()
            # Width 2 solo para Inside/Outside
            if tp == "Width" and cond_var.get() in ("Inside", "Outside"):
                w2_row.pack(fill=tk.X, padx=8, pady=(2, 2),
                            before=w_level_row)
            else:
                w2_row.pack_forget()

        def on_type_change(_e=None):
            self.trigger_config["type"] = type_var.get()
            refresh_type_visibility()
            self._refresh_trigger()
        type_combo.bind("<<ComboboxSelected>>", on_type_change)

        def on_cond_change(_e=None):
            self.trigger_config["condition"] = cond_var.get()
            refresh_type_visibility()
            self._refresh_trigger()
        cond_combo.bind("<<ComboboxSelected>>", on_cond_change)

        # --- Bindings de cambio para los valores ---
        def on_slope_change(*_):
            self.trigger_config["slope"] = slope_var.get()
            self._refresh_trigger()
        slope_var.trace_add("write", on_slope_change)

        def on_polarity_change(*_):
            self.trigger_config["polarity"] = polarity_var.get()
            self._refresh_trigger()
        polarity_var.trace_add("write", on_polarity_change)

        def commit_numeric(var, key):
            try:
                self.trigger_config[key] = float(var.get())
                self._refresh_trigger()
            except ValueError:
                pass

        for ent, var, key in [
            (edge_level_entry, edge_level_var, "level"),
            (w_level_entry,    w_level_var,    "level"),
            (w1_entry,         w1_var,         "width1"),
            (w2_entry,         w2_var,         "width2"),
        ]:
            ent.bind("<Return>",   lambda e, v=var, k=key: commit_numeric(v, k))
            ent.bind("<FocusOut>", lambda e, v=var, k=key: commit_numeric(v, k))

        refresh_type_visibility()

        # ---- Resultados ----
        results_frame = tk.Frame(win, bg=theme.COLOR_BG)
        results_frame.pack(fill=tk.X, padx=14, pady=(10, 4))

        count_label = tk.Label(results_frame, text="0 trigger events found",
                               bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                               font=("Segoe UI", 10, "bold"))
        count_label.pack(side=tk.LEFT)
        win._count_label = count_label

        nav_row = tk.Frame(win, bg=theme.COLOR_BG)
        nav_row.pack(fill=tk.X, padx=14, pady=(2, 6))

        def prev_ev():
            self._trigger_goto(-1)
        def next_ev():
            self._trigger_goto(+1)

        prev_btn = tk.Button(
            nav_row, text="◀  Prev",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            activebackground=theme.COLOR_BG_HOVER,
            bd=1, relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=10, pady=4,
            highlightbackground=theme.COLOR_BORDER, highlightthickness=1,
            command=prev_ev,
        )
        prev_btn.pack(side=tk.LEFT, padx=(0, 4))
        self._add_hover_shadow(prev_btn)

        next_btn = tk.Button(
            nav_row, text="Next  ▶",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            activebackground=theme.COLOR_BG_HOVER,
            bd=1, relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=10, pady=4,
            highlightbackground=theme.COLOR_BORDER, highlightthickness=1,
            command=next_ev,
        )
        next_btn.pack(side=tk.LEFT, padx=(0, 4))
        self._add_hover_shadow(next_btn)

        # Show markers
        markers_var = tk.BooleanVar(value=self.trigger_config["show_markers"])
        def on_markers():
            self.trigger_config["show_markers"] = bool(markers_var.get())
            self.update_plot()
            self._mark_dirty()
        ttk.Checkbutton(win, text="Show markers on plot",
                        variable=markers_var, command=on_markers
                        ).pack(anchor="w", padx=14, pady=(6, 2))

        # ---- Botones (btns ya fue packeado al principio) ----
        def do_clear():
            self.trigger_config["enabled"] = False
            enable_var.set(False)
            self.trigger_events = []
            self.trigger_current_idx = -1
            self._update_trigger_count_label()
            self.update_plot()
            # Find sigue al estado del checkbox
            try:
                find_btn.config(state="disabled")
            except Exception:
                pass

        def on_close():
            self._trigger_window = None
            win.destroy()

        def do_find():
            """Ejecuta la búsqueda con los criterios actuales. Solo se
            permite con el checkbox 'Enable trigger' activo (el propio
            botón está deshabilitado en caso contrario, pero defendemos)."""
            if not self.trigger_config.get("enabled"):
                return
            self._refresh_trigger()

        ttk.Button(btns, text="Close", command=on_close).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Clear", command=do_clear
                   ).pack(side=tk.RIGHT, padx=(0, 6))
        find_btn = ttk.Button(btns, text="Find",
                              style="Accent.TButton",
                              command=do_find)
        find_btn.pack(side=tk.RIGHT, padx=(0, 6))
        Tooltip(find_btn,
                "Run the trigger search with the current parameters.\n"
                "Only enabled while 'Enable trigger' is checked.")
        # Registrar para que on_enable pueda manipular el estado
        _find_btn_ref.append(find_btn)
        # Estado inicial coherente con el checkbox
        find_btn.config(state=("normal" if enable_var.get() else "disabled"))

        win.protocol("WM_DELETE_WINDOW", on_close)
        self._bind_escape_to_close(win, on_close=on_close)
        self._trigger_window = win

        # Compute inicial
        self._refresh_trigger()
