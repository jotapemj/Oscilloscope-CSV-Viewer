"""Session lifecycle in the UI: title and dirty state, save/load prompts, state <-> payload."""
import tkinter as tk

from tkinter import ttk

from ocv.core.math_expr import math_parse
from ocv.core.session import (
    embedded_csv,
    is_newer,
    read_session,
    serialize_annotations,
    SESSION_VERSION,
    SessionError,
    write_session,
)
from ocv.core.si_format import CHANNEL_UNITS, DEFAULT_UNIT
from ocv.ui import theme


class SessionMixin:
    """Mixin de OscilloscopeApp: usa su estado vía self."""

    # =============================================================
    # SESIONES: guardar/cargar estado de la aplicación a .ocv
    # =============================================================
    # Un "session file" es un JSON con toda la configuración que el
    # usuario ha dejado montada: canales (nombres, colores, offsets,
    # v/div, visibilidad), MATH, cursores, trigger, XY, tema de grid,
    # annotations, medidas activas, Y el contenido del CSV embebido.
    #
    # Embeber el CSV hace el .ocv autocontenido (portabilidad total: el
    # usuario puede mandar el .ocv a otra máquina sin tener que enviar
    # el CSV por separado). Coste: el .ocv pesa más que el CSV original
    # (overhead JSON: comillas, escapes, indentación).
    #
    # Formato y versión: ver ocv.core.session
    SESSION_VERSION = SESSION_VERSION

    # =============================================================
    # SESIONES: helpers de estado (Untitled / dirty / título) — v1.25
    # =============================================================
    def _session_display_name(self):
        """Nombre legible de la sesión actual para el título.

        - Si hay session_path, devuelve el basename sin extensión .ocv.
        - Si no, devuelve 'Untitled'.
        """
        import os
        if self.session_path:
            base = os.path.basename(self.session_path)
            stem, _ext = os.path.splitext(base)
            return stem or base
        return "Untitled"

    def _update_title(self):
        """Reconstruye el título de la ventana principal a partir del estado
        de sesión. Formato:
            "<*><nombre> — Oscilloscope CSV Viewer"
        El asterisco solo aparece si hay cambios sin guardar.
        """
        try:
            mark = "*" if self.session_dirty else ""
            self.root.title(
                f"{mark}{self._session_display_name()} — Oscilloscope CSV Viewer"
            )
        except Exception:
            # Si la root no existe o ha sido destruida, no es crítico.
            pass

    def _mark_dirty(self):
        """Marca la sesión como modificada. Idempotente: si ya estaba dirty
        no toca nada (evita refrescar el título en cada tick de un slider).
        """
        if self._suppress_dirty:
            return
        if self.session_dirty:
            return
        self.session_dirty = True
        self._update_title()

    def _clear_dirty(self):
        """Quita el flag dirty y refresca el título."""
        if self.session_dirty:
            self.session_dirty = False
            self._update_title()
        else:
            # aún así puede haber cambiado el nombre (Save As)
            self._update_title()

    def _prompt_save_if_dirty(self, context="closing"):
        """Si hay cambios sin guardar pregunta al usuario qué hacer.

        Devuelve True si la operación que provocó la llamada puede continuar
        (Save OK / Discard), False si el usuario cancela (Cancel o Save falló).
        Si no hay datos cargados o no hay nada dirty, devuelve True directamente.
        """
        if not self.session_dirty:
            return True
        choice = self._ask_save_changes_dialog(context)
        if choice == "cancel":
            return False
        if choice == "save":
            # El usuario quiere guardar. Si falla (cancela el filedialog en
            # Untitled, o error de IO), abortamos la operación.
            ok = self.save_session()
            return bool(ok)
        # "discard" → continuar perdiendo los cambios.
        return True

    def _ask_save_changes_dialog(self, context):
        """Diálogo modal con chrome del tema actual: Save / Don't save / Cancel.

        Devuelve "save", "discard" o "cancel". Sustituye al messagebox.
        askyesnocancel nativo (que rompía la coherencia visual del resto de
        la app y no respondía al tema).

        Atajos: Enter = Save (acción primaria), Escape = Cancel.
        """
        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("Unsaved changes")
        win.configure(bg=theme.COLOR_BG)
        self._center_dialog(win, 460, 220)
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)
        try:
            self._apply_window_chrome(win)
        except Exception:
            pass

        result = {"choice": "cancel"}  # default si cierran con la X del WM

        # Header del estilo del resto de diálogos (Trigger, XY, Load CSV…).
        hdr = tk.Frame(win, bg=theme.COLOR_BG_ALT, height=44)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="UNSAVED CHANGES",
                 bg=theme.COLOR_BG_ALT, fg=theme.COLOR_ACCENT,
                 font=("Segoe UI", 11, "bold")
                 ).pack(side=tk.LEFT, padx=14, pady=10)

        # Cuerpo: nombre de la sesión + acción que iba a pasar.
        body = tk.Frame(win, bg=theme.COLOR_BG)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=(14, 6))
        tk.Label(body,
                 text=f"You have unsaved changes in '{self._session_display_name()}'.",
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 10, "bold"),
                 wraplength=420, justify="left", anchor="w"
                 ).pack(fill=tk.X)
        tk.Label(body,
                 text=f"Save before {context}?",
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 9),
                 wraplength=420, justify="left", anchor="w"
                 ).pack(fill=tk.X, pady=(4, 0))

        # Botones: misma fila, alineados a la derecha, Save destacado.
        btns = tk.Frame(win, bg=theme.COLOR_BG)
        btns.pack(fill=tk.X, padx=14, pady=(8, 12))

        def pick(choice):
            result["choice"] = choice
            try:
                win.destroy()
            except Exception:
                pass

        save_btn = ttk.Button(btns, text="Save",
                              style="Accent.TButton",
                              command=lambda: pick("save"))
        save_btn.pack(side=tk.RIGHT, padx=(4, 0))

        discard_btn = ttk.Button(btns, text="Don't save",
                                 command=lambda: pick("discard"))
        discard_btn.pack(side=tk.RIGHT, padx=(4, 0))

        cancel_btn = ttk.Button(btns, text="Cancel",
                                command=lambda: pick("cancel"))
        cancel_btn.pack(side=tk.RIGHT, padx=(4, 0))

        # Atajos: Enter = Save (acción primaria), Escape = Cancel.
        win.bind("<Return>",  lambda _e: pick("save"))
        win.bind("<Escape>",  lambda _e: pick("cancel"))
        # El WM-close se considera Cancel (no destruye datos por descuido).
        win.protocol("WM_DELETE_WINDOW", lambda: pick("cancel"))

        save_btn.focus_set()
        win.wait_window()
        return result["choice"]

    def save_session(self):
        """Guarda la sesión en su path actual. Si todavía es Untitled
        (no hay session_path), delega en save_session_as.

        Devuelve True si se guardó con éxito, False si el usuario canceló
        el filedialog o si hubo un error de IO. El valor de retorno lo usa
        _prompt_save_if_dirty para decidir si la operación que disparó el
        prompt puede continuar.
        """
        if not self.channels:
            self._show_toast("Nothing to save")
            return False
        if not self.session_path:
            return self.save_session_as()
        return self._write_session_to(self.session_path)

    def save_session_as(self):
        """Pregunta path con un filedialog y guarda allí. Actualiza
        session_path al nuevo path en caso de éxito (Save posterior irá
        directo sin diálogo).
        """
        import os
        from tkinter import filedialog

        if not self.channels:
            self._show_toast("Nothing to save")
            return False

        # Nombre inicial: si ya tenemos session_path, su basename.
        # Si no, intentamos con el nombre del CSV; si no, session.ocv.
        if self.session_path:
            default = os.path.basename(self.session_path)
        elif self.current_filename:
            default = os.path.splitext(self.current_filename)[0] + ".ocv"
        else:
            default = "session.ocv"

        # initialdir: misma carpeta que el path actual si existe, si no
        # la del CSV cargado. Si nada, deja que el SO decida.
        initial_dir = None
        if self.session_path:
            initial_dir = os.path.dirname(self.session_path) or None
        elif self.current_filepath:
            initial_dir = os.path.dirname(self.current_filepath) or None

        kwargs = dict(
            defaultextension=".ocv",
            initialfile=default,
            filetypes=[
                ("Oscilloscope CSV Viewer Session", "*.ocv"),
                ("All files", "*.*"),
            ],
            title="Save session as...",
        )
        if initial_dir:
            kwargs["initialdir"] = initial_dir
        path = filedialog.asksaveasfilename(**kwargs)
        if not path:
            return False

        return self._write_session_to(path)

    def _write_session_to(self, path):
        """Escribe el payload de sesión a `path`. Actualiza session_path,
        limpia dirty y refresca el título. Devuelve True/False.
        """
        from tkinter import messagebox
        try:
            payload = self._build_session_dict()
            write_session(path, payload)
            self.session_path = path
            self._clear_dirty()  # también refresca el título con el nuevo nombre
            self._show_toast("Session saved")
            return True
        except Exception as e:
            messagebox.showerror(
                "Save session failed",
                f"Could not save session:\n\n{e}",
            )
            return False

    def load_session(self):
        """Abre diálogo y carga una sesión .ocv (JSON con CSV embebido)."""
        import os, tempfile
        from tkinter import filedialog, messagebox

        # Si hay cambios sin guardar en la sesión actual, ofrece guardarlos
        # antes de descartarlos para abrir otra. El usuario puede cancelar.
        if not self._prompt_save_if_dirty(context="loading another session"):
            return

        path = filedialog.askopenfilename(
            filetypes=[
                ("Oscilloscope CSV Viewer Session", "*.ocv"),
                ("All files", "*.*"),
            ],
            title="Load session",
        )
        if not path:
            return

        # --- 1) Leer y validar formato/versión ---
        try:
            payload = read_session(path)
        except SessionError as e:
            messagebox.showerror("Load session", str(e))
            return
        if is_newer(payload):
            if not messagebox.askyesno(
                "Load session",
                "This session was saved with a newer version of the app.\n"
                "Try to load it anyway?",
            ):
                return

        # --- 2) Recuperar el CSV embebido ---
        try:
            csv_content, csv_name = embedded_csv(payload)
        except SessionError as e:
            messagebox.showerror("Load session", str(e))
            return

        # Escribir el contenido a un fichero temporal para reusar el camino
        # estándar de carga (_read_csv_smart espera un path). Lo borramos en
        # el finalize del callback. Si tempfile falla, abortamos.
        try:
            suffix = os.path.splitext(csv_name)[1] or ".csv"
            tmp = tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=suffix, delete=False)
            tmp.write(csv_content)
            tmp.close()
            tmp_path = tmp.name
        except Exception as e:
            messagebox.showerror(
                "Load session",
                f"Could not extract embedded CSV:\n\n{e}",
            )
            return

        # Callback encadenado: aplicar sesión Y limpiar el temp.
        # Importante: durante toda la fase de carga suprimimos _mark_dirty
        # para que los writes a tk.Vars que hace _apply_session no marquen
        # la sesión recién cargada como modificada.
        def _after_load():
            self._suppress_dirty = True
            try:
                try:
                    self._apply_session(payload)
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
                    # Restaurar el nombre original "visible" del CSV
                    # (en la UI queremos que figure como el CSV embebido,
                    # no como el path temp del sistema).
                    try:
                        self.current_filename = csv_name
                        self.current_filepath = None
                        # Cachear el CSV embebido: el temp ya se borró, y
                        # un próximo save sin esto generaría csv_content="".
                        self._embedded_csv_content = csv_content
                    except Exception:
                        pass
                    # Sesión cargada con éxito: pasa a ser la sesión activa
                    # con su path y sin cambios pendientes.
                    self.session_path = path
            finally:
                self._suppress_dirty = False
                self._clear_dirty()

        # Si el load_csv interno marca dirty por trazas/hooks, también lo
        # suprimimos durante esa fase.
        self._suppress_dirty = True
        try:
            self._load_csv_from_path(tmp_path, on_loaded=_after_load)
        finally:
            # _load_csv_from_path es síncrono en este flujo (no usa after),
            # así que ya hemos vuelto. Si en algún momento se vuelve async,
            # _after_load se encarga de re-suprimir.
            self._suppress_dirty = False

    def _build_session_dict(self):
        """Serializa el estado relevante a un dict JSON-compatible."""
        # Canales: separamos "reales" de MATH. Para reales guardamos solo
        # metadatos (los voltajes vienen del CSV al recargar). Para MATH
        # guardamos la expresión, que se re-evalúa contra los canales nuevos.
        channels_out = []
        for i, ch in enumerate(self.channels):
            entry = {
                "idx":      i,
                "name":     ch.get("name"),
                "color":    ch.get("color"),
                "visible":  bool(ch["visible"].get()) if "visible" in ch else True,
                "v_div":    float(ch.get("v_div", 1.0)),
                "v_offset": float(ch.get("v_offset", 0.0)),
                "unit":     ch.get("unit", DEFAULT_UNIT),  # v1.29
                "is_math":  "math" in ch,
            }
            if "math" in ch:
                entry["math_expr"] = ch["math"].get("expr", "")
            channels_out.append(entry)

        # Annotations: stroke listas de (t, v); text tiene pos + content
        annotations_out = serialize_annotations(self.annotations)

        # Leer el CSV original como texto para embeberlo. Si no se puede
        # (CSV borrado entre carga y save), guardamos cadena vacía y al
        # cargar la sesión se notificará el error.
        csv_content = ""
        if self.current_filepath:
            try:
                with open(self.current_filepath, "r",
                          encoding="utf-8", errors="replace") as _f:
                    csv_content = _f.read()
            except Exception:
                csv_content = ""
        # Fallback: si la sesión actual vino de un .ocv (current_filepath
        # quedó en None tras borrar el temp), usar el cache embebido.
        # Sin esto, re-guardar una sesión recién cargada producía un .ocv
        # con csv_content="" y al re-abrir saltaba el error de "no
        # embedded CSV data".
        if not csv_content and self._embedded_csv_content:
            csv_content = self._embedded_csv_content

        return {
            "version":       self.SESSION_VERSION,
            "app":           "OscilloscopeCSVViewer",
            "csv_name":      self.current_filename,
            "csv_content":   csv_content,
            "active_idx":    self.active_channel_idx,
            "render_mode":   self.render_mode.get(),
            "t_div":         float(self.t_div.get()),
            "v_div":         float(self.v_div.get()),
            "t_offset":      float(self.t_offset.get()),
            "v_offset":      float(self.v_offset.get()),
            "fine_mode":     bool(self.fine_mode.get()),
            "time_origin":   float(self.time_origin),
            "channels":      channels_out,
            "cursors": {
                "enabled": bool(self.cursors_enabled.get()),
                "mode":    self.cursor_mode.get(),
                "x":       [float(x) for x in self.cursor_x],
                "y":       [float(y) for y in self.cursor_y],
            },
            "trigger":       dict(self.trigger_config),
            "xy":            dict(self.xy_config),
            # v1.29: app_theme y grid_theme NO se persisten en sesión
            # (preferencias personales, guardadas en user_config).
            # Sesiones viejas que los traen se ignoran en _apply_session.
            "annotations":   annotations_out,
            "measurements":  [
                {"channel_idx": m.get("channel_idx"), "name": m.get("name"),
                 "channel_idx2": m.get("channel_idx2")}
                for m in self.active_measurements
            ],
        }

    def _apply_session(self, payload):
        """Aplica los campos de una sesión DESPUÉS de cargar el CSV.

        Es tolerante a diferencias: si el CSV nuevo tiene distinto número
        de canales, aplicamos lo que podamos y saltamos el resto.
        """
        from tkinter import messagebox

        # --- Canales reales: aplicar nombre/color/visible/v_div/v_offset ---
        saved_channels = payload.get("channels", [])
        # Separar reales y math (los math van tras el load_csv sin MATH)
        real_saved = [c for c in saved_channels if not c.get("is_math")]
        math_saved = [c for c in saved_channels if c.get("is_math")]

        mismatched_count = False
        for i, saved in enumerate(real_saved):
            if i >= len(self.channels):
                mismatched_count = True
                break
            ch = self.channels[i]
            try:
                if saved.get("name"):    ch["name"]  = saved["name"]
                if saved.get("color"):   ch["color"] = saved["color"]
                if "v_div" in saved:     ch["v_div"]    = float(saved["v_div"])
                if "v_offset" in saved:  ch["v_offset"] = float(saved["v_offset"])
                # v1.29: unit por canal. Sesiones viejas sin "unit" → "V".
                if "unit" in saved and saved["unit"] in CHANNEL_UNITS:
                    ch["unit"] = saved["unit"]
                if "visible" in saved and "visible" in ch:
                    ch["visible"].set(bool(saved["visible"]))
            except Exception:
                continue

        if len(real_saved) > len(self.channels):
            mismatched_count = True

        # --- Recrear MATH channels ---
        math_errors = []
        for m in math_saved:
            expr = m.get("math_expr", "").strip()
            name = m.get("name") or "M"
            if not expr:
                continue
            try:
                ast = math_parse(expr)
                ast_resolved = self._resolve_refs(ast)
                # v1.29: la unit del MATH guardada se aplica al crear.
                saved_unit = m.get("unit") if m.get("unit") in CHANNEL_UNITS else DEFAULT_UNIT
                self._create_math_channel(expr, ast_resolved, name, unit=saved_unit)
                # Aplicar color guardado al canal recién creado
                if m.get("color") and self.channels:
                    self.channels[-1]["color"] = m["color"]
            except Exception as e:
                math_errors.append(f"{name}: {e}")

        # --- Vista (t_div, offset, etc.) ---
        try:
            if "t_div" in payload:    self.t_div.set(float(payload["t_div"]))
            if "v_div" in payload:    self.v_div.set(float(payload["v_div"]))
            if "t_offset" in payload: self.t_offset.set(float(payload["t_offset"]))
            if "v_offset" in payload: self.v_offset.set(float(payload["v_offset"]))
            if "fine_mode" in payload:
                self.fine_mode.set(bool(payload["fine_mode"]))
        except Exception:
            pass

        # --- Canal activo (si índice válido) ---
        try:
            idx = int(payload.get("active_idx", 0))
            if 0 <= idx < len(self.channels):
                self.active_channel_idx = idx
                self._sync_active_channel_vars()
        except Exception:
            pass

        # --- Render mode ---
        rm = payload.get("render_mode")
        if rm in ("Detail", "Fast"):
            self.render_mode.set(rm)
            self._update_render_mode_btn_visual()

        # --- Cursores ---
        cur = payload.get("cursors", {}) or {}
        try:
            self.cursors_enabled.set(bool(cur.get("enabled", False)))
            if cur.get("mode"):
                self.cursor_mode.set(cur["mode"])
            if isinstance(cur.get("x"), list) and len(cur["x"]) == 2:
                self.cursor_x = [float(cur["x"][0]), float(cur["x"][1])]
            if isinstance(cur.get("y"), list) and len(cur["y"]) == 2:
                self.cursor_y = [float(cur["y"][0]), float(cur["y"][1])]
            self.on_cursors_toggle()
        except Exception:
            pass

        # --- Trigger ---
        trig = payload.get("trigger")
        if isinstance(trig, dict):
            # Copiamos solo claves conocidas por si el formato evoluciona
            for k, v in trig.items():
                if k in self.trigger_config:
                    self.trigger_config[k] = v
            try:
                self._compute_trigger_events()
            except Exception:
                pass

        # --- XY ---
        xy = payload.get("xy")
        if isinstance(xy, dict):
            for k, v in xy.items():
                if k in self.xy_config:
                    self.xy_config[k] = v

        # --- App theme: v1.29 ya NO se carga desde sesión (preferencia
        # personal en user_config). Sesiones viejas con "app_theme" se
        # ignoran silenciosamente — el tema actual se mantiene.

        # --- Grid theme: v1.29 ya NO se carga desde sesión (preferencia
        # personal en user_config). Sesiones viejas con "grid_theme" se
        # ignoran silenciosamente.

        # --- Annotations (ojo: pueden venir con coords en datos; recrear) ---
        # Cargar una sesión reemplaza completamente el estado: el historial
        # de undo de la sesión anterior deja de tener sentido.
        self._undo_stack = []
        self._redo_stack = []
        try:
            self.annotations = []
            for a in payload.get("annotations", []):
                if not isinstance(a, dict):
                    continue
                if a.get("type") == "stroke":
                    entry = {
                        "type":   "stroke",
                        "color":  a.get("color", "#ffff00"),
                        "width":  int(a.get("width", 2)),
                        "points": [tuple(p) for p in a.get("points", []) if len(p) == 2],
                    }
                    # ch_idx opcional: sesiones v1.23 y anteriores no lo
                    # traen → la anotación se trata como legacy en draw
                    # (y_screen directo, sin transformar).
                    if isinstance(a.get("ch_idx"), int):
                        entry["ch_idx"] = a["ch_idx"]
                    self.annotations.append(entry)
                elif a.get("type") == "text":
                    entry = {
                        "type":    "text",
                        "color":   a.get("color", "#ffff00"),
                        "size":    int(a.get("size", 10)),
                        "pos":     tuple(a.get("pos", (0, 0))),
                        "content": str(a.get("content", "")),
                    }
                    if isinstance(a.get("ch_idx"), int):
                        entry["ch_idx"] = a["ch_idx"]
                    self.annotations.append(entry)
        except Exception:
            self.annotations = []

        # --- Measurements ---
        try:
            self.active_measurements = []
            for m in payload.get("measurements", []):
                if not isinstance(m, dict):
                    continue
                ci = m.get("channel_idx")
                nm = m.get("name")
                if ci is None or nm is None:
                    continue
                if not (0 <= int(ci) < len(self.channels)):
                    continue
                entry = {"channel_idx": int(ci), "name": str(nm)}
                ci2 = m.get("channel_idx2")
                if ci2 is not None and 0 <= int(ci2) < len(self.channels):
                    entry["channel_idx2"] = int(ci2)
                self.active_measurements.append(entry)
            self._rebuild_measurements_panel()
        except Exception:
            pass

        # --- Refresco total ---
        try:
            self._rebuild_channels_panel()
        except Exception:
            pass
        self.update_plot()

        # --- Informe de avisos al usuario ---
        warnings_msgs = []
        if mismatched_count:
            warnings_msgs.append(
                "The CSV has a different number of channels than the session.\n"
                "Applied what could be matched."
            )
        if math_errors:
            warnings_msgs.append(
                "Some MATH channels could not be recreated:\n  - "
                + "\n  - ".join(math_errors)
            )
        if warnings_msgs:
            messagebox.showwarning("Session loaded with warnings",
                                    "\n\n".join(warnings_msgs))
        else:
            self._show_toast("Session loaded")
