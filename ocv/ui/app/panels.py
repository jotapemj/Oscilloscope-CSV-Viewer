"""Top bar, side controls and channel panel, with per-channel rename, colour and unit dialogs."""
import tkinter as tk

from tkinter import ttk

from ocv.core.config import save_user_config
from ocv.core.si_format import CHANNEL_UNITS
from ocv.ui import theme
from ocv.ui.widgets import Tooltip


class PanelsMixin:
    """Mixin de OscilloscopeApp: usa su estado vía self."""

    # -----------------------------
    def build_topbar(self, bar):
        """Barra horizontal superior: TOOLS (izq) | CURSORS (centro) | ☀ (der)."""
        # --- Helper local para crear botones con hover ---
        def mkbtn(parent, text, command, width=8, fg=None):
            b = tk.Button(
                parent, text=text,
                bg=theme.COLOR_BG_ALT, fg=fg or theme.COLOR_TEXT,
                activebackground=theme.COLOR_BG_HOVER,
                activeforeground=theme.COLOR_TEXT,
                bd=1, relief="flat",
                font=("Segoe UI", 9, "bold"),
                padx=10, pady=4,
                width=width,
                highlightbackground=theme.COLOR_BORDER,
                highlightthickness=1,
                command=command,
            )
            self._add_hover_shadow(b)
            return b

        # ---- TOOLS (grupo izquierdo) ----
        tools_frame = tk.Frame(bar, bg=theme.COLOR_BG_ALT)
        tools_frame.pack(side=tk.LEFT, padx=(8, 0), pady=8)

        tk.Label(tools_frame, text="TOOLS",
                 bg=theme.COLOR_BG_ALT, fg=theme.COLOR_ACCENT,
                 font=("Segoe UI", 10, "bold")
                 ).pack(side=tk.LEFT, padx=(0, 8))

        for txt, cmd, tip in [
            ("FFT",     self.open_fft_window,
             "Open FFT analyzer window\nFrequency-domain view of the active channel"),
            ("XY",      self.open_xy_window,
             "X-Y plot\nPlot one channel against another (Lissajous, I-V curves, etc.)"),
            ("Eye",     self.open_eye_window,
             "Eye diagram\nOverlay UI slices to evaluate jitter, eye height/width and ISI"),
            ("Decode",  self.open_decoder_window,
             "Protocol decoder (UART / I2C / SPI)\n"
             "Map channels to protocol roles and decode frames"),
            ("Math",    self.open_math_window,
             "Create math channels (M1, M2, ...)\nExpressions: CH1+CH2, sin(CH1), abs(CH1-CH2), etc."),
            ("Meas",    self.open_meas_window,
             "Add measurements (amplitude, frequency, rise time, ...)\nShown live in the right-hand panel"),
            ("Annot",   self.open_annotation_toolbar,
             "Annotations\nFreehand draw, text, erase. Ctrl+Z to undo, Ctrl+Y to redo"),
            ("Trigger", self.open_trigger_window,
             "Post-capture trigger\nEdge / Pulse-width detection on a chosen channel"),
        ]:
            btn = mkbtn(tools_frame, txt, cmd, width=7)
            btn.pack(side=tk.LEFT, padx=2)
            Tooltip(btn, tip)

        # --- Toggle Detail / Fast ---
        # Se "pinta" diferente según modo para dar feedback visual sin abrir ventana.
        self._render_mode_btn = tk.Button(
            tools_frame,
            text="Detail",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_ACCENT,
            activebackground=theme.COLOR_BG_HOVER,
            activeforeground=theme.COLOR_ACCENT,
            bd=1, relief="flat",
            font=("Segoe UI", 9, "bold"),
            padx=10, pady=4, width=7,
            highlightbackground=theme.COLOR_ACCENT,
            highlightthickness=1,
            command=self._toggle_render_mode,
        )
        self._add_hover_shadow(self._render_mode_btn)
        self._render_mode_btn.pack(side=tk.LEFT, padx=(8, 2))
        self._update_render_mode_btn_visual()
        Tooltip(self._render_mode_btn,
                lambda: (
                    "Render mode: DETAIL — draws every sample (best fidelity, slower)"
                    if self.render_mode.get() == "Detail" else
                    "Render mode: FAST — min/max decimation per pixel (best fluidity)\n"
                    "Switch to Detail for fine zoom inspection"
                ))

        # --- Botón Session con menú desplegable Save/Load ---
        session_btn = mkbtn(tools_frame, "Session",
                            self._open_session_menu, width=8)
        session_btn.pack(side=tk.LEFT, padx=(8, 2))
        self._session_btn = session_btn
        Tooltip(session_btn,
                "Save or load full session (channels, settings, annotations, ...)\n"
                "Ctrl+S to save, Ctrl+O to load")

        # Separador vertical
        tk.Frame(bar, bg=theme.COLOR_BORDER, width=1
                 ).pack(side=tk.LEFT, fill=tk.Y, padx=12, pady=8)

        # ---- CURSORS (grupo central) ----
        cursors_frame = tk.Frame(bar, bg=theme.COLOR_BG_ALT)
        cursors_frame.pack(side=tk.LEFT, pady=8)

        tk.Label(cursors_frame, text="CURSORS",
                 bg=theme.COLOR_BG_ALT, fg=theme.COLOR_ACCENT,
                 font=("Segoe UI", 10, "bold")
                 ).pack(side=tk.LEFT, padx=(0, 8))

        cursor_enable_chk = ttk.Checkbutton(
            cursors_frame, text="Enable",
            variable=self.cursors_enabled,
            command=self.on_cursors_toggle
        )
        cursor_enable_chk.pack(side=tk.LEFT)
        Tooltip(cursor_enable_chk,
                "Enable measurement cursors\n"
                "Click on the plot to position. Drag to move.")

        self.cursor_mode_combo = ttk.Combobox(
            cursors_frame,
            textvariable=self.cursor_mode,
            values=["Vertical", "Horizontal", "Horizontal + Vertical"],
            state="disabled",
            width=20
        )
        self.cursor_mode_combo.pack(side=tk.LEFT, padx=(8, 4))
        self.cursor_mode_combo.bind(
            "<<ComboboxSelected>>",
            lambda event: self.update_plot()
        )
        Tooltip(self.cursor_mode_combo,
                "Cursor type:\n"
                "  Vertical — time markers (Δt, frequency 1/Δt)\n"
                "  Horizontal — voltage markers (ΔV)\n"
                "  Both — full XY readout")

        center_cursors_btn = mkbtn(cursors_frame, "Center", self.center_cursors, width=7)
        center_cursors_btn.pack(side=tk.LEFT, padx=(4, 0))
        Tooltip(center_cursors_btn,
                "Re-center cursors at 25% and 75% of the visible window")

        # Checkbox: pintar la cruz de tensión en todos los canales o solo
        # en el activo. Persiste el valor al cambiar para que se respete
        # entre sesiones.
        def _on_cursor_show_all_toggle():
            try:
                save_user_config({
                    "cursor_show_all_channels":
                        bool(self.cursor_show_all.get())
                })
            except Exception:
                pass
            # Repintar para reflejar el cambio inmediatamente
            self.update_plot()

        cursor_all_chk = ttk.Checkbutton(
            cursors_frame, text="Show on all channels",
            variable=self.cursor_show_all,
            command=_on_cursor_show_all_toggle
        )
        cursor_all_chk.pack(side=tk.LEFT, padx=(8, 0))
        Tooltip(cursor_all_chk,
                "When on, the vertical cursor draws a voltage marker (X)\n"
                "on every visible channel at the same time instant.\n"
                "Useful for comparing voltages across channels at a glance.")

        # ---- ⚙ Settings (derecha) — v1.30 ----
        # Antes era "☀ Grid view". Ahora muestra una tuerca y abre la
        # ventana de Settings (tema, grid colors, phosphor, commands legend).
        gear_btn = tk.Frame(
            bar, bg=theme.COLOR_BG_ALT,
            highlightbackground=theme.COLOR_BORDER,
            highlightthickness=1,
            cursor="hand2",
        )
        gear_btn.pack(side=tk.RIGHT, padx=12, pady=8)

        # Canvas con tuerca dibujada
        gear_size = 22
        gear_canvas = tk.Canvas(
            gear_btn, width=gear_size, height=gear_size,
            bg=theme.COLOR_BG_ALT, highlightthickness=0, bd=0,
        )
        gear_canvas.pack(side=tk.LEFT, padx=(6, 2), pady=4)

        def draw_gear(color=None):
            """Dibuja una tuerca: anillo dentado + círculo central hueco.
            Polígono de 8 dientes alternando radio outer/inner."""
            if color is None:
                color = theme.COLOR_ACCENT
            gear_canvas.delete("all")
            cx = cy = gear_size / 2
            import math
            n_teeth = 8
            r_outer = gear_size * 0.46
            r_inner = gear_size * 0.34
            r_hole  = gear_size * 0.18
            # Cada diente = 2 vértices outer + 2 inner (separados angular).
            # Construimos 4 * n_teeth puntos para que el polígono dibuje
            # los dientes rectangulares en lugar de triangulares.
            pts = []
            step = math.pi / (2 * n_teeth)  # 1/4 de paso por diente
            for k in range(n_teeth):
                base = k * 2 * math.pi / n_teeth
                # 2 vértices en outer (lados del diente)
                a1 = base - step
                a2 = base + step
                pts.extend([cx + r_outer * math.cos(a1),
                            cy + r_outer * math.sin(a1)])
                pts.extend([cx + r_outer * math.cos(a2),
                            cy + r_outer * math.sin(a2)])
                # 2 vértices en inner (entre dientes)
                a3 = base + step
                a4 = base + 2 * math.pi / n_teeth - step
                pts.extend([cx + r_inner * math.cos(a3),
                            cy + r_inner * math.sin(a3)])
                pts.extend([cx + r_inner * math.cos(a4),
                            cy + r_inner * math.sin(a4)])
            gear_canvas.create_polygon(
                pts, outline=color, fill="", width=1.6, smooth=False,
            )
            # Círculo central (hueco)
            gear_canvas.create_oval(
                cx - r_hole, cy - r_hole, cx + r_hole, cy + r_hole,
                outline=color, width=1.6,
            )

        draw_gear()

        # Texto
        gear_label = tk.Label(
            gear_btn, text="Settings",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            font=("Segoe UI", 9, "bold"),
        )
        gear_label.pack(side=tk.LEFT, padx=(2, 8), pady=4)

        # v1.30: registrar redraw callback para que el icono se repinte
        # con COLOR_ACCENT actualizado cuando cambia el tema. El tk.Canvas
        # mantiene los items dibujados con su color absoluto — si no
        # repintamos aquí, la tuerca queda con el accent del tema viejo.
        def _gear_redraw_for_theme():
            try:
                if not gear_canvas.winfo_exists():
                    return
                cur_bg = theme.COLOR_BG_ALT
                gear_btn.config(bg=cur_bg,
                                highlightbackground=theme.COLOR_BORDER)
                gear_canvas.config(bg=cur_bg)
                gear_label.config(bg=cur_bg, fg=theme.COLOR_TEXT)
                draw_gear(theme.COLOR_ACCENT)
            except Exception:
                pass
        self._gear_redraw = _gear_redraw_for_theme

        # Hover y click: propagar a todos los hijos para que se comporte como botón
        def on_enter(_e=None):
            gear_btn.config(highlightbackground=theme.COLOR_ACCENT, bg=theme.COLOR_BG_HOVER)
            gear_canvas.config(bg=theme.COLOR_BG_HOVER)
            gear_label.config(bg=theme.COLOR_BG_HOVER)
            draw_gear(theme.COLOR_ACCENT)
        def on_leave(_e=None):
            gear_btn.config(highlightbackground=theme.COLOR_BORDER, bg=theme.COLOR_BG_ALT)
            gear_canvas.config(bg=theme.COLOR_BG_ALT)
            gear_label.config(bg=theme.COLOR_BG_ALT)
            draw_gear(theme.COLOR_ACCENT)
        def on_click(_e=None):
            self._open_grid_view_window()

        for w in (gear_btn, gear_canvas, gear_label):
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)
            w.bind("<Button-1>", on_click)

        # Tooltip explica ambos roles: tema global y tema fino del plot
        Tooltip(gear_btn,
                "Settings: app theme, plot colors, phosphor mode\n"
                "and keyboard shortcuts legend")

        # ---- About button (a la izquierda del settings, pero a la derecha del resto) ----
        # Con side=RIGHT el orden de pack importa: lo primero packeado queda
        # más a la derecha. Como gear_btn ya se packeó, el About queda a su
        # izquierda. Visualmente: [...cursors...]  [About]  [⚙ Settings]
        about_btn = tk.Frame(
            bar, bg=theme.COLOR_BG_ALT,
            highlightbackground=theme.COLOR_BORDER,
            highlightthickness=1,
            cursor="hand2",
        )
        about_btn.pack(side=tk.RIGHT, padx=(0, 4), pady=8)
        about_label = tk.Label(
            about_btn, text="ⓘ  About",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )
        about_label.pack(side=tk.LEFT, padx=10, pady=4)

        def about_enter(_e=None):
            about_btn.config(highlightbackground=theme.COLOR_ACCENT, bg=theme.COLOR_BG_HOVER)
            about_label.config(bg=theme.COLOR_BG_HOVER)
        def about_leave(_e=None):
            about_btn.config(highlightbackground=theme.COLOR_BORDER, bg=theme.COLOR_BG_ALT)
            about_label.config(bg=theme.COLOR_BG_ALT)
        def about_click(_e=None):
            self._open_about_window()
        for w in (about_btn, about_label):
            w.bind("<Enter>", about_enter)
            w.bind("<Leave>", about_leave)
            w.bind("<Button-1>", about_click)
        Tooltip(about_btn, "About this application")

    def build_controls(self, frame):
        # Configurar el frame padre como oscuro
        frame.configure(bg=theme.COLOR_BG, padx=10, pady=10)

        # ---- Load CSV destacado arriba ----
        open_btn = ttk.Button(frame, text="📂  Open CSV", command=self.load_csv,
                              style="Accent.TButton")
        open_btn.pack(fill=tk.X, pady=(0, 4))
        Tooltip(open_btn,
                "Load a CSV file exported from an oscilloscope\n"
                "Tek, Keysight, R&S, Rigol, Siglent, Hantek, LeCroy, PicoScope, OWON supported")

        auto_fit_btn = ttk.Button(frame, text="Auto fit", command=self.auto_fit)
        auto_fit_btn.pack(fill=tk.X, pady=(0, 10))
        Tooltip(auto_fit_btn,
                "Automatic fit: pick V/div and Time/div so every channel fits in view")

        # ---- Sección: Scale ----
        self._section_header(frame, "TIMEBASE & VERTICAL")

        ttk.Label(frame, text="V / div").pack(anchor="w", pady=(4, 0))

        # Fila con Entry + botones ⯅ ⯆
        vdiv_row = tk.Frame(frame, bg=theme.COLOR_BG)
        vdiv_row.pack(fill=tk.X, pady=(2, 4))

        self.v_div_entry = ttk.Entry(
            vdiv_row,
            width=10,
            font=("Consolas", 13),
            justify="center",
            textvariable=self.v_div_display,
        )
        self.v_div_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.v_div_display.trace_add("write", self._on_v_div_display_changed)
        self.v_div_entry.bind("<Return>",   lambda e: self._on_v_div_typed())
        self.v_div_entry.bind("<FocusOut>", lambda e: self._on_v_div_typed())
        Tooltip(self.v_div_entry,
                "Volts per division of the ACTIVE channel (the one tagged ▶ on the plot)\n"
                "Type a value (e.g. '500 mV', '2 V') and press Enter, or use the arrow buttons.\n"
                "Snaps to 1-2-5 series unless Fine is enabled.")

        v_step_up = tk.Button(
            vdiv_row, text="⯅",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            activebackground=theme.COLOR_BG_HOVER,
            bd=1, relief="flat",
            font=("Segoe UI", 8, "bold"),
            padx=4, pady=0,
            highlightbackground=theme.COLOR_BORDER, highlightthickness=1,
            command=lambda: self._step_v_div("up"),
        )
        v_step_up.pack(side=tk.LEFT, padx=(2, 0))
        self._add_hover_shadow(v_step_up)
        Tooltip(v_step_up, "Increase V/div (1 → 2 → 5 → 10 ...)")

        v_step_dn = tk.Button(
            vdiv_row, text="⯆",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            activebackground=theme.COLOR_BG_HOVER,
            bd=1, relief="flat",
            font=("Segoe UI", 8, "bold"),
            padx=4, pady=0,
            highlightbackground=theme.COLOR_BORDER, highlightthickness=1,
            command=lambda: self._step_v_div("down"),
        )
        v_step_dn.pack(side=tk.LEFT, padx=(0, 0))
        self._add_hover_shadow(v_step_dn)
        Tooltip(v_step_dn, "Decrease V/div (... 5 → 2 → 1)")

        # Checkbox Fine
        fine_chk = ttk.Checkbutton(
            frame, text="Fine (0.1 V steps)",
            variable=self.fine_mode,
            command=self._on_fine_mode_toggle,
        )
        fine_chk.pack(anchor="w", pady=(0, 6))
        Tooltip(fine_chk,
                "Fine mode: free V/div values instead of the 1-2-5 series\n"
                "Useful for matching the exact value of a reference signal")

        ttk.Label(frame, text="Time / div").pack(anchor="w", pady=(4, 0))

        # Fila con Entry central + botones ⯅ ⯆ manuales (evita los problemas
        # del Spinbox con valores no-canónicos/textuales).
        tdiv_row = tk.Frame(frame, bg=theme.COLOR_BG)
        tdiv_row.pack(fill=tk.X, pady=(2, 10))

        self.t_div_entry = ttk.Entry(
            tdiv_row,
            width=10,
            font=("Consolas", 13),
            justify="center",
            textvariable=self.t_div_display,
        )
        self.t_div_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.t_div_display.trace_add("write", self._on_t_div_display_changed)
        self.t_div_entry.bind("<Return>",   lambda e: self._on_t_div_typed())
        self.t_div_entry.bind("<FocusOut>", lambda e: self._on_t_div_typed())
        Tooltip(self.t_div_entry,
                "Time per division (horizontal scale)\n"
                "Type e.g. '100 us', '1 ms', '5 s' and press Enter, or use the arrows.\n"
                "Snaps to the 1-2-5 series.")

        # Botones de step
        step_up = tk.Button(
            tdiv_row, text="⯅",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            activebackground=theme.COLOR_BG_HOVER,
            bd=1, relief="flat",
            font=("Segoe UI", 8, "bold"),
            padx=4, pady=0,
            highlightbackground=theme.COLOR_BORDER, highlightthickness=1,
            command=lambda: self._step_t_div("up"),
        )
        step_up.pack(side=tk.LEFT, padx=(2, 0))
        self._add_hover_shadow(step_up)
        Tooltip(step_up, "Slower time base (zoom out)")

        step_dn = tk.Button(
            tdiv_row, text="⯆",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            activebackground=theme.COLOR_BG_HOVER,
            bd=1, relief="flat",
            font=("Segoe UI", 8, "bold"),
            padx=4, pady=0,
            highlightbackground=theme.COLOR_BORDER, highlightthickness=1,
            command=lambda: self._step_t_div("down"),
        )
        step_dn.pack(side=tk.LEFT, padx=(0, 0))
        self._add_hover_shadow(step_dn)
        Tooltip(step_dn, "Faster time base (zoom in)")

        # ---- Sección: Position ----
        self._section_header(frame, "POSITION")

        ttk.Label(frame, text="Horizontal").pack(anchor="w", pady=(4, 0))
        self.h_slider = ttk.Scale(
            frame,
            from_=0, to=0,
            orient=tk.HORIZONTAL,
            command=self.on_slider_move
        )
        self.h_slider.pack(fill=tk.X, pady=(2, 6))
        Tooltip(self.h_slider,
                "Horizontal position — pan through the captured trace\n"
                "Tip: you can also click-drag on the plot itself")

        # ---- Align grid to t=0 ----
        # Justo bajo el slider horizontal porque es una acción de
        # posicionamiento horizontal fino. Solo útil cuando el eje se
        # muestra en modo "Absolute" (toggle en Appearance): desplaza
        # t_offset para que la línea vertical t=0 absoluto del CSV
        # coincida con un tick del grid. Equivalente al snap-to-grid
        # vertical pero para el eje temporal.
        self._snap_t0_btn = ttk.Button(
            frame, text="Align grid to t=0",
            command=self._snap_horizontal_to_zero)
        self._snap_t0_btn.pack(fill=tk.X, pady=(0, 6))
        Tooltip(self._snap_t0_btn,
                "Shift the trace horizontally so the absolute t=0 of the "
                "CSV (the trigger instant in scope captures) lands exactly "
                "on a grid tick.\nOnly available when Time axis is in "
                "'Absolute' mode (Appearance).")
        # Estado inicial — se actualiza cuando cambia el toggle de
        # time_show_absolute o cuando se carga un CSV nuevo.
        self._update_snap_t0_state()

        ttk.Label(frame, text="Vertical").pack(anchor="w", pady=(4, 0))
        self.v_slider = ttk.Scale(
            frame,
            from_=-5.0, to=5.0,
            orient=tk.HORIZONTAL,
            command=self.on_v_slider_move
        )
        self.v_slider.set(0.0)
        self.v_slider.pack(fill=tk.X, pady=(2, 6))
        Tooltip(self.v_slider,
                "Vertical offset — shift the whole view up/down")

        center_trace_btn = ttk.Button(frame, text="Center trace", command=self.center_trace)
        center_trace_btn.pack(fill=tk.X, pady=(2, 10))
        Tooltip(center_trace_btn, "Reset horizontal and vertical position to zero")

        # ---- Sección: Channels ----
        self._section_header(frame, "CHANNELS")

        # Frame contenedor que se rellena dinámicamente al cargar CSV
        self.channels_frame = tk.Frame(frame, bg=theme.COLOR_BG)
        self.channels_frame.pack(fill=tk.X, pady=(4, 10))

        # Placeholder cuando no hay canales
        self.channels_placeholder = tk.Label(
            self.channels_frame, text="No CSV loaded",
            fg=theme.COLOR_TEXT_DIM, bg=theme.COLOR_BG,
            font=("Segoe UI", 9, "italic")
        )
        self.channels_placeholder.pack(anchor="w", padx=4)

    def _section_header(self, parent, text):
        """Crea una cabecera de sección con línea separadora."""
        sep = ttk.Separator(parent, orient="horizontal")
        sep.pack(fill=tk.X, pady=(6, 2))
        tk.Label(
            parent, text=text,
            font=("Segoe UI", 8, "bold"),
            fg=theme.COLOR_ACCENT, bg=theme.COLOR_BG
        ).pack(anchor="w", pady=(0, 2))

    def _rebuild_channels_panel(self):
        """Rellena el panel de canales con un checkbox por canal,
        coloreado con su color asignado, y un radio oculto para indicar
        el canal activo (el check del propio canal va "iluminado" si es
        el activo)."""
        # Limpiar contenido previo
        for w in self.channels_frame.winfo_children():
            w.destroy()

        if not self.channels:
            self.channels_placeholder = tk.Label(
                self.channels_frame, text="No CSV loaded",
                fg=theme.COLOR_TEXT_DIM, bg=theme.COLOR_BG,
                font=("Segoe UI", 9, "italic")
            )
            self.channels_placeholder.pack(anchor="w", padx=4)
            return

        for i, ch in enumerate(self.channels):
            row = tk.Frame(self.channels_frame, bg=theme.COLOR_BG)
            row.pack(fill=tk.X, pady=1)

            # Rectángulo de color (pequeño cuadrado)
            swatch = tk.Label(
                row, text="■",
                fg=ch["color"], bg=theme.COLOR_BG,
                font=("Segoe UI", 12, "bold")
            )
            swatch.pack(side=tk.LEFT, padx=(2, 4))

            # Checkbox visible/oculto
            cb = tk.Checkbutton(
                row,
                text=ch["name"],
                variable=ch["visible"],
                bg=theme.COLOR_BG,
                fg=ch["color"],
                activebackground=theme.COLOR_BG,
                activeforeground=ch["color"],
                selectcolor=theme.COLOR_BG_ALT,
                font=("Segoe UI", 10, "bold" if i == self.active_channel_idx else "normal"),
                bd=0, highlightthickness=0,
                anchor="w",
            )
            cb.pack(side=tk.LEFT, fill=tk.X, expand=True)

            # Click sobre el nombre → seleccionar como activo
            def make_selector(idx):
                def _sel(event):
                    self.set_active_channel(idx)
                    self._rebuild_channels_panel()
                return _sel

            def make_color_picker(idx):
                def _pick(event):
                    self._open_color_picker(idx)
                    return "break"
                return _pick

            def make_click_handler(idx, widget):
                """Distingue clic simple (toggle visible) de doble (rename).
                Al primer Button-1 programamos un 'after' con el delay de doble
                clic del sistema; si llega el doble antes, lo cancelamos."""
                state = {"after_id": None}

                def _on_single(event):
                    # Cancelar cualquier pendiente anterior
                    if state["after_id"] is not None:
                        widget.after_cancel(state["after_id"])

                    def _do_toggle():
                        state["after_id"] = None
                        widget.invoke()  # togglea el Checkbutton

                    # Delay estándar de doble clic en Tk
                    delay = widget.tk.call("::tk::DoubleClickTime") \
                        if False else 150
                    state["after_id"] = widget.after(delay, _do_toggle)
                    return "break"  # bloquea el toggle nativo

                def _on_double(event):
                    # Cancelar el single pendiente y abrir rename
                    if state["after_id"] is not None:
                        widget.after_cancel(state["after_id"])
                        state["after_id"] = None
                    self._open_rename_dialog(idx)
                    return "break"

                return _on_single, _on_double

            single_cb, double_cb = make_click_handler(i, cb)
            cb.bind("<Button-3>", make_selector(i))                 # click derecho: activa canal
            cb.bind("<Button-1>", single_cb)                        # clic simple: toggle (con delay)
            cb.bind("<Double-Button-1>", double_cb)                 # doble clic: renombrar
            swatch.bind("<Button-1>", make_color_picker(i))         # clic izq swatch: color

            # Orden visual deseado (izq → der): [unit] [⟲] [✕]
            # Con side=RIGHT, el PRIMER pack queda a la derecha.
            # → pack en orden inverso: ✕, ⟲, unit.

            # Botón X para borrar el canal (data o MATH).
            # Para canales MATH: si hay otros MATH que dependen de éste,
            # pregunta confirmación de borrado en cascada.
            # Para canales de datos: si hay MATH que los referencian,
            # mismo flujo de cascada (un MATH sin su fuente no tiene sentido).
            # Si se borra el último canal, la app vuelve al estado
            # "sin datos cargados" (igual que arranque limpio).
            def make_deleter(idx):
                def _del():
                    self._delete_channel(idx)
                return _del
            del_btn = tk.Label(
                row, text="✕",
                bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                font=("Segoe UI", 9, "bold"),
                cursor="hand2",
            )
            del_btn.pack(side=tk.RIGHT, padx=(2, 4))
            del_btn.bind("<Button-1>", lambda e, f=make_deleter(i): f())
            del_btn.bind("<Enter>", lambda e, w=del_btn: w.config(fg=theme.COLOR_DANGER))
            del_btn.bind("<Leave>", lambda e, w=del_btn: w.config(fg=theme.COLOR_TEXT_DIM))

            # Botón ⟲ GND: alinea el 0V del canal a la división vertical más cercana
            def make_gnd_snap(idx):
                def _snap():
                    self._snap_channel_gnd_to_grid(idx)
                return _snap
            gnd_btn = tk.Label(
                row, text="⟲",
                bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                font=("Segoe UI", 10, "bold"),
                cursor="hand2",
            )
            gnd_btn.pack(side=tk.RIGHT, padx=(2, 2))
            gnd_btn.bind("<Button-1>", lambda e, f=make_gnd_snap(i): f())
            gnd_btn.bind("<Enter>", lambda e, w=gnd_btn: w.config(fg=theme.COLOR_ACCENT))
            gnd_btn.bind("<Leave>", lambda e, w=gnd_btn: w.config(fg=theme.COLOR_TEXT_DIM))

            # Botón-casilla de unidad (v1.29): muestra la unidad del canal.
            # Clic → abre picker grid 5x5 para seleccionar otra.
            ch_unit = self._ch_unit(i)
            unit_btn = tk.Label(
                row, text=ch_unit,
                bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
                font=("Segoe UI", 8, "bold"),
                cursor="hand2",
                padx=5, pady=0,
                bd=1, relief="solid",
            )
            unit_btn.pack(side=tk.RIGHT, padx=(2, 2))
            unit_btn.bind(
                "<Button-1>",
                lambda e, idx=i: self._open_unit_picker_dialog(idx),
            )
            unit_btn.bind(
                "<Enter>",
                lambda e, w=unit_btn: w.config(
                    highlightbackground=theme.COLOR_ACCENT, fg=theme.COLOR_ACCENT),
            )
            unit_btn.bind(
                "<Leave>",
                lambda e, w=unit_btn: w.config(
                    highlightbackground=theme.COLOR_BORDER, fg=theme.COLOR_TEXT),
            )
            Tooltip(unit_btn, "Channel unit — click to change")

        # Leyenda minimalista — wrap para no forzar el ancho de la sidebar
        # cuando hay muchos canales o la sidebar es estrecha.
        tk.Label(
            self.channels_frame,
            text=("swatch = color · dbl-click = rename · "
                  "box = unit · ⟲ = snap · ✕ = delete"),
            fg=theme.COLOR_TEXT_DIM, bg=theme.COLOR_BG,
            font=("Segoe UI", 7, "italic"),
            wraplength=240, justify="left",
        ).pack(anchor="w", padx=4, pady=(4, 0))

    def _open_rename_dialog(self, ch_idx):
        """Ventana modal de tamaño fijo para renombrar un canal.
        El Entry viene precargado con el nombre actual y seleccionado."""
        if not (0 <= ch_idx < len(self.channels)):
            return

        old_name = self.channels[ch_idx]["name"]

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash: ocultar antes de cualquier configure
        win.title("Rename channel")
        win.configure(bg=theme.COLOR_BG)
        # Geometry PRIMERO, luego transient (evita fantasma).
        self._make_resizable_dialog(win, 320, 140)
        win.transient(self.root)
        win.grab_set()

        tk.Label(
            win,
            text=f"New name for {old_name}:",
            bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack(padx=12, pady=(14, 6), anchor="w")

        var = tk.StringVar(value=old_name)
        entry = ttk.Entry(win, textvariable=var, font=("Segoe UI", 10))
        entry.pack(fill=tk.X, padx=12)
        entry.focus_set()
        entry.select_range(0, tk.END)
        entry.icursor(tk.END)

        btns = tk.Frame(win, bg=theme.COLOR_BG)
        btns.pack(fill=tk.X, padx=12, pady=12)

        def apply_rename(*_):
            new_name = var.get().strip()
            if new_name and new_name != old_name:
                self.channels[ch_idx]["name"] = new_name
                self._rebuild_channels_panel()
                # Redibuja plot para actualizar leyenda/etiquetas
                try:
                    self.update_plot()
                except Exception:
                    pass
                self._mark_dirty()
            win.destroy()

        def cancel(*_):
            win.destroy()

        ttk.Button(btns, text="Cancel", command=cancel).pack(side=tk.RIGHT)
        ttk.Button(btns, text="OK", style="Accent.TButton",
                   command=apply_rename).pack(side=tk.RIGHT, padx=(0, 6))

        entry.bind("<Return>", apply_rename)
        entry.bind("<Escape>", cancel)

    def _open_color_picker(self, ch_idx):
        """Paleta tipo Paint para elegir color del canal ch_idx."""
        if not (0 <= ch_idx < len(self.channels)):
            return

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title(f"Color — {self.channels[ch_idx]['name']}")
        win.configure(bg=theme.COLOR_BG)
        win.resizable(False, False)
        # Geometry PRIMERO, luego transient (evita fantasma).
        self._center_dialog(win, 320, 360)
        win.transient(self.root)
        win.grab_set()

        # Header
        tk.Label(
            win,
            text=f"Pick color for {self.channels[ch_idx]['name']}",
            bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack(padx=12, pady=(12, 4), anchor="w")

        # Paleta: 8×10 = 80 colores bien distribuidos
        palette = [
            # Fila 1: básicos vivos
            "#ffeb3b", "#ffc107", "#ff9800", "#ff5722",
            "#f44336", "#e91e63", "#9c27b0", "#673ab7",
            "#3f51b5", "#2196f3",
            # Fila 2: cool
            "#03a9f4", "#00bcd4", "#009688", "#4caf50",
            "#8bc34a", "#cddc39", "#ffc107", "#ff9800",
            "#795548", "#607d8b",
            # Fila 3: neones / scope
            "#00e5ff", "#00ffff", "#00ff88", "#00ff00",
            "#aaff00", "#ffff00", "#ff8800", "#ff4400",
            "#ff0080", "#ff00ff",
            # Fila 4: pasteles
            "#ffcdd2", "#f8bbd0", "#e1bee7", "#d1c4e9",
            "#c5cae9", "#bbdefb", "#b3e5fc", "#b2ebf2",
            "#b2dfdb", "#c8e6c9",
            # Fila 5: oscuros / saturados
            "#b71c1c", "#880e4f", "#4a148c", "#311b92",
            "#1a237e", "#0d47a1", "#01579b", "#006064",
            "#004d40", "#1b5e20",
            # Fila 6: grises / blancos
            "#ffffff", "#e0e0e0", "#bdbdbd", "#9e9e9e",
            "#757575", "#616161", "#424242", "#212121",
            "#101010", "#000000",
        ]

        grid = tk.Frame(win, bg=theme.COLOR_BG)
        grid.pack(padx=12, pady=4)

        def make_picker(color):
            def _pick(event=None):
                self.channels[ch_idx]["color"] = color
                self._rebuild_channels_panel()
                self.update_plot()
                self._mark_dirty()
                win.destroy()
            return _pick

        COLS = 10
        for i, color in enumerate(palette):
            r, c = divmod(i, COLS)
            sq = tk.Frame(
                grid, bg=color,
                width=24, height=24,
                highlightbackground=theme.COLOR_BORDER,
                highlightcolor=theme.COLOR_ACCENT,
                highlightthickness=1,
                cursor="hand2",
            )
            sq.grid(row=r, column=c, padx=1, pady=1)
            sq.bind("<Button-1>", make_picker(color))
            # Hover feedback (bordecillo azul al pasar encima)
            sq.bind("<Enter>", lambda e, s=sq: s.config(highlightthickness=2, highlightbackground=theme.COLOR_ACCENT))
            sq.bind("<Leave>", lambda e, s=sq: s.config(highlightthickness=1, highlightbackground=theme.COLOR_BORDER))

        # Botón Cancel
        btns = tk.Frame(win, bg=theme.COLOR_BG)
        btns.pack(pady=(10, 12))
        ttk.Button(btns, text="Cancel", command=win.destroy).pack()

        # Centrar sobre la root (con fallback a pantalla)
        win.update_idletasks()
        rw_min = win.winfo_reqwidth()
        rh_min = win.winfo_reqheight()
        rx = self.root.winfo_rootx()
        ry = self.root.winfo_rooty()
        rw = self.root.winfo_width()
        rh = self.root.winfo_height()
        if rw < rw_min + 40 or rh < rh_min + 40:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            x = (sw - rw_min) // 2
            y = (sh - rh_min) // 2
        else:
            x = rx + (rw - rw_min) // 2
            y = ry + (rh - rh_min) // 2
        win.geometry(f"+{x}+{y}")

    def _open_unit_picker_dialog(self, ch_idx):
        """Diálogo modal para elegir la unidad del canal `ch_idx`.

        Grid 5x5 de botones-casilla con la unidad actual marcada. El
        cambio se aplica solo al confirmar con OK (no en vivo).
        """
        if not (0 <= ch_idx < len(self.channels)):
            return

        current = self._ch_unit(ch_idx)
        # Selección pendiente (no se aplica hasta OK).
        selected = {"unit": current}

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title(f"Unit — {self.channels[ch_idx].get('name', f'CH{ch_idx+1}')}")
        win.configure(bg=theme.COLOR_BG)
        win.resizable(False, False)
        # Geometry PRIMERO, transient DESPUÉS (patrón anti-fantasma).
        self._center_dialog(win, 380, 440)
        win.transient(self.root)
        win.grab_set()
        self._apply_window_chrome(win)
        self._bind_escape_to_close(win)

        # Header
        hdr = tk.Frame(win, bg=theme.COLOR_BG_ALT, height=40)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(
            hdr, text="CHANNEL UNIT",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_ACCENT,
            font=("Segoe UI", 11, "bold"),
        ).pack(side=tk.LEFT, padx=14, pady=10)

        # Grid de botones
        grid = tk.Frame(win, bg=theme.COLOR_BG)
        grid.pack(padx=14, pady=12)

        cells = {}  # unit -> Label widget

        def refresh_styles():
            for u, w in cells.items():
                if u == selected["unit"]:
                    w.config(bg=theme.COLOR_ACCENT, fg=theme.COLOR_TEXT_ON_ACCENT,
                             relief="sunken",
                             highlightbackground=theme.COLOR_ACCENT)
                else:
                    w.config(bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
                             relief="solid",
                             highlightbackground=theme.COLOR_BORDER)

        def make_pick(unit):
            def _pick(_e=None):
                selected["unit"] = unit
                refresh_styles()
            return _pick

        def make_hover(w, unit):
            def _enter(_e):
                if selected["unit"] != unit:
                    w.config(bg=theme.COLOR_BG_HOVER)
            def _leave(_e):
                if selected["unit"] != unit:
                    w.config(bg=theme.COLOR_BG_ALT)
            return _enter, _leave

        # Layout 5x5 — itera CHANNEL_UNITS y rellena huecos vacíos.
        ROWS, COLS = 5, 5
        for idx in range(ROWS * COLS):
            r = idx // COLS
            c = idx % COLS
            if idx < len(CHANNEL_UNITS):
                u = CHANNEL_UNITS[idx]
                cell = tk.Label(
                    grid, text=u,
                    bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
                    font=("Segoe UI", 11, "bold"),
                    cursor="hand2",
                    width=4, height=2,
                    bd=1, relief="solid",
                    highlightbackground=theme.COLOR_BORDER,
                    highlightthickness=1,
                )
                cell.grid(row=r, column=c, padx=3, pady=3)
                cell.bind("<Button-1>", make_pick(u))
                e, l = make_hover(cell, u)
                cell.bind("<Enter>", e)
                cell.bind("<Leave>", l)
                cells[u] = cell
            else:
                # Hueco vacío — placeholder invisible para mantener layout
                tk.Label(grid, text="", bg=theme.COLOR_BG,
                         width=4, height=2).grid(row=r, column=c, padx=3, pady=3)

        refresh_styles()

        # Bottom bar
        bottom = tk.Frame(win, bg=theme.COLOR_BG)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=12)

        def on_cancel():
            win.destroy()

        def on_ok():
            new_unit = selected["unit"]
            if new_unit != current:
                self.channels[ch_idx]["unit"] = new_unit
                self._mark_dirty()
                # Forzar full redraw para que ticks, V/div label y cursores
                # se rebuilden con la nueva unidad.
                self._last_structure_fingerprint = None
                self._update_v_div_display()
                self._rebuild_channels_panel()
                self.update_plot()
            win.destroy()

        ttk.Button(bottom, text="Cancel",
                   command=on_cancel).pack(side=tk.RIGHT)
        ttk.Button(bottom, text="OK", style="Accent.TButton",
                   command=on_ok).pack(side=tk.RIGHT, padx=(0, 6))

        # Enter = OK
        win.bind("<Return>", lambda _e: on_ok())

    def set_active_channel(self, idx):
        """Cambia el canal activo. Persiste lo anterior y sincroniza lo nuevo.
        Refresca el sidebar para que el negrita del canal activo siga al
        cambio (sin esto, los atajos 1-9 movían los controles pero el
        sidebar quedaba con el resaltado del canal anterior)."""
        if not (0 <= idx < len(self.channels)):
            return
        self._persist_active_channel_vars()
        self.active_channel_idx = idx
        self._sync_active_channel_vars()
        try:
            self._rebuild_channels_panel()
        except Exception:
            pass
        self.update_plot()
        self._mark_dirty()

    def _open_session_menu(self):
        """Muestra un menú contextual bajo el botón Session con Save/Load."""
        menu = tk.Menu(
            self.root, tearoff=0,
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            activebackground=theme.COLOR_ACCENT, activeforeground=theme.COLOR_TEXT_ON_ACCENT,
            bd=0,
        )
        menu.add_command(label="Save             Ctrl+S",
                         command=self.save_session)
        menu.add_command(label="Save as...       Ctrl+Shift+S",
                         command=self.save_session_as)
        menu.add_separator()
        menu.add_command(label="Load session...  Ctrl+O",
                         command=self.load_session)
        try:
            btn = self._session_btn
            x = btn.winfo_rootx()
            y = btn.winfo_rooty() + btn.winfo_height()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _toggle_render_mode(self):
        """Alterna entre 'Detail' (sin decimation) y 'Fast' (min/max decim)."""
        new_mode = "Fast" if self.render_mode.get() == "Detail" else "Detail"
        self.render_mode.set(new_mode)
        self._update_render_mode_btn_visual()
        self._show_toast(f"Render: {new_mode}")
        # Redibujar con el modo nuevo
        self.update_plot()

    def _update_render_mode_btn_visual(self):
        """Refresca el look del boton segun el modo actual.
        Detail -> borde acento azul, texto acento.
        Fast   -> fondo acento, texto negro (activo)."""
        if not hasattr(self, "_render_mode_btn") or self._render_mode_btn is None:
            return
        mode = self.render_mode.get()
        if mode == "Fast":
            self._render_mode_btn.config(
                text="Fast",
                bg=theme.COLOR_ACCENT, fg=theme.COLOR_TEXT_ON_ACCENT,
                activebackground=theme.COLOR_ACCENT_HOVER,
                activeforeground=theme.COLOR_TEXT_ON_ACCENT_HOVER,
                highlightbackground=theme.COLOR_ACCENT,
            )
        else:
            self._render_mode_btn.config(
                text="Detail",
                bg=theme.COLOR_BG_ALT, fg=theme.COLOR_ACCENT,
                activebackground=theme.COLOR_BG_HOVER,
                activeforeground=theme.COLOR_ACCENT,
                highlightbackground=theme.COLOR_ACCENT,
            )
