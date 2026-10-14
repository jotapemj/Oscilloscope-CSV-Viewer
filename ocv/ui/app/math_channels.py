"""MATH channels: reference resolution, dependency graph, editor window and channel deletion."""
import numpy as np
import tkinter as tk

from tkinter import ttk

from ocv.core.math_expr import math_eval_with, math_parse
from ocv.core.scope import V_DIVS
from ocv.core.si_format import CHANNEL_UNITS, DEFAULT_UNIT
from ocv.ui import theme
from ocv.ui.theme import channel_color


class MathChannelsMixin:
    """Mixin de OscilloscopeApp: usa su estado vía self."""

    # =============================
    # MATH channels
    # =============================
    def _real_channels(self):
        """Lista de (idx, ch) de canales NO-MATH en orden de self.channels."""
        return [(i, ch) for i, ch in enumerate(self.channels) if "math" not in ch]

    def _ch_ref_map(self):
        """Dict idx_CH_1based -> índice real en self.channels."""
        return {i + 1: idx for i, (idx, ch) in enumerate(self._real_channels())}

    def _m_ref_map(self):
        """Dict idx_M_1based -> índice en self.channels."""
        return {i + 1: idx for i, idx in enumerate(self.math_order)}

    def _resolve_refs(self, ast):
        """Recorre el AST y convierte ('ref', kind, user_idx) a índices
        reales en self.channels. Devuelve (ast_resuelto, falta_de_datos_msg|None).
        Los user_idx son 0-based (como dan los tokens)."""
        ch_map = self._ch_ref_map()  # 1-based user -> idx channels
        m_map = self._m_ref_map()

        def resolve(n):
            tag = n[0]
            if tag in ("num",):
                return n
            if tag == "ref":
                kind = n[1]; user_idx = n[2] + 1
                src_map = ch_map if kind == "CH" else m_map
                if user_idx not in src_map:
                    raise ValueError(f"{kind}{user_idx} does not exist")
                real_idx = src_map[user_idx]
                # guardamos como ('ref_real', real_idx)
                return ("ref_real", real_idx)
            if tag == "neg":
                return ("neg", resolve(n[1]))
            if tag == "bin":
                return ("bin", n[1], resolve(n[2]), resolve(n[3]))
            if tag == "fn1":
                return ("fn1", n[1], resolve(n[2]))
            if tag == "fn2":
                return ("fn2", n[1], resolve(n[2]), resolve(n[3]))
            return n

        return resolve(ast)

    def _eval_resolved(self, ast):
        """Evalúa AST ya resuelto (con ref_real)."""
        return math_eval_with(
            ast, self.time, lambda n: self.channels[n[1]]["voltage"])

    def _math_deps_resolved(self, ast):
        """Devuelve set de índices (en self.channels) de los que depende
        el AST ya resuelto."""
        deps = set()
        def walk(n):
            tag = n[0]
            if tag == "num": return
            if tag == "ref_real": deps.add(n[1]); return
            if tag == "neg": walk(n[1]); return
            if tag in ("bin", "fn2"): walk(n[2]); walk(n[3]); return
            if tag == "fn1": walk(n[2]); return
        walk(ast)
        return deps

    def _math_would_create_cycle(self, target_idx, new_deps):
        """¿Crear/editar la MATH en target_idx con las dependencias new_deps
        crearía un ciclo en el grafo de canales MATH?

        Algoritmo: DFS sobre las dependencias transitivas de new_deps. Si en
        algún punto llegamos a target_idx, hay ciclo (porque entonces target_idx
        dependería de sí mismo).

        Parámetros:
            target_idx : índice del canal MATH que se está creando/editando.
                         Puede ser len(self.channels) si todavía no existe
                         (no estará en self.channels), en cuyo caso solo se
                         comprueba si target_idx es elemento de new_deps
                         (autorefencia directa).
            new_deps   : set de índices (en self.channels) de los que dependerá.

        Devuelve True si hay ciclo.

        Notas:
        - Cualquier dep que apunte a un canal REAL (no MATH) no propaga, son
          hojas del grafo.
        - Si new_deps contiene índices fuera de rango, los ignoramos (deberían
          haberse filtrado antes; aquí solo evitamos crashear).
        """
        # Autoreferencia directa
        if target_idx in new_deps:
            return True

        # DFS
        visited = set()
        stack = list(new_deps)
        while stack:
            idx = stack.pop()
            if idx in visited:
                continue
            visited.add(idx)
            if idx == target_idx:
                return True
            # Solo seguir si idx es MATH (tiene deps propias)
            if 0 <= idx < len(self.channels):
                ch = self.channels[idx]
                math = ch.get("math")
                if math:
                    transitive = math.get("deps_resolved", set())
                    for d in transitive:
                        if d not in visited:
                            stack.append(d)
        return False

    def _recompute_math_channel(self, ch_idx):
        """Recalcula el voltage de un canal MATH a partir de su AST."""
        ch = self.channels[ch_idx]
        if "math" not in ch:
            return
        try:
            v = self._eval_resolved(ch["math"]["ast_resolved"])
            ch["voltage"] = np.asarray(v, dtype=float)
            ch["math"]["invalid"] = False
        except Exception as e:
            ch["math"]["invalid"] = True
            ch["math"]["error"] = str(e)
            ch["voltage"] = np.zeros_like(self.time) if self.time is not None else np.array([])

    def _recompute_all_math(self):
        """Recalcula todos los MATH en orden M1..M4 (los posteriores pueden
        depender de los anteriores)."""
        for idx in list(self.math_order):
            if 0 <= idx < len(self.channels):
                self._recompute_math_channel(idx)

    def _recompute_math_depending_on(self, changed_idx):
        """Recalcula los MATH que dependen (directa o transitivamente) del
        canal changed_idx."""
        dirty = {changed_idx}
        for idx in self.math_order:
            ch = self.channels[idx]
            deps = ch["math"]["deps_resolved"]
            if deps & dirty:
                self._recompute_math_channel(idx)
                dirty.add(idx)

    def open_math_window(self):
        """Ventana para crear un nuevo canal MATH."""
        if self.time is None or not self._real_channels():
            self._show_toast("Load a CSV first")
            return
        if len(self.math_order) >= self.MAX_MATH:
            self._show_toast(f"Max {self.MAX_MATH} MATH channels reached")
            return

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("New MATH channel")
        win.configure(bg=theme.COLOR_BG)
        # 720 de ancho garantiza que los 11 botones de la fila "Nums" entren
        # con padding. El alto 500 sigue siendo suficiente para todo el contenido.
        # Geometry PRIMERO, chrome (transient) DESPUÉS (evita fantasma).
        self._make_resizable_dialog(win, 720, 500, min_w=680, min_h=460)
        self._apply_window_chrome(win)
        win.grab_set()
        # ESC cierra
        self._bind_escape_to_close(win)

        # Bottom bar reservada PRIMERO con side=BOTTOM. Si la packeáramos al
        # final, Tk la pondría arriba (orden de llamada a pack manda, no el
        # atributo side, cuando hay TOP+BOTTOM). Los botones se añaden más
        # abajo a este frame ya packeado.
        btns = tk.Frame(win, bg=theme.COLOR_BG)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=12)

        tk.Label(win, text="Math expression",
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 10, "bold")
                 ).pack(anchor="w", padx=14, pady=(14, 4))

        expr_var = tk.StringVar(value="")
        entry = ttk.Entry(win, textvariable=expr_var, font=("Consolas", 11))
        entry.pack(fill=tk.X, padx=14, pady=(0, 6))
        entry.focus_set()

        status = tk.Label(win, text="Type an expression",
                          bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                          font=("Segoe UI", 9), anchor="w", justify="left")
        status.pack(fill=tk.X, padx=14, pady=(0, 6))

        # Insertadores
        def insert(text):
            entry.insert(tk.INSERT, text)
            entry.focus_set()

        # Canales reales
        pads = tk.Frame(win, bg=theme.COLOR_BG)
        pads.pack(fill=tk.X, padx=14, pady=(4, 0))

        tk.Label(pads, text="Channels:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="nw", pady=(4, 0))

        # Lista combinada: primero CHs reales, luego MATH
        chan_btns = []
        for i, (_ridx, ch) in enumerate(self._real_channels()):
            chan_btns.append((f"CH{i+1}", ch["name"], ch["color"]))
        for i, midx in enumerate(self.math_order):
            mch = self.channels[midx]
            chan_btns.append((f"M{i+1}", mch["name"], mch["color"]))

        CHANS_PER_ROW = 4
        ch_grid = tk.Frame(pads, bg=theme.COLOR_BG)
        ch_grid.grid(row=0, column=1, columnspan=20, sticky="we", pady=(0, 4))
        for idx, (tag, name, color) in enumerate(chan_btns):
            r, c = divmod(idx, CHANS_PER_ROW)
            # Etiqueta con flecha para evitar confusión con el operador '·' (multiplicación)
            b = tk.Button(
                ch_grid, text=f"{tag}  →  {name}",
                bg=theme.COLOR_BG_ALT, fg=color,
                activebackground=theme.COLOR_BG_HOVER,
                activeforeground=color,
                bd=1, relief="flat",
                font=("Segoe UI", 9, "bold"),
                padx=6, pady=3,
                highlightbackground=theme.COLOR_BORDER,
                highlightthickness=1,
                command=lambda s=tag: insert(s),
            )
            b.grid(row=r, column=c, padx=2, pady=2, sticky="we")
            ch_grid.columnconfigure(c, weight=1)
            self._add_hover_shadow(b)

        tk.Label(pads, text="Ops:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=(6, 0))
        for j, op in enumerate(["+", "-", "*", "/", "(", ")"]):
            ttk.Button(pads, text=op, width=4,
                       command=lambda s=op: insert(s)
                       ).grid(row=1, column=1 + j, padx=2, pady=(6, 0))

        tk.Label(pads, text="Funcs:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", pady=(6, 0))
        funcs_1 = [("INT", "INT("), ("DIFF", "DIFF("),
                   ("ABS", "ABS("), ("POW", "POW("), ("SQRT", "SQRT(")]
        funcs_2 = [("MIN", "MIN(,)"), ("MAX", "MAX(,)")]
        for j, (lbl, tx) in enumerate(funcs_1 + funcs_2):
            ttk.Button(pads, text=lbl, width=6,
                       command=lambda s=tx: insert(s)
                       ).grid(row=2, column=1 + j, padx=2, pady=(6, 0))

        # Fila de números
        tk.Label(pads, text="Nums:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 9)).grid(row=3, column=0, sticky="w", pady=(6, 0))
        nums = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "."]
        for j, n in enumerate(nums):
            ttk.Button(pads, text=n, width=3,
                       command=lambda s=n: insert(s)
                       ).grid(row=3, column=1 + j, padx=2, pady=(6, 0))

        # Nombre + Unidad (v1.29)
        name_frame = tk.Frame(win, bg=theme.COLOR_BG)
        name_frame.pack(fill=tk.X, padx=14, pady=(14, 0))
        tk.Label(name_frame, text="Name:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        name_var = tk.StringVar(value="")
        name_entry = ttk.Entry(name_frame, textvariable=name_var,
                               font=("Segoe UI", 10))
        name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        tk.Label(name_frame, text="Unit:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(12, 4))
        unit_var = tk.StringVar(value=DEFAULT_UNIT)
        unit_combo = ttk.Combobox(
            name_frame, textvariable=unit_var,
            values=CHANNEL_UNITS, state="readonly",
            width=5, font=("Segoe UI", 10),
        )
        unit_combo.pack(side=tk.LEFT)

        # Validación en vivo
        validation = {"ast": None, "ok": False}
        name_touched = {"v": False}

        def on_name_typed(*_):
            name_touched["v"] = True
        name_var.trace_add("write", on_name_typed)

        def validate(*_):
            expr = expr_var.get()
            if not expr.strip():
                status.config(text="Type an expression", fg=theme.COLOR_TEXT_DIM)
                validation["ast"] = None
                validation["ok"] = False
                if not name_touched["v"]:
                    name_var.set("")
                return
            try:
                ast = math_parse(expr)
                resolved = self._resolve_refs(ast)
                deps = self._math_deps_resolved(resolved)
                # check ciclos: no puede depender de sí mismo (no aplica al crear
                # nuevo porque aún no existe, pero lo dejamos preparado para edit)
                status.config(
                    text=f"✓ valid   (deps: {len(deps)} channel(s))",
                    fg="#4caf50"
                )
                validation["ast"] = resolved
                validation["ok"] = True
                if not name_touched["v"]:
                    # normalizar para el nombre
                    name_var.trace_remove("write", name_trace_id[0])
                    name_var.set(expr.upper().replace(" ", ""))
                    name_trace_id[0] = name_var.trace_add("write", on_name_typed)
            except ValueError as e:
                status.config(text=f"✗ {e}", fg=theme.COLOR_DANGER)
                validation["ast"] = None
                validation["ok"] = False

        name_trace_id = [name_var.trace_add("write", on_name_typed)]
        expr_var.trace_add("write", validate)

        # Botones (btns ya fue packeado al principio para que quede abajo
        # incluso si la ventana es muy pequeña)
        def on_cancel():
            win.destroy()

        def on_create():
            if not validation["ok"] or validation["ast"] is None:
                return
            expr = expr_var.get().strip()
            name = name_var.get().strip() or expr.upper().replace(" ", "")
            unit = unit_var.get() or DEFAULT_UNIT
            self._create_math_channel(expr, validation["ast"], name, unit=unit)
            win.destroy()

        ttk.Button(btns, text="Cancel", command=on_cancel).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Create", style="Accent.TButton",
                   command=on_create).pack(side=tk.RIGHT, padx=(0, 6))

        entry.bind("<Return>", lambda e: on_create())

    def _create_math_channel(self, expr, ast_resolved, name, unit=DEFAULT_UNIT):
        """Añade un canal MATH a self.channels y recalcula.

        Retorna True si se creó, False si fue rechazado (p.ej. por ciclo).
        `unit` (v1.29) es la etiqueta de unidad del canal, solo display.
        """
        deps = self._math_deps_resolved(ast_resolved)

        # Defensa en profundidad: detección de ciclos. En el flujo normal
        # de creación no debería ser posible (las refs apuntan a canales ya
        # existentes), pero esto cubre dos casos no triviales:
        #   1) Sesiones cargadas con dependencias circulares corruptas.
        #   2) Una hipotética futura feature de edición de MATH.
        # Si se detecta, abortamos sin tocar self.channels y avisamos.
        target_idx = len(self.channels)
        if self._math_would_create_cycle(target_idx, deps):
            self._show_toast(f"Cannot create {name}: would form a cycle")
            return False

        # Color: toma el siguiente disponible (usamos el índice del canal como
        # semilla estable)
        next_idx = len(self.channels)
        color = channel_color(next_idx)

        ch = {
            "_uid":     self._next_channel_uid,
            "name":     name,
            "color":    color,
            "voltage":  np.zeros_like(self.time),
            "visible":  tk.BooleanVar(value=True),
            "v_div":    1.0,
            "v_offset": 0.0,
            "unit":     unit,
            "math": {
                "expr":          expr,
                "ast_resolved":  ast_resolved,
                "deps_resolved": deps,
                "invalid":       False,
                "error":         None,
            },
        }
        self._next_channel_uid += 1
        ch["visible"].trace_add(
            "write", lambda *a: (self.update_plot(), self._mark_dirty()))
        self.channels.append(ch)
        self.math_order.append(len(self.channels) - 1)

        # Calcular
        self._recompute_math_channel(len(self.channels) - 1)

        # Auto-scale razonable
        v = ch["voltage"]
        if len(v) > 0 and np.all(np.isfinite(v)):
            vmax = float(np.max(np.abs(v)))
            if vmax > 0:
                ch["v_div"] = max(vmax / (V_DIVS / 2), 1e-9)

        self._rebuild_channels_panel()
        self.update_plot()
        self._show_toast(f"Created {name}")
        self._mark_dirty()
        return True

    def _math_dependents_of(self, ch_idx):
        """Devuelve lista de índices MATH que dependen (directa o
        transitivamente) de ch_idx."""
        dependents = []
        dirty = {ch_idx}
        for midx in self.math_order:
            if midx == ch_idx:
                continue
            deps = self.channels[midx]["math"]["deps_resolved"]
            if deps & dirty:
                dependents.append(midx)
                dirty.add(midx)
        return dependents

    def _delete_math_channel(self, ch_idx):
        """Borra un canal MATH. Si hay otros MATH que dependen de él,
        pregunta al usuario si borrarlos en cascada.

        Mantenido por compatibilidad — el código nuevo debería llamar a
        _delete_channel(idx), que maneja correctamente tanto MATH como
        canales de datos."""
        return self._delete_channel(ch_idx)

    def _delete_channel(self, ch_idx):
        """Borra cualquier canal (data o MATH) del listado.

        Reglas:
        - Si hay canales MATH que dependen del que se borra (referencian
          su voltaje vía la fórmula), se propone borrado en cascada.
        - Si se borra el último canal, la app vuelve al estado "sin datos
          cargados" (igual que arranque limpio): self.time/voltage = None
          y panel mostrando "No CSV loaded".
        """
        if ch_idx < 0 or ch_idx >= len(self.channels):
            return
        ch = self.channels[ch_idx]
        is_math = "math" in ch

        # Detectar MATH dependientes — un MATH puede referenciar tanto a
        # otro MATH como a un canal de datos.
        dependents = self._math_dependents_of(ch_idx)
        if dependents:
            names = [self.channels[d]["name"] for d in dependents]
            if not self._ask_delete_cascade(ch["name"], names):
                return
            to_delete = sorted(dependents + [ch_idx], reverse=True)
        else:
            to_delete = [ch_idx]

        will_be_empty = (len(self.channels) - len(to_delete)) == 0

        self._delete_channels_by_indices(to_delete)

        if will_be_empty:
            # Vaciar también el resto del estado para que la app vuelva
            # al modo "sin datos". Esto es útil porque si dejamos
            # self.time y self.voltage con valores antiguos, las medidas
            # y FFT siguen "pensando" que hay datos pero no hay canales
            # — comportamiento inconsistente.
            self.time = None
            self.voltage = None
            self.active_channel_idx = 0
            self._rebuild_channels_panel()
            try:
                self._rebuild_measurements_panel()
            except Exception:
                pass
            try:
                self.update_plot()
            except Exception:
                pass
            self._show_toast("All channels removed — no data loaded")
            return

        if len(to_delete) > 1:
            self._show_toast(f"Deleted {len(to_delete)} channel(s)")
        else:
            kind = "MATH" if is_math else "data"
            self._show_toast(f"Deleted {kind} channel")
        self._mark_dirty()

    def _delete_channels_by_indices(self, indices_desc):
        """Borra canales de self.channels en orden descendente de índices.
        Actualiza math_order y resuelve referencias (el ast_resolved guarda
        índices reales, así que hay que remapear)."""
        indices_desc = sorted(indices_desc, reverse=True)

        # Mapa viejo_idx -> nuevo_idx para remapear referencias de AST
        idx_map = {}
        new_idx = 0
        for old_idx in range(len(self.channels)):
            if old_idx in indices_desc:
                idx_map[old_idx] = None  # borrado
            else:
                idx_map[old_idx] = new_idx
                new_idx += 1

        # Borrar
        for idx in indices_desc:
            del self.channels[idx]

        # Remapear math_order
        self.math_order = [idx_map[i] for i in self.math_order if idx_map.get(i) is not None]

        # Remapear AST de MATH restantes
        def remap(n):
            tag = n[0]
            if tag == "num": return n
            if tag == "ref_real":
                new = idx_map.get(n[1])
                if new is None:
                    raise ValueError("dependency deleted")
                return ("ref_real", new)
            if tag == "neg": return ("neg", remap(n[1]))
            if tag == "bin": return ("bin", n[1], remap(n[2]), remap(n[3]))
            if tag == "fn1": return ("fn1", n[1], remap(n[2]))
            if tag == "fn2": return ("fn2", n[1], remap(n[2]), remap(n[3]))
            return n

        for midx in self.math_order:
            ch = self.channels[midx]
            try:
                ch["math"]["ast_resolved"] = remap(ch["math"]["ast_resolved"])
                ch["math"]["deps_resolved"] = self._math_deps_resolved(ch["math"]["ast_resolved"])
            except Exception as e:
                ch["math"]["invalid"] = True
                ch["math"]["error"] = str(e)

        # Ajustar active_channel_idx si fue borrado o quedó fuera de rango
        if self.active_channel_idx >= len(self.channels):
            self.active_channel_idx = max(0, len(self.channels) - 1)

        # Limpiar measurements que apunten a canales borrados y reasignar índices
        new_meas = []
        for m in self.active_measurements:
            old = m.get("channel_idx", 0)
            new = idx_map.get(old)
            if new is not None:
                m["channel_idx"] = new
                new_meas.append(m)
        self.active_measurements = new_meas

        self._rebuild_channels_panel()
        self._rebuild_measurements_panel()
        self.update_plot()

    def _ask_delete_cascade(self, ch_name, dependent_names):
        """Diálogo modal: confirmar borrado en cascada."""
        result = {"ok": False}

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("Delete MATH channel")
        win.configure(bg=theme.COLOR_BG)
        # Geometry PRIMERO, transient DESPUÉS (evita fantasma).
        self._make_resizable_dialog(win, 420, 200)
        win.transient(self.root)
        win.grab_set()

        tk.Label(
            win, text=f"Delete {ch_name}?",
            bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack(padx=14, pady=(14, 4), anchor="w")

        dep_list = ", ".join(dependent_names)
        tk.Label(
            win,
            text=f"The following MATH channel(s) depend on it and will\n"
                 f"also be deleted:\n\n   {dep_list}",
            bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
            font=("Segoe UI", 9), justify="left"
        ).pack(padx=14, pady=(0, 10), anchor="w")

        btns = tk.Frame(win, bg=theme.COLOR_BG)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=12)

        def do_ok():
            result["ok"] = True
            win.destroy()

        def do_cancel():
            result["ok"] = False
            win.destroy()

        ttk.Button(btns, text="Cancel", command=do_cancel).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Delete all", style="Accent.TButton",
                   command=do_ok).pack(side=tk.RIGHT, padx=(0, 6))

        self.root.wait_window(win)
        return result["ok"]
