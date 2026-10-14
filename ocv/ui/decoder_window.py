"""Protocol decoder window: channel mapping, parameters, frame table."""
import tkinter as tk

from tkinter import ttk

from ocv.core.scope import H_DIVS
from ocv.core.si_format import format_si
from ocv.decoders import DECODER_CATEGORIES, DECODER_REGISTRY
from ocv.ui import theme
from ocv.ui.widgets import Tooltip


class DecoderWindow:
    """Ventana de configuración y resultados del decoder de protocolos.

    Flujo:
      1. Usuario elige protocolo del combo.
      2. La UI se reconstruye según los `channels_needed` y `default_params`.
      3. Asigna canales reales del scope a cada rol del protocolo.
      4. Pulsa Decode → se llama al método decode() del decoder.
      5. Frames resultantes:
         - Se muestran como tabla en esta ventana.
         - Se envían al scope para pintar BAJO la traza correspondiente
           (scope._set_protocol_frames(frames, channel_roles)).
    """

    def __init__(self, scope):
        self.scope = scope
        self.win = tk.Toplevel(scope.root)
        self.win.withdraw()  # anti-flash
        self.win.title("Protocol decoder")
        # Centrado sobre la principal (igual que FFT/Eye). En lugar de
        # geometry("700x600") fijo que dejaba al WM elegir posición
        # (acababa en cualquier esquina, cambiando entre aperturas).
        scope._center_dialog(self.win, 700, 960)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        # ESC también cierra la ventana
        try:
            self.win.bind("<Escape>", lambda _e: self._on_close())
        except Exception:
            pass
        # Hereda icono de la ventana raíz y se marca transient para que en
        # Windows no aparezca como entrada propia en la taskbar.
        scope._apply_window_chrome(self.win)

        # Estado
        self.current_decoder_cls = DECODER_REGISTRY[0]
        # Variables Tk para mapeo canal->rol y params
        self.role_vars = {}     # role_name -> StringVar(canal)
        self.param_vars = {}    # param_name -> Var (Bool/Int/StringVar)
        self.last_frames = []

        self._build_ui()
        self._on_protocol_changed()

    def _on_close(self):
        """Al cerrar la ventana, limpiar el render de frames bajo la traza."""
        try:
            self.scope._set_protocol_frames([], {})
        except Exception:
            pass
        self.win.destroy()

    # Token interno para "canal no asignado" en comboboxes de roles opcionales.
    _NONE_TOKEN = "— (none) —"

    def _build_ui(self):
        self.win.configure(bg=theme.COLOR_BG)
        # NO sobrescribir geometry aquí: el __init__ ya llamó a
        # _center_dialog(700, 960) que centra Y dimensiona. Hacer
        # geometry("780x720") aquí machacaba ese tamaño y dejaba la
        # tabla de frames cortada por la parte de abajo.

        # Header igual estilo MEAS
        hdr = tk.Frame(self.win, bg=theme.COLOR_BG_ALT, height=40)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="PROTOCOL DECODER",
                 bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 11, "bold")
                 ).pack(side=tk.LEFT, padx=14, pady=8)
        # Indicador del protocolo seleccionado a la derecha del header
        self._selected_lbl = tk.Label(
            hdr, text="", bg=theme.COLOR_BG_ALT, fg=theme.COLOR_ACCENT,
            font=("Segoe UI", 10, "bold"))
        self._selected_lbl.pack(side=tk.RIGHT, padx=14, pady=8)

        # ---- Panel superior: acordeón de categorías ----
        cats_outer = tk.Frame(self.win, bg=theme.COLOR_BG)
        cats_outer.pack(fill=tk.X, padx=10, pady=(8, 4))

        self.protocol_var = tk.StringVar(value=DECODER_REGISTRY[0].name)

        # Estado del acordeón: solo una sección abierta a la vez
        first_cat = next(iter(DECODER_CATEGORIES.keys()))
        section_state = {"open": first_cat}
        section_grids = {}
        section_carets = {}
        # Mapa botón -> nombre de protocolo, para repintar selección
        self._proto_buttons = {}

        def make_cat_section(parent, title, decoder_list):
            hdr_f = tk.Frame(parent, bg=theme.COLOR_BG_ALT, height=28, cursor="hand2")
            hdr_f.pack(fill=tk.X, pady=(6, 0))
            hdr_f.pack_propagate(False)

            caret = tk.Label(hdr_f, text="▼", bg=theme.COLOR_BG_ALT, fg=theme.COLOR_ACCENT,
                             font=("Segoe UI", 9, "bold"))
            caret.pack(side=tk.LEFT, padx=(10, 6), pady=4)
            title_lbl = tk.Label(hdr_f, text=title, bg=theme.COLOR_BG_ALT,
                                 fg=theme.COLOR_TEXT,
                                 font=("Segoe UI", 9, "bold"))
            title_lbl.pack(side=tk.LEFT, pady=4)
            section_carets[title] = caret

            grid = tk.Frame(parent, bg=theme.COLOR_BG)
            section_grids[title] = grid

            COLS = 2
            for i, dec_cls in enumerate(decoder_list):
                r, c = divmod(i, COLS)
                btn = tk.Button(
                    grid, text=dec_cls.name,
                    bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
                    activebackground=theme.COLOR_BG_HOVER,
                    activeforeground=theme.COLOR_TEXT,
                    bd=1, relief="flat",
                    font=("Segoe UI", 9),
                    padx=8, pady=6,
                    anchor="w", width=28,
                    highlightbackground=theme.COLOR_BORDER,
                    highlightthickness=1,
                )
                btn.grid(row=r, column=c, padx=3, pady=2, sticky="we")
                self._proto_buttons[dec_cls.name] = btn

                # Tooltip con la descripción del protocolo
                desc = getattr(dec_cls, "description", "") or ""
                if desc:
                    Tooltip(btn, desc)

                def on_enter(e, b=btn):
                    if int(b.cget("highlightthickness")) < 2:
                        b.config(bg=theme.COLOR_BG_HOVER,
                                 highlightbackground=theme.COLOR_ACCENT)
                def on_leave(e, b=btn):
                    if int(b.cget("highlightthickness")) < 2:
                        b.config(bg=theme.COLOR_BG_ALT,
                                 highlightbackground=theme.COLOR_BORDER)
                btn.bind("<Enter>", on_enter)
                btn.bind("<Leave>", on_leave)

                def make_handler(name=dec_cls.name, b=btn):
                    def _click():
                        # Repintar todos los botones a estado normal
                        for nm, btn_ref in self._proto_buttons.items():
                            btn_ref.config(highlightbackground=theme.COLOR_BORDER,
                                           highlightthickness=1,
                                           bg=theme.COLOR_BG_ALT)
                        b.config(highlightbackground=theme.COLOR_ACCENT,
                                 highlightthickness=2)
                        self.protocol_var.set(name)
                        self._on_protocol_changed()
                    return _click
                btn.config(command=make_handler())
                grid.columnconfigure(c, weight=1)

            def toggle(_e=None):
                if section_state["open"] == title:
                    return
                old = section_state["open"]
                if old in section_grids:
                    section_grids[old].pack_forget()
                    section_carets[old].config(text="▶")
                section_state["open"] = title
                section_grids[title].pack(
                    fill=tk.X, padx=4, pady=4,
                    after=section_carets[title].master)
                section_carets[title].config(text="▼")
            hdr_f.bind("<Button-1>", toggle)
            caret.bind("<Button-1>", toggle)
            title_lbl.bind("<Button-1>", toggle)

        for cat_title, dec_list in DECODER_CATEGORIES.items():
            make_cat_section(cats_outer, cat_title, dec_list)

        # Abrir solo la primera categoría
        section_grids[first_cat].pack(
            fill=tk.X, padx=4, pady=4,
            after=section_carets[first_cat].master)
        for other in list(DECODER_CATEGORIES.keys())[1:]:
            section_carets[other].config(text="▶")

        # ---- Diagrama explicativo del frame ----
        # Cada decoder puede aportar un diagrama (método draw_diagram).
        # Si no lo implementa, este panel muestra un placeholder discreto.
        # Se redibuja al cambiar protocolo y al cambiar el tamaño del canvas.
        self.diagram_frame = ttk.LabelFrame(
            self.win, text="Frame structure", padding=4)
        self.diagram_frame.pack(fill=tk.X, padx=10, pady=(4, 4))

        # Canvas con altura fija razonable; ancho responde al pack.
        # bg viene del tema; lo refrescamos en _refresh_diagram.
        self.diagram_canvas = tk.Canvas(
            self.diagram_frame, height=130, bd=0, highlightthickness=0,
            bg=theme.COLOR_BG_ALT,
        )
        self.diagram_canvas.pack(fill=tk.X, expand=False)
        # Repintar al redimensionar (el ancho cambia con la ventana).
        self.diagram_canvas.bind(
            "<Configure>", lambda _e: self._refresh_diagram())

        # ---- Channel mapping y parámetros ----
        ctrl = ttk.Frame(self.win, padding=(10, 4, 10, 4))
        ctrl.pack(side=tk.TOP, fill=tk.X)

        # Frame dinámico para mapeo de canales
        self.channel_map_frame = ttk.LabelFrame(
            ctrl, text="Channel mapping", padding=8)
        self.channel_map_frame.pack(fill=tk.X, pady=(4, 4))

        # Frame dinámico para parámetros
        self.params_frame = ttk.LabelFrame(
            ctrl, text="Parameters", padding=8)
        self.params_frame.pack(fill=tk.X, pady=(4, 4))

        # Botones
        btn_row = ttk.Frame(ctrl)
        btn_row.pack(fill=tk.X, pady=(8, 4))
        decode_btn = ttk.Button(btn_row, text="Decode",
                                style="Accent.TButton",
                                command=self._decode)
        decode_btn.pack(side=tk.LEFT, padx=2)
        Tooltip(decode_btn,
                "Decode the full capture using the selected channels and "
                "parameters.\nResults are listed below and overlaid on the "
                "main plot.")
        clear_btn = ttk.Button(btn_row, text="Clear", command=self._clear)
        clear_btn.pack(side=tk.LEFT, padx=2)
        Tooltip(clear_btn, "Clear decoded frames and remove overlay from main plot")
        export_btn = ttk.Button(btn_row, text="Export CSV…",
                                command=self._export_csv)
        export_btn.pack(side=tk.LEFT, padx=2)
        Tooltip(export_btn, "Save the decoded frames table to a CSV file")

        self.status_label = tk.Label(
            btn_row, text="", bg=theme.COLOR_BG,
            fg=theme.COLOR_TEXT_DIM, font=("Segoe UI", 9))
        self.status_label.pack(side=tk.RIGHT, padx=10)

        # Tabla de resultados
        table_frame = ttk.Frame(self.win)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        cols = ("t_start", "type", "label", "duration")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings",
                                 height=12)
        self.tree.heading("t_start", text="Time")
        self.tree.heading("type",    text="Type")
        self.tree.heading("label",   text="Value")
        self.tree.heading("duration", text="Duration")
        self.tree.column("t_start", width=120, anchor="w")
        self.tree.column("type",    width=80,  anchor="w")
        self.tree.column("label",   width=240, anchor="w")
        self.tree.column("duration", width=100, anchor="w")
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(table_frame, orient="vertical",
                               command=self.tree.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=scroll.set)

        # Doble-click en una fila → centrar la vista del scope ahí
        self.tree.bind("<Double-1>", self._on_row_double_click)

        # Marcar como seleccionado el botón del protocolo inicial
        init_btn = self._proto_buttons.get(self.protocol_var.get())
        if init_btn is not None:
            init_btn.config(highlightbackground=theme.COLOR_ACCENT,
                            highlightthickness=2)

    def _on_protocol_changed(self):
        # Localizar la clase
        name = self.protocol_var.get()
        for d in DECODER_REGISTRY:
            if d.name == name:
                self.current_decoder_cls = d
                break

        # Etiqueta superior con nombre del protocolo activo
        try:
            self._selected_lbl.config(text=name)
        except Exception:
            pass

        # Reconstruir channel mapping
        for w in self.channel_map_frame.winfo_children():
            w.destroy()
        self.role_vars = {}
        ch_names = [ch["name"] for ch in self.scope.channels]
        required = list(self.current_decoder_cls.channels_needed)
        optional = list(getattr(self.current_decoder_cls,
                                "channels_optional", []))

        if not ch_names:
            tk.Label(self.channel_map_frame,
                     text="No channels loaded. Open a CSV first.",
                     bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT_DIM
                     ).pack(anchor="w")
        else:
            # Layout: cabecera + filas. Obligatorios primero, opcionales después
            # en una sub-sección visualmente diferenciada.
            all_roles = [(r, False) for r in required] + \
                        [(r, True)  for r in optional]
            for i, (role, is_opt) in enumerate(all_roles):
                row = ttk.Frame(self.channel_map_frame)
                row.pack(fill=tk.X, pady=2)
                label_text = f"{role}:" + ("  (opt)" if is_opt else "")
                ttk.Label(row, text=label_text, width=14).pack(side=tk.LEFT)

                # Para opcionales añadimos el token "(none)" al inicio
                if is_opt:
                    values = [self._NONE_TOKEN] + ch_names
                    default = self._NONE_TOKEN
                else:
                    values = ch_names
                    default = ch_names[i] if i < len(ch_names) else ch_names[0]

                var = tk.StringVar(value=default)
                self.role_vars[role] = var
                cb = ttk.Combobox(row, textvariable=var, values=values,
                                  state="readonly", width=18)
                cb.pack(side=tk.LEFT, padx=4)
                if is_opt:
                    Tooltip(cb,
                            f"Optional. Channel carrying the {role} signal.\n"
                            f"Set to '{self._NONE_TOKEN}' to skip this role.")
                else:
                    Tooltip(cb, f"Channel carrying the {role} signal")

        # Reconstruir parámetros
        for w in self.params_frame.winfo_children():
            w.destroy()
        self.param_vars = {}
        params = self.current_decoder_cls.default_params()
        # Layout: 2 columnas
        i = 0
        for pname, pdef in params.items():
            row_idx = i // 2
            col_idx = i % 2
            cell = ttk.Frame(self.params_frame)
            cell.grid(row=row_idx, column=col_idx, padx=8, pady=2, sticky="w")
            ttk.Label(cell, text=f"{pdef['label']}:", width=18,
                      ).pack(side=tk.LEFT)
            kind = pdef["kind"]
            default = pdef["default"]
            if kind == "bool":
                var = tk.BooleanVar(value=bool(default))
                w = ttk.Checkbutton(cell, variable=var)
            elif kind == "choice":
                var = tk.StringVar(value=str(default))
                w = ttk.Combobox(cell, textvariable=var,
                                 values=[str(o) for o in pdef["options"]],
                                 state="readonly", width=18)
            elif kind == "int":
                var = tk.IntVar(value=int(default))
                w = ttk.Spinbox(cell, from_=0, to=int(1e9),
                                textvariable=var, width=12)
            else:  # float
                var = tk.DoubleVar(value=float(default))
                w = ttk.Spinbox(cell, from_=0, to=1e9, increment=0.1,
                                textvariable=var, width=12)
            w.pack(side=tk.LEFT)
            self.param_vars[pname] = (var, kind)
            i += 1

        # Refrescar diagrama explicativo del frame
        self._refresh_diagram()

    def _refresh_diagram(self):
        """Limpia el canvas de diagrama y pide al decoder activo que se
        dibuje. Si no hay diagrama implementado, muestra un placeholder."""
        if not hasattr(self, "diagram_canvas"):
            return
        c = self.diagram_canvas
        c.delete("all")

        # Asegurar fondo coherente con el tema actual
        c.configure(bg=theme.COLOR_BG_ALT)

        # El widget puede no estar aún mapeado: usar winfo_width como mejor
        # aproximación, con fallback al request.
        w = c.winfo_width()
        if w <= 1:
            w = c.winfo_reqwidth() or 600
        h = int(c.cget("height"))

        dgm_theme = {
            "bg":     theme.COLOR_BG_ALT,
            "fg":     theme.COLOR_TEXT,
            "dim":    theme.COLOR_TEXT_DIM,
            "accent": theme.COLOR_ACCENT,
            "ok":     "#7fdb7f",
            "err":    "#ff6b6b",
            "data":   "#5fa8ff",
            "ctrl":   "#888888",
            "border": theme.COLOR_BORDER,
        }

        drew = False
        try:
            drew = bool(
                self.current_decoder_cls.draw_diagram(c, 0, 0, w, h, dgm_theme))
        except Exception:
            # Si el diagrama falla, no rompemos la ventana — sólo logueamos.
            import traceback
            traceback.print_exc()
            drew = False

        # Si el decoder no aporta diagrama, mostrar un placeholder discreto
        if not drew:
            c.create_text(
                w / 2, h / 2,
                text="(no diagram available for this protocol)",
                fill=theme.COLOR_TEXT_DIM, font=("Segoe UI", 9, "italic"))

    def _collect_params(self):
        """Lee los param_vars y devuelve dict {name: value} con conversiones
        adecuadas (especialmente: convertir str 'choice' a int/str según
        el tipo de la opción original)."""
        result = {}
        defaults = self.current_decoder_cls.default_params()
        for name, (var, kind) in self.param_vars.items():
            val = var.get()
            if kind == "choice":
                # Convertir a tipo nativo de la opción
                options = defaults[name]["options"]
                # Si las options son ints, intentar parsear
                if all(isinstance(o, int) for o in options):
                    try:
                        val = int(val)
                    except ValueError:
                        val = options[0]
            result[name] = val
        return result

    def _decode(self):
        if not self.scope.channels:
            self.status_label.config(text="No channels loaded.")
            return
        # Mapear roles a arrays. Para roles opcionales, "(none)" → None.
        ch_voltages = {}
        ch_by_name = {ch["name"]: ch for ch in self.scope.channels}
        roles_to_idx = {}
        optional_set = set(getattr(self.current_decoder_cls,
                                   "channels_optional", []))
        for role, var in self.role_vars.items():
            name = var.get()
            if role in optional_set and name == self._NONE_TOKEN:
                ch_voltages[role] = None
                continue
            if name not in ch_by_name:
                self.status_label.config(text=f"Channel {name} not found")
                return
            ch_voltages[role] = ch_by_name[name]["voltage"]
            roles_to_idx[role] = self.scope.channels.index(ch_by_name[name])

        params = self._collect_params()
        try:
            frames = self.current_decoder_cls.decode(
                self.scope.time, ch_voltages, params)
        except Exception as e:
            self.status_label.config(text=f"Decode error: {e}")
            import traceback; traceback.print_exc()
            return

        self.last_frames = frames
        self._populate_table(frames)
        # Pasar al scope para overlay bajo la traza. Elegimos como "principal"
        # el primer rol obligatorio que esté efectivamente mapeado.
        primary_idx = None
        for role in self.current_decoder_cls.channels_needed:
            if role in roles_to_idx:
                primary_idx = roles_to_idx[role]
                break
        try:
            if primary_idx is not None:
                self.scope._set_protocol_frames(frames,
                                                {"primary": primary_idx})
        except Exception:
            pass
        self.status_label.config(text=f"{len(frames)} frames")

    def _populate_table(self, frames):
        # Limpiar
        for it in self.tree.get_children():
            self.tree.delete(it)
        # time_origin del scope: suma al t_start para mostrar tiempos REALES
        # (consistentes con las etiquetas del eje X principal).
        # Duration NO se ajusta porque es una diferencia.
        t_o = self.scope._t_display_origin() if hasattr(self.scope, "_t_display_origin") else getattr(self.scope, "time_origin", 0.0)
        for f in frames:
            t_str = format_si(f["t_start"] + t_o, "s")
            dur = format_si(f["t_end"] - f["t_start"], "s")
            self.tree.insert("", "end", values=(t_str, f["type"],
                                                f.get("label", ""), dur))

    def _on_row_double_click(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        if idx < 0 or idx >= len(self.last_frames):
            return
        f = self.last_frames[idx]
        # Centrar la vista del scope en el frame seleccionado
        try:
            t_center = (f["t_start"] + f["t_end"]) / 2.0
            window = self.scope.t_div.get() * H_DIVS
            self.scope.t_offset.set(max(0.0, t_center - window / 2.0))
            self.scope.update_plot()
        except Exception:
            pass

    def _clear(self):
        self.last_frames = []
        for it in self.tree.get_children():
            self.tree.delete(it)
        try:
            self.scope._set_protocol_frames([], {})
        except Exception:
            pass
        self.status_label.config(text="Cleared")

    def _export_csv(self):
        if not self.last_frames:
            self.status_label.config(text="Nothing to export")
            return
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            parent=self.win,
            title="Export decoded frames",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("t_start,t_end,type,value,label,error\n")
                for f in self.last_frames:
                    val = f.get("value", "")
                    if isinstance(val, int):
                        val = f"0x{val:X}"
                    label = str(f.get("label", "")).replace(",", " ")
                    fh.write(f"{f['t_start']},{f['t_end']},{f['type']},"
                             f"{val},{label},{int(f.get('error', False))}\n")
            self.scope._show_toast(f"Saved: {path}")
        except Exception as e:
            self.status_label.config(text=f"Export failed: {e}")
