"""Main plot rendering: grid and labels, fast/full redraw, phosphor, markers and protocol overlay."""
import matplotlib.pyplot as plt
import numpy as np
import tkinter as tk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from ocv.core.dsp import minmax_decimate, repeated_time_index
from ocv.core.scope import H_DIVS, V_DIVS
from ocv.core.si_format import DEFAULT_UNIT, format_si
from ocv.ui import theme


class PlotMixin:
    """Mixin de OscilloscopeApp: usa su estado vía self."""

    def _schedule_update_plot(self):
        """Coalesce múltiples peticiones de redraw en una sola.

        Si ya hay un redraw planificado, no hacemos nada (el pendiente
        se encargará). Si no, programamos uno con Tk .after().
        Esto convierte una ráfaga de 100 eventos de mouse en 1-3 frames
        reales, aliviando matplotlib enormemente.
        """
        if self._redraw_pending:
            return
        self._redraw_pending = True
        self.root.after(self._redraw_interval_ms, self._do_scheduled_update)

    def _do_scheduled_update(self):
        self._redraw_pending = False
        self.update_plot()

    # -----------------------------
    def build_plot(self, frame):
        self.fig, self.ax = plt.subplots(figsize=(9, 5))
        # Márgenes propios: los de matplotlib dejaban ~12 % vacío arriba y
        # 10 % a la derecha. Izquierda y abajo se mantienen: ahí van las
        # etiquetas GND de canal, los ticks y el recuadro de cursores.
        self.fig.subplots_adjust(left=0.125, right=0.975, top=0.97, bottom=0.10)

        # time_div_label se crea dinámicamente en configure_grid()
        # para que pueda usar coordenadas de datos (1 división de ancho).
        self.time_div_label = None

        # =====================================================
        # Artists persistentes para render incremental.
        # =====================================================
        # En vez de hacer ax.clear() + ax.plot() en cada frame (caro), creamos
        # los Line2D una sola vez por canal y reutilizamos con set_data().
        # Esto es decisivo cuando se hace pan con muchos puntos.
        #
        # Diccionarios indexados por algún ID estable:
        #   _channel_lines:    ch_uid -> Line2D  (un Line2D por canal visible)
        #   _cursor_v_lines:   [axvline0, axvline1] persistentes (visible toggle)
        #   _cursor_h_lines:   [axhline0, axhline1]
        #   _hashmark_artists: lista de Line2D de las hash marks centrales
        #   _gnd_marker_artists: lista de artists del marcador GND por canal
        #   _cursor_v_marker:  marcador (x) + texto del cursor vertical sobre la traza
        #
        # Fingerprint del estado "estructural": si cambia, full rebuild;
        # si no, fast path.
        self._channel_lines = {}
        self._cursor_v_lines = [None, None]
        self._cursor_h_lines = [None, None]
        self._hashmark_artists = []
        self._gnd_marker_artists = []
        # Estos los gestiona _draw_vertical_cursor_marker:
        self._cursor_v_marker_artists = []
        # Annotations y trigger markers — limpiados en sus respectivos draws
        self._annotation_artists = []
        self._trigger_marker_artists = []
        # Protocol decoder overlay (cajas bajo la traza)
        self._protocol_artists = []
        self._protocol_frames = []
        self._protocol_channel_idx = None
        self._last_structure_fingerprint = None

        self.cursors_info_label = self.fig.text(
            0.02, 0.02,              # esquina inferior izquierda de la FIGURA
            "",
            ha="left",
            va="bottom",
            fontsize=10,
            color="white",
            bbox=dict(
                facecolor="black",
                edgecolor="#888888",
                alpha=0.9,
                boxstyle="round,pad=0.3"
            )
        )
        
        
        self.meas_info_label = self.fig.text(
            0.02, 0.06,
            "",
            ha="left",
            va="bottom",
            fontsize=10,
            color="white",
            bbox=dict(
                facecolor="black",
                edgecolor="#888888",
                alpha=0.9,
                boxstyle="round,pad=0.3"
            )
        )

        # Label discreto del nombre del fichero abierto (esquina inferior derecha)
        self.filename_label = self.fig.text(
            0.985, 0.012,
            "",
            ha="right",
            va="bottom",
            fontsize=8,
            color="#6a6a6a",      # gris oscuro discreto
            family="monospace",
        )

        self.canvas = FigureCanvasTkAgg(self.fig, master=frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.configure_grid()
        
        self.canvas.mpl_connect("button_press_event", self.on_mouse_press)
        self.canvas.mpl_connect("button_release_event", self.on_mouse_release)
        self.canvas.mpl_connect("motion_notify_event", self.on_mouse_move)

        # Rueda del ratón sobre el plot:
        #   - Shift + rueda → pan horizontal (avanzar/retroceder en el tiempo)
        #   - Ctrl  + rueda → zoom horizontal (cambia T/div, igual que la
        #     rueda sobre el minimap pero sin tener que mover el cursor).
        #   - Sin modificador → reservado para futuro. Por ahora no
        #     hace nada para no sorprender al usuario.
        # Usamos el tk widget directamente porque mpl_connect("scroll_event")
        # no expone los modificadores Shift/Ctrl de forma fiable en todos los SO.
        tk_canvas = self.canvas.get_tk_widget()
        tk_canvas.bind("<Shift-MouseWheel>", self._on_plot_shift_wheel)   # Win/Mac
        tk_canvas.bind("<Shift-Button-4>",   self._on_plot_shift_wheel)   # Linux up
        tk_canvas.bind("<Shift-Button-5>",   self._on_plot_shift_wheel)   # Linux down
        tk_canvas.bind("<Control-MouseWheel>", self._on_plot_ctrl_wheel)  # Win/Mac
        tk_canvas.bind("<Control-Button-4>",   self._on_plot_ctrl_wheel)  # Linux up
        tk_canvas.bind("<Control-Button-5>",   self._on_plot_ctrl_wheel)  # Linux down

    
        
    def update_time_div_label(self):
        """Dibuja el recuadro Time/div dentro del plot, del tamaño aprox.
        de una división de la grilla, con esquinas redondeadas.

        Se invoca tras configure_grid (que hace ax.clear) y tras update_plot.
        """
        text = format_si(self.t_div.get(), "s")

        t_div = self.t_div.get()
        v_div = self.v_div.get()
        t_range = H_DIVS * t_div

        # Centro de la "celda" superior derecha: centrada en la última división
        x_center = t_range - t_div / 2.0

        # Con el ylim fijo [-v_range/2, +v_range/2], el borde superior
        # está en v_div * V_DIVS / 2.0 (no depende del offset)
        y_top = v_div * V_DIVS / 2.0
        # Colocar el centro vertical del texto a media división del borde superior
        y_center = y_top - v_div * 0.5

        # Eliminar el label anterior si existía (ax.clear ya lo habrá borrado,
        # pero por si acaso queda una referencia vieja)
        if self.time_div_label is not None:
            try:
                self.time_div_label.remove()
            except Exception:
                pass

        self.time_div_label = self.ax.text(
            x_center, y_center, text,
            ha="center", va="center",
            fontsize=10,
            color="white",
            family="monospace",
            bbox=dict(
                facecolor="#2a2a2a",
                edgecolor="#888888",
                alpha=0.92,
                boxstyle="round,pad=0.4",
                linewidth=0.6,
            ),
            zorder=10,
        )

        
    # ------------MEAS METHODS-----------------   
    
        

    # -----------------------------
    def configure_grid(self):
        t_div = self.t_div.get()
        v_div = self.v_div.get()
        v_offset = self.v_offset.get()

        t_range = H_DIVS * t_div
        v_range = V_DIVS * v_div

        self.ax.clear()
        
        self.ax.set_facecolor(self.grid_theme["bg"])
        self.fig.patch.set_facecolor(self.grid_theme["bg"])

        self.ax.set_xlim(0, t_range)
        # ylim centrado en 0. El desplazamiento de cada canal se aplica al
        # dibujar (update_plot) restando el v_offset del canal a la señal.
        self.ax.set_ylim(-v_range / 2, v_range / 2)



        xticks = np.linspace(
            0,
            t_range,
            H_DIVS + 1,     # 12 divisiones → 13 líneas
            endpoint=True
        )
        self.ax.set_xticks(xticks)
        # Los xticks tienen coordenadas internas [0..t_range] (relativas al
        # viewport). El tiempo REAL que representa cada tick es:
        #   t_real = t_offset + x_tick + time_origin
        # Donde time_origin es el offset original del CSV (pre-trigger
        # negativo en capturas de scope con trigger en t=0).
        t_off = self.t_offset.get()
        t_o = self._t_display_origin()
        self.ax.set_xticklabels(
            [format_si(t_off + x + t_o, "s", sig_figs=3) for x in xticks])




        # Ticks verticales: posiciones de pantalla en divisiones,
        # pero etiquetados con los VALORES REALES del canal activo
        yticks_screen = np.linspace(
            -v_range / 2,
             v_range / 2,
            V_DIVS + 1,
            endpoint=True
        )
        self.ax.set_yticks(yticks_screen)

        # ¿Color del canal activo? Si hay canales, usamos su color
        y_tick_color = self.grid_theme["text_color"]
        # sig_figs=4 (no 3) para no perder el decimal en modo Fine con
        # v_div > 100 V: con sig_figs=3 el valor 100.1 se redondea a "100"
        # y todos los ticks aparecen como enteros aunque la escala sea fina.
        # 4 da "100.1" y, gracias a {:.4g}, no añade ceros decorativos en
        # valores enteros ("1 V", no "1.000 V").
        # Snap a 0 si el valor cae por debajo de v_div*1e-9: tras escalar
        # v_offset proporcionalmente al cambio de v_div (pivot por divisiones
        # del grid) los residuos de coma flotante hacen que la línea "0 V"
        # acabe siendo 1e-13 ó 1e-14, que el formateador SI muestra como
        # "100 fV" o similar. Ese rango está varios órdenes de magnitud por
        # debajo de cualquier medida real, así que es seguro tratarlo como 0.
        noise_thr = v_div * 1e-9
        def _clean(y):
            return 0.0 if abs(y) < noise_thr else y
        if self.channels:
            active = self.channels[self.active_channel_idx]
            y_tick_color = self._ch_color(active, text=True)
            # Convertir cada posición de pantalla a valor físico del canal activo:
            #   screen = (v_real - offset) / v_div_ch * v_div_active
            #   v_real = screen * v_div_ch / v_div_active + offset
            # Con el canal activo: v_div_ch == v_div_active, así que:
            #   v_real = screen + offset
            yticks_real = yticks_screen + active["v_offset"]
            y_unit = self._active_unit()
            self.ax.set_yticklabels([format_si(_clean(y), y_unit, sig_figs=4) for y in yticks_real])
        else:
            y_unit = self._active_unit()
            self.ax.set_yticklabels([format_si(_clean(y), y_unit, sig_figs=4) for y in yticks_screen])


        
        self.ax.grid(
            True,
            which="both",
            color=self.grid_theme["grid_color"],
            linewidth=0.8
        )
        
        
        # -----------------------------
        # Recuadro de la vista (spines)
        # -----------------------------
        for spine in self.ax.spines.values():
            spine.set_visible(True)
            spine.set_color(self.grid_theme["grid_color"])
            spine.set_linewidth(1.2)

        
        self.ax.tick_params(axis='x', colors=self.grid_theme["text_color"])
        self.ax.tick_params(axis='y', colors=y_tick_color)

        for label in self.ax.get_xticklabels():
            label.set_color(self.grid_theme["text_color"])
        for label in self.ax.get_yticklabels():
            label.set_color(y_tick_color)

        # -----------------------------
        # Mini-marcas ("hash marks") en los ejes centrales del grid.
        # Estilo osciloscopio real: cada división se subdivide en 5 partes
        # (cada 0.2 divisiones) con rayitas cortas sobre la línea central
        # horizontal (y=0) y la línea central vertical (x=t_range/2).
        # -----------------------------
        tick_color = self.grid_theme["text_color"]
        t_range_val = t_range
        v_range_val = v_range
        x_center = t_range_val / 2.0

        # Tamaño de las rayitas (más cortas: 5% de división)
        minor_x_len = v_div * 0.05   # rayitas verticales sobre la línea horizontal
        minor_y_len = t_div * 0.05   # rayitas horizontales sobre la línea vertical

        # --- Rayitas verticales sobre la línea horizontal central (y=0) ---
        # 5 subdivisiones por división: paso = t_div/5
        sub_x_step = t_div / 5.0
        x = 0.0
        while x <= t_range_val + sub_x_step * 0.5:
            # No dibujar encima de las divisiones principales (ya existen como grid)
            # Dividimos en 5: las posiciones intermedias son las que valen.
            frac = (x / t_div)
            if abs(frac - round(frac)) > 1e-6:
                self.ax.plot(
                    [x, x],
                    [-minor_x_len / 2, minor_x_len / 2],
                    color=tick_color, linewidth=0.6, alpha=0.4,
                    zorder=2,
                )
            x += sub_x_step

        # --- Rayitas horizontales sobre la línea vertical central ---
        sub_y_step = v_div / 5.0
        y = -v_range_val / 2.0
        while y <= v_range_val / 2.0 + sub_y_step * 0.5:
            frac = (y / v_div)
            if abs(frac - round(frac)) > 1e-6:
                self.ax.plot(
                    [x_center - minor_y_len / 2, x_center + minor_y_len / 2],
                    [y, y],
                    color=tick_color, linewidth=0.6, alpha=0.4,
                    zorder=2,
                )
            y += sub_y_step

        #self.ax.set_xlabel("Tiempo [s]")
        #self.ax.set_ylabel("Voltaje [V]")
        self.update_time_div_label()

    # -----------------------------
    def draw_ground_marker(self):
        """Dibuja un indicador del nivel 0 V (GND) en el lado izquierdo del plot."""
        v_range = self.v_div.get() * V_DIVS
        v_offset = self.v_offset.get()
        t_range = self.t_div.get() * H_DIVS

        y_gnd = 0.0  # la posición real del 0 V

        # Mostrar solo si está dentro del área visible
        y_min = -v_range / 2 - v_offset
        y_max =  v_range / 2 - v_offset

        if y_min <= y_gnd <= y_max:
            # Flecha / marcador a la izquierda (bastante separado del eje)
            self.ax.annotate(
                "▶ 0",
                xy=(0, y_gnd),
                xytext=(-t_range * 0.06, y_gnd),
                ha="right",
                va="center",
                color="yellow",
                fontsize=9,
                fontweight="bold",
                annotation_clip=False,
            )
            # Línea tenue horizontal marcando el 0
            self.ax.axhline(
                y_gnd,
                color="yellow",
                linewidth=0.5,
                linestyle=(0, (1, 4)),   # punteado sutil
                alpha=0.4
            )

    # -----------------------------
    # Curva gamma para acentuar las zonas medias de slope. <1 destaca más
    # tramos como "rápidos". 0.5 da un efecto visualmente notable sin que
    # los tramos planos pierdan brillo.
    _PHOSPHOR_GAMMA = 0.5

    # Percentil usado como "máximo" para normalizar. Usar el max real es
    # frágil: un único pico (glitch, transición ideal) deja al resto de
    # los segmentos con slope normalizada ≈ 0 → casi no se ve el efecto.
    # 95 absorbe outliers y reparte el rango dinámico entre los slopes
    # típicos.
    _PHOSPHOR_PERCENTILE = 95.0

    # Tope de segmentos por canal cuando phosphor está activo. Cada
    # segmento es un draw call en LineCollection; con datasets típicos
    # de osciloscopio (>50k puntos por canal) la app se vuelve inusable
    # sin esto. 1500 (ajustado tras pruebas reales con la app) da el
    # mejor compromiso fluidez/resolución del efecto. min-max-decimate
    # conserva picos (no promedia), así que los flancos siguen viéndose
    # pese al cap.
    _PHOSPHOR_MAX_SEGMENTS = 1500

    def _ch_color(self, ch, text=False, bg=None):
        """Color de un canal tal como se pinta: ajustado para leerse sobre
        el fondo del plot (o `bg`). En Light el amarillo de CH1 pasa a
        mostaza; en temas oscuros no cambia. El color guardado no se toca."""
        if bg is None:
            bg = self.grid_theme.get("bg", theme.COLOR_BG)
        return theme.readable_on(
            ch["color"], bg, theme.TEXT_MIN if text else theme.GRAPHIC_MIN)

    def _compute_phosphor_rgba(self, t, y, base_color, base_alpha, intensity):
        """Devuelve (segments, rgba) para construir/refrescar un
        LineCollection con alpha por segmento (CRT-style intensity grading).

        Modelo:
          - slope = |dy/dt| por segmento.
          - smax = percentil 95 de slope (robusto a outliers).
          - norm = (slope/smax)**gamma, clip [0, 1]   (gamma=0.5 acentúa
            zonas medias; sin gamma, un único flanco ahoga el resto).
          - alpha_min = base_alpha · (1 − intensity)
              · intensity = 0 → alpha_min = base_alpha (sin efecto)
              · intensity = 1 → alpha_min = 0
          - alpha_seg = lerp(alpha_min, base_alpha, 1 − norm)

        Degenerados (n<2, todo plano, dt=0) devuelven arrays vacíos /
        alpha uniforme — seguro de pasar a LineCollection sin efecto.
        """
        import numpy as _np

        t = _np.asarray(t, dtype=float)
        y = _np.asarray(y, dtype=float)
        n = len(t)
        if n < 2:
            return _np.empty((0, 2, 2), dtype=float), _np.empty((0, 4), dtype=float)

        pts = _np.column_stack([t, y])
        segments = _np.stack([pts[:-1], pts[1:]], axis=1)  # (n-1, 2, 2)

        dt = _np.diff(t)
        dy = _np.diff(y)
        idx = repeated_time_index(t)
        if idx is None:
            dt_safe = _np.where(dt == 0, 1e-30, dt)
            slope = _np.abs(dy / dt_safe)
        else:
            # Peak detect (mín/máx con el mismo instante): pendiente de la
            # media de cada instante, aplicada a sus segmentos. Con dt = 0
            # la mitad salían ~infinitos y el zigzag del ruido tapaba la
            # señal: sin graduación.
            tu, inv = idx
            yu = _np.bincount(inv, weights=y) / _np.bincount(inv)
            su = _np.abs(_np.gradient(yu, tu)) if len(tu) > 1 \
                else _np.zeros(len(tu))
            slope = su[inv[:-1]]

        if slope.size:
            # Percentil 95 como "max" robusto. Si todos son 0 da 0, manejado
            # abajo. nanpercentile tolera NaN puntuales.
            smax = float(_np.nanpercentile(slope, self._PHOSPHOR_PERCENTILE))
        else:
            smax = 0.0
        if smax <= 0.0 or not _np.isfinite(smax):
            norm = _np.zeros_like(slope)
        else:
            norm = _np.clip(slope / smax, 0.0, 1.0)
            # Curva gamma: rebaja la rampa para que zonas medias de slope
            # también se vean atenuadas, no solo los picos.
            norm = _np.power(norm, self._PHOSPHOR_GAMMA)

        intensity = max(0.0, min(1.0, float(intensity)))
        alpha_min = base_alpha * (1.0 - intensity)
        alpha_seg = alpha_min + (base_alpha - alpha_min) * (1.0 - norm)

        r, g, b = self._hex_to_rgb01(base_color)
        rgba = _np.empty((segments.shape[0], 4), dtype=float)
        rgba[:, 0] = r
        rgba[:, 1] = g
        rgba[:, 2] = b
        rgba[:, 3] = alpha_seg
        return segments, rgba

    def _build_phosphor_collection(
            self, t, y, base_color, base_alpha, linewidth, intensity):
        """Construye un LineCollection nuevo con alpha por segmento.
        Path lento (full redraw). Para refrescar uno ya existente usa
        directamente lc.set_segments(...) + lc.set_color(...) con el
        resultado de _compute_phosphor_rgba."""
        from matplotlib.collections import LineCollection
        segments, rgba = self._compute_phosphor_rgba(
            t, y, base_color, base_alpha, intensity)
        return LineCollection(
            segments, colors=rgba, linewidths=linewidth,
            capstyle="round", joinstyle="round", antialiased=True,
        )

    def _hex_to_rgb01(self, hex_color):
        """Convierte '#rrggbb' a (r, g, b) en [0, 1]. Tolerante a '#rgb'
        y a strings sin '#'. Si no parsea, devuelve blanco."""
        h = (hex_color or "").strip().lstrip("#")
        try:
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            if len(h) != 6:
                return (1.0, 1.0, 1.0)
            return (
                int(h[0:2], 16) / 255.0,
                int(h[2:4], 16) / 255.0,
                int(h[4:6], 16) / 255.0,
            )
        except Exception:
            return (1.0, 1.0, 1.0)

    def _structure_fingerprint(self):
        """Devuelve una tupla hashable que captura el estado 'estructural' del
        plot. Si cambia entre dos llamadas, hay que hacer un full rebuild
        (clear + recreate). Si no, basta con set_data() (fast path).

        Estructural = aquello que cambia los artists necesarios o sus propiedades
        fijas (color, linewidth, visibilidad). NO incluye t_offset, v_offset
        del scope, ni posiciones de cursores → esos son fast-path.
        """
        # Canales: UIDs visibles + sus colores + v_div/v_offset por canal +
        # render_mode. Si cambia algo de esto, hay que redibujar.
        chs = tuple(
            (ch.get("_uid", id(ch)),
             ch["color"],
             bool(ch["visible"].get()),
             float(ch["v_div"]),
             float(ch["v_offset"]),
             ch.get("unit", DEFAULT_UNIT))   # v1.29: unit afecta a ticks Y
            for ch in self.channels
        )
        return (
            chs,
            self.active_channel_idx,
            self.render_mode.get(),
            float(self.t_div.get()),     # t_div cambia los ticks y rango
            float(self.v_div.get()),     # v_div cambia el grid (eje Y screen)
            theme.CURRENT_THEME_NAME,
            id(self.time),                # cargar otro CSV cambia el array
            bool(self._cursors_vertical_active()),
            bool(self._cursors_horizontal_active()),
            # Colores personalizados del plot (Appearance → Advanced).
            # Si cambian, hay que repintar TODO (facecolor, spines, grid,
            # tick colors) y eso solo lo hace _full_redraw_plot. Sin esto,
            # cambiar el color en Advanced no se ve hasta el siguiente
            # cambio estructural (pan no basta — usa fast path).
            self.grid_theme.get("bg"),
            self.grid_theme.get("grid_color"),
            self.grid_theme.get("text_color"),
            self.grid_theme.get("time_label_bg"),
            # Phosphor cambia el tipo de artist por canal (Line2D vs
            # LineCollection). Toggle → full rebuild obligado.
            bool(self.phosphor_enabled.get()),
        )

    def update_plot(self):
        """Punto de entrada único para redibujar. Internamente decide entre:
          - Full rebuild (clear + recreate artists): cuando cambia algo
            estructural (canales, render_mode, tema, t_div, v_div...).
          - Fast update (solo set_data en líneas existentes): para pan, drag
            de offset, drag de cursores. ~5-10x más rápido con datasets grandes.
        """
        # Guard de init: durante la construcción de la UI, traces de tk.Vars
        # (V/div, sliders, etc.) pueden disparar update_plot antes de que
        # self.ax exista. El primer redraw real lo hará _build_ui al final.
        if not hasattr(self, "ax"):
            return
        # XY mode: branch al principio, ignora el resto del plot time-domain
        if self.xy_config["enabled"]:
            self._draw_xy()
            try:
                self._redraw_minimap()
            except Exception:
                pass
            return

        if self.time is None or not self.channels:
            # Nada que mostrar: hacer full rebuild para limpiar pantalla
            self._full_redraw_plot()
            return

        # Decidir camino: si la estructura no ha cambiado, fast path.
        # Phosphor mode (v1.27) sigue por fast path: _fast_update_plot
        # detecta el tipo del artist cacheado (LineCollection vs Line2D)
        # y refresca segments+colors en vez de set_data. Eso evita el
        # cost de ax.clear()+rebuild que es el cuello de botella.
        fp = self._structure_fingerprint()
        if fp == self._last_structure_fingerprint and self._channel_lines:
            self._fast_update_plot()
        else:
            self._full_redraw_plot()
            self._last_structure_fingerprint = fp

    def _fast_update_plot(self):
        """Camino rápido: solo actualiza set_data() de las trazas y las
        posiciones de los cursores. No toca ticks ni grid (no han cambiado).
        Usado durante pan horizontal/vertical y drag de cursores.

        Asume que _channel_lines ya contiene un Line2D por cada canal visible
        (creado por _full_redraw_plot en la llamada anterior).
        """
        t_start = self.t_offset.get()
        window = self.t_div.get() * H_DIVS
        t_end = t_start + window

        i_lo = int(np.searchsorted(self.time, t_start, side="left"))
        i_hi = int(np.searchsorted(self.time, t_end,   side="right"))
        t_vis = self.time[i_lo:i_hi]

        try:
            bbox = self.ax.get_window_extent()
            n_pixels = max(int(bbox.width), 100)
        except Exception:
            n_pixels = 1200

        v_div_active = self.v_div.get()

        # phosphor on: leemos intensity una vez (no por canal)
        phosphor = bool(self.phosphor_enabled.get())
        intensity = float(self.phosphor_intensity.get()) if phosphor else 0.0

        # Actualizar cada línea de canal. El artist en cache puede ser:
        #   - Line2D       (modo normal): set_data → muy rápido.
        #   - LineCollection (modo phosphor): set_segments + set_color con
        #     RGBA por segmento → más caro pero evita el rebuild completo.
        for ch in self.channels:
            uid = ch.get("_uid")
            line = self._channel_lines.get(uid)
            if line is None:
                continue
            if not ch["visible"].get():
                line.set_visible(False)
                continue
            line.set_visible(True)

            v = ch["voltage"][i_lo:i_hi]
            if self.render_mode.get() == "Fast":
                t_plot, v_plot = minmax_decimate(t_vis, v, n_pixels)
            else:
                t_plot, v_plot = t_vis, v

            divs = (v_plot - ch["v_offset"]) / ch["v_div"]
            y_plot = divs * v_div_active
            tx = t_plot - t_start

            # set_segments existe en LineCollection; en Line2D no. Lo usamos
            # como discriminador para evitar isinstance + import.
            if hasattr(line, "set_segments"):
                is_active = (ch is self.channels[self.active_channel_idx])
                base_alpha = 1.0 if is_active else 0.85
                # Mismo cap que en _full_redraw_plot: sin él arrastrar el
                # plot con phosphor on es inviable en datasets grandes.
                tp, yp = tx, y_plot
                cap = self._PHOSPHOR_MAX_SEGMENTS
                if len(tp) > cap:
                    tp, yp = minmax_decimate(tp, yp, cap // 2)
                segs, rgba = self._compute_phosphor_rgba(
                    tp, yp,
                    base_color=self._ch_color(ch),
                    base_alpha=base_alpha,
                    intensity=intensity,
                )
                line.set_segments(segs)
                line.set_color(rgba)
            else:
                # set_data evita recrear el artist; reuse del path interno
                line.set_data(tx, y_plot)

        # ─────────────────────────────────────────────────────────────────
        # IMPORTANTE: en fast path NO tocamos set_ylim ni set_yticks.
        # ─────────────────────────────────────────────────────────────────
        # En este modelo el "v_offset global del scope" (self.v_offset) no es
        # un offset del viewport — es un alias del v_offset del canal activo
        # (sincronizados por on_v_slider_move / on_mouse_drag). El grid SIEMPRE
        # se queda centrado en (-v_range/2, +v_range/2) y los datos se desplazan
        # vía `(v_data - ch["v_offset"]) / ch["v_div"] * v_div_active` arriba.
        #
        # Si tocásemos set_ylim aquí desplazaríamos el viewport entero junto
        # con los ticks, el time_div_label de la esquina, la línea de 0V del
        # grid… que es precisamente el bug que veíamos.
        #
        # Las etiquetas de los ticks Y pueden cambiar si el v_offset del canal
        # activo cambia (porque muestran V_real del canal). Eso lo actualizamos
        # aquí en O(V_DIVS) — barato.
        v_range = v_div_active * V_DIVS
        if self.channels:
            yticks_screen = np.linspace(-v_range / 2, v_range / 2, V_DIVS + 1)
            active = self.channels[self.active_channel_idx]
            # y_real = y_screen / v_div_active * sel.v_div + sel.v_offset
            yreal = yticks_screen / v_div_active * active["v_div"] + active["v_offset"]
            # sig_figs=4 para consistencia con configure_grid (ver allí el
            # razonamiento del modo Fine). Limpieza idéntica del residuo
            # numérico cerca de cero (evita "100 fV" en la línea del 0 V).
            noise_thr = v_div_active * 1e-9
            y_unit = self._active_unit()
            self.ax.set_yticklabels([
                format_si(0.0 if abs(y) < noise_thr else y, y_unit, sig_figs=4)
                for y in yreal
            ])
            for lbl in self.ax.get_yticklabels():
                lbl.set_color(self._ch_color(active, text=True))

        # Xticklabels: aunque las POSICIONES de los xticks no cambian
        # (el plot tiene xlim fijo [0, t_range]), las ETIQUETAS sí: cada
        # tick muestra "t_offset + x + time_origin". Durante un pan,
        # t_offset cambia y debemos refrescar O(H_DIVS) etiquetas. Barato.
        try:
            xticks_arr = self.ax.get_xticks()
            t_o = self._t_display_origin()
            self.ax.set_xticklabels(
                [format_si(t_start + x + t_o, "s", sig_figs=3)
                 for x in xticks_arr])
        except Exception:
            pass

        # Cursores verticales (tiempo)
        self._fast_update_vertical_cursors(t_start)

        # Cursores horizontales (voltaje)
        self._fast_update_horizontal_cursors()

        # Marcadores GND, time_div_label, info text, etc.
        # Los marcadores GND son baratos, los redibujamos siempre.
        # (Más adelante también los podemos persistir si fuese cuello de botella.)
        self._clear_gnd_markers()
        for i, ch in enumerate(self.channels):
            if ch["visible"].get():
                self._draw_channel_ground_marker(
                    ch, v_div_active, is_active=(i == self.active_channel_idx)
                )

        self.update_cursors_info_label()
        self.update_measurements_label()
        self._update_filename_label()
        # Anotaciones y triggers: estos limpian/redibujan internamente
        self._draw_annotations()
        self._draw_trigger_markers()
        self._draw_protocol_frames()
        self.update_time_div_label()

        self.canvas.draw_idle()
        try:
            self._redraw_minimap()
        except Exception:
            pass

    def _fast_update_vertical_cursors(self, t_start):
        """Mueve los axvline persistentes a las nuevas posiciones, o los
        oculta si los cursores verticales no están activos."""
        active = self._cursors_vertical_active()
        for i, line in enumerate(self._cursor_v_lines):
            if line is None:
                continue
            if not active:
                line.set_visible(False)
                continue
            x = self.cursor_x[i]
            window = self.t_div.get() * H_DIVS
            if t_start <= x <= t_start + window:
                line.set_xdata([x - t_start, x - t_start])
                line.set_visible(True)
            else:
                line.set_visible(False)

        # Marker (X) sobre traza y texto del cursor: limpiar y redibujar.
        # Son baratos (un punto y un texto), no merece la pena persistirlos
        # con tanta lógica de estado.
        self._clear_cursor_v_markers()
        if active:
            for x in self.cursor_x:
                if t_start <= x <= t_start + self.t_div.get() * H_DIVS:
                    self._draw_vertical_cursor_marker(x)

    def _fast_update_horizontal_cursors(self):
        """Mueve los axhline persistentes o los oculta."""
        active = self._cursors_horizontal_active()
        for i, line in enumerate(self._cursor_h_lines):
            if line is None:
                continue
            if not active:
                line.set_visible(False)
                continue
            line.set_ydata([self.cursor_y[i], self.cursor_y[i]])
            line.set_visible(True)

    def _clear_gnd_markers(self):
        """Borra los artists del marcador GND. Pequeño coste pero estable."""
        for a in self._gnd_marker_artists:
            try:
                a.remove()
            except Exception:
                pass
        self._gnd_marker_artists = []

    def _clear_cursor_v_markers(self):
        """Borra los artists del cursor vertical (marker X + texto)."""
        for a in self._cursor_v_marker_artists:
            try:
                a.remove()
            except Exception:
                pass
        self._cursor_v_marker_artists = []

    def _full_redraw_plot(self):
        """Camino lento: clear() + recrear todos los artists. Se llama cuando
        cambia algo estructural (canales, render_mode, tema, t_div, v_div).
        Es la lógica antigua de update_plot, adaptada para guardar referencias
        a los artists creados (Line2D por canal, cursores, hash marks...) para
        que el siguiente fast_update_plot pueda reutilizarlos.
        """
        # configure_grid hace ax.clear() entre otras cosas → invalida todos
        # los artists previos. Limpiamos también nuestros caches.
        self._channel_lines = {}
        self._cursor_v_lines = [None, None]
        self._cursor_h_lines = [None, None]
        self._gnd_marker_artists = []
        self._cursor_v_marker_artists = []
        # ax.clear() ya invalidó estos artists; vacíar listas para evitar
        # llamar a .remove() sobre artists muertos en el próximo draw.
        self._annotation_artists = []
        self._trigger_marker_artists = []
        self._protocol_artists = []
        # Las hashmarks las repuebla configure_grid()
        self._hashmark_artists = []

        self.configure_grid()

        if self.time is None or not self.channels:
            self.canvas.draw_idle()
            return

        t_start = self.t_offset.get()
        window = self.t_div.get() * H_DIVS
        t_end = t_start + window

        i_lo = int(np.searchsorted(self.time, t_start, side="left"))
        i_hi = int(np.searchsorted(self.time, t_end,   side="right"))
        t_vis = self.time[i_lo:i_hi]

        try:
            bbox = self.ax.get_window_extent()
            n_pixels = max(int(bbox.width), 100)
        except Exception:
            n_pixels = 1200

        v_div_active = self.v_div.get()
        # NB: NO aplicamos `self.v_offset` global como offset del viewport.
        # configure_grid() (llamado antes) ya dejó el ax con ylim simétrico
        # en (-v_range/2, +v_range/2). El desplazamiento del slider/drag se
        # propaga al `v_offset` del canal activo y se manifiesta abajo en
        # `y_plot = (v - ch["v_offset"]) / ch["v_div"] * v_div_active`.
        # Si hiciésemos `set_ylim(... +v_off)` arrastraríamos también la línea
        # central de 0V del grid y el time_div_label de la esquina.

        # Orden: primero los no activos, luego el activo arriba
        order = [i for i in range(len(self.channels)) if i != self.active_channel_idx]
        order.append(self.active_channel_idx)

        for i in order:
            ch = self.channels[i]
            if not ch["visible"].get():
                continue

            v = ch["voltage"][i_lo:i_hi]
            if self.render_mode.get() == "Fast":
                t_plot, v_plot = minmax_decimate(t_vis, v, n_pixels)
            else:
                t_plot, v_plot = t_vis, v

            divs = (v_plot - ch["v_offset"]) / ch["v_div"]
            y_plot = divs * v_div_active

            is_active = (i == self.active_channel_idx)
            base_lw    = 1.0 if is_active else 0.8
            base_alpha = 1.0 if is_active else 0.85

            if self.phosphor_enabled.get() and len(t_plot) >= 2:
                # Cada segmento del LineCollection es un draw call. Si la
                # ventana visible tiene 50k+ puntos, sin decimar la app se
                # arrastra. minmax_decimate conserva picos (no promedia),
                # así que el efecto visual de los flancos se preserva.
                tp = t_plot - t_start
                yp = y_plot
                cap = self._PHOSPHOR_MAX_SEGMENTS
                if len(tp) > cap:
                    tp, yp = minmax_decimate(tp, yp, cap // 2)
                line = self._build_phosphor_collection(
                    tp, yp,
                    base_color=self._ch_color(ch),
                    base_alpha=base_alpha,
                    linewidth=base_lw,
                    intensity=float(self.phosphor_intensity.get()),
                )
                self.ax.add_collection(line)
            else:
                (line,) = self.ax.plot(
                    t_plot - t_start,
                    y_plot,
                    color=self._ch_color(ch),
                    linewidth=base_lw,
                    alpha=base_alpha,
                )
            uid = ch.get("_uid")
            if uid is not None:
                self._channel_lines[uid] = line

            self._draw_channel_ground_marker(
                ch, v_div_active, is_active=(i == self.active_channel_idx)
            )

        # Cursores verticales (tiempo) — crear axvline persistentes SIEMPRE
        # que los cursores estén activos, aunque caigan fuera de la ventana
        # visible. Si están fuera del rango, los creamos invisibles; el
        # fast path (_fast_update_vertical_cursors) los hará visibles cuando
        # entren en rango por pan/zoom posterior.
        # Antes, si el cursor caía fuera en el momento del full redraw, el
        # artist NO se creaba (_cursor_v_lines[k] quedaba en None) y el
        # fast path lo ignoraba para siempre — bug que solo se "arreglaba"
        # forzando otro full redraw (cambiando modo de cursor, p.ej.).
        if self._cursors_vertical_active():
            for k, x in enumerate(self.cursor_x):
                # Posición efectiva: si está fuera de rango, lo creamos en
                # 0 invisible. La posición real la pondrá el fast path.
                in_range = (t_start <= x <= t_end)
                x_pos = (x - t_start) if in_range else 0.0
                line = self.ax.axvline(
                    x_pos, color="white",
                    linestyle=(0, (5, 5)), linewidth=1.2
                )
                line.set_visible(in_range)
                self._cursor_v_lines[k] = line
                if in_range:
                    self._draw_vertical_cursor_marker(x)

        # Cursores horizontales (voltaje)
        if self._cursors_horizontal_active():
            for k, y in enumerate(self.cursor_y):
                line = self.ax.axhline(
                    y, color="white",
                    linestyle=(0, (5, 5)), linewidth=1.2
                )
                self._cursor_h_lines[k] = line

        self.update_cursors_info_label()
        self.update_measurements_label()
        self._update_filename_label()
        self._draw_annotations()
        self._draw_trigger_markers()
        self._draw_protocol_frames()
        self.canvas.draw_idle()
        try:
            self._redraw_minimap()
        except Exception:
            pass

    def _draw_vertical_cursor_marker(self, cursor_time):
        """Dibuja un marcador (X) + valor de tensión en el punto donde el
        cursor vertical cruza la traza.

        Por defecto solo dibuja en el canal activo. Si
        `self.cursor_show_all` está activado, dibuja en TODOS los canales
        visibles — útil para comparar tensiones de varios canales en el
        mismo instante temporal.

        La etiqueta de cada marker incluye el nombre del canal cuando hay
        múltiples (para evitar ambigüedad al leer)."""
        if not self.channels:
            return

        show_all = bool(self.cursor_show_all.get())

        # Lista de canales a procesar
        if show_all:
            targets = [(i, ch) for i, ch in enumerate(self.channels)
                       if ch["visible"].get()]
        else:
            ch = self.channels[self.active_channel_idx]
            if not ch["visible"].get():
                return
            targets = [(self.active_channel_idx, ch)]

        if not targets:
            return

        # Índice más cercano en tiempo — común a todos los canales
        # (mismo timebase self.time).
        idx = int(np.searchsorted(self.time, cursor_time))
        idx = max(0, min(idx, len(self.time) - 1))

        # Coordenadas de pantalla — comunes para X
        x_screen = cursor_time - self.t_offset.get()
        v_div_active = self.v_div.get()
        v_range = v_div_active * V_DIVS
        t_range = self.t_div.get() * H_DIVS

        # Si hay más de un marker, prefijar con el nombre del canal
        # para que se entienda cuál es cuál.
        prefix_name = show_all and len(targets) > 1

        for ch_idx, ch in targets:
            v_real = float(ch["voltage"][idx])
            y_screen = (v_real - ch["v_offset"]) / ch["v_div"] * v_div_active

            # Solo dibujar si cae en el área visible verticalmente
            if not (-v_range / 2 <= y_screen <= v_range / 2):
                continue

            # Marcador X en color del canal
            marker_line = self.ax.plot(
                x_screen, y_screen,
                marker="x",
                color=self._ch_color(ch),
                markersize=10,
                markeredgewidth=1.4,
            )
            if marker_line:
                self._cursor_v_marker_artists.append(marker_line[0])

            # Etiqueta con el valor — con prefijo de nombre si hay varios.
            # Unidad propia de cada canal (v1.29).
            ch_unit = ch.get("unit", DEFAULT_UNIT)
            if prefix_name:
                label_text = f" {ch['name']}: {format_si(v_real, ch_unit)}"
            else:
                label_text = f" {format_si(v_real, ch_unit)}"

            ann = self.ax.annotate(
                label_text,
                xy=(x_screen, y_screen),
                xytext=(x_screen + t_range * 0.008, y_screen),
                ha="left", va="center",
                fontsize=8,
                color=ch["color"],
                fontweight="bold",
                bbox=dict(
                    facecolor="#111111",
                    edgecolor="none",
                    alpha=0.6,
                    boxstyle="round,pad=0.15",
                ),
            )
            self._cursor_v_marker_artists.append(ann)

    def _update_filename_label(self):
        """Actualiza el label discreto de la esquina inferior derecha con
        el nombre del fichero abierto y la marca del osciloscopio detectada."""
        if self.current_filename is None:
            self.filename_label.set_text("")
            return
        text = self.current_filename
        if self.current_brand:
            text += f"  |  Oscilloscope: {self.current_brand}"
        self.filename_label.set_text(text)

    def _draw_channel_ground_marker(self, ch, v_div_active, is_active=False):
        """Dibuja un pequeño marcador a la izquierda indicando el nivel 0V
        de cada canal, en su color."""
        v_range = v_div_active * V_DIVS
        v_offset_global = self.v_offset.get()
        t_range = self.t_div.get() * H_DIVS

        # El 0V real del canal, en coordenadas de pantalla:
        # y_screen = (0 - ch_offset)/ch_vdiv · v_div_active
        y_gnd = (-ch["v_offset"] / ch["v_div"]) * v_div_active

        y_min = -v_range / 2
        y_max =  v_range / 2
        if not (y_min <= y_gnd <= y_max):
            return

        # Canales con el 0 V a la misma altura: apilar la etiqueta debajo
        # de las ya dibujadas en vez de taparlas. Alto de etiqueta ~15 px
        # pasado a unidades de datos.
        try:
            label_h = v_range * 15.0 / max(self.ax.bbox.height, 1.0)
        except Exception:
            label_h = v_range * 0.04
        y_text = y_gnd
        taken = [a.xyann[1] for a in self._gnd_marker_artists]
        while any(abs(y_text - y) < label_h * 0.95 for y in taken):
            y_text -= label_h

        # Etiqueta con el nombre del canal a la izquierda.
        # Usa un bbox semitransparente del color de fondo del grid para que
        # NUNCA se superponga con los ticks del eje Y.
        ann = self.ax.annotate(
            f"▶ {ch['name']}",
            xy=(0, y_gnd),
            xytext=(-t_range * 0.09, y_text),
            ha="right",
            va="center",
            color=self._ch_color(ch, text=True),
            fontsize=9,
            fontweight="bold" if is_active else "normal",
            annotation_clip=False,
            bbox=dict(
                boxstyle="round,pad=0.15",
                facecolor=self.grid_theme.get("bg", "black"),
                edgecolor="none",
                alpha=0.9,
            ),
        )
        self._gnd_marker_artists.append(ann)

    def _set_protocol_frames(self, frames, channel_roles):
        """Recibe los frames decodificados de DecoderWindow.

        frames: lista de dicts (ver ProtocolDecoder.decode).
        channel_roles: dict {"primary": ch_idx} indicando bajo qué canal
                       se dibuja la barra de campos.

        Almacena el estado y fuerza un full redraw para que las cajas
        aparezcan. Llamar con frames=[] para limpiar el overlay.
        """
        self._protocol_frames = list(frames)
        self._protocol_channel_idx = channel_roles.get("primary", 0) if frames else None
        # Forzar full redraw: invalidar fingerprint para que update_plot
        # vaya por el camino lento que llama a _draw_protocol_frames.
        self._last_structure_fingerprint = None
        self.update_plot()

    def _draw_protocol_frames(self):
        """Dibuja las cajas del decoder bajo la traza del canal asociado.

        Se llama desde _full_redraw_plot y _fast_update_plot, igual que
        _draw_trigger_markers. Limpia artists previos antes de redibujar.
        """
        # Limpiar previos
        for a in getattr(self, "_protocol_artists", []):
            try:
                a.remove()
            except Exception:
                pass
        self._protocol_artists = []

        frames = getattr(self, "_protocol_frames", None)
        ch_idx = getattr(self, "_protocol_channel_idx", None)
        if not frames or ch_idx is None:
            return
        if ch_idx >= len(self.channels):
            return

        ch = self.channels[ch_idx]
        if not ch["visible"].get():
            return

        t_start = self.t_offset.get()
        window = self.t_div.get() * H_DIVS
        t_end = t_start + window
        v_range = self.v_div.get() * V_DIVS

        # Posición Y de la barra: debajo del GND del canal, fija respecto al
        # eje screen. Usamos coordenadas blended para que el alto sea fijo
        # en píxeles pero la posición X siga datos.
        # Lo más simple: poner la barra entre y = -v_range/2 * 0.95
        # y -v_range/2 * 0.82 (un 13% del alto total cerca del fondo).
        y_lo, y_hi = self.ax.get_ylim()
        y_top = y_lo + (y_hi - y_lo) * 0.10
        y_bot = y_lo + (y_hi - y_lo) * 0.03
        box_h = y_top - y_bot

        # Para no dibujar miles de cajas fuera de pantalla: filtrar
        visible = [f for f in frames
                   if f["t_end"] >= t_start and f["t_start"] <= t_end]

        # Import local (no contaminar el módulo)
        from matplotlib.patches import Rectangle

        # Si hay demasiados frames visibles, el texto se solapa — degradamos
        # mostrando solo cajas sin texto cuando son muy estrechas (<20 px).
        bbox = self.ax.get_window_extent()
        px_per_s = bbox.width / max(window, 1e-12)

        for f in visible:
            t0 = max(f["t_start"], t_start)
            t1 = min(f["t_end"], t_end)
            if t1 <= t0:
                continue
            x0_screen = t0 - t_start
            x1_screen = t1 - t_start
            width_px = (t1 - t0) * px_per_s

            color = f.get("color", "#5fa8ff")
            err = f.get("error", False)
            edge = "#ff0000" if err else "#000000"

            # Caja
            rect = Rectangle(
                (x0_screen, y_bot),
                x1_screen - x0_screen, box_h,
                facecolor=color,
                edgecolor=edge,
                linewidth=1.0 if err else 0.5,
                alpha=0.85,
                zorder=10,
            )
            self.ax.add_patch(rect)
            self._protocol_artists.append(rect)

            # Texto si cabe (al menos ~20px de ancho)
            if width_px > 20:
                # Truncar label si la caja es estrecha
                label = f.get("label", "")
                max_chars = max(1, int(width_px / 8))   # ~8 px por char
                if len(label) > max_chars:
                    label = label[:max_chars - 1] + "…"
                txt = self.ax.text(
                    (x0_screen + x1_screen) / 2.0,
                    y_bot + box_h / 2.0,
                    label,
                    color="black",
                    fontsize=8,
                    fontfamily="Consolas",
                    ha="center", va="center",
                    zorder=11,
                )
                self._protocol_artists.append(txt)
