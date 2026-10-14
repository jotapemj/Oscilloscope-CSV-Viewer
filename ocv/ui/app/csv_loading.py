"""CSV loading in the UI: replace/append, time-column and channel pickers, loading overlay."""
import numpy as np
import pandas as pd
import tkinter as tk

from tkinter import filedialog, ttk

from ocv.core.csv_io import _read_csv_smart
from ocv.core.math_expr import math_parse
from ocv.core.scope import H_DIVS, V_DIVS
from ocv.core.si_format import DEFAULT_UNIT, format_si
from ocv.ui import theme
from ocv.ui.theme import channel_color, CHANNEL_COLORS
from ocv.ui.widgets import Tooltip


class CsvLoadingMixin:
    """Mixin de OscilloscopeApp: usa su estado vía self."""

    def _load_csv_from_path(self, filename, on_loaded=None):
        """Variante de load_csv que recibe la ruta directamente y ejecuta
        un callback cuando los canales ya están cargados. Se usa para
        restaurar sesiones.
        """
        from tkinter import messagebox
        overlay = self._show_loading_overlay("Loading CSV...")
        self.root.update_idletasks()

        import threading
        result = {}

        def worker():
            try:
                result["parsed"] = _read_csv_smart(filename)
            except Exception as e:
                result["error"] = str(e)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        def check_done():
            if t.is_alive():
                self.root.after(80, check_done)
                return
            if "error" in result:
                self._hide_loading_overlay(overlay)
                messagebox.showerror("Load session",
                                     f"Could not read CSV:\n\n{result['error']}")
                return
            parsed = result["parsed"]
            time = parsed["time"]
            columns = parsed["columns"]
            col_names = parsed["col_names"]
            if not columns:
                self._hide_loading_overlay(overlay)
                messagebox.showerror("Load session",
                                     "The referenced CSV has no data channels.")
                return
            import os
            self.current_filename = os.path.basename(filename)
            self.current_filepath = os.path.abspath(filename)
            # CSV real de disco → invalidar cache embebido previo.
            self._embedded_csv_content = None
            self.current_brand = parsed.get("brand")

            def do_load():
                self._load_channels(time, columns, col_names)
                # Dejamos un tick para que _load_channels acabe el auto_fit
                self.root.after(50, lambda: self._hide_loading_overlay(overlay))
                if on_loaded is not None:
                    # Aplicar sesión tras otro tick para que el plot ya esté listo
                    self.root.after(80, on_loaded)
            self.root.after(50, do_load)

        self.root.after(80, check_done)

    # -----------------------------
    # Máximo de canales soportados — limitado por la paleta de colores
    # disponibles. Suma canales de datos + canales MATH.
    MAX_TOTAL_CHANNELS = len(CHANNEL_COLORS)   # 12 (v1.26)

    def load_csv(self):
        """Punto de entrada del botón "Open CSV".

        Si ya hay datos cargados, primero pregunta si el nuevo CSV
        sustituye los datos actuales o añade canales adicionales al
        timebase existente (interpolando si es necesario)."""
        # Preguntar modo solo si ya hay datos cargados
        if self.channels and len(self.time) > 1:
            # Si ya estamos al máximo de canales, no se puede añadir.
            # Decirlo explícitamente para que la opción Add no aparezca
            # vacía en el diálogo.
            free_slots = self.MAX_TOTAL_CHANNELS - len(self.channels)
            mode = self._ask_load_mode(free_slots)
            if mode is None:
                return
            if mode == "append":
                self._load_csv_append()
                return
            # mode == "replace": cae al flujo legacy
        self._load_csv_replace()

    def _ask_load_mode(self, free_slots):
        """Diálogo modal: replace / append / cancel.

        Devuelve "replace", "append" o None (si cancela).
        free_slots = número de canales que aún caben (puede ser 0)."""
        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("Load CSV")
        win.configure(bg=theme.COLOR_BG)
        self._center_dialog(win, 420, 230)
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)

        result = {"mode": None}

        # Header
        hdr = tk.Frame(win, bg=theme.COLOR_BG_ALT, height=44)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="LOAD CSV",
                 bg=theme.COLOR_BG_ALT, fg=theme.COLOR_ACCENT,
                 font=("Segoe UI", 11, "bold")
                 ).pack(side=tk.LEFT, padx=14, pady=10)

        # Pregunta
        tk.Label(win,
                 text=("A CSV is already loaded. What do you want to do "
                       "with the new file?"),
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9),
                 wraplength=380, justify="left"
                 ).pack(padx=14, pady=(14, 8), anchor="w")

        # Opciones
        opts_frame = tk.Frame(win, bg=theme.COLOR_BG)
        opts_frame.pack(padx=14, pady=4, fill=tk.X)

        def pick(mode):
            result["mode"] = mode
            win.destroy()

        b_replace = ttk.Button(
            opts_frame, text="Replace current data",
            command=lambda: pick("replace"),
            style="Accent.TButton")
        b_replace.pack(fill=tk.X, pady=3)
        Tooltip(b_replace,
                "Discard the current data and load the new file from scratch.")

        b_append = ttk.Button(
            opts_frame,
            text=(f"Add channels to existing data  ({free_slots} free)"
                  if free_slots > 0 else
                  "Add channels — limit reached"),
            command=lambda: pick("append"))
        b_append.pack(fill=tk.X, pady=3)
        # Desactivar si no caben más canales
        if free_slots <= 0:
            b_append.config(state="disabled")
            Tooltip(b_append,
                    f"Maximum {self.MAX_TOTAL_CHANNELS} channels reached.\n"
                    "Replace or remove channels first.")
        else:
            Tooltip(b_append,
                    "Pick channels from the new file and append them to the\n"
                    "current view. The new channels will be resampled to\n"
                    "the existing timebase (linear interpolation).")

        ttk.Button(opts_frame, text="Cancel",
                   command=win.destroy).pack(fill=tk.X, pady=(6, 0))

        # Esperar a que se cierre
        win.wait_window()
        return result["mode"]

    def _load_csv_replace(self):
        """Comportamiento clásico: pick file, parse, finalize.
        Equivalente al load_csv original."""
        # Si hay una sesión con cambios sin guardar, ofrecer guardarla
        # antes de descartarla. Si no hay dirty (primer load, etc.) el
        # prompt no aparece. El usuario puede cancelar.
        if not self._prompt_save_if_dirty(context="loading a new CSV"):
            return
        filename = filedialog.askopenfilename(
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not filename:
            return
        self._do_csv_load(filename, append_mode=False)

    def _load_csv_append(self):
        """Modo "añadir canales": pick file, parse, abrir picker de
        canales, interpolar y añadir al estado actual."""
        filename = filedialog.askopenfilename(
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not filename:
            return
        self._do_csv_load(filename, append_mode=True)

    def _do_csv_load(self, filename, append_mode):
        """Carga el CSV (thread + overlay) y enruta al finalize correcto
        según append_mode."""
        overlay = self._show_loading_overlay(
            "Loading CSV..." if not append_mode else "Loading channels...")
        self.root.update_idletasks()

        import threading
        result = {}

        def worker():
            try:
                result["parsed"] = _read_csv_smart(filename)
            except Exception as e:
                result["error"] = str(e)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        def check_done():
            if t.is_alive():
                self.root.after(80, check_done)
                return
            if "error" in result:
                self._hide_loading_overlay(overlay)
                self._open_csv_import_dialog(filename, error=result["error"])
                return
            parsed = result["parsed"]
            if not parsed["all_columns"]:
                self._hide_loading_overlay(overlay)
                self._open_csv_import_dialog(
                    filename, error="No data channels found.")
                return

            if append_mode:
                # En modo append no necesitamos validar la columna
                # temporal con el mismo rigor (vamos a interpolar al
                # timebase existente). Pero seguimos necesitándola para
                # saber a qué tiempo corresponde cada muestra del nuevo
                # CSV. Si el _read_csv_smart no la detectó con alta
                # confianza, abrimos el diálogo de selección.
                if parsed["time_confidence"] != "high":
                    self._hide_loading_overlay(overlay)
                    self._open_time_column_dialog_for_append(
                        filename, parsed)
                    return
                self._hide_loading_overlay(overlay)
                self._open_add_channels_dialog(filename, parsed)
                return

            # Modo replace: mismo flujo que antes
            # Validar la columna temporal con la heurística completa.
            # Si la confianza en la columna temporal NO es alta, o si es
            # alta pero el dt mediano cae fuera del rango "razonable como
            # segundos" (típico cuando vienen en µs o ns sin sufijo
            # declarado), abrimos el diálogo de confirmación. Umbral 1.0 s
            # generoso: capturas lentas legítimas (10s/div) lo superan
            # solo si el dt real es >1s — caso muy raro.
            tcol = parsed.get("time_col_index")
            ambiguous_unit = False
            if parsed["time_confidence"] == "high" and tcol is not None:
                dt_med = parsed["time_scores"][tcol]["dt_median"]
                if dt_med > 1.0:
                    ambiguous_unit = True
            if parsed["time_confidence"] != "high" or ambiguous_unit:
                self._hide_loading_overlay(overlay)
                self._open_time_column_dialog(filename, parsed)
                return
            # Confianza alta y unidad razonable → ir directo al load
            self._finalize_csv_load(parsed, filename, overlay)

        # Arrancar el poller (no llamar a check_done() directamente — el
        # thread acaba de empezar y root.after garantiza que damos tiempo
        # a que progrese).
        self.root.after(80, check_done)

    def _finalize_csv_load(self, parsed, filename, overlay):
        """Camino final común para cargar el CSV una vez decidido qué columna
        es tiempo y en qué unidad viene. Se llama tanto desde load_csv (path
        rápido, confianza alta) como desde _open_time_column_dialog tras la
        confirmación del usuario."""
        import os
        time = parsed["time"]
        columns = parsed["columns"]
        col_names = parsed["col_names"]

        if not columns:
            self._hide_loading_overlay(overlay)
            from tkinter import messagebox
            messagebox.showerror("CSV load",
                                 "After selecting the time column, no data "
                                 "channels remain to plot.")
            return

        self.current_filename = os.path.basename(filename)
        self.current_filepath = os.path.abspath(filename)
        # CSV real de disco → invalidar cache embebido previo.
        self._embedded_csv_content = None
        self.current_brand = parsed.get("brand")

        def do_load():
            # Suprimimos dirty durante la inicialización de canales y vista:
            # _load_channels escribe en muchas tk.Vars que tienen trace para
            # marcar dirty, y esas escrituras son las del bootstrap interno
            # (no acciones del usuario), así que no queremos contarlas.
            self._suppress_dirty = True
            try:
                self._load_channels(time, columns, col_names)
                # Replace de un CSV nuevo → sesión Untitled.
                self.session_path = None
            finally:
                self._suppress_dirty = False
            # Cargar un CSV es un cambio de estado relevante: la sesión
            # Untitled queda dirty desde el principio para que el usuario
            # sea consciente de que cerrarla pierde la configuración aun
            # sin haberla tocado más.
            self.session_dirty = True
            self._update_title()
            self.root.after(50, lambda: self._hide_loading_overlay(overlay))
        self.root.after(50, do_load)

    # =====================================================
    # Append-mode CSV load: añadir canales sin reemplazar
    # =====================================================
    def _open_time_column_dialog_for_append(self, filename, parsed):
        """Variante simplificada del diálogo de columna de tiempo para
        modo append. Reutilizamos la lógica de detección y delegamos
        en el diálogo full; al confirmar, en lugar de _finalize_csv_load
        llamamos a _open_add_channels_dialog para que el usuario elija
        qué canales añadir.

        Implementación: como _open_time_column_dialog tiene mucha UI
        compartida, abrimos el diálogo normal pero monkey-patcheamos
        el camino del OK. Es feo, pero evita duplicar 200 líneas de UI.
        Una limpieza futura sería separar la UI del action handler.

        Aquí simplificamos: si el usuario ya seleccionó "OK" en ese
        diálogo, llamará a _finalize_csv_load y eso reemplaza. Para
        evitarlo, abrimos un diálogo más sencillo aquí: tabla de
        columnas + selector de unidad + OK que va al add picker."""
        import os

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("Confirm time column (append)")
        win.configure(bg=theme.COLOR_BG)
        self._center_dialog(win, 680, 480)
        win.transient(self.root)
        win.grab_set()

        hdr = tk.Frame(win, bg=theme.COLOR_BG_ALT, height=44)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="SELECT TIME COLUMN OF NEW FILE",
                 bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 11, "bold")
                 ).pack(side=tk.LEFT, padx=14, pady=10)
        tk.Label(hdr, text=os.path.basename(filename),
                 bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 9)
                 ).pack(side=tk.RIGHT, padx=14, pady=10)

        tk.Label(win,
                 text=("The new file's time axis was not detected with "
                       "high confidence. Pick the time column and its "
                       "unit so we can resample the new channels onto "
                       "the existing timebase."),
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 9), wraplength=640, justify="left"
                 ).pack(padx=14, pady=(10, 6), anchor="w")

        # Tabla de columnas (idéntica a la del diálogo principal)
        table_frame = ttk.Frame(win)
        table_frame.pack(padx=14, pady=4, fill=tk.BOTH, expand=True)
        cols = ("name", "dt_median", "score", "monotonic", "preview")
        tree = ttk.Treeview(table_frame, columns=cols, show="headings",
                            height=8, selectmode="browse")
        for k, lbl, w, anchor in (
                ("name", "Column", 140, "w"),
                ("dt_median", "dt median", 110, "w"),
                ("score", "Score", 70, "w"),
                ("monotonic", "Monotonic", 80, "center"),
                ("preview", "First 4 values", 240, "w")):
            tree.heading(k, text=lbl)
            tree.column(k, width=w, anchor=anchor)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(table_frame, orient="vertical",
                               command=tree.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tree.configure(yscrollcommand=scroll.set)

        all_cols = parsed["all_columns"]
        all_names = parsed["all_col_names"]
        scores = parsed["time_scores"]
        for j, (col, nm, sc) in enumerate(zip(all_cols, all_names, scores)):
            preview = ", ".join(f"{col[k]:.6g}" for k in range(min(4, len(col))))
            dt = sc["dt_median"]
            dt_str = f"{dt:.6g}" if dt > 0 else "—"
            tree.insert("", "end", iid=str(j),
                        values=(nm, dt_str, f"{sc['score']:.2f}",
                                "✓" if sc["monotonic"] else "✗",
                                preview))
        best_j = parsed.get("time_col_index")
        if best_j is not None:
            tree.selection_set(str(best_j))
            tree.focus(str(best_j))

        # Selector de unidad
        unit_row = tk.Frame(win, bg=theme.COLOR_BG)
        unit_row.pack(padx=14, pady=(4, 8), anchor="w")
        tk.Label(unit_row, text="Time unit:",
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT
                 ).pack(side=tk.LEFT)
        unit_var = tk.StringVar(value=self._TIME_UNIT_CHOICES[0][0])
        unit_combo = ttk.Combobox(
            unit_row, textvariable=unit_var,
            values=[lbl for lbl, _ in self._TIME_UNIT_CHOICES],
            state="readonly", width=22)
        unit_combo.pack(side=tk.LEFT, padx=(6, 0))
        # Sugerencia automática si la heurística lo soporta
        dt_med = parsed.get("time_dt_median", 0.0) or 0.0
        if dt_med > 0:
            unit_var.set(self._guess_time_unit(dt_med))

        # Botones
        btns = ttk.Frame(win)
        btns.pack(pady=(6, 12))

        def on_ok():
            sel = tree.selection()
            if not sel:
                from tkinter import messagebox
                messagebox.showwarning("Time column",
                                       "Select a time column.",
                                       parent=win)
                return
            j = int(sel[0])
            unit_label = unit_var.get()
            factor = next((f for lbl, f in self._TIME_UNIT_CHOICES
                           if lbl == unit_label), 1.0)
            new_parsed = dict(parsed)
            new_parsed["time"] = all_cols[j] * factor
            new_parsed["columns"] = [all_cols[k]
                                     for k in range(len(all_cols))
                                     if k != j]
            new_parsed["col_names"] = [all_names[k]
                                       for k in range(len(all_names))
                                       if k != j]
            new_parsed["time_col_index"] = j
            win.destroy()
            self._open_add_channels_dialog(filename, new_parsed)

        ttk.Button(btns, text="OK", command=on_ok,
                   style="Accent.TButton").pack(side=tk.LEFT, padx=6)
        # Doble clic en la fila también confirma
        tree.bind("<Double-Button-1>", lambda e: on_ok())
        ttk.Button(btns, text="Cancel",
                   command=win.destroy).pack(side=tk.LEFT, padx=6)

    def _open_add_channels_dialog(self, filename, parsed):
        """Diálogo de selección de canales para modo append.

        Muestra una lista con checkbox por cada canal del nuevo CSV,
        con preview de nombre y rango de valores. Un contador en vivo
        indica cuántos quedan por seleccionar — no se puede marcar más
        del free_slots disponible.

        Al confirmar, llama a _append_channels con las columnas
        elegidas. La interpolación al timebase existente se hace allí."""
        import os

        free_slots = self.MAX_TOTAL_CHANNELS - len(self.channels)
        if free_slots <= 0:
            from tkinter import messagebox
            messagebox.showinfo(
                "Add channels",
                f"Channel limit ({self.MAX_TOTAL_CHANNELS}) reached.",
                parent=self.root)
            return

        new_time = np.asarray(parsed["time"], dtype=float)
        new_cols = parsed["columns"]
        new_names = parsed["col_names"]

        if not new_cols:
            from tkinter import messagebox
            messagebox.showwarning(
                "Add channels",
                "The new file has no data channels.",
                parent=self.root)
            return

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("Add channels")
        win.configure(bg=theme.COLOR_BG)
        self._center_dialog(win, 560, 520)
        win.transient(self.root)
        win.grab_set()

        # Header
        hdr = tk.Frame(win, bg=theme.COLOR_BG_ALT, height=44)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="ADD CHANNELS",
                 bg=theme.COLOR_BG_ALT, fg=theme.COLOR_ACCENT,
                 font=("Segoe UI", 11, "bold")
                 ).pack(side=tk.LEFT, padx=14, pady=10)
        tk.Label(hdr, text=os.path.basename(filename),
                 bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 9)
                 ).pack(side=tk.RIGHT, padx=14, pady=10)

        # Info de timebase
        t_cur_start = float(self.time[0]) if len(self.time) else 0.0
        t_cur_end = float(self.time[-1]) if len(self.time) else 0.0
        t_new_start = float(new_time[0]) if len(new_time) else 0.0
        t_new_end = float(new_time[-1]) if len(new_time) else 0.0
        info_text = (
            f"Current timebase: {format_si(t_cur_start, 's')} → "
            f"{format_si(t_cur_end, 's')}  ({len(self.time)} samples)\n"
            f"New file timebase: {format_si(t_new_start, 's')} → "
            f"{format_si(t_new_end, 's')}  ({len(new_time)} samples)\n"
            f"New channels will be linearly interpolated to the "
            f"current timebase."
        )
        tk.Label(win, text=info_text,
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 9), justify="left"
                 ).pack(padx=14, pady=(10, 4), anchor="w")

        # Contador en vivo
        counter_var = tk.StringVar()
        counter_lbl = tk.Label(win, textvariable=counter_var,
                               bg=theme.COLOR_BG, fg=theme.COLOR_ACCENT,
                               font=("Segoe UI", 9, "bold"))
        counter_lbl.pack(padx=14, pady=(4, 6), anchor="w")

        # Lista de canales con checkbox
        list_frame = ttk.Frame(win)
        list_frame.pack(padx=14, pady=4, fill=tk.BOTH, expand=True)

        # Scrollable inner frame
        canvas = tk.Canvas(list_frame, bg=theme.COLOR_BG, highlightthickness=0)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(list_frame, orient="vertical",
                               command=canvas.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.configure(yscrollcommand=scroll.set)
        inner = tk.Frame(canvas, bg=theme.COLOR_BG)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_config(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_inner_config)

        def _on_canvas_config(e):
            canvas.itemconfigure(inner_id, width=e.width)
        canvas.bind("<Configure>", _on_canvas_config)

        check_vars = []      # BooleanVar por canal
        check_widgets = []   # widget ref para enable/disable

        def update_counter_and_states():
            picked = sum(1 for v in check_vars if v.get())
            remaining = free_slots - picked
            counter_var.set(
                f"Selected: {picked}  ·  Remaining slots: {remaining}  "
                f"·  Max for this file: {free_slots}")
            # Deshabilitar checkboxes no marcados si ya estamos al límite
            at_limit = (picked >= free_slots)
            for v, w in zip(check_vars, check_widgets):
                if at_limit and not v.get():
                    w.config(state="disabled")
                else:
                    w.config(state="normal")

        for i, (col, nm) in enumerate(zip(new_cols, new_names)):
            row = tk.Frame(inner, bg=theme.COLOR_BG)
            row.pack(fill=tk.X, pady=2)
            v = tk.BooleanVar(value=False)
            # Si caben todos sin tocar limit, dejarlos default-checked
            # hasta agotar slots. Esto es lo que el usuario probablemente
            # quiera más a menudo.
            if i < free_slots:
                v.set(True)
            cb = ttk.Checkbutton(
                row, variable=v, text=nm,
                command=update_counter_and_states)
            cb.pack(side=tk.LEFT, padx=4)
            # Stats del canal
            try:
                vmin = float(np.min(col))
                vmax = float(np.max(col))
                stats = (f"range {format_si(vmin, 'V')} → "
                         f"{format_si(vmax, 'V')}  · {len(col)} samples")
            except Exception:
                stats = f"{len(col)} samples"
            tk.Label(row, text=stats,
                     bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                     font=("Segoe UI", 8)
                     ).pack(side=tk.LEFT, padx=10)
            check_vars.append(v)
            check_widgets.append(cb)

        update_counter_and_states()

        # Botones
        btns = ttk.Frame(win)
        btns.pack(pady=(6, 12))

        def on_ok():
            selected = [i for i, v in enumerate(check_vars) if v.get()]
            if not selected:
                from tkinter import messagebox
                messagebox.showwarning(
                    "Add channels",
                    "Select at least one channel to add.",
                    parent=win)
                return
            if len(selected) > free_slots:
                # No debería pasar (el limit ya bloquea), defensive
                from tkinter import messagebox
                messagebox.showerror(
                    "Add channels",
                    f"Selected {len(selected)} channels but only "
                    f"{free_slots} slots free.",
                    parent=win)
                return
            win.destroy()
            overlay = self._show_loading_overlay("Appending channels...")
            self.root.update_idletasks()
            self.root.after(50, lambda: self._append_channels(
                new_time, [new_cols[i] for i in selected],
                [new_names[i] for i in selected], overlay))

        ttk.Button(btns, text="Add selected", command=on_ok,
                   style="Accent.TButton").pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Cancel",
                   command=win.destroy).pack(side=tk.LEFT, padx=6)

    def _append_channels(self, new_time, new_voltages, new_names, overlay):
        """Añade canales del CSV nuevo al estado actual.

        Cada canal nuevo se interpola linealmente al timebase actual
        (self.time). Para los instantes donde el CSV nuevo no tiene
        cobertura (su rango no llega), se rellena con NaN — matplotlib
        los oculta automáticamente y no contaminan medidas.

        El origen temporal (self.time_origin) NO cambia. El nuevo CSV
        se alinea como si compartiera el mismo punto cero del eje X.

        Limitaciones honestas (Opción B):
          - Si la fs del CSV nuevo es muy distinta y la señal tiene
            componentes de alta frecuencia, la interpolación linear
            puede introducir aliasing. Para casos críticos, lo
            correcto sería aplicar un filtro anti-aliasing antes —
            pero eso queda fuera del scope de este modo.
          - Si los rangos temporales no se solapan en absoluto, el
            canal añadido es todo NaN (visualmente vacío).
        """
        # Normalizar el tiempo del CSV nuevo. Lo trasladamos para que
        # comparta el origen "0" del self.time actual (que también empieza
        # en 0 — ver _load_channels donde se hace time = time - time[0]).
        new_time = np.asarray(new_time, dtype=float)
        if len(new_time) >= 1:
            new_time = new_time - new_time[0]

        n_existing = len(self.channels)
        added = 0
        from_outside_range = 0

        for col, nm in zip(new_voltages, new_names):
            col = np.asarray(col, dtype=float)
            # Interpolar al timebase actual.
            # np.interp es lineal y maneja mal los extrapolados (los
            # clampea a los extremos). Para los puntos del self.time
            # que caen fuera del rango del CSV nuevo, queremos NaN, no
            # un extremo repetido. Por eso hacemos:
            #   1) interp dentro del rango
            #   2) máscara fuera de rango → NaN
            if len(new_time) >= 2:
                interp = np.interp(self.time, new_time, col)
                mask_outside = ((self.time < new_time[0])
                                | (self.time > new_time[-1]))
                if np.any(mask_outside):
                    interp[mask_outside] = np.nan
                    from_outside_range += 1
            else:
                # Solo una muestra: no podemos interpolar nada útil
                interp = np.full_like(self.time, np.nan)

            color_idx = n_existing + added
            display_name = self._clean_channel_name(nm, color_idx)
            new_ch = {
                "_uid":     self._next_channel_uid,
                "name":     display_name,
                "color":    channel_color(color_idx),
                "voltage":  interp,
                "visible":  tk.BooleanVar(value=True),
                "v_div":    1.0,
                "v_offset": 0.0,
                "unit":     DEFAULT_UNIT,
            }
            self._next_channel_uid += 1
            new_ch["visible"].trace_add(
                "write", lambda *a: (self.update_plot(), self._mark_dirty()))
            self.channels.append(new_ch)
            # Auto-fit individual de este canal: ajustar v_div y v_offset
            # para que entre bien en pantalla con los actuales.
            try:
                vmin = np.nanmin(interp)
                vmax = np.nanmax(interp)
                if np.isfinite(vmin) and np.isfinite(vmax) and vmax > vmin:
                    span = vmax - vmin
                    new_ch["v_div"] = max(span / (V_DIVS - 1), 1e-6)
                    new_ch["v_offset"] = (vmin + vmax) / 2.0
            except Exception:
                pass
            added += 1

        # Reconstruir el panel y repintar
        self._rebuild_channels_panel()
        self.update_plot()
        self._hide_loading_overlay(overlay)

        # Toast informativo, con aviso si algún canal cayó fuera del rango
        if from_outside_range > 0:
            self._show_toast(
                f"Added {added} channel(s) — "
                f"{from_outside_range} partially outside timebase (NaN-padded)")
        else:
            self._show_toast(f"Added {added} channel(s)")

    # Unidades de tiempo soportadas en el diálogo de confirmación.
    # Tuplas (label_mostrado, factor_a_segundos).
    _TIME_UNIT_CHOICES = [
        ("seconds (s)",       1.0),
        ("milliseconds (ms)", 1e-3),
        ("microseconds (µs)", 1e-6),
        ("nanoseconds (ns)",  1e-9),
        ("minutes",           60.0),
        ("hours",             3600.0),
    ]

    @staticmethod
    def _guess_time_unit(dt_median):
        """Heurística: a partir del dt mediano sugiere la unidad más
        probable. Para un osciloscopio típico el sample time real va de ns
        a ms; si el dt mediano del CSV cae en rango "raro" (por ejemplo
        decenas o cientos cuando esperábamos fracciones de segundo), es
        muy probable que la columna venga en otra unidad.

        Casos típicos por rango de dt mediano:
          - dt ≤ 1e-3       → segundos (sample period ≤ 1 ms, normal)
          - dt 1e-3 .. 1.0  → segundos (sample period 1 ms a 1 s, dataloggers)
          - dt 1.0 .. 100   → AMBIGUO: puede ser
                              (a) microsegundos sin sufijo (caso muy común)
                              (b) segundos para una captura lenta legítima
                              → sugerimos microsegundos pero el diálogo
                              lo pregunta sí o sí.
          - dt 100 .. 1e5   → microsegundos casi seguro
          - dt > 1e5        → nanosegundos
        Devuelve el LABEL de _TIME_UNIT_CHOICES o "seconds (s)" por defecto.
        """
        if dt_median <= 0 or not np.isfinite(dt_median):
            return "seconds (s)"
        if dt_median <= 1.0:
            return "seconds (s)"
        if dt_median <= 100.0:
            # Zona ambigua: sugerimos µs pero será una sugerencia, el
            # usuario verá la preview de valores y decidirá.
            return "microseconds (µs)"
        if dt_median <= 1e5:
            return "microseconds (µs)"
        return "nanoseconds (ns)"

    def _open_time_column_dialog(self, filename, parsed):
        """Diálogo de confirmación cuando _read_csv_smart no está seguro
        de cuál es la columna temporal. Muestra:
          - lista de columnas con su nombre, dt mediano, score y monotonía
          - radio button para elegir la columna de tiempo (o "no time column")
          - combobox de UNIDAD de tiempo (s, ms, µs, ns, min, h) para
            convertir a segundos al cargar
          - nota explicativa según el confidence detectado

        Pulsando OK, aplicamos la elección y caemos en _finalize_csv_load.
        """
        import os

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("Confirm time column")
        win.configure(bg=theme.COLOR_BG)
        self._center_dialog(win, 680, 520)
        win.transient(self.root)
        win.grab_set()

        # ---- Header ----
        hdr = tk.Frame(win, bg=theme.COLOR_BG_ALT, height=44)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="CONFIRM TIME COLUMN",
                 bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 11, "bold")
                 ).pack(side=tk.LEFT, padx=14, pady=10)
        tk.Label(hdr, text=os.path.basename(filename),
                 bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 9)
                 ).pack(side=tk.RIGHT, padx=14, pady=10)

        # ---- Explicación ----
        conf = parsed["time_confidence"]
        if conf == "medium":
            explain_text = (
                "The first column looks like time but has irregularities "
                "(jumps, jitter). Please confirm which column is the "
                "time axis, and which UNIT it's in. If unsure, the "
                "best candidate is preselected below."
            )
        elif conf == "low":
            explain_text = (
                "Time-axis detection is uncertain. Please pick the "
                "correct column manually and verify the unit. If none "
                "of the columns is time, select 'No time column' to "
                "use uniform sample indexing."
            )
        else:  # 'none'
            explain_text = (
                "No column looks clearly like a time axis. Pick one "
                "manually if it exists, or use 'No time column' to "
                "treat the data as uniformly sampled."
            )
        tk.Label(win, text=explain_text,
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 9), wraplength=640, justify="left"
                 ).pack(padx=14, pady=(10, 6), anchor="w")

        # ---- Tabla de columnas ----
        table_frame = ttk.Frame(win)
        table_frame.pack(padx=14, pady=4, fill=tk.BOTH, expand=True)

        cols = ("name", "dt_median", "score", "monotonic", "preview")
        tree = ttk.Treeview(table_frame, columns=cols, show="headings",
                            height=8, selectmode="browse")
        tree.heading("name",      text="Column")
        tree.heading("dt_median", text="dt median")
        tree.heading("score",     text="Score")
        tree.heading("monotonic", text="Monotonic")
        tree.heading("preview",   text="First 4 values")
        tree.column("name",      width=140, anchor="w")
        tree.column("dt_median", width=110, anchor="w")
        tree.column("score",     width=70,  anchor="w")
        tree.column("monotonic", width=80,  anchor="center")
        tree.column("preview",   width=240, anchor="w")
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(table_frame, orient="vertical",
                               command=tree.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tree.configure(yscrollcommand=scroll.set)

        all_cols = parsed["all_columns"]
        all_names = parsed["all_col_names"]
        scores = parsed["time_scores"]

        for j, (col, nm, sc) in enumerate(zip(all_cols, all_names, scores)):
            preview = ", ".join(f"{col[k]:.6g}" for k in range(min(4, len(col))))
            dt = sc["dt_median"]
            dt_str = f"{dt:.6g}" if dt > 0 else "—"
            tree.insert("", "end", iid=str(j),
                        values=(nm, dt_str, f"{sc['score']:.2f}",
                                "✓" if sc["monotonic"] else "✗",
                                preview))

        # Preseleccionar el mejor candidato (si lo hay)
        best_j = parsed.get("time_col_index")
        if best_j is not None:
            tree.selection_set(str(best_j))
            tree.focus(str(best_j))

        # ---- Opción "No time column" ----
        no_time_var = tk.BooleanVar(value=False)
        no_time_chk = ttk.Checkbutton(
            win, text="No time column — use uniform sample indexing "
                     "(time = sample index in seconds)",
            variable=no_time_var)
        no_time_chk.pack(padx=14, pady=(6, 2), anchor="w")

        # ---- Selector de unidad ----
        unit_row = ttk.Frame(win)
        unit_row.pack(padx=14, pady=(2, 6), fill=tk.X)
        ttk.Label(unit_row, text="Time unit of the selected column:"
                  ).pack(side=tk.LEFT)
        unit_var = tk.StringVar()
        unit_combo = ttk.Combobox(
            unit_row, textvariable=unit_var,
            values=[lbl for lbl, _ in self._TIME_UNIT_CHOICES],
            state="readonly", width=22)
        unit_combo.pack(side=tk.LEFT, padx=(8, 0))

        # Inicializar unidad sugerida según el dt del candidato
        if best_j is not None:
            dt_med = scores[best_j]["dt_median"]
            unit_var.set(self._guess_time_unit(dt_med))
        else:
            unit_var.set("seconds (s)")

        # Label que muestra dt_median ya convertido a segundos para feedback
        feedback_lbl = tk.Label(win, text="", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                                font=("Segoe UI", 9, "italic"))
        feedback_lbl.pack(padx=14, pady=(0, 6), anchor="w")

        def update_feedback(*_):
            sel = tree.selection()
            if no_time_var.get() or not sel:
                feedback_lbl.config(text="")
                return
            j = int(sel[0])
            dt = scores[j]["dt_median"]
            unit_label = unit_var.get()
            factor = next((f for lbl, f in self._TIME_UNIT_CHOICES
                            if lbl == unit_label), 1.0)
            dt_s = dt * factor
            total_s = dt_s * (len(all_cols[j]) - 1)
            feedback_lbl.config(
                text=f"→ After unit conversion: dt ≈ {dt_s:.6g} s, "
                     f"total capture ≈ {total_s:.6g} s "
                     f"({len(all_cols[j])} samples)")

        tree.bind("<<TreeviewSelect>>", update_feedback)
        unit_var.trace_add("write", update_feedback)
        no_time_var.trace_add("write", update_feedback)
        update_feedback()

        # ---- Botones ----
        btns = ttk.Frame(win)
        btns.pack(pady=(6, 12))

        def on_ok():
            sel = tree.selection()
            use_no_time = no_time_var.get()
            if not use_no_time and not sel:
                from tkinter import messagebox
                messagebox.showwarning(
                    "Time column",
                    "Select a column or check 'No time column'.",
                    parent=win)
                return

            # Construir la versión modificada del 'parsed' dict según la
            # elección del usuario y caer en _finalize_csv_load.
            new_parsed = dict(parsed)
            new_overlay = self._show_loading_overlay("Applying selection...")

            if use_no_time:
                # No hay columna de tiempo → todas son datos. Tiempo
                # sintético (índice de muestra en segundos).
                new_parsed["time"] = np.arange(len(all_cols[0]),
                                                dtype=float)
                new_parsed["columns"] = [c for c in all_cols]
                new_parsed["col_names"] = list(all_names)
                new_parsed["time_col_index"] = None
            else:
                j = int(sel[0])
                unit_label = unit_var.get()
                factor = next((f for lbl, f in self._TIME_UNIT_CHOICES
                                if lbl == unit_label), 1.0)
                new_parsed["time"] = all_cols[j] * factor
                new_parsed["columns"] = [all_cols[k]
                                          for k in range(len(all_cols))
                                          if k != j]
                new_parsed["col_names"] = [all_names[k]
                                            for k in range(len(all_names))
                                            if k != j]
                new_parsed["time_col_index"] = j

            win.destroy()
            self._finalize_csv_load(new_parsed, filename, new_overlay)

        ttk.Button(btns, text="OK", command=on_ok,
                   style="Accent.TButton").pack(side=tk.LEFT, padx=6)

        # Doble clic sobre una fila del Treeview equivale a pulsar OK.
        # Es UX estándar (el usuario ya manifestó intención al elegir)
        # y elimina el paso extra de bajar el ratón hasta el botón.
        # Si "No time column" está marcado y no hay selección, on_ok ya
        # valida y muestra warning, así que es seguro siempre.
        tree.bind("<Double-Button-1>", lambda e: on_ok())
        ttk.Button(btns, text="Manual import…",
                   command=lambda: (win.destroy(),
                                    self._open_csv_import_dialog(filename))
                   ).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Cancel",
                   command=win.destroy).pack(side=tk.LEFT, padx=6)

    def _show_loading_overlay(self, text="Loading..."):
        """Overlay inline: un Frame dentro de la ventana principal que se
        coloca centrado con place(). No es un Toplevel, así que no abre
        ventana nueva. Devuelve el Frame (con los mismos atributos de
        animación que antes) para poder ocultarlo luego."""
        # Frame contenedor, hijo directo de root
        ov = tk.Frame(self.root, bg=theme.COLOR_ACCENT, bd=0, highlightthickness=0)
        inner = tk.Frame(ov, bg=theme.COLOR_BG_ALT)
        inner.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        ov._label = tk.Label(
            inner, text=text,
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            font=("Segoe UI", 11, "bold")
        )
        ov._label.pack(expand=True, padx=20, pady=(16, 4))

        ov._dots_label = tk.Label(
            inner, text="●○○○",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_ACCENT,
            font=("Consolas", 14, "bold")
        )
        ov._dots_label.pack(pady=(0, 16))

        # Centrar en la ventana principal con place relativo
        ov.place(relx=0.5, rely=0.5, anchor="center", width=280, height=100)
        ov.lift()

        # Animación
        ov._anim_running = True
        ov._anim_step = 0

        def animate():
            if not ov._anim_running:
                return
            patterns = ["●○○○", "○●○○", "○○●○", "○○○●"]
            try:
                ov._dots_label.config(text=patterns[ov._anim_step % 4])
                ov._anim_step += 1
            except tk.TclError:
                return
            ov.after(120, animate)

        ov.after(120, animate)
        ov.update_idletasks()
        return ov

    def _hide_loading_overlay(self, overlay):
        if overlay is None:
            return
        overlay._anim_running = False
        try:
            overlay.place_forget()
            overlay.destroy()
        except Exception:
            pass

    def _load_channels(self, time, voltage_columns, col_names):
        """Sustituye todos los canales actuales por los nuevos.
        Si había canales MATH, pregunta al usuario qué hacer con ellos."""
        time = np.asarray(time, dtype=float)
        # Preservar el origen real ANTES de normalizar al 0. Esto permite
        # mostrar tiempos absolutos del CSV en las etiquetas del eje X y
        # cursores (capturas con pre-trigger negativo no pierden contexto).
        # Internamente la app sigue trabajando con time empezando en 0,
        # lo que simplifica MATH, decoders, FFT, etc.
        self.time_origin = float(time[0])
        time = time - time[0]

        # Decidir qué hacer con los MATH existentes
        keep_math = False
        math_snapshots = []
        if self.math_order:
            keep_math = self._ask_keep_math_on_new_csv()
            if keep_math:
                # Guardar copia de las expresiones y nombres ANTES de resetear
                for idx in self.math_order:
                    ch = self.channels[idx]
                    math_snapshots.append({
                        "name":  ch["name"],
                        "color": ch["color"],
                        "expr":  ch["math"]["expr"],
                    })

        self.time = time
        self.t_total = time[-1]

        # Heurística: si el CSV es enorme, arrancar en modo Fast para que
        # pan/zoom vayan fluidos de entrada. El usuario puede volver a Detail
        # con el toggle de la topbar cuando quiera ver fino.
        if len(time) > 500_000 and self.render_mode.get() == "Detail":
            self.render_mode.set("Fast")
            self._update_render_mode_btn_visual()

        # Construir lista de canales
        self.channels = []
        self.math_order = []
        for i, col in enumerate(voltage_columns):
            name = col_names[i] if i < len(col_names) else f"CH{i+1}"
            # Limpiar nombre para que sea corto y legible
            display_name = self._clean_channel_name(name, i)
            self.channels.append({
                "_uid":     self._next_channel_uid,
                "name":     display_name,
                "color":    channel_color(i),
                "voltage":  np.asarray(col, dtype=float),
                "visible":  tk.BooleanVar(value=True),
                "v_div":    1.0,      # se ajusta en auto_fit
                "v_offset": 0.0,      # se ajusta en auto_fit
                "unit":     DEFAULT_UNIT,
            })
            self._next_channel_uid += 1
            # Si el BooleanVar cambia → redibujar + marcar dirty
            self.channels[-1]["visible"].trace_add(
                "write", lambda *a: (self.update_plot(), self._mark_dirty())
            )

        self.active_channel_idx = 0
        self._sync_active_channel_vars()

        # Rellenar panel de canales
        self._rebuild_channels_panel()

        # Auto-fit completo (ajusta T/div, y V/div/offset para cada canal)
        self.auto_fit()

        # Inicializar cursores dentro de la ventana visible
        t_start = self.t_offset.get()
        window = self.t_div.get() * H_DIVS
        self.cursor_x = [
            t_start + window * 0.25,
            t_start + window * 0.75,
        ]
        v_range = self.v_div.get() * V_DIVS
        self.cursor_y = [
            -v_range * 0.25,
             v_range * 0.25,
        ]

        # Restaurar MATH del snapshot si el usuario lo pidió
        if math_snapshots:
            restored, skipped = 0, 0
            for snap in math_snapshots:
                try:
                    ast = math_parse(snap["expr"])
                    resolved = self._resolve_refs(ast)
                    # Crear con el nombre original
                    self._create_math_channel(snap["expr"], resolved, snap["name"])
                    restored += 1
                except Exception:
                    skipped += 1
            if skipped:
                self._show_toast(f"MATH: {restored} restored, {skipped} skipped (missing refs)",
                                 duration_ms=2500)
            elif restored:
                self._show_toast(f"MATH: {restored} restored")

        # Recalcular trigger si estaba activo
        if self.trigger_config["enabled"]:
            self._refresh_trigger()

        # Habilitar/deshabilitar el botón "Snap horizontal to t=0" según
        # el modo Absolute esté activo (el CSV ya está cargado a estas alturas).
        self._update_snap_t0_state()

    def _ask_keep_math_on_new_csv(self):
        """Diálogo modal: preguntar si conservar los MATH al cargar un CSV nuevo.
        Devuelve True si el usuario quiere mantenerlos."""
        result = {"keep": False}

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("MATH channels")
        win.configure(bg=theme.COLOR_BG)
        # Geometry PRIMERO, transient DESPUÉS (evita fantasma).
        self._make_resizable_dialog(win, 380, 160)
        win.transient(self.root)
        win.grab_set()

        tk.Label(
            win, text="Existing MATH channels",
            bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack(padx=14, pady=(14, 4), anchor="w")

        tk.Label(
            win,
            text=f"There {'is' if len(self.math_order)==1 else 'are'} "
                 f"{len(self.math_order)} MATH channel(s). "
                 f"Keep and recalculate\nwith the new data, or discard?",
            bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
            font=("Segoe UI", 9), justify="left"
        ).pack(padx=14, pady=(0, 10), anchor="w")

        btns = tk.Frame(win, bg=theme.COLOR_BG)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=12)

        def do_keep():
            result["keep"] = True
            win.destroy()

        def do_drop():
            result["keep"] = False
            win.destroy()

        ttk.Button(btns, text="Discard", command=do_drop).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Keep", style="Accent.TButton",
                   command=do_keep).pack(side=tk.RIGHT, padx=(0, 6))

        self.root.wait_window(win)
        return result["keep"]

    def _clean_channel_name(self, raw, idx):
        """Nombres tipo 'CH1 in V' o 'Volt' → 'CH1'."""
        n = raw.strip()
        # Si es genérico (Volt, Value…) usar CH(idx+1)
        generic = {"volt", "voltage", "value", "v", "ch", ""}
        if n.lower() in generic:
            return f"CH{idx+1}"
        # Quitar sufijos tipo " in V"
        for suffix in [" in V", " [V]", " (V)"]:
            if n.endswith(suffix):
                n = n[: -len(suffix)].strip()
        # Truncar si es muy largo
        if len(n) > 12:
            n = n[:12]
        return n

    def _open_csv_import_dialog(self, filename, error=""):
        """Fallback: preview de las primeras líneas del fichero y permitir
        al usuario indicar manualmente 'saltar N líneas' y columnas."""
        try:
            with open(filename, "r", encoding="utf-8", errors="replace") as f:
                raw = f.readlines()[:40]
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("Error", f"Cannot read file:\n{e}")
            return

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("CSV Import — manual fallback")
        win.configure(bg=theme.COLOR_BG)
        self._center_dialog(win, 800, 600)
        win.transient(self.root)

        if error:
            tk.Label(
                win, text=f"Auto-detection failed: {error}",
                fg=theme.COLOR_DANGER, bg=theme.COLOR_BG,
                font=("Segoe UI", 9, "italic"),
                wraplength=760, justify="left",
            ).pack(padx=12, pady=(10, 4), anchor="w")

        tk.Label(
            win, text="Preview (first 40 lines):",
            fg=theme.COLOR_TEXT_DIM, bg=theme.COLOR_BG,
        ).pack(padx=12, pady=(6, 2), anchor="w")

        # Text widget con las primeras líneas, números de línea
        txt_frame = ttk.Frame(win)
        txt_frame.pack(padx=12, pady=4, fill=tk.BOTH, expand=True)
        text = tk.Text(
            txt_frame,
            font=("Consolas", 9),
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            insertbackground=theme.COLOR_TEXT,
            height=15,
            wrap="none",
        )
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(txt_frame, orient="vertical", command=text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        text.configure(yscrollcommand=scroll.set)
        for i, line in enumerate(raw):
            text.insert("end", f"{i:3d} | {line.rstrip()}\n")
        text.config(state="disabled")

        # Controles
        ctrl = ttk.Frame(win)
        ctrl.pack(padx=12, pady=8, fill=tk.X)

        ttk.Label(ctrl, text="Skip rows:").pack(side=tk.LEFT)
        skip_var = tk.IntVar(value=0)
        ttk.Spinbox(ctrl, from_=0, to=500, width=6, textvariable=skip_var).pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(ctrl, text="Separator:").pack(side=tk.LEFT)
        sep_var = tk.StringVar(value=",")
        ttk.Combobox(ctrl, textvariable=sep_var,
                     values=[",", ";", "\\t"], width=6,
                     state="readonly").pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(ctrl, text="Time col:").pack(side=tk.LEFT)
        tcol = tk.IntVar(value=0)
        ttk.Spinbox(ctrl, from_=0, to=20, width=4, textvariable=tcol).pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(ctrl, text="Voltage col:").pack(side=tk.LEFT)
        vcol = tk.IntVar(value=1)
        ttk.Spinbox(ctrl, from_=0, to=20, width=4, textvariable=vcol).pack(side=tk.LEFT, padx=(4, 12))

        def do_load():
            sep = sep_var.get()
            if sep == "\\t":
                sep = "\t"
            try:
                df = pd.read_csv(
                    filename,
                    sep=sep,
                    skiprows=skip_var.get(),
                    header=None,
                    engine="python",
                    decimal=".",
                    on_bad_lines="skip",
                )
                t = df.iloc[:, tcol.get()].astype(float).values
                v = df.iloc[:, vcol.get()].astype(float).values
            except Exception as e:
                from tkinter import messagebox
                messagebox.showerror("Import error", str(e))
                return
            win.destroy()
            self._load_channels(t, [v], ["CH1"])

        btns = ttk.Frame(win)
        btns.pack(pady=(4, 12))
        ttk.Button(btns, text="Load", command=do_load,
                   style="Accent.TButton").pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side=tk.LEFT, padx=6)
