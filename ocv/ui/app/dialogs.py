"""About, Settings and shortcut legend windows, plus shared dialog helpers (placement, chrome, toast)."""
import tkinter as tk

from tkinter import ttk

from ocv import __version__
from ocv.core.config import save_user_config
from ocv.resources import _resource_path
from ocv.ui import theme
from ocv.ui.theme import THEMES
from ocv.ui.widgets import Tooltip


class DialogsMixin:
    """Mixin de OscilloscopeApp: usa su estado vía self."""

    # =====================================================
    # About dialog
    # =====================================================
    # NOTA: para personalizar el contenido del Acerca de... (versión,
    # autor, descripción, enlaces...), edita los strings de este método.
    # Las claves de configuración están agrupadas en la parte superior
    # para facilitar el cambio sin tener que tocar la lógica de layout.

    def _open_about_window(self):
        """Ventana modal "About" con información de la aplicación."""

        # === EDITA AQUÍ los textos del Acerca de... ===
        APP_NAME      = "Oscilloscope CSV Viewer"
        APP_VERSION   = __version__   # única fuente: ocv/__init__.py
        APP_AUTHOR    = "Jotapeveloper"
        APP_DESCRIPTION = (
            "Offline visualizer for oscilloscope CSV captures. "
            "Supports Tektronix, Keysight, R&S, Rigol, Siglent, Hantek, "
            "LeCroy, PicoScope and OWON formats among others.\n\n"
            "Features include math channels, measurements, FFT analysis "
            "with harmonics & THD, eye diagrams, protocol decoding "
            "(UART / I²C / SPI / LIN / I²S / CAN / CAN FD / J1939 / SDIO / "
            "MIL-STD-1553 / ARINC 429), annotations and full session save/load."
        )
        APP_HOMEPAGE  = ""   # ej: "https://github.com/yourname/scope-viewer"
        APP_LICENSE   = "Personal use"
        # === fin de la zona editable ===

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("About")
        win.configure(bg=theme.COLOR_BG)
        # Geometry PRIMERO, chrome (transient) DESPUÉS (evita fantasma).
        self._make_resizable_dialog(win, 480, 380, min_w=440, min_h=320)
        self._apply_window_chrome(win)
        win.grab_set()
        self._bind_escape_to_close(win)

        # Bottom bar primero para que el botón Close quede siempre visible
        bottom = tk.Frame(win, bg=theme.COLOR_BG)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=12)

        # Logo / nombre prominente
        title_lbl = tk.Label(
            win, text=APP_NAME,
            bg=theme.COLOR_BG, fg=theme.COLOR_ACCENT,
            font=("Segoe UI", 16, "bold"),
        )
        title_lbl.pack(pady=(20, 4))

        version_lbl = tk.Label(
            win, text=f"Version {APP_VERSION}",
            bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
            font=("Segoe UI", 10),
        )
        version_lbl.pack(pady=(0, 14))

        # Descripción
        desc = tk.Label(
            win, text=APP_DESCRIPTION,
            bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
            font=("Segoe UI", 9),
            wraplength=420, justify="center",
        )
        desc.pack(padx=20, pady=4)

        # Info adicional
        info_frame = tk.Frame(win, bg=theme.COLOR_BG)
        info_frame.pack(pady=(14, 4))

        def info_row(label, value):
            row = tk.Frame(info_frame, bg=theme.COLOR_BG)
            row.pack(anchor="center", pady=1)
            tk.Label(row, text=label, bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                     font=("Segoe UI", 9)).pack(side=tk.LEFT)
            tk.Label(row, text=value, bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                     font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(4, 0))

        info_row("Author:",  APP_AUTHOR)
        info_row("License:", APP_LICENSE)
        if APP_HOMEPAGE:
            info_row("Web:",  APP_HOMEPAGE)

        # Close button
        ttk.Button(bottom, text="Close", command=win.destroy,
                   style="Accent.TButton"
                   ).pack(side=tk.RIGHT)

    def _open_grid_view_window(self):
        """Ventana para editar la apariencia: tema global de la app + colores
        finos del plot del osciloscopio."""
        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("Settings")
        win.configure(bg=theme.COLOR_BG)
        # Geometry PRIMERO, chrome (transient) DESPUÉS (evita fantasma).
        self._make_resizable_dialog(win, 440, 680, min_w=400, min_h=480)
        self._apply_window_chrome(win)
        win.grab_set()
        # Cleanup del callback de swatch refresh (v1.29) al cerrar con Esc.
        def _close_with_cleanup():
            self._appearance_swatch_refresh = None
            try:
                win.destroy()
            except Exception:
                pass
        self._bind_escape_to_close(win, on_close=_close_with_cleanup)

        # Layout: contenido scrollable arriba, bottom bar fija abajo.
        # El botón Close se packea en `bottom` y queda SIEMPRE visible
        # aunque la ventana sea más pequeña que el contenido.
        content, bottom = self._make_scrollable_dialog(win)

        # =====================
        # Sección 1: Tema global
        # =====================
        tk.Label(content, text="App theme",
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 10, "bold")
                 ).pack(anchor="w", padx=14, pady=(14, 4))

        tk.Label(content,
                 text="Changes colors of the whole UI. Plot colors below "
                      "are synchronized to the chosen theme.",
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 8), justify="left", wraplength=400
                 ).pack(anchor="w", padx=14, pady=(0, 6))

        theme_frame = tk.Frame(content, bg=theme.COLOR_BG)
        theme_frame.pack(fill=tk.X, padx=14, pady=(0, 4))

        # Botones-swatch para cada tema. Visualmente preview del color de fondo
        # con el texto del tema en su color de texto.
        theme_buttons = {}
        def tile_text(name):
            # El borde acento del tema activo apenas se distingue: marca ✓
            return f"✓ {name}" if name == theme.CURRENT_THEME_NAME else name

        def make_theme_btn(parent, name):
            t = THEMES[name]
            # highlightthickness FIJO para todos los estados.
            # Si lo cambiamos según activo/inactivo, el botón crece/encoge y
            # los demás botones del grid se reflowan — efecto "salto" feo al
            # cambiar tema. Mantenemos un borde constante y solo variamos su
            # color (ACCENT cuando activo, BORDER cuando inactivo).
            b = tk.Button(
                parent, text=tile_text(name),
                bg=t["BG_ALT"], fg=t["TEXT"],
                activebackground=t["BG_HOVER"],
                activeforeground=t["TEXT"],
                bd=2, relief="flat",
                font=("Segoe UI", 9, "bold"),
                padx=10, pady=8,
                highlightbackground=t["ACCENT"] if name == theme.CURRENT_THEME_NAME else t["BORDER"],
                highlightthickness=3,
                command=lambda n=name: choose_theme(n),
            )
            # Estos botones SON una muestra/preview del tema correspondiente:
            # los colores deben permanecer fijos (los del tema que representan)
            # incluso cuando el usuario cambia el tema activo. La marca
            # _theme_protected hace que _retheme_tk_widgets() y el barrido
            # ligero del fade los ignoren. El highlightbackground (que sí
            # cambia para indicar cuál está activo) se gestiona aparte vía
            # refresh_theme_buttons() y no pasa por el barrido.
            b._theme_protected = True
            return b

        def refresh_theme_buttons():
            # Solo cambia el COLOR del borde, NO su grosor (el grosor es
            # fijo en make_theme_btn). Cambiar grosor causa que el botón
            # crezca/encoja y reflowea el grid → efecto "salto".
            for n, btn in theme_buttons.items():
                t = THEMES[n]
                is_active = (n == theme.CURRENT_THEME_NAME)
                btn.config(
                    text=tile_text(n),
                    highlightbackground=t["ACCENT"] if is_active else t["BORDER"],
                )

        def choose_theme(name):
            self.apply_theme_runtime(name, sync_plot=True)
            # Refrescar la propia ventana
            try:
                win.configure(bg=theme.COLOR_BG)
                refresh_theme_buttons()
                refresh_swatches()
                # Las etiquetas de esta ventana ya las remapea
                # apply_theme_runtime (barrido desde root)
            except Exception:
                pass

        names = list(THEMES.keys())
        for i, n in enumerate(names):
            b = make_theme_btn(theme_frame, n)
            theme_buttons[n] = b
            b.grid(row=i // 2, column=i % 2, padx=4, pady=4, sticky="we")
            theme_frame.columnconfigure(i % 2, weight=1)

        ttk.Separator(content, orient="horizontal").pack(fill=tk.X, padx=14, pady=10)

        # =====================
        # Sección 1.5: Time axis
        # =====================
        # Control de cómo se muestra el eje temporal. Capturas de osciloscopios
        # físicos suelen traer pre-trigger negativo. Internamente la app siempre
        # normaliza a t=0 para simplificar; aquí elegimos qué mostrar al usuario.
        tk.Label(content, text="Time axis",
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 10, "bold")
                 ).pack(anchor="w", padx=14, pady=(4, 4))

        tk.Label(content,
                 text="How the time axis labels are displayed. Internally the "
                      "data is always referenced from 0; this only changes "
                      "what you see in the X-axis ticks, cursors and the "
                      "decoder table.",
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 8), justify="left", wraplength=400
                 ).pack(anchor="w", padx=14, pady=(0, 6))

        time_axis_frame = tk.Frame(content, bg=theme.COLOR_BG)
        time_axis_frame.pack(fill=tk.X, padx=14, pady=(0, 4))

        # Usamos un StringVar interno como "radio" para garantizar mutua
        # exclusión. La elección se sincroniza con self.time_show_absolute
        # (BooleanVar) que es lo que consulta _t_display_origin().
        ta_radio_var = tk.StringVar(
            value=("absolute" if self.time_show_absolute.get() else "relative"))

        def _apply_time_axis_choice(*_):
            is_abs = (ta_radio_var.get() == "absolute")
            self.time_show_absolute.set(is_abs)
            # Persistir en config.json para que se recuerde en la próxima
            # sesión sin necesidad de guardar/cargar manualmente.
            try:
                save_user_config({"time_show_absolute": bool(is_abs)})
            except Exception:
                pass
            # Repintar el plot para que el cambio se vea inmediatamente.
            try:
                self.update_plot()
            except Exception:
                pass
            # Refrescar disponibilidad del botón "Snap horizontal to t=0"
            # (solo tiene sentido en modo Absolute).
            self._update_snap_t0_state()

        rb_abs = ttk.Radiobutton(
            time_axis_frame,
            text="Absolute — real CSV times (pre-trigger is negative)",
            variable=ta_radio_var, value="absolute",
            command=_apply_time_axis_choice,
        )
        rb_abs.pack(anchor="w", pady=2)

        rb_rel = ttk.Radiobutton(
            time_axis_frame,
            text="Relative to start — first sample of the CSV is t = 0",
            variable=ta_radio_var, value="relative",
            command=_apply_time_axis_choice,
        )
        rb_rel.pack(anchor="w", pady=2)

        ttk.Separator(content, orient="horizontal").pack(fill=tk.X, padx=14, pady=10)

        # =====================
        # Sección 2: Plot grid
        # =====================
        tk.Label(content, text="Plot grid (advanced)",
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 10, "bold")
                 ).pack(anchor="w", padx=14, pady=(4, 4))

        tk.Label(content,
                 text="Fine-tune the oscilloscope plot independently of the app theme.",
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 8), justify="left", wraplength=400
                 ).pack(anchor="w", padx=14, pady=(0, 6))

        # Presets de plot (legacy, dejados por si alguien los prefiere).
        # Importante: todos los valores deben ser hex (#rrggbb), no nombres
        # CSS ("white", "black"), porque al persistir + recargar el config
        # se validan estrictamente como hex (ver __init__ de OscilloscopeApp).
        presets = {
            "Classic dark":  {"bg": "#000000", "grid_color": "#555555", "text_color": "#ffffff"},
            "Classic light": {"bg": "#ffffff", "grid_color": "#888888", "text_color": "#000000"},
            "Blueprint":     {"bg": "#0a2540", "grid_color": "#5fa8d3", "text_color": "#e8f4ff"},
            "Phosphor":      {"bg": "#0a1a0a", "grid_color": "#2d5a2d", "text_color": "#7fff7f"},
        }

        tk.Label(content, text="Plot presets:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(4, 2))

        preset_frame = tk.Frame(content, bg=theme.COLOR_BG)
        preset_frame.pack(fill=tk.X, padx=14, pady=(0, 10))

        def apply_preset(p):
            self.grid_theme.update(p)
            # Persistir el grid_theme completo en config.json para que el
            # preset elegido se conserve al reabrir la app. Misma lógica
            # que cuando el usuario edita un color individual en pick_color.
            try:
                save_user_config({"grid_theme": dict(self.grid_theme)})
            except Exception:
                pass
            refresh_swatches()
            self.update_plot()
            # v1.29: grid_theme es preferencia personal, NO marca dirty.

        for i, (name, p) in enumerate(presets.items()):
            b = tk.Button(
                preset_frame, text=name,
                bg=p["bg"], fg=p["text_color"],
                activebackground=p["bg"],
                activeforeground=p["text_color"],
                bd=1, relief="flat",
                font=("Segoe UI", 9, "bold"),
                padx=8, pady=4,
                highlightbackground=p["grid_color"],
                highlightthickness=1,
                command=lambda pp=p: apply_preset(pp),
            )
            # Igual que los botones del selector de tema en Appearance:
            # estos son muestras visuales del preset que representan, y sus
            # colores deben permanecer fijos al cambiar el tema activo.
            # _theme_protected hace que _retheme_tk_widgets() y el barrido
            # ligero del fade los ignoren.
            b._theme_protected = True
            b.grid(row=i // 2, column=i % 2, padx=3, pady=3, sticky="we")
            preset_frame.columnconfigure(i % 2, weight=1)

        # Editores individuales
        tk.Label(content, text="Custom:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(4, 2))

        custom_frame = tk.Frame(content, bg=theme.COLOR_BG)
        custom_frame.pack(fill=tk.X, padx=14, pady=(0, 10))

        swatches = {}

        def pick_color(key, label):
            from tkinter import colorchooser
            initial = self.grid_theme.get(key, "#000000")
            color = colorchooser.askcolor(initial, parent=win, title=f"Pick {label}")
            if color and color[1]:
                self.grid_theme[key] = color[1]
                # Persistir el grid_theme completo en config.json para que
                # se recuerden los colores personalizados al reabrir la app.
                try:
                    save_user_config({"grid_theme": dict(self.grid_theme)})
                except Exception:
                    pass
                refresh_swatches()
                self.update_plot()
                # v1.29: grid_theme es preferencia personal, NO marca dirty.

        def refresh_swatches():
            for key, lbl in swatches.items():
                try:
                    lbl.config(bg=self.grid_theme.get(key, "#000000"))
                except Exception:
                    # widgets ya destruidos (ventana cerrada)
                    pass

        # v1.29: registrar callback para que _apply_theme_immediate refresque
        # los swatches cuando el fade de tema termine. Sin esto, los swatches
        # quedaban con el color residual del tema previo.
        def _refresh_after_theme():
            refresh_swatches()
            # ✓ del tema activo: con fade el tema cambia al final
            try:
                refresh_theme_buttons()
            except tk.TclError:
                pass
        self._appearance_swatch_refresh = _refresh_after_theme
        def _on_close_appearance():
            self._appearance_swatch_refresh = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _on_close_appearance)

        for i, (key, label) in enumerate([
            ("bg",         "Background"),
            ("grid_color", "Grid lines"),
            ("text_color", "Text / axis"),
        ]):
            row = tk.Frame(custom_frame, bg=theme.COLOR_BG)
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=label, bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                     font=("Segoe UI", 9), width=14, anchor="w"
                     ).pack(side=tk.LEFT)
            sw = tk.Label(row, bg=self.grid_theme[key], width=4, height=1,
                          relief="flat", bd=1,
                          highlightbackground=theme.COLOR_BORDER, highlightthickness=1,
                          cursor="hand2")
            sw.pack(side=tk.LEFT, padx=(0, 8))
            sw.bind("<Button-1>", lambda e, k=key, l=label: pick_color(k, l))
            swatches[key] = sw
            ttk.Button(row, text="Pick...",
                       command=lambda k=key, l=label: pick_color(k, l)
                       ).pack(side=tk.LEFT)

        # =====================
        # Sección 3: Phosphor intensity grading (v1.27)
        # =====================
        ttk.Separator(content, orient="horizontal").pack(fill=tk.X, padx=14, pady=10)

        tk.Label(content, text="Phosphor (intensity grading)",
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 10, "bold")
                 ).pack(anchor="w", padx=14, pady=(4, 4))

        tk.Label(content,
                 text=("Emulates a CRT phosphor: trace alpha varies with "
                       "|dV/dt|. Flat segments stay bright, fast edges fade. "
                       "Disables the fast-redraw path while on, so it is "
                       "slightly heavier on the CPU."),
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                 font=("Segoe UI", 8), justify="left", wraplength=400
                 ).pack(anchor="w", padx=14, pady=(0, 6))

        phos_frame = tk.Frame(content, bg=theme.COLOR_BG)
        phos_frame.pack(fill=tk.X, padx=14, pady=(0, 4))

        # Toggle enable — la variable ya está enlazada con el trace que
        # persiste a config.json y fuerza redraw, así que solo packeamos
        # un Checkbutton normal.
        phos_check = ttk.Checkbutton(
            phos_frame, text="Enable phosphor mode",
            variable=self.phosphor_enabled,
        )
        phos_check.pack(anchor="w", pady=(0, 6))
        try:
            Tooltip(phos_check,
                    "Toggle the CRT-style intensity grading on the plot.\n"
                    "Persisted across sessions.")
        except Exception:
            pass

        # Slider de intensidad. tk.Scale opera sobre DoubleVar; el rango
        # interno es 0..100 (entero, más cómodo para el usuario), pero
        # mapeamos a 0..1 que es lo que consume el render.
        # Para no acoplar la lógica de mapping con el trace de persistencia
        # usamos una StringVar "display" intermedia + un wrapper.
        sl_row = tk.Frame(phos_frame, bg=theme.COLOR_BG)
        sl_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(sl_row, text="Intensity:",
                 bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9), width=10, anchor="w"
                 ).pack(side=tk.LEFT)

        pct_var = tk.IntVar(value=int(round(self.phosphor_intensity.get() * 100)))
        pct_label = tk.Label(sl_row, text=f"{pct_var.get():3d}%",
                             bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                             font=("Segoe UI", 9), width=5, anchor="e")

        def _on_pct_change(_v=None):
            pct = int(pct_var.get())
            pct_label.config(text=f"{pct:3d}%")
            self.phosphor_intensity.set(pct / 100.0)

        scale = tk.Scale(
            sl_row, from_=0, to=100, orient=tk.HORIZONTAL,
            variable=pct_var, command=_on_pct_change,
            showvalue=False, length=240,
            bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
            troughcolor=theme.COLOR_BG_ALT, activebackground=theme.COLOR_ACCENT,
            highlightthickness=0, bd=0,
        )
        scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 6))
        pct_label.pack(side=tk.LEFT)
        try:
            Tooltip(scale,
                    "0%  = no effect (uniform brightness).\n"
                    "100% = maximum dimming on fast slopes.")
        except Exception:
            pass

        # Close — packeado en la bottom bar fija, siempre visible
        ttk.Button(bottom, text="Close", command=win.destroy
                   ).pack(side=tk.RIGHT)
        # Commands legend (v1.30): atajos de teclado en dialog informativo
        ttk.Button(
            bottom, text="Commands legend",
            command=self._open_commands_legend_dialog,
        ).pack(side=tk.LEFT)

    def _open_commands_legend_dialog(self):
        """Diálogo informativo con la lista completa de atajos de teclado.
        Solo lectura: Close cierra."""
        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("Commands legend")
        win.configure(bg=theme.COLOR_BG)
        win.resizable(False, False)
        # Geometry PRIMERO, transient DESPUÉS (anti-fantasma).
        self._center_dialog(win, 560, 680)
        self._apply_window_chrome(win)
        win.grab_set()
        self._bind_escape_to_close(win)

        # Header
        hdr = tk.Frame(win, bg=theme.COLOR_BG_ALT, height=40)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(
            hdr, text="KEYBOARD SHORTCUTS",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_ACCENT,
            font=("Segoe UI", 11, "bold"),
        ).pack(side=tk.LEFT, padx=14, pady=10)

        # Lista de (sección, [(keys, descripción), ...])
        SECTIONS = [
            ("Channel", [
                ("1 – 9",         "Set active channel (1 → CH1, …)"),
                ("Ctrl+1 – Ctrl+9", "Toggle channel visibility"),
                ("U",             "Open unit picker for the active channel"),
                ("G",             "Snap channel ground to the nearest grid line"),
                ("0",             "Center the active channel on the origin"),
            ]),
            ("View", [
                ("←  / →",            "Pan horizontally (0.1 div per press)"),
                ("↑  / ↓",            "Pan the active channel vertically (0.1 div)"),
                ("Shift+↑/↓",         "Step V/div of the active channel"),
                ("Ctrl+Shift+↑/↓",    "Step T/div globally"),
            ]),
            ("Cursors", [
                ("C",                 "Toggle cursors on/off"),
                ("Ctrl+←/→",          "Cursor X1 (vertical left, time)"),
                ("Ctrl+↑/↓",          "Cursor Y1 (horizontal lower, voltage)"),
                ("Ctrl+Alt+←/→",      "Cursor X2 (vertical right)"),
                ("Ctrl+Alt+↑/↓",      "Cursor Y2 (horizontal upper)"),
            ]),
            ("Session", [
                ("Ctrl+S",        "Save session"),
                ("Ctrl+Shift+S",  "Save session as…"),
                ("Ctrl+O",        "Open session"),
                ("Ctrl+Z / Y",    "Undo / redo annotation"),
            ]),
            ("Misc", [
                ("Esc",           "Close the active dialog"),
                ("Right-click plot", "Context menu (copy image, etc.)"),
            ]),
        ]

        body = tk.Frame(win, bg=theme.COLOR_BG)
        body.pack(fill=tk.BOTH, expand=True, padx=20, pady=(14, 6))

        for section, items in SECTIONS:
            tk.Label(
                body, text=section.upper(),
                bg=theme.COLOR_BG, fg=theme.COLOR_ACCENT,
                font=("Segoe UI", 9, "bold"),
            ).pack(anchor="w", pady=(8, 2))
            for keys, desc in items:
                row = tk.Frame(body, bg=theme.COLOR_BG)
                row.pack(fill=tk.X, pady=1)
                tk.Label(
                    row, text=keys,
                    bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                    font=("Consolas", 10, "bold"),
                    width=18, anchor="w",
                ).pack(side=tk.LEFT)
                tk.Label(
                    row, text=desc,
                    bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                    font=("Segoe UI", 9),
                    anchor="w",
                ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Nota inferior
        tk.Label(
            body,
            text=("Shortcuts are ignored while typing in any text field "
                  "(V/div, names, MATH, etc.)."),
            bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
            font=("Segoe UI", 8, "italic"),
            wraplength=500, justify="left",
        ).pack(anchor="w", pady=(14, 0))

        # Close
        bottom = tk.Frame(win, bg=theme.COLOR_BG)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=12)
        ttk.Button(bottom, text="Close", style="Accent.TButton",
                   command=win.destroy).pack(side=tk.RIGHT)

    def _make_resizable_dialog(self, win, preferred_w, preferred_h, min_w=None, min_h=None):
        """Hace que una ventana se comporte bien en cualquier monitor:
        - resizable
        - minsize razonable
        - tamaño preferido pero nunca más grande que ~90% de la pantalla
          primaria (como heurística de "no es absurdamente grande")
        - centrada sobre la root usando _center_dialog para que en multi-
          monitor el diálogo aparezca en el monitor donde está la app.

        Nota multimonitor: winfo_screenwidth/height devuelve SIEMPRE la
        pantalla primaria, por eso solo lo usamos como techo del tamaño
        y NO para clampear coordenadas — el clamp con la pantalla primaria
        empujaba los diálogos de vuelta al primario cuando la app estaba
        en un monitor secundario.
        """
        try:
            self.root.update_idletasks()
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            w = min(preferred_w, int(sw * 0.9))
            h = min(preferred_h, int(sh * 0.9))
            win.minsize(min_w or min(preferred_w, 320),
                        min_h or min(preferred_h, 220))
            win.resizable(True, True)
            # Delegamos el posicionamiento al helper que sí maneja bien
            # multi-monitor (toma coords absolutas de la root).
            self._center_dialog(win, w, h)
        except Exception:
            pass

    def _make_scrollable_dialog(self, win):
        """Convierte una ventana Toplevel en un layout de dos zonas:

          ┌──────────────────────────────┐
          │  Scrollable content area     │
          │  (auto-scroll si no cabe)    │
          │                              │
          ├──────────────────────────────┤
          │  Fixed bottom bar            │  ← siempre visible
          └──────────────────────────────┘

        Devuelve `(content, bottom)`. El llamante debe:
          - packear sus widgets de contenido en `content` (NO en win).
          - packear sus botones de acción (Close, OK, ...) en `bottom`.

        Detalles:
        - `content` es un Frame dentro de un Canvas con Scrollbar. Si el
          contenido cabe, la scrollbar se oculta visualmente (sigue ocupando
          un ancho mínimo, pero sin barra).
        - Soporta scroll con rueda del ratón en todo el área de `content`.
        - Es theme-aware: usa los colores actuales.

        Patrón para usarlo:
            content, bottom = self._make_scrollable_dialog(win)
            tk.Label(content, text="...").pack(...)
            ttk.Button(bottom, text="Close", command=win.destroy).pack(...)
        """
        # IMPORTANTE: packear la bottom_bar PRIMERO con side=tk.BOTTOM.
        # Si la packeáramos al final, Tk la pondría arriba (orden de
        # llamada manda, no el atributo side, cuando hay TOP+BOTTOM).
        # v1.30: separar la línea divisoria de los botones (pady arriba).
        bottom = tk.Frame(win, bg=theme.COLOR_BG)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(12, 10))

        # Separador visual entre contenido y bottom bar
        sep = ttk.Separator(win, orient="horizontal")
        sep.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(4, 0))

        # Canvas scroll
        canvas = tk.Canvas(win, bg=theme.COLOR_BG,
                           highlightthickness=0, bd=0)
        vbar = ttk.Scrollbar(win, orient="vertical",
                             command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Frame interior donde irá el contenido real
        content = tk.Frame(canvas, bg=theme.COLOR_BG)
        content_window = canvas.create_window((0, 0), window=content,
                                              anchor="nw")

        def _on_content_configure(_e):
            # Cuando cambia el tamaño del contenido, actualizar la región
            # scrollable del canvas para que la scrollbar lo refleje.
            canvas.configure(scrollregion=canvas.bbox("all"))
        content.bind("<Configure>", _on_content_configure)

        def _on_canvas_configure(e):
            # Estirar el contenido al ancho del canvas (que va creciendo si
            # el usuario agranda la ventana). Sin esto los widgets quedan a la
            # izquierda con espacio sobrante a la derecha.
            canvas.itemconfigure(content_window, width=e.width)
        canvas.bind("<Configure>", _on_canvas_configure)

        # Scroll con rueda del ratón. Se enlaza sobre el canvas Y sobre los
        # hijos del content para que funcione independientemente de dónde
        # esté el cursor dentro de la ventana.
        def _on_mousewheel(event):
            # event.delta: Windows/Mac = ±120 múltiplos; Linux botones 4/5
            if event.num == 5 or event.delta < 0:
                canvas.yview_scroll(1, "units")
            elif event.num == 4 or event.delta > 0:
                canvas.yview_scroll(-1, "units")
        # Bind global a la ventana mientras esté activa
        win.bind_all("<MouseWheel>", _on_mousewheel, add="+")
        win.bind_all("<Button-4>",   _on_mousewheel, add="+")  # Linux
        win.bind_all("<Button-5>",   _on_mousewheel, add="+")  # Linux

        # Limpiar binds al cerrar (si no, otros Toplevels herederían el scroll)
        def _on_close(*_):
            try:
                win.unbind_all("<MouseWheel>")
                win.unbind_all("<Button-4>")
                win.unbind_all("<Button-5>")
            except Exception:
                pass
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _on_close)

        return content, bottom

    def _add_hover_shadow(self, widget, normal_border=None, hover_border=None,
                          normal_bg=None, hover_bg=None):
        """Añade efecto sombra/glow al pasar el ratón. Los colores son opcionales;
        si no se pasan, usa los defaults del tema ACTUAL.

        IMPORTANTE: los handlers leen las constantes COLOR_* en CADA evento,
        no en el momento de crear el widget. Si los capturáramos en el closure
        en el momento de construcción, al cambiar de tema en runtime el
        botón volvería al color del tema VIEJO en cada <Leave>, quedando un
        "hover persistente" inconsistente con el resto de la UI.

        Los argumentos opcionales (cuando alguien quiere un color fijo,
        ej. hover_border=COLOR_DANGER para el botón Clear de anotaciones)
        SÍ se capturan en el closure: son colores semánticos específicos
        que no dependen del tema.
        """

        def on_enter(_e):
            try:
                hb = hover_border or theme.COLOR_ACCENT
                hbg = hover_bg or theme.COLOR_BG_HOVER
                widget.config(highlightbackground=hb, bg=hbg)
            except tk.TclError:
                pass

        def on_leave(_e):
            try:
                nb = normal_border or theme.COLOR_BORDER
                nbg = normal_bg or theme.COLOR_BG_ALT
                widget.config(highlightbackground=nb, bg=nbg)
            except tk.TclError:
                pass

        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)

    def _bind_escape_to_close(self, win, on_close=None):
        """Hace que ESC cierre la ventana. Si se pasa `on_close`, se llama
        en lugar de win.destroy() (útil cuando hay limpieza pendiente como
        liberar el singleton). Bind solo al Toplevel en cuestión — no
        contamina otros widgets."""
        def _handler(_e=None):
            if on_close is not None:
                try:
                    on_close()
                    return
                except Exception:
                    pass
            try:
                win.destroy()
            except Exception:
                pass
        try:
            win.bind("<Escape>", _handler)
        except Exception:
            pass

    def _apply_window_chrome(self, win):
        """Aplica a una ventana Toplevel el mismo icono que la ventana raíz
        y la marca como transitoria de la principal.

        Efectos:
          - Icono de la barra de título igual al de la app (no la pluma de Tk).
          - En Windows, la ventana NO aparece como entrada separada en la
            taskbar — queda agrupada con la principal (mismo comportamiento
            que diálogos modales). Esto se consigue con transient(root):
            Tk lo traduce a WS_EX_TOOLWINDOW en Windows.
          - Mitiga el "flash" de ventana pequeña antes de que la UI termine
            de construirse: la ventana se oculta (withdraw) al entrar y
            se re-muestra (deiconify) en after_idle, cuando ya hay
            geometry/pack aplicados. El withdraw también permite al WM
            recolocar la ventana en su geometría final al deiconify.

        Todas las ventanas secundarias (FFT, Eye, Decoder, Meas, Trigger,
        XY, Annotations, About, etc.) deberían llamar a este helper justo
        después de crear el Toplevel.

        Tolerante a fallos: si el icono no se puede aplicar (por ejemplo,
        falta el .ico en runtime), continúa sin error.
        """
        # PASO 1: ocultar la ventana antes de tocarla. Si el caller ya hizo
        # withdraw inline tras crear el Toplevel, este es redundante (no daña).
        try:
            win.withdraw()
        except Exception:
            pass

        # Aplicar icono. Usa _resource_path para localizar el asset tanto
        # en modo desarrollo como empaquetado con PyInstaller.
        try:
            win.iconbitmap(_resource_path("assets/icon.ico"))
        except Exception:
            pass
        try:
            win.transient(self.root)
        except Exception:
            pass

        # PASO 2: re-mostrar después de que el event loop haya procesado
        # geometry/pack. update_idletasks fuerza el cómputo del layout
        # antes del deiconify para que la ventana nazca ya con su tamaño
        # y posición finales — el WM la posiciona al deiconify.
        def _show_when_ready():
            try:
                win.update_idletasks()
                win.deiconify()
            except Exception:
                pass
        try:
            win.after_idle(_show_when_ready)
        except Exception:
            try:
                win.deiconify()
            except Exception:
                pass

    def _center_dialog(self, win, w, h):
        """Posiciona y dimensiona el Toplevel `win` (w×h) centrado sobre
        la ventana principal. Si la principal aún no tiene dimensiones
        válidas (arranque, withdrawn), centra en pantalla primaria.

        Llamar a este método EN LUGAR de `win.geometry("WxH")` cuando
        quieras que el diálogo aparezca en una posición predecible —
        importante para diálogos que se abren tras una acción del usuario
        (selección de archivo, confirmaciones), porque sin esto Windows
        decide y suele dejar la ventana en algún lugar inesperado.

        El cálculo usa winfo_rootx/y de la principal (coordenadas
        absolutas de pantalla, no del WM), así funciona aunque la
        principal esté en un monitor secundario.

        Anti-flash: oculta la ventana (withdraw) al entrar y la re-
        muestra (deiconify) en after_idle, cuando la geometría ya está
        aplicada. Sin esto, Windows alcanzaba a pintar el Toplevel en
        posición/tamaño default antes de que geometry surtiera efecto."""
        try:
            win.withdraw()
        except Exception:
            pass
        try:
            self.root.update_idletasks()
            rw = self.root.winfo_width()
            rh = self.root.winfo_height()
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
        except Exception:
            rw = rh = 0
            rx = ry = 0

        if rw <= 1 or rh <= 1:
            # Fallback: pantalla primaria
            try:
                scr_w = win.winfo_screenwidth()
                scr_h = win.winfo_screenheight()
            except Exception:
                scr_w, scr_h = 1920, 1080
            x = (scr_w - w) // 2
            y = (scr_h - h) // 2
        else:
            x = rx + (rw - w) // 2
            y = ry + (rh - h) // 2

        # Clampear el tamaño al área disponible de la pantalla primaria.
        # Sin esto, una ventana pedida más alta que la pantalla (p. ej.
        # Decoder 960 px en un portátil 1366×768) sale con su parte
        # inferior fuera del workspace y los botones del fondo no se ven.
        # 0.92 deja margen para taskbar + chrome del WM.
        try:
            scr_w_max = int(win.winfo_screenwidth() * 0.96)
            scr_h_max = int(win.winfo_screenheight() * 0.92)
            if w > scr_w_max:
                w = scr_w_max
            if h > scr_h_max:
                h = scr_h_max
        except Exception:
            pass

        # Clampear a coordenadas positivas (un valor negativo se traduce
        # a una posición relativa al borde derecho/inferior en Tk, lo cual
        # no es lo que queremos aquí).
        x = max(0, x)
        y = max(0, y)
        # Recalcular x/y por si el tamaño se clampeó (mantener centrado)
        if rw > 1 and rh > 1:
            x = max(0, rx + (rw - w) // 2)
            y = max(0, ry + (rh - h) // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")

        # Re-mostrar después de que el layout esté procesado. Si el caller
        # llama a _apply_window_chrome después, su after_idle deiconify
        # también se ejecutará (idempotente).
        def _show_when_ready():
            try:
                win.update_idletasks()
                win.deiconify()
            except Exception:
                pass
        try:
            win.after_idle(_show_when_ready)
        except Exception:
            try:
                win.deiconify()
            except Exception:
                pass

    def _show_toast(self, text, duration_ms=2000):
        """Popup mínimo que aparece arriba-centro con fade-in/fade-out y
        desaparece.

        Duración: 2000 ms (similar a Android Toast.LENGTH_SHORT). De ese
        total: ~200 ms fade-in + ~1600 ms estable + ~200 ms fade-out.

        El fade se hace ajustando attributes("-alpha", ...) que en Windows,
        macOS y la mayoría de WMs de Linux funciona. Si el WM no lo soporta,
        el alpha se ignora silenciosamente y el toast aparece/desaparece
        sin transición (degradación elegante).
        """
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.configure(bg=theme.COLOR_ACCENT)
        toast.attributes("-topmost", True)

        inner = tk.Frame(toast, bg=theme.COLOR_BG_ALT, padx=16, pady=8)
        inner.pack(padx=2, pady=2)
        tk.Label(
            inner, text=text,
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack()

        # Centrar arriba
        self.root.update_idletasks()
        toast.update_idletasks()
        tw = toast.winfo_reqwidth()
        th = toast.winfo_reqheight()
        rx = self.root.winfo_rootx()
        ry = self.root.winfo_rooty()
        rw = self.root.winfo_width()
        x = rx + (rw - tw) // 2
        y = ry + 40
        toast.geometry(f"+{x}+{y}")

        # Iniciar invisible y hacer fade-in.
        try:
            toast.attributes("-alpha", 0.0)
        except Exception:
            # Si el WM no soporta alpha, salimos del modo fade y mostramos
            # con el comportamiento clásico (aparición instantánea).
            toast.after(duration_ms, toast.destroy)
            return

        # Parámetros de animación. Total ≈ duration_ms.
        # fade_in_ms + steady_ms + fade_out_ms = duration_ms
        fade_in_ms  = 200
        fade_out_ms = 200
        steady_ms   = max(0, duration_ms - fade_in_ms - fade_out_ms)
        steps       = 12   # nº de pasos por fase de fade (suave a 60 FPS)
        step_in_ms  = max(1, fade_in_ms // steps)
        step_out_ms = max(1, fade_out_ms // steps)

        def _fade_in(i=0):
            if not toast.winfo_exists():
                return
            alpha = i / steps
            try:
                toast.attributes("-alpha", alpha)
            except Exception:
                pass
            if i < steps:
                toast.after(step_in_ms, lambda: _fade_in(i + 1))
            else:
                # Mantener al 100% durante steady_ms, luego fade-out
                toast.after(steady_ms, _fade_out)

        def _fade_out(i=0):
            if not toast.winfo_exists():
                return
            alpha = 1.0 - (i / steps)
            try:
                toast.attributes("-alpha", alpha)
            except Exception:
                pass
            if i < steps:
                toast.after(step_out_ms, lambda: _fade_out(i + 1))
            else:
                try:
                    toast.destroy()
                except Exception:
                    pass

        _fade_in(0)
