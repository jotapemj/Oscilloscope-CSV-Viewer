"""Measurement panel and window, cursor state and their readout labels."""
import tkinter as tk

from tkinter import ttk

from ocv.core.measurements import (
    ALL_MEASUREMENTS,
    AMPLITUDE_MEASUREMENTS,
    MEASUREMENT_DESCRIPTIONS,
    TIME_MEASUREMENTS,
)
from ocv.core.scope import H_DIVS
from ocv.core.si_format import DEFAULT_UNIT, format_si
from ocv.ui import theme
from ocv.ui.meas_preview import make_measurement_preview


class MeasurementsMixin:
    """Mixin de OscilloscopeApp: usa su estado vía self."""

    def _build_measurements_panel(self):
        """Construye la cabecera del panel derecho. El contenido (filas de
        medidas) se regenera con _rebuild_measurements_panel."""
        # Header
        hdr = tk.Frame(self.meas_panel, bg=theme.COLOR_BG_ALT, height=28)
        hdr.pack(fill=tk.X, pady=(0, 4))
        hdr.pack_propagate(False)
        tk.Label(hdr, text="MEASUREMENTS",
                 bg=theme.COLOR_BG_ALT, fg=theme.COLOR_ACCENT,
                 font=("Segoe UI", 8, "bold")
                 ).pack(side=tk.LEFT, padx=10, pady=6)

        # Frame contenedor scrollable de filas
        self.meas_rows_frame = tk.Frame(self.meas_panel, bg=theme.COLOR_BG)
        self.meas_rows_frame.pack(fill=tk.BOTH, expand=True)

        # Placeholder
        self._rebuild_measurements_panel()

    def _rebuild_measurements_panel(self):
        """Reconstruye las filas con los valores actuales."""
        if not hasattr(self, "meas_rows_frame"):
            return

        # Limpiar contenido previo
        for w in self.meas_rows_frame.winfo_children():
            w.destroy()

        if not self.active_measurements:
            # Placeholder
            tk.Label(
                self.meas_rows_frame,
                text="No measurements\nadded",
                bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                font=("Segoe UI", 9, "italic"),
                justify="center"
            ).pack(expand=True, pady=20)
            return

        results = self.compute_measurements()
        # Dibujar una fila por resultado
        for i, r in enumerate(results):
            self._draw_meas_row(i, r)

    def _draw_meas_row(self, idx, result):
        """Dibuja una fila del panel de medidas con color del canal, nombre,
        valor y click derecho para eliminar."""
        row = tk.Frame(self.meas_rows_frame, bg=theme.COLOR_BG)
        row.pack(fill=tk.X, padx=4, pady=1)

        # Separador superior sutil
        sep = tk.Frame(row, bg=theme.COLOR_BORDER, height=1)
        sep.pack(fill=tk.X)

        # Contenido
        body = tk.Frame(row, bg=theme.COLOR_BG)
        body.pack(fill=tk.X, padx=6, pady=4)

        # Cabecera: canal + nombre de la medida (color del canal)
        top_line = tk.Frame(body, bg=theme.COLOR_BG)
        top_line.pack(fill=tk.X)

        tk.Label(
            top_line, text=result["channel_name"],
            bg=theme.COLOR_BG, fg=result["color"],
            font=("Segoe UI", 8, "bold")
        ).pack(side=tk.LEFT)
        tk.Label(
            top_line, text=f"  {result['name']}",
            bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
            font=("Segoe UI", 8)
        ).pack(side=tk.LEFT)

        # Valor en grande
        value_text = (format_si(result["value"], result["unit"])
                      if result["value"] is not None else "---")
        val_lbl = tk.Label(
            body, text=value_text,
            bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
            font=("Consolas", 11, "bold"),
            anchor="w"
        )
        val_lbl.pack(fill=tk.X, pady=(2, 0))

        # Click derecho en cualquier parte de la fila → menú eliminar
        def show_ctx(event, i=idx):
            menu = tk.Menu(
                self.root, tearoff=0,
                bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
                activebackground=theme.COLOR_ACCENT, activeforeground=theme.COLOR_TEXT_ON_ACCENT,
                bd=0,
            )
            menu.add_command(
                label="Remove measurement",
                command=lambda: self._remove_measurement(i)
            )
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        for w in (row, body, top_line, val_lbl):
            w.bind("<Button-3>", show_ctx)

    def _remove_measurement(self, idx):
        """Elimina la medida activa en la posición idx."""
        if 0 <= idx < len(self.active_measurements):
            self.active_measurements.pop(idx)
            self.update_plot()
            self._mark_dirty()

        
        
    

    def _cursors_vertical_active(self):
        """True si los cursores verticales (de tiempo) deben pintarse."""
        if not self.cursors_enabled.get():
            return False
        m = self.cursor_mode.get()
        return m in ("Vertical", "Horizontal + Vertical")

    def _cursors_horizontal_active(self):
        """True si los cursores horizontales (de voltaje) deben pintarse."""
        if not self.cursors_enabled.get():
            return False
        m = self.cursor_mode.get()
        return m in ("Horizontal", "Horizontal + Vertical")

    def update_cursors_info_label(self):
        v_on = self._cursors_vertical_active()
        h_on = self._cursors_horizontal_active()

        if not (v_on or h_on):
            self.cursors_info_label.set_text("")
            return

        parts = []

        # Info de cursores verticales (tiempo)
        if v_on and len(self.cursor_x) == 2:
            t1, t2 = sorted(self.cursor_x)
            dt = t2 - t1
            freq_text = format_si(1.0 / dt, "Hz") if dt > 0 else "—"
            # Sumamos time_origin a t1/t2 para mostrar tiempos REALES del
            # CSV (consistente con las etiquetas del eje X). Δt y 1/Δt
            # son diferencias → time_origin se cancela, no hace falta sumar.
            t_o = self._t_display_origin()
            parts.append(
                f"t1: {format_si(t1 + t_o, 's')}   "
                f"t2: {format_si(t2 + t_o, 's')}   "
                f"Δt: {format_si(dt, 's')}   "
                f"1/Δt: {freq_text}"
            )

        # Info de cursores horizontales (voltaje)
        if h_on and len(self.cursor_y) == 2:
            offset = self.v_offset.get() if self.channels else 0.0
            v1 = self.cursor_y[0] + offset
            v2 = self.cursor_y[1] + offset
            dv = v2 - v1
            prefix = ""
            unit = DEFAULT_UNIT
            if self.channels:
                prefix = f"[{self.channels[self.active_channel_idx]['name']}] "
                unit = self._active_unit()
            parts.append(
                f"{prefix}"
                f"V1: {format_si(v1, unit)}   "
                f"V2: {format_si(v2, unit)}   "
                f"ΔV: {format_si(abs(dv), unit)}"
            )

        # Juntar con un separador visual
        self.cursors_info_label.set_text("     |     ".join(parts))

    def update_horizontal_cursors_info_label(self):
        # Legacy: ya no se usa, ahora todo va en update_cursors_info_label
        return True

        
    # -----------------------------
 
    def get_visible_signal(self):
        t_start = self.t_offset.get()
        window = self.t_div.get() * H_DIVS
        t_end = t_start + window

        mask = (self.time >= t_start) & (self.time <= t_end)
        t = self.time[mask] - t_start
        v = self.voltage[mask]

        return t, v

        
        

    def compute_measurements(self):
        """Calcula las medidas activas sobre la señal VISIBLE de cada canal.
        Devuelve una lista de dicts:
          [{"channel_idx": i, "channel_name": str, "color": hex,
            "name": str, "value": float|None, "unit": str}]
        Las medidas dual-channel (Phase) usan también 'ref_channel_idx'.
        """
        if not self.active_measurements or not self.channels:
            return []

        t_start = self.t_offset.get()
        window = self.t_div.get() * H_DIVS
        t_end = t_start + window
        mask = (self.time >= t_start) & (self.time <= t_end)
        t_vis = self.time[mask] - t_start

        results = []
        for meas in self.active_measurements:
            ch_idx = meas["channel_idx"]
            name = meas["name"]
            if ch_idx < 0 or ch_idx >= len(self.channels):
                continue
            if name not in ALL_MEASUREMENTS:
                continue
            ch = self.channels[ch_idx]
            v_vis = ch["voltage"][mask]
            unit, func = ALL_MEASUREMENTS[name]
            # v1.29: sustituir la unidad base "V" / "V/s" por la unit real
            # del canal. Así Peak-Peak en un canal "A" muestra "Apk-pk",
            # etc. Las unidades estructurales (s, Hz, %, °) se mantienen.
            ch_unit = ch.get("unit", DEFAULT_UNIT)
            if unit == "V":
                unit = ch_unit
            elif unit == "V/s":
                unit = f"{ch_unit}/s"

            # Detectar dual-channel: func es una tupla ('dual', real_func)
            is_dual = isinstance(func, tuple) and len(func) == 2 and func[0] == "dual"
            try:
                if is_dual:
                    ref_idx = meas.get("ref_channel_idx")
                    if ref_idx is None or not (0 <= ref_idx < len(self.channels)):
                        value = None
                    else:
                        v_ref = self.channels[ref_idx]["voltage"][mask]
                        value = func[1](t_vis, v_vis, v_ref)
                else:
                    value = func(t_vis, v_vis)
            except Exception:
                value = None

            results.append({
                "channel_idx":  ch_idx,
                "channel_name": ch["name"],
                "color":        ch["color"],
                "name":         name,
                "value":        value,
                "unit":         unit,
                "ref_channel_idx": meas.get("ref_channel_idx"),
            })
        return results

    # Alias retrocompatible
    def compute_basic_measurements(self):
        return self.compute_measurements()

    def update_measurements_label(self):
        """Actualiza el panel derecho de medidas activas."""
        self._rebuild_measurements_panel()
        # El label inline inferior izquierdo ya no se usa, lo limpiamos
        if hasattr(self, "meas_info_label"):
            self.meas_info_label.set_text("")

        
        
        
    def open_meas_window(self):
        # Singleton — ver open_xy_window. Si ya hay una ventana Meas
        # abierta, la traemos al frente en lugar de abrir otra.
        if getattr(self, "_meas_window", None) is not None \
                and self._meas_window.winfo_exists():
            self._meas_window.lift()
            self._meas_window.focus_force()
            return
        if not self.channels:
            return

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("Add Measurements")
        win.configure(bg=theme.COLOR_BG)
        # Geometry PRIMERO, chrome (transient) DESPUÉS (evita fantasma).
        # Chrome unificado (icono + transient para taskbar en Windows).
        self._make_resizable_dialog(win, 640, 620, min_w=560, min_h=500)
        self._apply_window_chrome(win)
        self._meas_window = win
        # Cuando se cierre, limpiar referencia para que la próxima vez
        # se pueda abrir.
        def _on_meas_close():
            self._meas_window = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _on_meas_close)
        self._bind_escape_to_close(win, on_close=_on_meas_close)

        # Estado local de la ventana: medida actualmente seleccionada en el preview
        selected_name = tk.StringVar(value="Peak-Peak")

        # ---- Header ----
        hdr = tk.Frame(win, bg=theme.COLOR_BG_ALT, height=40)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="ADD MEASUREMENTS",
                 bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT, padx=14, pady=8)

        # ---- Preview panel (imagen + descripción) ----
        preview_frame = tk.Frame(win, bg=theme.COLOR_BG_ALT, height=180)
        preview_frame.pack(fill=tk.X, padx=10, pady=(8, 6))
        preview_frame.pack_propagate(False)

        # Canvas para la imagen mpl
        canvas_holder = tk.Frame(preview_frame, bg="#3a3a3a", width=300, height=160)
        canvas_holder.pack(side=tk.LEFT, padx=8, pady=8)
        canvas_holder.pack_propagate(False)

        desc_frame = tk.Frame(preview_frame, bg=theme.COLOR_BG_ALT)
        desc_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 8), pady=8)

        title_lbl = tk.Label(
            desc_frame, text="", bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            font=("Segoe UI", 11, "bold"), anchor="w", justify="left")
        title_lbl.pack(anchor="w")

        desc_lbl = tk.Label(
            desc_frame, text="", bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT_DIM,
            font=("Segoe UI", 9), anchor="nw", justify="left",
            wraplength=280)
        desc_lbl.pack(anchor="w", fill=tk.BOTH, expand=True, pady=(4, 0))

        # Canvas mpl
        preview_canvas = {"obj": None}  # holder mutable

        def update_preview(name):
            # Limpiar el canvas anterior
            for w in canvas_holder.winfo_children():
                w.destroy()
            fig = make_measurement_preview(name)
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            c = FigureCanvasTkAgg(fig, master=canvas_holder)
            c.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            c.draw()
            preview_canvas["obj"] = c
            title_lbl.config(text=name)
            desc_lbl.config(text=MEASUREMENT_DESCRIPTIONS.get(name, ""))

        # ---- Source + Add ----
        source_row = tk.Frame(win, bg=theme.COLOR_BG)
        source_row.pack(fill=tk.X, padx=10, pady=(6, 4))

        tk.Label(source_row, text="Source:",
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(4, 8))

        channel_names = [ch["name"] for ch in self.channels]
        source_var = tk.StringVar(value=channel_names[self.active_channel_idx])
        source_combo = ttk.Combobox(
            source_row, textvariable=source_var,
            values=channel_names, state="readonly", width=14)
        source_combo.pack(side=tk.LEFT)

        # Combo de referencia (solo visible para medidas dual-channel)
        ref_lbl = tk.Label(source_row, text="vs",
                           bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                           font=("Segoe UI", 10))
        ref_default = channel_names[1] if len(channel_names) > 1 else channel_names[0]
        ref_var = tk.StringVar(value=ref_default)
        ref_combo = ttk.Combobox(
            source_row, textvariable=ref_var,
            values=channel_names, state="readonly", width=14)

        def _is_dual(name):
            if name not in ALL_MEASUREMENTS:
                return False
            _, f = ALL_MEASUREMENTS[name]
            return isinstance(f, tuple) and len(f) == 2 and f[0] == "dual"

        def _refresh_dual_ui(*_):
            if _is_dual(selected_name.get()):
                ref_lbl.pack(side=tk.LEFT, padx=(8, 6))
                ref_combo.pack(side=tk.LEFT)
            else:
                ref_lbl.pack_forget()
                ref_combo.pack_forget()

        selected_name.trace_add("write", _refresh_dual_ui)

        def on_add():
            if len(self.active_measurements) >= self.MAX_MEASUREMENTS:
                msg_lbl.config(
                    text=f"Max {self.MAX_MEASUREMENTS} measurements reached.",
                    fg=theme.COLOR_DANGER)
                return
            # Canal por nombre
            src_name = source_var.get()
            ch_idx = 0
            for i, ch in enumerate(self.channels):
                if ch["name"] == src_name:
                    ch_idx = i
                    break
            new_meas = {
                "channel_idx": ch_idx,
                "name": selected_name.get(),
            }
            # Para medidas dual-channel, guardar el canal de referencia
            if _is_dual(selected_name.get()):
                ref_name = ref_var.get()
                ref_idx = ch_idx
                for i, ch in enumerate(self.channels):
                    if ch["name"] == ref_name:
                        ref_idx = i
                        break
                if ref_idx == ch_idx:
                    msg_lbl.config(
                        text="Phase needs two different channels.",
                        fg=theme.COLOR_DANGER)
                    return
                new_meas["ref_channel_idx"] = ref_idx
            self.active_measurements.append(new_meas)
            self.update_plot()
            self._mark_dirty()
            msg_lbl.config(
                text=f"Added {selected_name.get()} on {src_name}  ({len(self.active_measurements)}/{self.MAX_MEASUREMENTS})",
                fg=theme.COLOR_ACCENT)

        add_btn = ttk.Button(source_row, text="Add", command=on_add,
                             style="Accent.TButton")
        add_btn.pack(side=tk.RIGHT, padx=(4, 4))

        close_btn = ttk.Button(source_row, text="Close",
                               command=win.destroy)
        close_btn.pack(side=tk.RIGHT, padx=(4, 4))

        msg_lbl = tk.Label(source_row, text="",
                           bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                           font=("Segoe UI", 8))
        msg_lbl.pack(side=tk.RIGHT, padx=(10, 10))

        # ---- Categorías de medidas ----
        cats_frame = tk.Frame(win, bg=theme.COLOR_BG)
        cats_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 10))

        # Estado: solo una sección abierta a la vez (tipo accordion)
        section_state = {"open": "AMPLITUDE MEASUREMENTS"}
        section_grids = {}   # title -> grid Frame (el contenido que se oculta)
        section_carets = {}  # title -> Label con ▼/▶

        def make_cat_section(parent, title, meas_dict):
            # Cabecera clicable
            hdr_f = tk.Frame(parent, bg=theme.COLOR_BG_ALT, height=28, cursor="hand2")
            hdr_f.pack(fill=tk.X, pady=(6, 0))
            hdr_f.pack_propagate(False)

            caret = tk.Label(hdr_f, text="▼", bg=theme.COLOR_BG_ALT, fg=theme.COLOR_ACCENT,
                             font=("Segoe UI", 9, "bold"))
            caret.pack(side=tk.LEFT, padx=(10, 6), pady=4)
            title_lbl = tk.Label(hdr_f, text=title, bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
                                 font=("Segoe UI", 9, "bold"))
            title_lbl.pack(side=tk.LEFT, pady=4)

            section_carets[title] = caret

            grid = tk.Frame(parent, bg=theme.COLOR_BG)
            # Se empaqueta solo si está abierto
            section_grids[title] = grid

            COLS = 3
            for i, name in enumerate(meas_dict.keys()):
                r, c = divmod(i, COLS)
                btn = tk.Button(
                    grid, text=name,
                    bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
                    activebackground=theme.COLOR_BG_HOVER,
                    activeforeground=theme.COLOR_TEXT,
                    bd=1, relief="flat",
                    font=("Segoe UI", 9),
                    padx=8, pady=6,
                    anchor="w", width=18,
                    highlightbackground=theme.COLOR_BORDER,
                    highlightthickness=1,
                )
                btn.grid(row=r, column=c, padx=3, pady=2, sticky="we")

                # Hover "sombra": cambia el borde a accent y sube el bg
                def on_enter(e, b=btn):
                    # No sobrescribir si está seleccionado (borde accent gordo)
                    cur_thick = int(b.cget("highlightthickness"))
                    if cur_thick < 2:
                        b.config(bg=theme.COLOR_BG_HOVER, highlightbackground=theme.COLOR_ACCENT)
                def on_leave(e, b=btn):
                    cur_thick = int(b.cget("highlightthickness"))
                    if cur_thick < 2:
                        b.config(bg=theme.COLOR_BG_ALT, highlightbackground=theme.COLOR_BORDER)
                btn.bind("<Enter>", on_enter)
                btn.bind("<Leave>", on_leave)

                def make_handler(n, b):
                    def _click():
                        selected_name.set(n)
                        update_preview(n)
                        for child in grid.winfo_children():
                            if isinstance(child, tk.Button):
                                child.config(highlightbackground=theme.COLOR_BORDER,
                                             highlightthickness=1,
                                             bg=theme.COLOR_BG_ALT)
                        b.config(highlightbackground=theme.COLOR_ACCENT,
                                 highlightthickness=2)
                    return _click
                btn.config(command=make_handler(name, btn))
                grid.columnconfigure(c, weight=1)

            # Toggle al clicar header
            def toggle(_e=None):
                is_open = section_state["open"] == title
                if is_open:
                    # Cerrar = no hacer nada (mantener una abierta)
                    return
                # Cerrar la actual y abrir esta
                old = section_state["open"]
                if old in section_grids:
                    section_grids[old].pack_forget()
                    section_carets[old].config(text="▶")
                section_state["open"] = title
                section_grids[title].pack(fill=tk.X, padx=4, pady=4,
                                          after=section_carets[title].master)
                section_carets[title].config(text="▼")
            hdr_f.bind("<Button-1>", toggle)
            caret.bind("<Button-1>", toggle)
            title_lbl.bind("<Button-1>", toggle)

        make_cat_section(cats_frame, "AMPLITUDE MEASUREMENTS", AMPLITUDE_MEASUREMENTS)
        make_cat_section(cats_frame, "TIME MEASUREMENTS", TIME_MEASUREMENTS)

        # Abrir solo la primera
        section_grids["AMPLITUDE MEASUREMENTS"].pack(
            fill=tk.X, padx=4, pady=4,
            after=section_carets["AMPLITUDE MEASUREMENTS"].master
        )
        section_carets["TIME MEASUREMENTS"].config(text="▶")

        # Inicializar preview con la medida por defecto
        update_preview(selected_name.get())

        
    def on_cursors_toggle(self):
        if self.cursors_enabled.get():
            self.cursor_mode_combo.config(state="readonly")
        else:
            self.cursor_mode_combo.config(state="disabled")
        self.update_plot()

        
        
        
    def center_cursors(self):
        if self.time is None:
            return

        # Ventana visible actual
        t_start = self.t_offset.get()
        window = self.t_div.get() * H_DIVS

        # Recolocar cursores dentro del view
        self.cursor_x = [
            t_start + window * 0.25,
            t_start + window * 0.75,
        ]

        # Redibujar
        self.update_plot()
