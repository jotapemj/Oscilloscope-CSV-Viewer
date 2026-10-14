"""Freehand and text annotations tied to a channel's scale, with undo/redo."""
import tkinter as tk

from tkinter import ttk

from ocv.core.scope import H_DIVS
from ocv.ui import theme
from ocv.ui.widgets import Tooltip


class AnnotationsMixin:
    """Mixin de OscilloscopeApp: usa su estado vía self."""

    # =============================
    # Annotations
    # =============================
    # ---- Undo/redo helpers ----
    def _push_undo_annotation(self, kind, items):
        """Registra una acción reversible en el stack. Cualquier acción nueva
        invalida el redo stack (rama del futuro abandonada).

        kind:  "add" o "remove"
        items: lista de (index, annotation_dict) en orden ASCENDENTE de index.
        """
        if not items:
            return
        self._undo_stack.append({"kind": kind, "items": list(items)})
        if len(self._undo_stack) > self.UNDO_STACK_LIMIT:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        # Cualquier acción de anotación (add o remove) modifica la sesión.
        self._mark_dirty()

    def _undo_annotation_action(self):
        """Deshace la última acción del stack. Llamado por Ctrl+Z."""
        if not self._undo_stack:
            self._show_toast("Nothing to undo")
            return
        action = self._undo_stack.pop()
        kind = action["kind"]
        items = action["items"]
        if kind == "add":
            # Estos índices fueron añadidos: quitarlos. En orden descendente
            # para que los índices no se descuadren entre pops.
            for idx, _ann in sorted(items, key=lambda p: -p[0]):
                if 0 <= idx < len(self.annotations):
                    self.annotations.pop(idx)
        else:  # "remove"
            # Re-insertar en orden ascendente
            for idx, ann in sorted(items, key=lambda p: p[0]):
                if idx <= len(self.annotations):
                    self.annotations.insert(idx, ann)
                else:
                    self.annotations.append(ann)
        # La acción inversa va al redo stack
        self._redo_stack.append(action)
        self.update_plot()
        self._show_toast(f"Undo: {kind} ({len(items)})")
        self._mark_dirty()

    def _redo_annotation_action(self):
        """Rehace la última acción deshecha. Llamado por Ctrl+Y / Ctrl+Shift+Z."""
        if not self._redo_stack:
            self._show_toast("Nothing to redo")
            return
        action = self._redo_stack.pop()
        kind = action["kind"]
        items = action["items"]
        if kind == "add":
            # Re-añadir en orden ascendente
            for idx, ann in sorted(items, key=lambda p: p[0]):
                if idx <= len(self.annotations):
                    self.annotations.insert(idx, ann)
                else:
                    self.annotations.append(ann)
        else:  # "remove"
            for idx, _ann in sorted(items, key=lambda p: -p[0]):
                if 0 <= idx < len(self.annotations):
                    self.annotations.pop(idx)
        self._undo_stack.append(action)
        self.update_plot()
        self._show_toast(f"Redo: {kind} ({len(items)})")
        self._mark_dirty()

    def open_annotation_toolbar(self):
        """Muestra (o trae al frente) la toolbar flotante de annotations."""
        if self._annotation_toolbar is not None and self._annotation_toolbar.winfo_exists():
            self._annotation_toolbar.lift()
            return

        tb = tk.Toplevel(self.root)
        tb.withdraw()  # anti-flash
        tb.title("Annotations")
        tb.configure(bg=theme.COLOR_BG_ALT)
        tb.transient(self.root)
        tb.resizable(False, False)
        # Colocarla arriba-centro del plot
        self.root.update_idletasks()
        rx = self.root.winfo_rootx()
        ry = self.root.winfo_rooty()
        rw = self.root.winfo_width()
        tb.geometry(f"+{rx + rw//2 - 180}+{ry + 60}")

        # Borde accent
        inner = tk.Frame(tb, bg=theme.COLOR_BG_ALT)
        inner.pack(padx=2, pady=2)

        # Botones de modo
        mode_btns = {}

        def set_mode(m):
            # Toggle: clic al mismo botón deselecciona
            new = None if self.annotation_mode == m else m
            self.annotation_mode = new
            for name, b in mode_btns.items():
                if name == new:
                    b.config(bg=theme.COLOR_ACCENT, fg=theme.COLOR_TEXT_ON_ACCENT)
                else:
                    b.config(bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT)

        def make_mode_btn(parent, text, mode, tooltip):
            b = tk.Button(
                parent, text=text,
                bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
                activebackground=theme.COLOR_BG_HOVER,
                bd=1, relief="flat",
                font=("Segoe UI", 11, "bold"),
                padx=8, pady=4,
                highlightbackground=theme.COLOR_BORDER,
                highlightthickness=1,
                command=lambda: set_mode(mode),
            )
            b.pack(side=tk.LEFT, padx=2, pady=4)
            # Hover (solo si no está seleccionado)
            def make_hover(bb, mm):
                def enter(_e):
                    if self.annotation_mode != mm:
                        bb.config(highlightbackground=theme.COLOR_ACCENT, bg=theme.COLOR_BG_HOVER)
                def leave(_e):
                    if self.annotation_mode != mm:
                        bb.config(highlightbackground=theme.COLOR_BORDER, bg=theme.COLOR_BG_ALT)
                return enter, leave
            e, l = make_hover(b, mode)
            b.bind("<Enter>", e)
            b.bind("<Leave>", l)
            mode_btns[mode] = b
            if tooltip:
                Tooltip(b, tooltip)
            return b

        row1 = tk.Frame(inner, bg=theme.COLOR_BG_ALT)
        row1.pack(fill=tk.X, padx=4, pady=(4, 0))
        make_mode_btn(row1, "✏ Draw",  "draw",
                      "Freehand draw\nClick and drag on the plot to draw strokes")
        make_mode_btn(row1, "📝 Text", "text",
                      "Place text annotation\nClick on the plot, type in the dialog")
        make_mode_btn(row1, "🧹 Erase","erase",
                      "Erase mode\nClick on a stroke or text to delete it")

        def clear_all():
            if not self.annotations:
                return
            if self._confirm("Clear all annotations?", "Delete all annotations?"):
                # Registrar como "remove" de todas, con sus índices originales.
                items = [(i, a) for i, a in enumerate(self.annotations)]
                self._push_undo_annotation("remove", items)
                self.annotations = []
                self.update_plot()

        clear_btn = tk.Button(
            row1, text="🗑 Clear",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_DANGER,
            activebackground=theme.COLOR_BG_HOVER,
            bd=1, relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=8, pady=4,
            highlightbackground=theme.COLOR_BORDER,
            highlightthickness=1,
            command=clear_all,
        )
        clear_btn.pack(side=tk.LEFT, padx=(10, 2), pady=4)
        self._add_hover_shadow(clear_btn, hover_border=theme.COLOR_DANGER)
        Tooltip(clear_btn,
                "Delete ALL annotations\n"
                "This action can be undone with Ctrl+Z")

        # Segunda fila: color + width + close
        row2 = tk.Frame(inner, bg=theme.COLOR_BG_ALT)
        row2.pack(fill=tk.X, padx=4, pady=(0, 4))

        tk.Label(row2, text="Color:", bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(4, 2))
        color_sw = tk.Label(row2, bg=self.annotation_color, width=3, height=1,
                            relief="flat", bd=1,
                            highlightbackground=theme.COLOR_BORDER, highlightthickness=1,
                            cursor="hand2")
        color_sw.pack(side=tk.LEFT, padx=(0, 8))

        def pick_color(_e=None):
            from tkinter import colorchooser
            c = colorchooser.askcolor(self.annotation_color, parent=tb,
                                      title="Annotation color")
            if c and c[1]:
                self.annotation_color = c[1]
                color_sw.config(bg=self.annotation_color)
        color_sw.bind("<Button-1>", pick_color)

        tk.Label(row2, text="Width:", bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(4, 2))
        width_btns = {}
        def set_width(w):
            self.annotation_width = w
            for ww, bb in width_btns.items():
                bb.config(bg=theme.COLOR_ACCENT if ww == w else theme.COLOR_BG_ALT,
                          fg=theme.COLOR_TEXT_ON_ACCENT if ww == w else theme.COLOR_TEXT)
        for w in (1, 2, 3, 5):
            b = tk.Button(
                row2, text=str(w),
                bg=theme.COLOR_ACCENT if w == self.annotation_width else theme.COLOR_BG_ALT,
                fg=theme.COLOR_TEXT_ON_ACCENT if w == self.annotation_width else theme.COLOR_TEXT,
                bd=1, relief="flat",
                font=("Segoe UI", 9, "bold"),
                padx=6, pady=2,
                highlightbackground=theme.COLOR_BORDER, highlightthickness=1,
                command=lambda ww=w: set_width(ww),
            )
            b.pack(side=tk.LEFT, padx=1)
            width_btns[w] = b
            # Hover solo si no está seleccionado
            def make_hover(bb, ww):
                def enter(_e):
                    if self.annotation_width != ww:
                        bb.config(highlightbackground=theme.COLOR_ACCENT, bg=theme.COLOR_BG_HOVER)
                def leave(_e):
                    if self.annotation_width != ww:
                        bb.config(highlightbackground=theme.COLOR_BORDER, bg=theme.COLOR_BG_ALT)
                return enter, leave
            e, l = make_hover(b, w)
            b.bind("<Enter>", e)
            b.bind("<Leave>", l)

        def close_toolbar():
            self.annotation_mode = None
            self._current_stroke = None
            self._annotation_toolbar = None
            tb.destroy()

        # Sin botón ✕ propio: la ventana ya tiene su X en la barra de
        # título (WM_DELETE_WINDOW) y Escape para cerrar — duplicar el
        # botón solo añadía ruido visual.
        tb.protocol("WM_DELETE_WINDOW", close_toolbar)
        self._bind_escape_to_close(tb, on_close=close_toolbar)
        # Anti-flash: re-mostrar tras procesar layout pendiente.
        try:
            tb.after_idle(lambda: (tb.update_idletasks(), tb.deiconify()))
        except Exception:
            try:
                tb.deiconify()
            except Exception:
                pass
        self._annotation_toolbar = tb

    def _confirm(self, title, message):
        """Diálogo Yes/No simple reutilizable."""
        result = {"ok": False}
        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title(title)
        win.configure(bg=theme.COLOR_BG)
        # Geometry PRIMERO, transient DESPUÉS (evita fantasma).
        self._make_resizable_dialog(win, 320, 130)
        win.transient(self.root)
        win.grab_set()
        tk.Label(win, text=message, bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 10), wraplength=280, justify="left"
                 ).pack(padx=14, pady=(18, 10))
        btns = tk.Frame(win, bg=theme.COLOR_BG)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=12)
        def yes():
            result["ok"] = True; win.destroy()
        def no():
            result["ok"] = False; win.destroy()
        ttk.Button(btns, text="No", command=no).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Yes", style="Accent.TButton",
                   command=yes).pack(side=tk.RIGHT, padx=(0, 6))
        self.root.wait_window(win)
        return result["ok"]

    # ---- Gestión de puntos data/pantalla ----
    def _data_point_from_event(self, event):
        """Convierte event (xdata/ydata) a coords del axes (time_real, y_screen).

        OJO con el nombre: event.ydata es la coordenada Y del eje matplotlib
        ("y_screen"), NO el voltaje real de ninguna traza. Las trazas se
        dibujan transformando v_real → y_screen mediante la escala del canal
        activo (ver _full_redraw_plot). Para que las anotaciones sigan a una
        traza de forma coherente al cambiar el canal activo (que reescala
        todo el eje Y), los handlers de creación deben transformar este
        y_screen a v_real con _annot_v_real_from_screen() antes de guardar.
        """
        if event.xdata is None or event.ydata is None:
            return None
        t_real = self.t_offset.get() + event.xdata
        return (t_real, event.ydata)

    # ---- Anotaciones: transformación entre v_real y y_screen ----
    # Las anotaciones se anclan al canal con el que se crearon (su "ch_idx").
    # Internamente almacenan el voltaje REAL del canal, no la y_screen. Así,
    # al cambiar el canal activo (que reescala el eje Y mediante v_div_active),
    # la anotación se re-mapea a la y_screen correcta y permanece visible y
    # alineada con su traza. Antes, almacenar y_screen "crudo" causaba que
    # la anotación desapareciera o se descentrara cuando canales con distinto
    # v_div se alternaban como activos.
    def _annot_v_real_from_screen(self, y_screen):
        """y_screen (coord del eje) → voltaje real del canal activo actual."""
        if not self.channels:
            return y_screen
        active = self.channels[self.active_channel_idx]
        v_div_active = self.v_div.get()
        if v_div_active <= 0 or active.get("v_div", 0) <= 0:
            return y_screen
        divs = y_screen / v_div_active
        return divs * active["v_div"] + active.get("v_offset", 0.0)

    def _annot_screen_from_v_real(self, v_real, ch_idx):
        """v_real (referido al canal ch_idx) → y_screen del axes actual.
        Fallback: si ch_idx no es válido, asume formato legacy y devuelve
        v_real tal cual (que en sesiones antiguas era y_screen directo)."""
        if not (isinstance(ch_idx, int) and 0 <= ch_idx < len(self.channels)):
            return v_real
        ch = self.channels[ch_idx]
        if ch.get("v_div", 0) <= 0:
            return v_real
        v_div_active = self.v_div.get()
        return (v_real - ch.get("v_offset", 0.0)) / ch["v_div"] * v_div_active

    def _is_annotation_active(self):
        return self.annotation_mode in ("draw", "text", "erase")

    # ---- Handlers de evento ----
    def _on_annotation_press(self, event):
        """Llamado desde on_mouse_press si annotation está activa. Devuelve
        True si consumió el evento."""
        if not self._is_annotation_active() or event.inaxes != self.ax:
            return False
        if event.button != 1:
            return False

        p_screen = self._data_point_from_event(event)
        if p_screen is None:
            return False

        # Para CREATE (draw/text): convertir y_screen → v_real del canal
        # activo. Para ERASE: dejar la y_screen tal cual (la comparación
        # se hace en coords del axes actual; _erase_at transforma las
        # anotaciones almacenadas a y_screen para comparar).
        t_real, y_screen = p_screen
        ch_idx_now = self.active_channel_idx

        if self.annotation_mode == "draw":
            p_real = (t_real, self._annot_v_real_from_screen(y_screen))
            self._current_stroke = {
                "type": "stroke",
                "color": self.annotation_color,
                "width": self.annotation_width,
                "points": [p_real],
                "ch_idx": ch_idx_now,
            }
            return True

        if self.annotation_mode == "text":
            p_real = (t_real, self._annot_v_real_from_screen(y_screen))
            self._prompt_text_annotation(p_real, ch_idx=ch_idx_now)
            return True

        if self.annotation_mode == "erase":
            self._erase_at(p_screen, event)
            return True

        return False

    def _on_annotation_move(self, event):
        """Llamado desde on_mouse_move. True si consume."""
        if self._current_stroke is None:
            return False
        if event.inaxes != self.ax or event.xdata is None:
            return False
        p_screen = self._data_point_from_event(event)
        if p_screen is None:
            return False
        t_real, y_screen = p_screen
        p_real = (t_real, self._annot_v_real_from_screen(y_screen))
        self._current_stroke["points"].append(p_real)
        self.update_plot()
        return True

    def _on_annotation_release(self, event):
        """Llamado desde on_mouse_release. True si consume."""
        if self._current_stroke is None:
            return False
        # Solo guardar si tiene al menos 2 puntos
        if len(self._current_stroke["points"]) >= 2:
            new_idx = len(self.annotations)
            self.annotations.append(self._current_stroke)
            self._push_undo_annotation("add", [(new_idx, self._current_stroke)])
        self._current_stroke = None
        self.update_plot()
        return True

    def _prompt_text_annotation(self, pos, ch_idx=None):
        """Ventana para introducir el texto de la annotation (Consolas, tamaño elegible).

        `pos` es (t_real, v_real) — el segundo elemento es voltaje real del
        canal `ch_idx`, NO y_screen. Ver _annot_v_real_from_screen().
        """
        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("Annotation text")
        win.configure(bg=theme.COLOR_BG)
        win.resizable(True, True)
        win.minsize(420, 280)
        # Geometry PRIMERO, transient DESPUÉS (evita fantasma).
        self._center_dialog(win, 480, 320)
        win.transient(self.root)
        win.grab_set()

        tk.Label(win, text="Annotation text:",
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 10, "bold")
                 ).pack(anchor="w", padx=14, pady=(14, 4))

        # Selector de tamaño
        size_row = tk.Frame(win, bg=theme.COLOR_BG)
        size_row.pack(fill=tk.X, padx=14, pady=(0, 6))
        tk.Label(size_row, text="Size:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 6))

        SIZES = [10, 12, 14, 18, 24, 32, 48]
        size_var = tk.IntVar(value=14)

        # Preview del texto al cambiar tamaño
        def on_size_change():
            s = size_var.get()
            txt.config(font=("Consolas", s))

        size_btns = {}
        def set_size(s):
            size_var.set(s)
            on_size_change()
            for ss, bb in size_btns.items():
                if ss == s:
                    bb.config(bg=theme.COLOR_ACCENT, fg=theme.COLOR_TEXT_ON_ACCENT)
                else:
                    bb.config(bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT)

        for s in SIZES:
            b = tk.Button(
                size_row, text=str(s),
                bg=theme.COLOR_ACCENT if s == size_var.get() else theme.COLOR_BG_ALT,
                fg=theme.COLOR_TEXT_ON_ACCENT if s == size_var.get() else theme.COLOR_TEXT,
                bd=1, relief="flat",
                font=("Segoe UI", 9, "bold"),
                padx=6, pady=2,
                highlightbackground=theme.COLOR_BORDER, highlightthickness=1,
                command=lambda ss=s: set_size(ss),
            )
            b.pack(side=tk.LEFT, padx=1)
            size_btns[s] = b
            # Hover
            def make_hover(bb, ss):
                def enter(_e):
                    if size_var.get() != ss:
                        bb.config(highlightbackground=theme.COLOR_ACCENT, bg=theme.COLOR_BG_HOVER)
                def leave(_e):
                    if size_var.get() != ss:
                        bb.config(highlightbackground=theme.COLOR_BORDER, bg=theme.COLOR_BG_ALT)
                return enter, leave
            e, l = make_hover(b, s)
            b.bind("<Enter>", e)
            b.bind("<Leave>", l)

        # IMPORTANTE: empaquetar los botones ANTES que el Text para que
        # reserven su espacio abajo aunque el texto crezca.
        btns = tk.Frame(win, bg=theme.COLOR_BG)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=10)

        txt = tk.Text(win, height=5, font=("Consolas", 14),
                      bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
                      insertbackground=theme.COLOR_TEXT,
                      wrap="word")
        txt.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 6))
        txt.focus_set()

        def on_ok():
            content = txt.get("1.0", "end-1c").strip()
            if content:
                new_ann = {
                    "type": "text",
                    "color": self.annotation_color,
                    "pos": pos,
                    "content": content,
                    "size": int(size_var.get()),
                }
                # Anclar la anotación al canal con el que se creó. None se
                # interpreta como "formato legacy" en el draw (Y como
                # y_screen directo), aceptable como fallback.
                if ch_idx is not None:
                    new_ann["ch_idx"] = ch_idx
                new_idx = len(self.annotations)
                self.annotations.append(new_ann)
                self._push_undo_annotation("add", [(new_idx, new_ann)])
                self.update_plot()
            win.destroy()

        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side=tk.RIGHT)
        ttk.Button(btns, text="OK", style="Accent.TButton",
                   command=on_ok).pack(side=tk.RIGHT, padx=(0, 6))
        txt.bind("<Control-Return>", lambda e: on_ok())

    def _erase_at(self, pos, event):
        """Borra la annotation más cercana al punto de clic.

        `pos` es (t_real, y_screen) — el segundo elemento es coord del
        axes (no v_real). La comparación se hace en y_screen: para cada
        anotación almacenada como (t_real, v_real) la transformamos a
        y_screen vía _annot_screen_from_v_real(v_real, ch_idx) antes de
        medir la distancia visual al clic.
        """
        if not self.annotations:
            return
        # Threshold en píxeles para detectar proximidad
        # Convierto 10 px a distancia en data coords
        try:
            inv = self.ax.transData.inverted()
            p0 = inv.transform((event.x, event.y))
            p1 = inv.transform((event.x + 10, event.y + 10))
            # Escala aprox en cada eje
            dx_thr = abs(p1[0] - p0[0])
            dy_thr = abs(p1[1] - p0[1])
        except Exception:
            return

        t_click, y_click_screen = pos

        def _pt_y_screen(pt, a_ch_idx):
            # Anotaciones con ch_idx (formato nuevo): transformar v_real a
            # y_screen del axes actual. Sin ch_idx (legacy): el valor ya
            # estaba almacenado como y_screen directo, devolvemos pt[1].
            if isinstance(a_ch_idx, int) and 0 <= a_ch_idx < len(self.channels):
                return self._annot_screen_from_v_real(pt[1], a_ch_idx)
            return pt[1]

        best_i = None
        best_d = float("inf")
        for i, a in enumerate(self.annotations):
            a_ch_idx = a.get("ch_idx")
            if a["type"] == "stroke":
                for pt in a["points"]:
                    pt_y = _pt_y_screen(pt, a_ch_idx)
                    dt = (pt[0] - t_click) / max(dx_thr, 1e-12)
                    dv = (pt_y - y_click_screen) / max(dy_thr, 1e-12)
                    d = dt*dt + dv*dv
                    if d < best_d:
                        best_d = d
                        best_i = i
            else:  # text
                pt = a["pos"]
                pt_y = _pt_y_screen(pt, a_ch_idx)
                dt = (pt[0] - t_click) / max(dx_thr, 1e-12)
                dv = (pt_y - y_click_screen) / max(dy_thr, 1e-12)
                d = dt*dt + dv*dv
                if d < best_d:
                    best_d = d
                    best_i = i

        # Umbral: dentro de ~15 px del clic
        if best_i is not None and best_d < 2.25:
            removed_ann = self.annotations.pop(best_i)
            self._push_undo_annotation("remove", [(best_i, removed_ann)])
            self.update_plot()

    def _draw_annotations(self):
        """Dibuja todas las annotations en el plot actual. Ignora las que
        están fuera de la ventana temporal visible (optimización).

        Limpia artists previos antes de redibujar — necesario en fast path
        donde no se hace ax.clear() entre frames.
        """
        # Limpiar artists previos
        for a in getattr(self, "_annotation_artists", []):
            try:
                a.remove()
            except Exception:
                pass
        self._annotation_artists = []

        if not self.annotations and self._current_stroke is None:
            return
        t_start = self.t_offset.get()
        window = self.t_div.get() * H_DIVS
        t_end = t_start + window

        def _y_screen(v_real, a_ch_idx):
            # Si la anotación tiene ch_idx válido (formato nuevo), v_real es
            # voltaje real del canal a_ch_idx y hay que transformarlo a la
            # y_screen actual usando el v_div_active vigente. Sin ch_idx
            # (legacy de sesiones v1.23 o anteriores), v_real era ya un
            # y_screen "crudo" — se devuelve tal cual.
            if isinstance(a_ch_idx, int) and 0 <= a_ch_idx < len(self.channels):
                return self._annot_screen_from_v_real(v_real, a_ch_idx)
            return v_real

        def draw_one(a):
            a_ch_idx = a.get("ch_idx")
            if a["type"] == "stroke":
                pts = a["points"]
                if len(pts) < 2:
                    return
                xs = [p[0] - t_start for p in pts]
                ys = [_y_screen(p[1], a_ch_idx) for p in pts]
                # Filtrado grueso: si todo el stroke está fuera, saltar
                if max(xs) < 0 or min(xs) > window:
                    return
                lines = self.ax.plot(xs, ys, color=a["color"],
                             linewidth=a.get("width", 2),
                             solid_capstyle="round",
                             solid_joinstyle="round",
                             zorder=5)
                if lines:
                    self._annotation_artists.append(lines[0])
            else:  # text
                t, v_real = a["pos"]
                if not (t_start <= t <= t_end):
                    return
                y = _y_screen(v_real, a_ch_idx)
                txt = self.ax.text(t - t_start, y, a["content"],
                             color=a["color"], fontsize=a.get("size", 10),
                             fontfamily="Consolas",
                             verticalalignment="center",
                             zorder=5)
                self._annotation_artists.append(txt)

        for a in self.annotations:
            draw_one(a)
        if self._current_stroke is not None:
            draw_one(self._current_stroke)
