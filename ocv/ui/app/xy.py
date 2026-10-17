"""XY mode: one channel against another, and its configuration window."""
import numpy as np
import tkinter as tk

from tkinter import ttk

from ocv.core.scope import H_DIVS
from ocv.ui import theme


class XYMixin:
    """Mixin de OscilloscopeApp: usa su estado vía self."""

    # =============================
    # XY mode
    # =============================
    def _draw_xy(self):
        """Dibuja el plot XY en self.ax. Limpia y configura el grid como
        cartesiano estándar. No dibuja cursores ni annotations."""
        cfg = self.xy_config
        self.ax.clear()
        # ax.clear() borra los Line2D del time-domain plot pero el diccionario
        # _channel_lines (uid → artist) sigue apuntando a artists muertos. Si
        # no lo vaciamos y tampoco invalidamos el fingerprint, al salir del
        # modo XY el fast path intenta hacer set_data() sobre artists que ya
        # no están en el ax y el plot se queda en blanco hasta el siguiente
        # cambio estructural. Reseteamos ambos para forzar full redraw al
        # volver al modo time-domain.
        self._channel_lines = {}
        self._last_structure_fingerprint = None
        self.ax.set_facecolor(self.grid_theme["bg"])
        self.fig.patch.set_facecolor(self.grid_theme["bg"])

        if self.time is None or not self.channels:
            self.canvas.draw()
            return

        nch = len(self.channels)
        xi = max(0, min(cfg["x_idx"], nch - 1))
        yi = max(0, min(cfg["y_idx"], nch - 1))
        ch_x = self.channels[xi]
        ch_y = self.channels[yi]

        # Seleccionar rango temporal
        if cfg["window"] == "Visible range":
            t_start = self.t_offset.get()
            window = self.t_div.get() * H_DIVS
            mask = (self.time >= t_start) & (self.time <= t_start + window)
            vx = ch_x["voltage"][mask]
            vy = ch_y["voltage"][mask]
        else:
            vx = ch_x["voltage"]
            vy = ch_y["voltage"]

        if len(vx) == 0 or len(vy) == 0:
            self.canvas.draw()
            return

        # Colores de canal legibles sobre el fondo del plot (Light)
        bg = self.grid_theme.get("bg", theme.COLOR_BG)
        y_line = theme.readable_on(ch_y["color"], bg, theme.GRAPHIC_MIN)
        x_text = theme.readable_on(ch_x["color"], bg)
        y_text = theme.readable_on(ch_y["color"], bg)

        # Estilo
        style = cfg.get("style", "Line")
        if style == "Dots":
            self.ax.plot(vx, vy, linestyle="none", marker=".",
                         markersize=2, color=y_line, alpha=0.85)
        elif style == "Line + Dots":
            self.ax.plot(vx, vy, linestyle="-", marker=".",
                         linewidth=1.0, markersize=3,
                         color=y_line, alpha=0.9)
        else:
            self.ax.plot(vx, vy, linestyle="-",
                         linewidth=1.0, color=y_line)

        # Encuadre tight con pequeño margen
        def _tight(arr):
            lo = float(np.min(arr))
            hi = float(np.max(arr))
            if lo == hi:
                lo -= 1.0; hi += 1.0
            span = hi - lo
            return lo - span * 0.05, hi + span * 0.05

        xlo, xhi = _tight(vx)
        ylo, yhi = _tight(vy)
        self.ax.set_xlim(xlo, xhi)
        self.ax.set_ylim(ylo, yhi)

        # Grid
        self.ax.grid(True, which="both",
                     color=self.grid_theme["grid_color"],
                     linewidth=0.6, alpha=0.6)

        # Labels con color del canal y unidad
        self.ax.set_xlabel(f"{ch_x['name']} (V)", color=x_text,
                           fontsize=10, fontweight="bold")
        self.ax.set_ylabel(f"{ch_y['name']} (V)", color=y_text,
                           fontsize=10, fontweight="bold")

        # Spines y ticks
        for spine in self.ax.spines.values():
            spine.set_color(self.grid_theme["grid_color"])
            spine.set_linewidth(1.2)
        self.ax.tick_params(axis="x", colors=self.grid_theme["text_color"])
        self.ax.tick_params(axis="y", colors=self.grid_theme["text_color"])

        # Título informativo arriba-derecha
        self.ax.text(
            0.98, 0.98,
            f"XY  ·  X: {ch_x['name']}   Y: {ch_y['name']}   ·   {len(vx)} pts",
            transform=self.ax.transAxes,
            ha="right", va="top",
            color=self.grid_theme["text_color"], fontsize=9,
            bbox=dict(boxstyle="round,pad=0.3",
                      facecolor=self.grid_theme["bg"],
                      edgecolor=self.grid_theme["grid_color"],
                      alpha=0.8)
        )

        self.canvas.draw()

    def open_xy_window(self):
        """Ventana de configuración del modo XY."""
        if self._xy_window is not None and self._xy_window.winfo_exists():
            self._xy_window.lift()
            return
        if self.time is None or len(self.channels) < 2:
            self._show_toast("XY needs at least 2 channels")
            return

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("XY mode")
        win.configure(bg=theme.COLOR_BG)
        # Geometry PRIMERO, chrome (transient) DESPUÉS (evita fantasma).
        self._make_resizable_dialog(win, 340, 280)
        self._apply_window_chrome(win)

        # Enable
        enable_var = tk.BooleanVar(value=self.xy_config["enabled"])
        def on_enable():
            self.xy_config["enabled"] = bool(enable_var.get())
            self.update_plot()
            self._mark_dirty()
        ttk.Checkbutton(win, text="Enable XY mode",
                        variable=enable_var, command=on_enable
                        ).pack(anchor="w", padx=14, pady=(14, 10))

        ch_names = [ch["name"] for ch in self.channels]

        def add_combo_row(label, key, default):
            row = tk.Frame(win, bg=theme.COLOR_BG)
            row.pack(fill=tk.X, padx=14, pady=(2, 4))
            tk.Label(row, text=label, bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                     font=("Segoe UI", 9), width=10, anchor="w"
                     ).pack(side=tk.LEFT)
            var = tk.StringVar(value=ch_names[default])
            combo = ttk.Combobox(row, textvariable=var,
                                 values=ch_names, state="readonly", width=16)
            combo.pack(side=tk.LEFT)

            def on_change(_e=None):
                n = var.get()
                for i, ch in enumerate(self.channels):
                    if ch["name"] == n:
                        self.xy_config[key] = i
                        break
                if self.xy_config["enabled"]:
                    self.update_plot()
                self._mark_dirty()
            combo.bind("<<ComboboxSelected>>", on_change)
            return var

        # Asegurar índices válidos
        xi = max(0, min(self.xy_config["x_idx"], len(ch_names) - 1))
        yi = max(0, min(self.xy_config["y_idx"], len(ch_names) - 1))
        add_combo_row("X axis:", "x_idx", xi)
        add_combo_row("Y axis:", "y_idx", yi)

        # Style
        style_row = tk.Frame(win, bg=theme.COLOR_BG)
        style_row.pack(fill=tk.X, padx=14, pady=(6, 4))
        tk.Label(style_row, text="Style:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9), width=10, anchor="w"
                 ).pack(side=tk.LEFT)
        style_var = tk.StringVar(value=self.xy_config["style"])
        style_combo = ttk.Combobox(style_row, textvariable=style_var,
                                   values=["Line", "Dots", "Line + Dots"],
                                   state="readonly", width=16)
        style_combo.pack(side=tk.LEFT)
        def on_style(_e=None):
            self.xy_config["style"] = style_var.get()
            if self.xy_config["enabled"]:
                self.update_plot()
            self._mark_dirty()
        style_combo.bind("<<ComboboxSelected>>", on_style)

        # Window
        win_row = tk.Frame(win, bg=theme.COLOR_BG)
        win_row.pack(fill=tk.X, padx=14, pady=(6, 4))
        tk.Label(win_row, text="Window:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9), width=10, anchor="w"
                 ).pack(side=tk.LEFT)
        win_var = tk.StringVar(value=self.xy_config["window"])
        win_combo = ttk.Combobox(win_row, textvariable=win_var,
                                 values=["Visible range", "Full capture"],
                                 state="readonly", width=16)
        win_combo.pack(side=tk.LEFT)
        def on_win(_e=None):
            self.xy_config["window"] = win_var.get()
            if self.xy_config["enabled"]:
                self.update_plot()
            self._mark_dirty()
        win_combo.bind("<<ComboboxSelected>>", on_win)

        # Close
        def on_close():
            self._xy_window = None
            win.destroy()

        ttk.Button(win, text="Close", command=on_close
                   ).pack(side=tk.BOTTOM, pady=14)

        win.protocol("WM_DELETE_WINDOW", on_close)
        self._xy_window = win
