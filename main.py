import tkinter as tk
from tkinter import ttk
from tkinter import filedialog
import os
import sys
import json
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# Lógica pura extraída a ocv.core (fase 2 del refactor)
from ocv.core.measurements import (
    _find_crossings,
    AMPLITUDE_MEASUREMENTS,
    TIME_MEASUREMENTS,
    MEASUREMENT_DESCRIPTIONS,
    ALL_MEASUREMENTS,
)
from ocv.core.dsp import (
    minmax_decimate,
)
from ocv.core.si_format import (
    CHANNEL_UNITS,
    DEFAULT_UNIT,
    format_si,
    parse_si,
)
from ocv.core.csv_io import (
    _read_csv_smart,
)
from ocv.core.math_expr import (
    math_parse,
    math_eval_with,
)
from ocv.ui import theme
from ocv.ui.theme import (
    _retheme_tk_widgets,
    apply_theme,
    channel_color,
    CHANNEL_COLORS,
    THEMES,
)
from ocv.core.config import (
    config_dir,
    check_config_writable,
    load_user_config,
    save_user_config,
)
from ocv.core.session import (
    SESSION_VERSION,
    SessionError,
    embedded_csv,
    is_newer,
    read_session,
    serialize_annotations,
    write_session,
)
from ocv.resources import (
    _resource_path,
)
from ocv.core.scope import (
    H_DIVS,
    V_DIVS,
    TIME_DIV_VALUES,
    V_DIV_VALUES,
)
from ocv.ui.widgets import (
    Tooltip,
)
from ocv.ui.meas_preview import (
    make_measurement_preview,
)
from ocv.ui.fft_window import (
    FFTWindow,
)
from ocv.ui.eye_window import (
    EyeDiagramWindow,
)
from ocv.ui.decoder_window import (
    DecoderWindow,
)
from ocv.ui.app.theme_runtime import ThemeRuntimeMixin


class OscilloscopeApp(ThemeRuntimeMixin):

    def __init__(self, root):
        self.root = root
        self.root.title("Oscilloscope CSV Viewer")

        # Datos
        self.time = None
        self.voltage = None
        self.t_total = 0.0
        # time_origin: offset original del primer punto del CSV. Internamente
        # self.time SIEMPRE empieza en 0 (simplifica todos los algoritmos),
        # pero los osciloscopios suelen guardar capturas con un eje temporal
        # que incluye pre-trigger (t < 0). Para no perder esa info, guardamos
        # el offset original y lo SUMAMOS solo en las etiquetas visibles
        # (eje X, cursores, etc.). Así "0 s" interno = "time_origin" real.
        self.time_origin = 0.0

        # Modo de visualización del eje temporal:
        #   True  = "Absolute": muestra tiempos REALES del CSV
        #           (incluye pre-trigger negativo si lo había)
        #   False = "Relative to start": el primer punto del CSV es t=0
        #           (comportamiento legacy de la app)
        # Se persiste en config.json. El valor real se aplica al renderizar
        # (ver _t_display_origin()). Inicializamos con el config persistido.
        _cfg_time_abs = load_user_config().get("time_show_absolute", True)
        self.time_show_absolute = tk.BooleanVar(value=bool(_cfg_time_abs))
        self.current_filename = None   # solo el nombre base, sin path
        self.current_filepath = None   # path completo (para sesiones)
        self.current_brand = None      # marca detectada del osciloscopio
        # Cache del CSV embebido tras cargar un .ocv. Necesario porque al
        # cargar sesión current_filepath queda en None (el temp se borra),
        # y un re-save sin este cache produciría csv_content vacío.
        # Se invalida al cargar un CSV nuevo desde disco.
        self._embedded_csv_content = None

        # -----------------------------
        # Sesión: estado Untitled + dirty (v1.25)
        # -----------------------------
        # session_path: path del .ocv activo (None = Untitled, aún no guardado).
        # session_dirty: True = hay cambios no guardados desde el último save/load.
        # _suppress_dirty: cuando True, _mark_dirty() es no-op. Se usa al cargar
        #   una sesión para evitar que los traces de tk.Vars que escribimos al
        #   restaurar marquen dirty de forma espuria.
        self.session_path = None
        self.session_dirty = False
        self._suppress_dirty = False

        # -----------------------------
        # Multi-canal
        # -----------------------------
        # Lista de dicts, uno por canal cargado:
        #   {
        #     "_uid":    int (estable durante la vida del canal),
        #     "name":    str,
        #     "color":   "#hex",
        #     "voltage": np.array,    # misma longitud que self.time
        #     "visible": tk.BooleanVar,
        #     "v_div":   float,       # individual por canal
        #     "v_offset": float,      # individual por canal
        #     "unit":    str,         # v1.29 — etiqueta de unidad (default "V").
        #                             # Solo afecta a display (V/div label, ticks,
        #                             # cursores, medidas). NO afecta a MATH.
        #   }
        self.channels = []
        self._next_channel_uid = 1   # contador monotónicamente creciente
        self.active_channel_idx = 0   # índice del canal "activo" (el que
                                       # controla V/div, V offset, cursores,
                                       # medidas, FFT…)

        # Variables de control
        self.t_div = tk.DoubleVar(value=1e-3)
        self.v_div = tk.DoubleVar(value=1.0)

        # Variables de display (formato SI legible) para los spinbox
        self.t_div_display = tk.StringVar(value=format_si(self.t_div.get(), "s"))
        self.v_div_display = tk.StringVar(value=format_si(self.v_div.get(), "V"))

        # Modo Fine: cuando está activo, V/div se mueve en pasos de 0.1 V
        # (en vez de saltar a los valores canónicos de V_DIV_VALUES)
        self.fine_mode = tk.BooleanVar(value=False)

        # Guarda para evitar que el trace del StringVar se dispare
        # cuando el propio código escribe en el display
        self._updating_display = False
        self.t_offset = tk.DoubleVar(value=0.0)
        self.v_offset = tk.DoubleVar(value=0.0)   # ⬅️ NUEVO: offset vertical (en voltios)
        
        # -----------------------------
        # Cursores
        # -----------------------------
        self.cursors_enabled = tk.BooleanVar(value=False)
        self.cursor_mode = tk.StringVar(value="Vertical")

        # Cuando el cursor vertical está activo, por defecto solo se pinta
        # la cruz (marker X + valor) en el canal activo. Con este flag
        # activado, se pinta también en TODOS los canales visibles, lo
        # que permite leer la tensión de cada canal en el mismo instante
        # temporal. Persistido en user_config.
        _cur_show_all = bool(
            load_user_config().get("cursor_show_all_channels", False))
        self.cursor_show_all = tk.BooleanVar(value=_cur_show_all)
        
        

        # -----------------------------
        # Cursores (estado)
        # -----------------------------
        self.cursor_x = [0.0, 0.0]   # posiciones iniciales (en segundos)
        self.active_cursor = None     # índice del cursor activo (0 o 1)
        
        
        
        # -----------------------------
        # Cursores horizontales (estado)
        # -----------------------------
        self.cursor_y = [0.0, 0.0]    # niveles de voltaje
        self.active_cursor_y = None  # cursor horizontal activo


        self.dragging = False
        self.drag_start_x = None
        self.drag_start_y = None                # ⬅️ NUEVO
        self.drag_start_offset = 0.0
        self.drag_start_v_offset = 0.0          # ⬅️ NUEVO
        
        
        # -----------------------------
        # Measurements
        # -----------------------------
        # Lista de dicts: [{"channel_idx": int, "name": str}], máx 10
        self.active_measurements = []
        self.MAX_MEASUREMENTS = 16

        # -----------------------------
        # MATH channels
        # -----------------------------
        # Máx 4 MATH. Se guardan como dicts dentro de self.channels con
        # un campo extra "math" = {"expr", "ast", "deps_ch", "deps_m"}.
        # self.math_order mantiene el orden de creación (determina M1..M4).
        self.MAX_MATH = 8
        self.math_order = []   # lista de índices en self.channels, en orden M1..M4

        # -----------------------------
        # Grid view (tema del plot)
        # -----------------------------
        # Inicialización en dos fases:
        #   1. Defaults: colores PLOT_* del tema GLOBAL activo. Si el config
        #      persistió "Light", al arrancar el plot ya sale claro sin
        #      necesidad de re-aplicar el tema manualmente.
        #   2. Sobreescritura desde config.json si el usuario personalizó
        #      colores en Appearance → Advanced. Persistir en config evita
        #      que al cerrar y reabrir la app se pierdan esos ajustes.
        _theme_plot = THEMES.get(theme.CURRENT_THEME_NAME, THEMES["Dark"])
        self.grid_theme = {
            "bg":            _theme_plot["PLOT_BG"],
            "grid_color":    _theme_plot["PLOT_GRID"],
            "text_color":    _theme_plot["PLOT_TEXT"],
            "time_label_bg": _theme_plot["PLOT_TIME_LBL_BG"],
        }
        # Cargar overrides del usuario si existen en config.json
        try:
            _cfg_grid = load_user_config().get("grid_theme", {})
            if isinstance(_cfg_grid, dict):
                for k in ("bg", "grid_color", "text_color", "time_label_bg"):
                    v = _cfg_grid.get(k)
                    if isinstance(v, str) and v.startswith("#"):
                        self.grid_theme[k] = v
        except Exception:
            pass

        # -----------------------------
        # Phosphor intensity grading (v1.27)
        # -----------------------------
        # Estilo visual que emula el brillo no uniforme del fósforo de un
        # CRT analógico: en zonas de la traza con |dV/dt| alto el "haz"
        # pasa rápido y deposita poca energía → segmento más tenue. En
        # zonas planas el haz se queda más tiempo → segmento más brillante.
        #
        # Para CSV estático esto se traduce en alpha por segmento:
        #   alpha = lerp(alpha_min, 1.0, 1 - normalized(|dV/dt|))
        # donde alpha_min = 1 - phosphor_intensity. A intensity=0 todos los
        # segmentos quedan alpha=1 (efecto desactivado). A intensity=1 los
        # tramos rápidos llegan a alpha_min=0 (casi invisibles).
        #
        # Ambos parámetros son preferencia visual del usuario, no parte
        # del estado de sesión: NO marcan dirty y SÍ persisten en config.json
        # como cualquier otro setting global (theme, grid_theme, etc.).
        _cfg = load_user_config()
        self.phosphor_enabled = tk.BooleanVar(
            value=bool(_cfg.get("phosphor_enabled", False)))
        self.phosphor_intensity = tk.DoubleVar(
            value=float(_cfg.get("phosphor_intensity", 0.6)))

        # -----------------------------
        # Annotations
        # -----------------------------
        # Lista de dicts. Coords en DATOS (tiempo, voltios) para que se muevan
        # con pan/zoom. Tipos: "stroke" (lista de puntos), "text" (pos + content).
        self.annotations = []
        self.annotation_mode = None      # None | "draw" | "text" | "erase"
        self.annotation_color = "#ffff00"
        self.annotation_width = 2
        self._current_stroke = None      # stroke en construcción
        self._annotation_toolbar = None  # Frame flotante

        # ---- Undo/redo de anotaciones ----
        # Cada entrada del stack es un dict que describe una acción reversible:
        #   {"kind": "add",    "items": [(index, annotation_dict), ...]}
        #     -> undo: borrar esos índices. redo: re-insertarlos.
        #   {"kind": "remove", "items": [(index, annotation_dict), ...]}
        #     -> undo: re-insertarlos en sus índices. redo: borrarlos.
        # Los items van en orden ASCENDENTE de índice. Para el remove con
        # múltiples elementos, la inserción undo debe hacerse en orden
        # ascendente para que los índices coincidan.
        # Stack acotado para no crecer indefinidamente.
        self._undo_stack = []
        self._redo_stack = []
        self.UNDO_STACK_LIMIT = 100

        # -----------------------------
        # Trigger
        # -----------------------------
        # Config viva: se recalcula al cambiar parámetros
        self.trigger_config = {
            "enabled":   False,
            "source":    0,          # ch_idx
            "type":      "Edge",     # "Edge" | "Width"
            "slope":     "Rising",   # "Rising" | "Falling"
            "level":     0.0,
            "polarity":  "Positive", # para Width
            "condition": "Width >",  # Width >, <, =, ≠, Inside, Outside
            "width1":    1e-3,
            "width2":    2e-3,
            "show_markers": True,
        }
        self.trigger_events = []     # lista de dicts: {"t_trigger", "t_start", "t_end" (Width)}
        self.trigger_current_idx = -1
        self._trigger_window = None

        # -----------------------------
        # XY mode
        # -----------------------------
        self.xy_config = {
            "enabled":  False,
            "x_idx":    0,       # canal X
            "y_idx":    1,       # canal Y
            "style":    "Line",  # "Line" | "Dots" | "Line + Dots"
            "window":   "Visible range",  # "Visible range" | "Full capture"
        }
        self._xy_window = None

        # -----------------------------
        # Render mode: Detail vs Fast
        # -----------------------------
        # "Detail" → sin decimation, pinta todos los puntos del rango visible.
        #            Mejor fidelidad visual, útil cuando la señal es corta o
        #            cuando quieres examinar detalles finos sin artefactos.
        # "Fast"   → decimation min/max activa, salta el aliasing vertical
        #            a cambio de mucha más fluidez con muchos puntos.
        # El botón de la topbar alterna entre los dos.
        self.render_mode = tk.StringVar(value="Detail")

        # -----------------------------
        # Throttle de redraws
        # -----------------------------
        # Durante pan/drag el ratón dispara eventos mucho más rápido
        # de lo que matplotlib puede redibujar. En vez de hacer
        # update_plot() por cada evento, planificamos uno con after()
        # y coalescemos los eventos intermedios.
        self._redraw_pending = False
        # 16 ms ~ 60 FPS objetivo. Si tu PC va justo sube a 25-33 ms.
        self._redraw_interval_ms = 16

        # Layout (debe ir DESPUÉS de inicializar estado que usan los builders)
        self.build_layout()

        # Atajos globales: sesiones y carga de CSV
        # Bindeamos en la root para que funcionen independientemente del
        # widget con foco (siempre que la app tenga el foco del SO).
        # Ctrl+S        → Save (sin diálogo si ya hay path; Save As si Untitled)
        # Ctrl+Shift+S  → Save As... (siempre pregunta path)
        # En tkinter, <Control-S> con S mayúscula = Ctrl+Shift+s.
        self.root.bind_all("<Control-s>", lambda e: self.save_session())
        self.root.bind_all("<Control-S>", lambda e: self.save_session_as())
        self.root.bind_all("<Control-o>", lambda e: self.load_session())
        self.root.bind_all("<Control-O>", lambda e: self.load_session())

        # Undo/redo de anotaciones (Ctrl+Z, Ctrl+Y, Ctrl+Shift+Z).
        # Convención: Ctrl+Z deshace, Ctrl+Y (Windows/Linux) y Ctrl+Shift+Z
        # (Mac/varios) rehacen. Las versiones con mayúscula cubren el caso
        # de tener Caps Lock activado.
        self.root.bind_all("<Control-z>", lambda e: self._undo_annotation_action())
        self.root.bind_all("<Control-Z>", lambda e: self._undo_annotation_action())
        self.root.bind_all("<Control-y>", lambda e: self._redo_annotation_action())
        self.root.bind_all("<Control-Y>", lambda e: self._redo_annotation_action())
        self.root.bind_all("<Control-Shift-Z>", lambda e: self._redo_annotation_action())
        self.root.bind_all("<Control-Shift-z>", lambda e: self._redo_annotation_action())

        # Atajos numéricos 1-9 → seleccionan canal activo por índice.
        # bind_all dispara con cualquier foco, así que el handler filtra
        # widgets de edición (Entry/Spinbox/Text) para no robar teclas
        # mientras el usuario escribe nombres, V/div, expresiones math, etc.
        for _n in range(1, 10):
            self.root.bind_all(
                f"<Key-{_n}>",
                lambda e, i=_n - 1: self._on_active_channel_hotkey(i),
            )

        # =====================
        # Atajos globales v1.30 — todos pasan por _hotkey_guard() para
        # ignorar la pulsación si el foco está en un Entry/Spinbox/Text
        # (sin esto la edición de V/div, MATH, rename, etc. se rompe).
        # Lista completa de combinaciones en el dialog Commands legend
        # (Settings → Commands legend).
        # =====================
        # Funciones (letras): cursores, unidad, snap GND, centrar.
        self.root.bind_all("<Key-c>", lambda e: self._hotkey_toggle_cursors())
        self.root.bind_all("<Key-C>", lambda e: self._hotkey_toggle_cursors())
        self.root.bind_all("<Key-u>", lambda e: self._hotkey_open_unit_picker())
        self.root.bind_all("<Key-U>", lambda e: self._hotkey_open_unit_picker())
        self.root.bind_all("<Key-g>", lambda e: self._hotkey_snap_gnd())
        self.root.bind_all("<Key-G>", lambda e: self._hotkey_snap_gnd())
        self.root.bind_all("<Key-0>", lambda e: self._hotkey_center_channel())

        # Ctrl+1..9 → toggle visibilidad del canal idx-1.
        for _n in range(1, 10):
            self.root.bind_all(
                f"<Control-Key-{_n}>",
                lambda e, i=_n - 1: self._hotkey_toggle_channel_visibility(i),
            )

        # Flechas (sin modificador) → mover canal activo / vista.
        # Arr/abj = v_offset del canal activo. Izq/der = t_offset global.
        self.root.bind_all("<Key-Up>",    lambda e: self._hotkey_pan_v(+1))
        self.root.bind_all("<Key-Down>",  lambda e: self._hotkey_pan_v(-1))
        self.root.bind_all("<Key-Left>",  lambda e: self._hotkey_pan_h(-1))
        self.root.bind_all("<Key-Right>", lambda e: self._hotkey_pan_h(+1))

        # Shift+flechas v → cambio discreto de V/div (canal activo).
        self.root.bind_all("<Shift-Up>",   lambda e: self._hotkey_step_v_div(-1))
        self.root.bind_all("<Shift-Down>", lambda e: self._hotkey_step_v_div(+1))

        # Ctrl+Shift+flechas v → cambio discreto de T/div global.
        # (Antes era Ctrl+↑/↓; v1.30 lo desplazó para liberar Ctrl+↑/↓
        # como cursor Y1.)
        self.root.bind_all("<Control-Shift-Up>",   lambda e: self._hotkey_step_t_div(-1))
        self.root.bind_all("<Control-Shift-Down>", lambda e: self._hotkey_step_t_div(+1))
        # Variantes con shift mayúscula explícita (Caps Lock):
        self.root.bind_all("<Control-Shift-KP_Up>",   lambda e: self._hotkey_step_t_div(-1))
        self.root.bind_all("<Control-Shift-KP_Down>", lambda e: self._hotkey_step_t_div(+1))

        # Cursores con Ctrl / Ctrl+Alt:
        #   Ctrl+←/→         → cursor X1 (tiempo, vertical izq)
        #   Ctrl+↑/↓         → cursor Y1 (voltaje, horizontal inf)
        #   Ctrl+Alt+←/→     → cursor X2 (vertical der)
        #   Ctrl+Alt+↑/↓     → cursor Y2 (horizontal sup)
        # Convención mental: Ctrl = "cursor", Alt = "el otro" (idx 1).
        self.root.bind_all("<Control-Left>",  lambda e: self._hotkey_move_cursor("x", 0, -1))
        self.root.bind_all("<Control-Right>", lambda e: self._hotkey_move_cursor("x", 0, +1))
        self.root.bind_all("<Control-Up>",    lambda e: self._hotkey_move_cursor("y", 0, +1))
        self.root.bind_all("<Control-Down>",  lambda e: self._hotkey_move_cursor("y", 0, -1))
        self.root.bind_all("<Control-Alt-Left>",  lambda e: self._hotkey_move_cursor("x", 1, -1))
        self.root.bind_all("<Control-Alt-Right>", lambda e: self._hotkey_move_cursor("x", 1, +1))
        self.root.bind_all("<Control-Alt-Up>",    lambda e: self._hotkey_move_cursor("y", 1, +1))
        self.root.bind_all("<Control-Alt-Down>",  lambda e: self._hotkey_move_cursor("y", 1, -1))

        # Título inicial: "Untitled — Oscilloscope CSV Viewer".
        # Sustituye al title() estático del principio de __init__.
        self._update_title()

        # Trace de tk.Vars que pertenecen al estado de sesión: cualquier
        # cambio marca dirty. _mark_dirty es O(1) si ya estaba dirty, así
        # que no penaliza arrastres de slider que disparan miles de set().
        # Durante load_session/load_csv envolvemos con _suppress_dirty para
        # que estas mismas escrituras no marquen dirty espurio.
        def _dirty_trace(*_):
            self._mark_dirty()
        for _var in (
            self.t_div, self.v_div, self.t_offset, self.v_offset,
            self.fine_mode, self.render_mode,
            self.cursors_enabled, self.cursor_mode, self.cursor_show_all,
        ):
            try:
                _var.trace_add("write", _dirty_trace)
            except Exception:
                # Si alguna Var aún no existe en esta versión, no es crítico.
                pass

        # Traces de phosphor: NO marcan dirty (preferencia visual), pero
        # SÍ persisten a config.json y refrescan el plot.
        #
        # Dos handlers para evitar cuellos de botella:
        #   - enabled  → inmediato. Toggle del checkbox debe responder ya.
        #     Invalida fingerprint (cambia el tipo de artist por canal).
        #   - intensity → debounced. tk.Scale dispara write por cada pixel
        #     que mueve el thumb; sin debounce, arrastrar el slider es un
        #     update_plot por píxel. 80 ms da feedback visual fluido sin
        #     saturar la CPU.
        def _persist_phosphor_config():
            try:
                save_user_config({
                    "phosphor_enabled":   bool(self.phosphor_enabled.get()),
                    "phosphor_intensity": float(self.phosphor_intensity.get()),
                })
            except Exception:
                pass

        def _on_phosphor_enabled(*_):
            self._last_structure_fingerprint = None
            try:
                self.update_plot()
            except Exception:
                pass
            _persist_phosphor_config()

        def _on_phosphor_intensity(*_):
            prev = getattr(self, "_phosphor_after_id", None)
            if prev is not None:
                try:
                    self.root.after_cancel(prev)
                except Exception:
                    pass
            def _flush():
                self._phosphor_after_id = None
                # No invalidamos fingerprint: el tipo de artist no cambia,
                # el fast path va a recomputar RGBA con la intensity nueva.
                try:
                    self.update_plot()
                except Exception:
                    pass
                _persist_phosphor_config()
            self._phosphor_after_id = self.root.after(80, _flush)

        try:
            self.phosphor_enabled.trace_add("write",   _on_phosphor_enabled)
            self.phosphor_intensity.trace_add("write", _on_phosphor_intensity)
        except Exception:
            pass


        
        







    # -----------------------------
    def build_layout(self):
        # ---- TopBar horizontal ----
        self.topbar = tk.Frame(self.root, bg=theme.COLOR_BG_ALT, height=52)
        self.topbar.pack(side=tk.TOP, fill=tk.X)
        self.topbar.pack_propagate(False)

        # ---- Zona principal (izquierda: sidebar, centro: plot, derecha: meas) ----
        main = tk.Frame(self.root, bg=theme.COLOR_BG)
        main.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Sidebar izquierdo
        sidebar = tk.Frame(main, bg=theme.COLOR_BG, padx=5)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)

        # Panel derecho: columna de medidas activas
        self.meas_panel = tk.Frame(main, bg=theme.COLOR_BG, width=220)
        self.meas_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 4), pady=4)
        self.meas_panel.pack_propagate(False)

        # Zona central: minimap arriba + plot debajo
        plot_area = tk.Frame(main, bg=theme.COLOR_BG)
        plot_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.minimap_canvas = tk.Canvas(
            plot_area, bg=theme.COLOR_BG_ALT, height=40,
            highlightthickness=1, highlightbackground=theme.COLOR_BORDER,
            bd=0, cursor="hand2",
        )
        self.minimap_canvas.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(4, 0))

        plot = tk.Frame(plot_area)
        plot.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Estado minimap
        self._minimap_dragging = False
        self._minimap_drag_offset = 0.0

        # Bindings minimap
        self.minimap_canvas.bind("<Configure>", lambda e: self._redraw_minimap())
        self.minimap_canvas.bind("<Button-1>",  self._on_minimap_press)
        self.minimap_canvas.bind("<B1-Motion>", self._on_minimap_drag)
        self.minimap_canvas.bind("<ButtonRelease-1>", self._on_minimap_release)
        # Scroll rueda: zoom
        self.minimap_canvas.bind("<MouseWheel>", self._on_minimap_wheel)  # Windows/Mac
        self.minimap_canvas.bind("<Button-4>",  lambda e: self._on_minimap_wheel_linux(e, +1))
        self.minimap_canvas.bind("<Button-5>",  lambda e: self._on_minimap_wheel_linux(e, -1))

        self.build_topbar(self.topbar)
        self.build_controls(sidebar)
        self.build_plot(plot)
        self._build_measurements_panel()

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


    # -----------------------------
    # Unidades de canal (v1.29)
    # -----------------------------
    def _ch_unit(self, ch_idx):
        """Devuelve la unidad del canal `ch_idx` con fallback al default.

        Backward-compat: canales cargados de sesiones <v1.29 no traen
        la clave 'unit'; en ese caso devolvemos DEFAULT_UNIT ('V')."""
        if not (0 <= ch_idx < len(self.channels)):
            return DEFAULT_UNIT
        return self.channels[ch_idx].get("unit", DEFAULT_UNIT)

    def _active_unit(self):
        """Unidad del canal activo (helper para ticks Y, V/div, cursores)."""
        return self._ch_unit(self.active_channel_idx)

    def _update_v_div_display(self):
        """Refresca la StringVar del label V/div con la unidad del canal
        activo. Útil cuando solo cambia la unidad (sin tocar el valor)."""
        try:
            self.v_div_display.set(
                format_si(self.v_div.get(), self._active_unit()))
        except Exception:
            pass

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

    # -----------------------------
    # Save image as...
    # -----------------------------
    def _snap_channel_gnd_to_grid(self, ch_idx):
        """Ajusta el v_offset del canal para que su 0V caiga exactamente
        sobre la división vertical más cercana a su posición actual."""
        if not (0 <= ch_idx < len(self.channels)):
            return
        ch = self.channels[ch_idx]
        v_div_active = self.v_div.get()
        if v_div_active <= 0 or ch["v_div"] <= 0:
            return
        # Posición pantalla actual del 0V del canal
        y_gnd_screen = (-ch["v_offset"] / ch["v_div"]) * v_div_active
        # División más cercana
        snapped = round(y_gnd_screen / v_div_active) * v_div_active
        # Clamp a ±4 divisiones para no salir del rango del slider
        max_y = v_div_active * (V_DIVS / 2 - 1)
        snapped = max(-max_y, min(max_y, snapped))
        # Calcular nuevo offset
        new_offset = -snapped / v_div_active * ch["v_div"]
        ch["v_offset"] = new_offset
        # Si es el canal activo, sincronizar var + slider
        if ch_idx == self.active_channel_idx:
            self.v_offset.set(new_offset)
            try:
                self.v_slider.set(-new_offset)
            except Exception:
                pass
        self.update_plot()

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
        APP_VERSION   = "1.24"
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
        def make_theme_btn(parent, name):
            t = THEMES[name]
            # highlightthickness FIJO para todos los estados.
            # Si lo cambiamos según activo/inactivo, el botón crece/encoge y
            # los demás botones del grid se reflowan — efecto "salto" feo al
            # cambiar tema. Mantenemos un borde constante y solo variamos su
            # color (ACCENT cuando activo, BORDER cuando inactivo).
            b = tk.Button(
                parent, text=name,
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
                    highlightbackground=t["ACCENT"] if is_active else t["BORDER"],
                )

        def choose_theme(name):
            self.apply_theme_runtime(name, sync_plot=True)
            # Refrescar la propia ventana
            try:
                win.configure(bg=theme.COLOR_BG)
                refresh_theme_buttons()
                refresh_swatches()
                # Rerender de etiquetas de la propia ventana
                _retheme_tk_widgets(win)
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
            text="Absolute — show real times from the CSV (e.g. pre-trigger "
                 "appears as negative)",
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
        self._appearance_swatch_refresh = refresh_swatches
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

    # =============================
    # Trigger
    # =============================
    def _compute_trigger_events(self):
        """Recorre la señal actual y genera self.trigger_events según la
        configuración. Eventos son tiempos reales (en segundos absolutos)."""
        self.trigger_events = []
        cfg = self.trigger_config
        if not cfg["enabled"] or self.time is None or not self.channels:
            return
        src_idx = cfg["source"]
        if not (0 <= src_idx < len(self.channels)):
            return
        v = self.channels[src_idx]["voltage"]
        t = self.time
        if len(v) < 2:
            return

        if cfg["type"] == "Edge":
            lvl = float(cfg["level"])
            edge = "rising" if cfg["slope"] == "Rising" else "falling"
            crossings = _find_crossings(t, v, lvl, edge=edge)
            for c in crossings:
                self.trigger_events.append({
                    "t_trigger": float(c),
                    "t_start":   float(c),
                    "t_end":     None,
                })
            return

        if cfg["type"] == "Width":
            lvl = float(cfg["level"])
            if cfg["polarity"] == "Positive":
                # Pulso positivo: rising -> falling
                rises = _find_crossings(t, v, lvl, edge="rising")
                falls = _find_crossings(t, v, lvl, edge="falling")
                pulses = []
                for r in rises:
                    nxt = falls[falls > r]
                    if len(nxt):
                        pulses.append((r, nxt[0]))
            else:
                # Pulso negativo: falling -> rising
                rises = _find_crossings(t, v, lvl, edge="rising")
                falls = _find_crossings(t, v, lvl, edge="falling")
                pulses = []
                for f in falls:
                    nxt = rises[rises > f]
                    if len(nxt):
                        pulses.append((f, nxt[0]))

            w1 = float(cfg["width1"])
            w2 = float(cfg["width2"])
            cond = cfg["condition"]
            TOL = max(w1 * 0.01, 1e-12)   # 1% para igualdad

            for (ts, te) in pulses:
                w = te - ts
                ok = False
                if cond == "Width >":       ok = w > w1
                elif cond == "Width <":     ok = w < w1
                elif cond == "Width =":     ok = abs(w - w1) <= TOL
                elif cond == "Width ≠":     ok = abs(w - w1) > TOL
                elif cond == "Inside":      ok = w1 <= w <= w2
                elif cond == "Outside":     ok = w < w1 or w > w2
                if ok:
                    self.trigger_events.append({
                        "t_trigger": float(ts),
                        "t_start":   float(ts),
                        "t_end":     float(te),
                    })

    def _refresh_trigger(self):
        """Recalcula y redibuja. Actualiza también la ventana si está abierta."""
        self._compute_trigger_events()
        # Ajustar índice actual si cambió la lista
        if self.trigger_events:
            if self.trigger_current_idx < 0 or self.trigger_current_idx >= len(self.trigger_events):
                self.trigger_current_idx = 0
        else:
            self.trigger_current_idx = -1
        self._update_trigger_count_label()
        self.update_plot()
        # Cualquier cambio de trigger (enable, src, type, level, etc.) pasa
        # por aquí — un único punto para marcar dirty.
        self._mark_dirty()

    def _update_trigger_count_label(self):
        """Actualiza el label de la ventana (si está abierta)."""
        if self._trigger_window is None:
            return
        try:
            lbl = self._trigger_window._count_label
            n = len(self.trigger_events)
            if n == 0:
                lbl.config(text="0 trigger events found", fg=theme.COLOR_DANGER)
            else:
                idx_str = (f"{self.trigger_current_idx + 1}/{n}"
                           if self.trigger_current_idx >= 0 else f"—/{n}")
                lbl.config(text=f"{n} trigger events found   ({idx_str})",
                           fg=theme.COLOR_ACCENT)
        except (AttributeError, tk.TclError):
            pass

    def _trigger_goto(self, delta):
        """Mueve el índice y centra la ventana visible en ese evento."""
        if not self.trigger_events:
            return
        n = len(self.trigger_events)
        self.trigger_current_idx = (self.trigger_current_idx + delta) % n
        ev = self.trigger_events[self.trigger_current_idx]
        t_ev = ev["t_trigger"]
        window = self.t_div.get() * H_DIVS
        # Centrar el evento en la ventana visible
        new_offset = max(0.0, t_ev - window / 2.0)
        if self.t_total is not None:
            new_offset = min(new_offset, max(0.0, self.t_total - window))
        self.t_offset.set(new_offset)
        self._update_trigger_count_label()
        self.update_plot()

    def _draw_trigger_markers(self):
        """Dibuja markers en la ventana temporal visible.

        Limpia artists previos antes de redibujar — necesario en fast path
        donde no se hace ax.clear() entre frames.
        """
        # Limpiar previos
        for a in getattr(self, "_trigger_marker_artists", []):
            try:
                a.remove()
            except Exception:
                pass
        self._trigger_marker_artists = []

        if not self.trigger_events or not self.trigger_config["show_markers"]:
            return
        t_start = self.t_offset.get()
        window = self.t_div.get() * H_DIVS
        t_end = t_start + window
        v_range = self.v_div.get() * V_DIVS

        cur = self.trigger_current_idx
        for i, ev in enumerate(self.trigger_events):
            t_ev = ev["t_trigger"]
            # Filtrar: ignorar los que queden fuera de la ventana visible
            if not (t_start <= t_ev <= t_end) and not (
                ev["t_end"] is not None
                and ev["t_start"] <= t_end and ev["t_end"] >= t_start
            ):
                continue

            is_current = (i == cur)
            color = "#ffeb3b"
            lw = 1.8 if is_current else 1.0
            alpha = 1.0 if is_current else 0.7

            # Para Width: sombreado del pulso
            if ev["t_end"] is not None:
                x1 = max(ev["t_start"] - t_start, 0)
                x2 = min(ev["t_end"] - t_start, window)
                span = self.ax.axvspan(x1, x2, color=color,
                                alpha=0.12 if is_current else 0.06,
                                zorder=1)
                self._trigger_marker_artists.append(span)

            # Línea vertical en el instante de trigger
            if t_start <= t_ev <= t_end:
                x = t_ev - t_start
                ln = self.ax.axvline(x, color=color, linewidth=lw, alpha=alpha,
                                linestyle=(0, (4, 3)), zorder=4)
                self._trigger_marker_artists.append(ln)
                # Marker "T" arriba
                txt = self.ax.text(x, v_range / 2 * 0.95, "T",
                             color=color,
                             fontsize=10 if is_current else 8,
                             fontweight="bold",
                             ha="center", va="top",
                             zorder=6)
                self._trigger_marker_artists.append(txt)

    def open_trigger_window(self):
        """Ventana de configuración del trigger (viva)."""
        if self._trigger_window is not None and self._trigger_window.winfo_exists():
            self._trigger_window.lift()
            return
        if self.time is None or not self.channels:
            self._show_toast("Load a CSV first")
            return

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("Trigger")
        win.configure(bg=theme.COLOR_BG)
        # Geometry PRIMERO (centra), chrome (transient) DESPUÉS. Si
        # transient se aplica antes de tener geometry, Tk en Windows
        # hace un deiconify transitorio que se percibe como fantasma.
        self._make_resizable_dialog(win, 420, 540)
        self._apply_window_chrome(win)

        # Bottom bar reservada PRIMERO (ver Math): asegura que los botones
        # Close/Clear queden siempre visibles aunque la ventana se reduzca.
        btns = tk.Frame(win, bg=theme.COLOR_BG)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=12)

        # ---- Enable + source ----
        header = tk.Frame(win, bg=theme.COLOR_BG)
        header.pack(fill=tk.X, padx=14, pady=(14, 6))

        enable_var = tk.BooleanVar(value=self.trigger_config["enabled"])
        # find_btn se define más abajo (en la zona de botones inferiores) y
        # lo referenciamos desde on_enable para habilitarlo/deshabilitarlo.
        # Usamos una "celda" mutable (lista) para captura por closure ya que
        # find_btn aún no existe cuando definimos on_enable.
        _find_btn_ref = []

        def on_enable():
            is_on = bool(enable_var.get())
            self.trigger_config["enabled"] = is_on
            # Habilitar/deshabilitar el botón Find acorde al estado
            if _find_btn_ref:
                try:
                    _find_btn_ref[0].config(
                        state=("normal" if is_on else "disabled"))
                except Exception:
                    pass
            # Si se desactiva, limpiar los eventos y el plot; si se activa,
            # NO disparar búsqueda — el usuario lo hará con Find. Así puede
            # ajustar todos los parámetros antes de evaluar.
            if not is_on:
                self.trigger_events = []
                self.trigger_current_idx = -1
                self._update_trigger_count_label()
                self.update_plot()

        ttk.Checkbutton(header, text="Enable trigger",
                        variable=enable_var, command=on_enable
                        ).pack(side=tk.LEFT)

        # Source
        src_row = tk.Frame(win, bg=theme.COLOR_BG)
        src_row.pack(fill=tk.X, padx=14, pady=(4, 6))
        tk.Label(src_row, text="Source:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        ch_names = [ch["name"] for ch in self.channels]
        src_idx = self.trigger_config["source"]
        if not (0 <= src_idx < len(ch_names)):
            src_idx = 0
        src_var = tk.StringVar(value=ch_names[src_idx])
        src_combo = ttk.Combobox(src_row, textvariable=src_var,
                                 values=ch_names, state="readonly", width=18)
        src_combo.pack(side=tk.LEFT)

        def on_src_change(_e=None):
            n = src_var.get()
            for i, ch in enumerate(self.channels):
                if ch["name"] == n:
                    self.trigger_config["source"] = i
                    break
            self._refresh_trigger()
        src_combo.bind("<<ComboboxSelected>>", on_src_change)

        # Type
        type_row = tk.Frame(win, bg=theme.COLOR_BG)
        type_row.pack(fill=tk.X, padx=14, pady=(4, 6))
        tk.Label(type_row, text="Type:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        type_var = tk.StringVar(value=self.trigger_config["type"])
        type_combo = ttk.Combobox(type_row, textvariable=type_var,
                                  values=["Edge", "Width"],
                                  state="readonly", width=12)
        type_combo.pack(side=tk.LEFT)

        # ---- Edge frame ----
        edge_frame = tk.LabelFrame(win, text=" Edge ", bg=theme.COLOR_BG,
                                   fg=theme.COLOR_ACCENT, bd=1,
                                   font=("Segoe UI", 9, "bold"))
        slope_var = tk.StringVar(value=self.trigger_config["slope"])
        slope_row = tk.Frame(edge_frame, bg=theme.COLOR_BG)
        slope_row.pack(fill=tk.X, padx=8, pady=(4, 4))
        tk.Label(slope_row, text="Slope:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Radiobutton(slope_row, text="Rising", variable=slope_var,
                        value="Rising").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(slope_row, text="Falling", variable=slope_var,
                        value="Falling").pack(side=tk.LEFT)

        edge_level_row = tk.Frame(edge_frame, bg=theme.COLOR_BG)
        edge_level_row.pack(fill=tk.X, padx=8, pady=(2, 6))
        tk.Label(edge_level_row, text="Level (V):", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        edge_level_var = tk.StringVar(value=f"{self.trigger_config['level']:g}")
        edge_level_entry = ttk.Entry(edge_level_row, textvariable=edge_level_var,
                                     width=12)
        edge_level_entry.pack(side=tk.LEFT)

        # ---- Width frame ----
        width_frame = tk.LabelFrame(win, text=" Width ", bg=theme.COLOR_BG,
                                    fg=theme.COLOR_ACCENT, bd=1,
                                    font=("Segoe UI", 9, "bold"))

        polarity_var = tk.StringVar(value=self.trigger_config["polarity"])
        pol_row = tk.Frame(width_frame, bg=theme.COLOR_BG)
        pol_row.pack(fill=tk.X, padx=8, pady=(4, 4))
        tk.Label(pol_row, text="Polarity:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Radiobutton(pol_row, text="Positive", variable=polarity_var,
                        value="Positive").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(pol_row, text="Negative", variable=polarity_var,
                        value="Negative").pack(side=tk.LEFT)

        cond_row = tk.Frame(width_frame, bg=theme.COLOR_BG)
        cond_row.pack(fill=tk.X, padx=8, pady=(2, 4))
        tk.Label(cond_row, text="Condition:", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        cond_var = tk.StringVar(value=self.trigger_config["condition"])
        cond_combo = ttk.Combobox(cond_row, textvariable=cond_var,
                                  values=["Width >", "Width <", "Width =",
                                          "Width ≠", "Inside", "Outside"],
                                  state="readonly", width=12)
        cond_combo.pack(side=tk.LEFT)

        w1_row = tk.Frame(width_frame, bg=theme.COLOR_BG)
        w1_row.pack(fill=tk.X, padx=8, pady=(2, 2))
        tk.Label(w1_row, text="Width 1 (s):", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        w1_var = tk.StringVar(value=f"{self.trigger_config['width1']:g}")
        w1_entry = ttk.Entry(w1_row, textvariable=w1_var, width=12)
        w1_entry.pack(side=tk.LEFT)

        w2_row = tk.Frame(width_frame, bg=theme.COLOR_BG)
        w2_row.pack(fill=tk.X, padx=8, pady=(2, 2))
        tk.Label(w2_row, text="Width 2 (s):", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        w2_var = tk.StringVar(value=f"{self.trigger_config['width2']:g}")
        w2_entry = ttk.Entry(w2_row, textvariable=w2_var, width=12)
        w2_entry.pack(side=tk.LEFT)

        w_level_row = tk.Frame(width_frame, bg=theme.COLOR_BG)
        w_level_row.pack(fill=tk.X, padx=8, pady=(2, 6))
        tk.Label(w_level_row, text="Level (V):", bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        w_level_var = tk.StringVar(value=f"{self.trigger_config['level']:g}")
        w_level_entry = ttk.Entry(w_level_row, textvariable=w_level_var,
                                  width=12)
        w_level_entry.pack(side=tk.LEFT)

        # Empaquetar: mostrar/ocultar según type
        def refresh_type_visibility():
            tp = type_var.get()
            if tp == "Edge":
                edge_frame.pack(fill=tk.X, padx=14, pady=(6, 4))
                width_frame.pack_forget()
            else:
                width_frame.pack(fill=tk.X, padx=14, pady=(6, 4))
                edge_frame.pack_forget()
            # Width 2 solo para Inside/Outside
            if tp == "Width" and cond_var.get() in ("Inside", "Outside"):
                w2_row.pack(fill=tk.X, padx=8, pady=(2, 2),
                            before=w_level_row)
            else:
                w2_row.pack_forget()

        def on_type_change(_e=None):
            self.trigger_config["type"] = type_var.get()
            refresh_type_visibility()
            self._refresh_trigger()
        type_combo.bind("<<ComboboxSelected>>", on_type_change)

        def on_cond_change(_e=None):
            self.trigger_config["condition"] = cond_var.get()
            refresh_type_visibility()
            self._refresh_trigger()
        cond_combo.bind("<<ComboboxSelected>>", on_cond_change)

        # --- Bindings de cambio para los valores ---
        def on_slope_change(*_):
            self.trigger_config["slope"] = slope_var.get()
            self._refresh_trigger()
        slope_var.trace_add("write", on_slope_change)

        def on_polarity_change(*_):
            self.trigger_config["polarity"] = polarity_var.get()
            self._refresh_trigger()
        polarity_var.trace_add("write", on_polarity_change)

        def commit_numeric(var, key):
            try:
                self.trigger_config[key] = float(var.get())
                self._refresh_trigger()
            except ValueError:
                pass

        for ent, var, key in [
            (edge_level_entry, edge_level_var, "level"),
            (w_level_entry,    w_level_var,    "level"),
            (w1_entry,         w1_var,         "width1"),
            (w2_entry,         w2_var,         "width2"),
        ]:
            ent.bind("<Return>",   lambda e, v=var, k=key: commit_numeric(v, k))
            ent.bind("<FocusOut>", lambda e, v=var, k=key: commit_numeric(v, k))

        refresh_type_visibility()

        # ---- Resultados ----
        results_frame = tk.Frame(win, bg=theme.COLOR_BG)
        results_frame.pack(fill=tk.X, padx=14, pady=(10, 4))

        count_label = tk.Label(results_frame, text="0 trigger events found",
                               bg=theme.COLOR_BG, fg=theme.COLOR_TEXT_DIM,
                               font=("Segoe UI", 10, "bold"))
        count_label.pack(side=tk.LEFT)
        win._count_label = count_label

        nav_row = tk.Frame(win, bg=theme.COLOR_BG)
        nav_row.pack(fill=tk.X, padx=14, pady=(2, 6))

        def prev_ev():
            self._trigger_goto(-1)
        def next_ev():
            self._trigger_goto(+1)

        prev_btn = tk.Button(
            nav_row, text="◀  Prev",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            activebackground=theme.COLOR_BG_HOVER,
            bd=1, relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=10, pady=4,
            highlightbackground=theme.COLOR_BORDER, highlightthickness=1,
            command=prev_ev,
        )
        prev_btn.pack(side=tk.LEFT, padx=(0, 4))
        self._add_hover_shadow(prev_btn)

        next_btn = tk.Button(
            nav_row, text="Next  ▶",
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            activebackground=theme.COLOR_BG_HOVER,
            bd=1, relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=10, pady=4,
            highlightbackground=theme.COLOR_BORDER, highlightthickness=1,
            command=next_ev,
        )
        next_btn.pack(side=tk.LEFT, padx=(0, 4))
        self._add_hover_shadow(next_btn)

        # Show markers
        markers_var = tk.BooleanVar(value=self.trigger_config["show_markers"])
        def on_markers():
            self.trigger_config["show_markers"] = bool(markers_var.get())
            self.update_plot()
            self._mark_dirty()
        ttk.Checkbutton(win, text="Show markers on plot",
                        variable=markers_var, command=on_markers
                        ).pack(anchor="w", padx=14, pady=(6, 2))

        # ---- Botones (btns ya fue packeado al principio) ----
        def do_clear():
            self.trigger_config["enabled"] = False
            enable_var.set(False)
            self.trigger_events = []
            self.trigger_current_idx = -1
            self._update_trigger_count_label()
            self.update_plot()
            # Find sigue al estado del checkbox
            try:
                find_btn.config(state="disabled")
            except Exception:
                pass

        def on_close():
            self._trigger_window = None
            win.destroy()

        def do_find():
            """Ejecuta la búsqueda con los criterios actuales. Solo se
            permite con el checkbox 'Enable trigger' activo (el propio
            botón está deshabilitado en caso contrario, pero defendemos)."""
            if not self.trigger_config.get("enabled"):
                return
            self._refresh_trigger()

        ttk.Button(btns, text="Close", command=on_close).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Clear", command=do_clear
                   ).pack(side=tk.RIGHT, padx=(0, 6))
        find_btn = ttk.Button(btns, text="Find",
                              style="Accent.TButton",
                              command=do_find)
        find_btn.pack(side=tk.RIGHT, padx=(0, 6))
        Tooltip(find_btn,
                "Run the trigger search with the current parameters.\n"
                "Only enabled while 'Enable trigger' is checked.")
        # Registrar para que on_enable pueda manipular el estado
        _find_btn_ref.append(find_btn)
        # Estado inicial coherente con el checkbox
        find_btn.config(state=("normal" if enable_var.get() else "disabled"))

        win.protocol("WM_DELETE_WINDOW", on_close)
        self._bind_escape_to_close(win, on_close=on_close)
        self._trigger_window = win

        # Compute inicial
        self._refresh_trigger()

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

        # Estilo
        style = cfg.get("style", "Line")
        if style == "Dots":
            self.ax.plot(vx, vy, linestyle="none", marker=".",
                         markersize=2, color=ch_y["color"], alpha=0.85)
        elif style == "Line + Dots":
            self.ax.plot(vx, vy, linestyle="-", marker=".",
                         linewidth=1.0, markersize=3,
                         color=ch_y["color"], alpha=0.9)
        else:
            self.ax.plot(vx, vy, linestyle="-",
                         linewidth=1.0, color=ch_y["color"])

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
        self.ax.set_xlabel(f"{ch_x['name']} (V)", color=ch_x["color"],
                           fontsize=10, fontweight="bold")
        self.ax.set_ylabel(f"{ch_y['name']} (V)", color=ch_y["color"],
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

    # =============================
    # Minimap
    # =============================
    def _redraw_minimap(self):
        """Redibuja el minimap: traza(s) decimadas + rectángulo de ventana visible."""
        c = self.minimap_canvas
        try:
            w = c.winfo_width()
            h = c.winfo_height()
        except tk.TclError:
            return
        if w <= 1 or h <= 1:
            return
        c.delete("all")

        # En modo XY el minimap no tiene sentido: ocultar contenido
        if self.xy_config.get("enabled"):
            c.create_text(w / 2, h / 2, text="XY mode — minimap disabled",
                          fill=theme.COLOR_TEXT_DIM, font=("Segoe UI", 9, "italic"))
            return

        if self.time is None or not self.channels or self.t_total <= 0:
            c.create_text(w / 2, h / 2, text="No data",
                          fill=theme.COLOR_TEXT_DIM, font=("Segoe UI", 9, "italic"))
            return

        # Decimación a ancho del canvas
        N = max(1, int(w))
        n_samples = len(self.time)
        if n_samples > N:
            step = n_samples // N
            idxs = np.arange(0, n_samples, step)
        else:
            idxs = np.arange(n_samples)
        t_vis = self.time[idxs]

        # Encuadre vertical: NORMALIZAR cada canal a su propio rango para que
        # señales de amplitudes muy distintas (5V vs 800V) se vean igual de
        # bien. Cada canal ocupa toda la altura del minimap.
        margin_y = 4
        def map_x(t):
            return (t / self.t_total) * (w - 2) + 1

        # Dibujar canales: primero los no activos (gris), luego el activo en su color
        order = [i for i in range(len(self.channels)) if i != self.active_channel_idx]
        order.append(self.active_channel_idx)
        for i in order:
            ch = self.channels[i]
            if not ch["visible"].get():
                continue
            v = ch["voltage"][idxs]
            v_lo = float(np.min(v))
            v_hi = float(np.max(v))
            if v_hi == v_lo:
                v_hi = v_lo + 1.0
            is_active = (i == self.active_channel_idx)
            color = ch["color"] if is_active else "#666666"
            width = 1.6 if is_active else 1.0
            # Cada canal se normaliza a [0..1] y ocupa toda la altura
            pts = []
            for k in range(len(v)):
                norm = (v[k] - v_lo) / (v_hi - v_lo)
                y = h - margin_y - norm * (h - 2 * margin_y)
                pts.append(map_x(t_vis[k]))
                pts.append(y)
            if len(pts) >= 4:
                c.create_line(*pts, fill=color, width=width, smooth=False)

        # Rectángulo de ventana visible: solo cuando hay zoom (ventana
        # visible es menor que la traza total).
        t_start = self.t_offset.get()
        window = self.t_div.get() * H_DIVS
        if window < self.t_total * 0.999:
            x1 = map_x(t_start)
            x2 = map_x(min(t_start + window, self.t_total))
            c.create_rectangle(x1, 1, x2, h - 1,
                               outline=theme.COLOR_ACCENT, width=1.5,
                               fill=theme.COLOR_ACCENT, stipple="gray25")

    def _minimap_click_to_time(self, x_pixel):
        """Convierte coordenada X del canvas a tiempo absoluto."""
        try:
            w = self.minimap_canvas.winfo_width()
        except tk.TclError:
            return 0.0
        if w <= 2 or self.t_total <= 0:
            return 0.0
        frac = (x_pixel - 1) / (w - 2)
        frac = max(0.0, min(1.0, frac))
        return frac * self.t_total

    def _minimap_rect_bounds_px(self):
        """Devuelve (x1, x2) en pixels del rectángulo visible actual."""
        try:
            w = self.minimap_canvas.winfo_width()
        except tk.TclError:
            return (0, 0)
        if self.t_total <= 0:
            return (0, 0)
        t_start = self.t_offset.get()
        window = self.t_div.get() * H_DIVS
        x1 = t_start / self.t_total * (w - 2) + 1
        x2 = min(t_start + window, self.t_total) / self.t_total * (w - 2) + 1
        return (x1, x2)

    def _on_minimap_press(self, event):
        if self.time is None or self.xy_config.get("enabled"):
            return
        x1, x2 = self._minimap_rect_bounds_px()
        # Si clicaste DENTRO del rectángulo: preparar drag (pan)
        if x1 <= event.x <= x2:
            self._minimap_dragging = True
            t_click = self._minimap_click_to_time(event.x)
            self._minimap_drag_offset = t_click - self.t_offset.get()
        else:
            # Clic fuera: centrar ventana en ese punto
            self._minimap_dragging = False
            window = self.t_div.get() * H_DIVS
            t_click = self._minimap_click_to_time(event.x)
            new_offset = max(0.0, min(t_click - window / 2.0,
                                      self.t_total - window))
            self.t_offset.set(new_offset)
            try:
                self.h_slider.set(new_offset)
            except Exception:
                pass
            self.update_plot()

    def _on_minimap_drag(self, event):
        if not self._minimap_dragging or self.time is None:
            return
        t_at_cursor = self._minimap_click_to_time(event.x)
        new_offset = t_at_cursor - self._minimap_drag_offset
        window = self.t_div.get() * H_DIVS
        new_offset = max(0.0, min(new_offset, self.t_total - window))
        self.t_offset.set(new_offset)
        try:
            self.h_slider.set(new_offset)
        except Exception:
            pass
        self.update_plot()

    def _on_minimap_release(self, event):
        self._minimap_dragging = False

    def _on_minimap_wheel(self, event):
        """Windows/Mac: event.delta ±120 por tick."""
        direction = "down" if event.delta > 0 else "up"  # rueda arriba = zoom in (t_div ↓)
        self._step_t_div(direction)

    def _on_minimap_wheel_linux(self, event, sign):
        """Linux: Button-4 = scroll up, Button-5 = scroll down."""
        direction = "down" if sign > 0 else "up"
        self._step_t_div(direction)

    def _open_save_image_dialog(self):
        """Ventana fija con opciones (cursores / measurements / escala Y)
        y luego guarda."""
        if self.time is None or not self.channels:
            self._show_toast("Nothing to save (no data loaded)")
            return

        cursors_available = (
            self._cursors_vertical_active() or self._cursors_horizontal_active()
        )
        meas_available = bool(self.active_measurements)

        win = tk.Toplevel(self.root)
        win.withdraw()  # anti-flash
        win.title("Save image")
        win.configure(bg=theme.COLOR_BG)
        # Geometry PRIMERO, transient DESPUÉS (evita fantasma).
        self._make_resizable_dialog(win, 360, 260)
        win.transient(self.root)
        win.grab_set()

        tk.Label(
            win, text="Image options",
            bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack(padx=12, pady=(14, 8), anchor="w")

        var_cursors = tk.BooleanVar(value=cursors_available)
        var_meas    = tk.BooleanVar(value=meas_available)

        cb_cursors = ttk.Checkbutton(
            win, text="Include cursors" + ("" if cursors_available else "  (none active)"),
            variable=var_cursors
        )
        cb_cursors.pack(anchor="w", padx=18, pady=2)
        if not cursors_available:
            cb_cursors.state(["disabled"])

        cb_meas = ttk.Checkbutton(
            win, text="Include measurements" + ("" if meas_available else "  (none)"),
            variable=var_meas
        )
        cb_meas.pack(anchor="w", padx=18, pady=2)
        if not meas_available:
            cb_meas.state(["disabled"])

        # Annotations
        annot_available = bool(self.annotations)
        var_annot = tk.BooleanVar(value=annot_available)
        cb_annot = ttk.Checkbutton(
            win, text="Include annotations" + ("" if annot_available else "  (none)"),
            variable=var_annot
        )
        cb_annot.pack(anchor="w", padx=18, pady=2)
        if not annot_available:
            cb_annot.state(["disabled"])

        # Selector de canal para escala vertical
        tk.Label(
            win, text="Show Y-scale ticks for channel:",
            bg=theme.COLOR_BG, fg=theme.COLOR_TEXT,
            font=("Segoe UI", 9)
        ).pack(anchor="w", padx=18, pady=(10, 2))

        NONE_LABEL = "(none — generic grid)"
        choices = [NONE_LABEL] + [ch["name"] for ch in self.channels]
        var_yscale = tk.StringVar(value=NONE_LABEL)
        combo = ttk.Combobox(
            win, textvariable=var_yscale,
            values=choices, state="readonly"
        )
        combo.pack(fill=tk.X, padx=18, pady=(0, 6))

        btns = tk.Frame(win, bg=theme.COLOR_BG)
        btns.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=12)

        def on_cancel():
            win.destroy()

        def on_save():
            include_cursors = bool(var_cursors.get()) and cursors_available
            include_meas    = bool(var_meas.get()) and meas_available
            include_annot   = bool(var_annot.get()) and annot_available
            sel = var_yscale.get()
            if sel == NONE_LABEL:
                yscale_ch_idx = None
            else:
                # nombres pueden repetirse tras rename: buscamos el primero
                yscale_ch_idx = next(
                    (i for i, ch in enumerate(self.channels) if ch["name"] == sel),
                    None
                )
            win.destroy()
            self._save_image_as(include_cursors, include_meas, yscale_ch_idx, include_annot)

        ttk.Button(btns, text="Cancel", command=on_cancel).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Save...", style="Accent.TButton",
                   command=on_save).pack(side=tk.RIGHT, padx=(0, 6))

    def _save_image_as(self, include_cursors, include_meas, yscale_ch_idx=None,
                       include_annot=False):
        """Pide ruta y construye una figura clara (fondo blanco, grid negro)
        con los canales, leyenda de escalas y opcionalmente cursores/medidas."""
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save oscilloscope image",
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"),
                       ("JPEG image", "*.jpg;*.jpeg"),
                       ("PDF", "*.pdf"),
                       ("SVG", "*.svg")],
        )
        if not path:
            return

        try:
            fig = self._build_export_figure(include_cursors, include_meas, yscale_ch_idx,
                                            include_annot)
            fig.savefig(path, dpi=150, facecolor="white", bbox_inches="tight")
            plt.close(fig)
            self._show_toast(f"Saved: {path.split('/')[-1].split(chr(92))[-1]}")
        except Exception as e:
            self._show_toast(f"Save failed: {e}", duration_ms=2500)

    def _build_export_figure(self, include_cursors, include_meas, yscale_ch_idx=None,
                             include_annot=False):
        """Construye una figura matplotlib con look 'paper': fondo blanco,
        grid negro, canales con sus colores, leyenda de escalas, flechitas
        de cada canal y opcionalmente ticks Y en unidades reales del canal
        seleccionado."""
        from matplotlib.figure import Figure

        fig = Figure(figsize=(12, 7), facecolor="white")
        ax = fig.add_subplot(111)
        ax.set_facecolor("white")

        t_div = self.t_div.get()
        v_div_active = self.v_div.get()
        t_start = self.t_offset.get()
        window = t_div * H_DIVS
        t_end = t_start + window
        mask = (self.time >= t_start) & (self.time <= t_end)

        # Ejes: centramos en 0, mismas divisiones que en pantalla
        v_range = V_DIVS * v_div_active
        ax.set_xlim(0, window)
        ax.set_ylim(-v_range / 2, v_range / 2)

        # Grid negro con líneas finas
        ax.grid(True, which="both", color="#000000", alpha=0.25,
                linewidth=0.6, linestyle="-")

        # Ticks en divisiones
        xticks = np.linspace(0, window, H_DIVS + 1)
        yticks = np.linspace(-v_range / 2, v_range / 2, V_DIVS + 1)
        ax.set_xticks(xticks)
        ax.set_yticks(yticks)
        # Etiquetas de tiempo REAL: posición del tick (x) + offset del
        # viewport (t_start) + origen del CSV (time_origin).
        t_o = self._t_display_origin()
        ax.set_xticklabels([format_si(t_start + x + t_o, "s", sig_figs=3)
                            for x in xticks],
                           fontsize=8, color="black", rotation=0)

        # Ticks Y: si hay canal elegido, valores reales en "V" de ese canal
        if yscale_ch_idx is not None and 0 <= yscale_ch_idx < len(self.channels):
            sel = self.channels[yscale_ch_idx]
            # y_screen = (v - offset_sel) / v_div_sel · v_div_active
            # → v_real = y_screen / v_div_active · v_div_sel + offset_sel
            yreal = yticks / v_div_active * sel["v_div"] + sel["v_offset"]
            y_unit = sel.get("unit", DEFAULT_UNIT)
            ax.set_yticklabels(
                [format_si(v, y_unit, sig_figs=3) for v in yreal],
                fontsize=8, color=sel["color"]
            )
        else:
            ax.set_yticklabels([""] * len(yticks))
        for spine in ax.spines.values():
            spine.set_color("black")
            spine.set_linewidth(1.0)

        # Dibujar canales + flechita de 0V con el nombre
        legend_lines = []
        order = [i for i in range(len(self.channels)) if i != self.active_channel_idx]
        order.append(self.active_channel_idx)
        t_range = window
        v_range_half = v_range / 2
        for i in order:
            ch = self.channels[i]
            if not ch["visible"].get():
                continue
            v = ch["voltage"][mask]
            divs = (v - ch["v_offset"]) / ch["v_div"]
            y_plot = divs * v_div_active
            is_active = (i == self.active_channel_idx)
            ax.plot(self.time[mask] - t_start, y_plot,
                    color=ch["color"],
                    linewidth=1.2 if is_active else 1.0)
            legend_lines.append(
                f"{ch['name']}: {format_si(ch['v_div'], ch.get('unit', DEFAULT_UNIT))}/div"
            )

            # Flechita indicando 0V del canal, a la izquierda del eje
            y_gnd = (-ch["v_offset"] / ch["v_div"]) * v_div_active
            if -v_range_half <= y_gnd <= v_range_half:
                ax.annotate(
                    f"▶ {ch['name']}",
                    xy=(0, y_gnd),
                    xytext=(-t_range * 0.015, y_gnd),
                    ha="right", va="center",
                    color=ch["color"],
                    fontsize=9,
                    fontweight="bold" if is_active else "normal",
                    annotation_clip=False,
                )

        # Título/escalas arriba
        title = f"T/div: {format_si(t_div, 's')}   |   "
        title += "   ".join(legend_lines) if legend_lines else "(no visible channels)"
        ax.set_title(title, fontsize=10, color="black", loc="left", pad=10)

        # Cursores
        if include_cursors:
            if self._cursors_vertical_active():
                for x in self.cursor_x:
                    if t_start <= x <= t_end:
                        ax.axvline(x - t_start, color="#444444",
                                   linestyle=(0, (5, 5)), linewidth=1.0)
                if len(self.cursor_x) == 2:
                    dt = abs(self.cursor_x[1] - self.cursor_x[0])
                    freq_txt = format_si(1.0 / dt, "Hz") if dt > 0 else "—"
                    ax.text(0.01, -0.12,
                            f"Δt = {format_si(dt, 's')}    1/Δt = {freq_txt}",
                            transform=ax.transAxes, fontsize=9, color="black")

            if self._cursors_horizontal_active():
                for y in self.cursor_y:
                    ax.axhline(y, color="#444444",
                               linestyle=(0, (5, 5)), linewidth=1.0)
                if len(self.cursor_y) == 2:
                    dv = abs(self.cursor_y[1] - self.cursor_y[0])
                    ax.text(0.35, -0.12,
                            f"ΔV = {format_si(dv, self._active_unit())}",
                            transform=ax.transAxes, fontsize=9, color="black")

        # Measurements: tabla compacta debajo
        if include_meas and self.active_measurements:
            results = self.compute_measurements()
            lines = []
            for m in results:
                name_ch = m.get("channel_name", "?")
                val = m.get("value")
                unit = m.get("unit", "")
                if val is None:
                    val_str = "—"
                elif unit:
                    val_str = format_si(val, unit)
                else:
                    val_str = f"{val:.4g}"
                lines.append(f"{name_ch} · {m.get('name', '?')}: {val_str}")
            if lines:
                txt = "   |   ".join(lines)
                fig.text(0.5, 0.01, txt, ha="center", fontsize=8, color="black")

        # Annotations (strokes + texto)
        if include_annot and self.annotations:
            for a in self.annotations:
                if a["type"] == "stroke":
                    pts = a["points"]
                    if len(pts) < 2:
                        continue
                    xs = [p[0] - t_start for p in pts]
                    ys = [p[1] for p in pts]
                    if max(xs) < 0 or min(xs) > window:
                        continue
                    ax.plot(xs, ys, color=a["color"],
                            linewidth=a.get("width", 2),
                            solid_capstyle="round",
                            solid_joinstyle="round",
                            zorder=5)
                else:  # text
                    t, v = a["pos"]
                    if not (t_start <= t <= t_end):
                        continue
                    ax.text(t - t_start, v, a["content"],
                            color=a["color"], fontsize=a.get("size", 10),
                            fontfamily="Consolas",
                            verticalalignment="center",
                            zorder=5)

        fig.tight_layout(rect=(0, 0.04 if include_meas else 0, 1, 1))
        return fig

    def _copy_plot_to_clipboard(self):
        """Copia la imagen actual del plot al portapapeles como DIB
        (CF_DIB). Solo Windows; en otras plataformas avisa con un toast.

        Sin Pillow: monta la cabecera BITMAPINFOHEADER a mano y mueve
        los pixels BGRA al clipboard via ctypes. Top-down (biHeight
        negativo) para no tener que voltear filas.

        Ojo: si SetClipboardData tiene éxito Windows toma posesión del
        handle de GlobalAlloc — NO se libera. Si falla cualquier paso
        previo, sí hay que GlobalFree para no leakear."""
        if not sys.platform.startswith("win"):
            self._show_toast("Clipboard image only on Windows")
            return
        if self.time is None or not self.channels:
            self._show_toast("Nothing to copy (no data loaded)")
            return

        try:
            import ctypes
            from ctypes import wintypes
        except Exception as e:
            self._show_toast(f"Clipboard unavailable: {e}", duration_ms=2200)
            return

        try:
            self.canvas.draw()
            w, h = self.canvas.get_width_height()
            rgba = np.asarray(self.canvas.buffer_rgba())
            # GDI espera BGRA en 32-bit BI_RGB.
            bgra = np.ascontiguousarray(rgba[:, :, [2, 1, 0, 3]]).tobytes()
        except Exception as e:
            self._show_toast(f"Render failed: {e}", duration_ms=2200)
            return

        DIB_HEADER_SIZE = 40
        BI_RGB = 0
        GMEM_MOVEABLE = 0x0002
        CF_DIB = 8

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize",          ctypes.c_uint32),
                ("biWidth",         ctypes.c_int32),
                ("biHeight",        ctypes.c_int32),
                ("biPlanes",        ctypes.c_uint16),
                ("biBitCount",      ctypes.c_uint16),
                ("biCompression",   ctypes.c_uint32),
                ("biSizeImage",     ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed",       ctypes.c_uint32),
                ("biClrImportant",  ctypes.c_uint32),
            ]

        hdr = BITMAPINFOHEADER()
        hdr.biSize = DIB_HEADER_SIZE
        hdr.biWidth = w
        hdr.biHeight = -h            # top-down
        hdr.biPlanes = 1
        hdr.biBitCount = 32
        hdr.biCompression = BI_RGB
        hdr.biSizeImage = w * h * 4

        total_size = DIB_HEADER_SIZE + hdr.biSizeImage

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE

        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, total_size)
        if not h_mem:
            self._show_toast("Clipboard alloc failed", duration_ms=2200)
            return

        try:
            ptr = kernel32.GlobalLock(h_mem)
            if not ptr:
                kernel32.GlobalFree(h_mem)
                self._show_toast("Clipboard lock failed", duration_ms=2200)
                return
            ctypes.memmove(ptr, ctypes.byref(hdr), DIB_HEADER_SIZE)
            ctypes.memmove(ptr + DIB_HEADER_SIZE, bgra, hdr.biSizeImage)
            kernel32.GlobalUnlock(h_mem)

            if not user32.OpenClipboard(None):
                kernel32.GlobalFree(h_mem)
                self._show_toast("Clipboard busy", duration_ms=2200)
                return
            try:
                user32.EmptyClipboard()
                if not user32.SetClipboardData(CF_DIB, h_mem):
                    kernel32.GlobalFree(h_mem)
                    user32.CloseClipboard()
                    self._show_toast("Clipboard write failed", duration_ms=2200)
                    return
                # Éxito: Windows posee el handle, NO liberar.
                h_mem = None
            finally:
                user32.CloseClipboard()
            self._show_toast("Copied to clipboard", duration_ms=1200)
        except Exception as e:
            if h_mem:
                try:
                    kernel32.GlobalFree(h_mem)
                except Exception:
                    pass
            self._show_toast(f"Clipboard error: {e}", duration_ms=2200)

    def _show_plot_context_menu(self, event):
        """Menú contextual al hacer click derecho sobre el plot."""
        # Coordenadas en pantalla (absolutas)
        try:
            x_root = self.root.winfo_pointerx()
            y_root = self.root.winfo_pointery()
        except Exception:
            return

        menu = tk.Menu(
            self.root, tearoff=0,
            bg=theme.COLOR_BG_ALT, fg=theme.COLOR_TEXT,
            activebackground=theme.COLOR_ACCENT, activeforeground=theme.COLOR_TEXT_ON_ACCENT,
            bd=0,
        )
        menu.add_command(
            label="Save image as...",
            command=self._open_save_image_dialog,
        )
        if sys.platform.startswith("win"):
            menu.add_command(
                label="Copy image to clipboard",
                command=self._copy_plot_to_clipboard,
            )
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()



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

    def _t_display_origin(self):
        """Devuelve el offset de tiempo a sumar a las coordenadas internas
        para producir las etiquetas visibles del eje X y cursores.

        Si time_show_absolute=True (default): devuelve self.time_origin →
        los ticks muestran tiempos absolutos del CSV original (con pre-trigger
        si lo había, igual que un osciloscopio físico).

        Si False: devuelve 0.0 → el primer punto del CSV es t=0.

        Llama a este helper SIEMPRE en lugar de leer self.time_origin
        directamente para que el toggle de Appearance afecte de forma
        consistente.
        """
        try:
            return self.time_origin if self.time_show_absolute.get() else 0.0
        except Exception:
            return self.time_origin

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
    def build_plot(self, frame):
        self.fig, self.ax = plt.subplots(figsize=(9, 5))

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
            y_tick_color = active["color"]
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
    def is_click_on_trace(self, event):
        """True si el click cae cerca de ALGÚN canal visible."""
        return self.find_channel_under_click(event) is not None

    def find_channel_under_click(self, event):
        """Devuelve el índice del canal visible cuyo trazo pasa más cerca
        del punto clicado, o None si ninguno está dentro de tolerancia.
        Usa las coordenadas de pantalla ya reescaladas (event.ydata está
        en 'V equivalentes del canal activo'). """
        if self.time is None or not self.channels:
            return None
        if event.xdata is None or event.ydata is None:
            return None

        # Tiempo real del CSV
        click_time = event.xdata + self.t_offset.get()
        idx = int(np.searchsorted(self.time, click_time))
        idx = max(0, min(idx, len(self.time) - 1))

        v_div_active = self.v_div.get()
        # Tolerancia: 0.5 divisiones (en coordenadas de pantalla)
        tol_screen = v_div_active * 0.5

        best_i = None
        best_dist = float("inf")
        for i, ch in enumerate(self.channels):
            if not ch["visible"].get():
                continue
            # Reescalar el valor del canal a coordenadas de pantalla
            v_raw = ch["voltage"][idx]
            y_screen = (v_raw - ch["v_offset"]) / ch["v_div"] * v_div_active
            dist = abs(event.ydata - y_screen)
            if dist < tol_screen and dist < best_dist:
                best_dist = dist
                best_i = i
        return best_i

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


    def _sync_active_channel_vars(self):
        """Copia v_div y v_offset del canal activo a los DoubleVar globales.
        Así cursores, FFT, medidas y sliders siguen usando self.v_div/self.v_offset
        sin saber nada del multi-canal."""
        if not self.channels:
            return
        ch = self.channels[self.active_channel_idx]
        self._updating_display = True
        self.v_div.set(ch["v_div"])
        self.v_div_display.set(format_si(ch["v_div"], self._active_unit()))
        self._updating_display = False
        self.v_offset.set(ch["v_offset"])
        try:
            # IMPORTANTE: primero reconfigurar el rango del slider al nuevo
            # canal (v_div cambia → rango cambia). Si no, un set() con valor
            # fuera del rango antiguo dispararía un callback con valor clampado
            # y descuadra el canal.
            self.configure_v_slider()
            # Flag para que el callback del slider no re-escriba el canal
            self._syncing_active = True
            # Slider invertido: derecha = traza arriba = offset negativo
            self.v_slider.set(-ch["v_offset"])
            self._syncing_active = False
        except Exception:
            self._syncing_active = False
        # Apuntar self.voltage al canal activo para que FFT/medidas funcionen
        self.voltage = ch["voltage"]


    def _persist_active_channel_vars(self):
        """Vuelca self.v_div y self.v_offset al canal activo.
        Se llama tras cambios del usuario en V/div o drag vertical."""
        if not self.channels:
            return
        ch = self.channels[self.active_channel_idx]
        ch["v_div"]    = self.v_div.get()
        ch["v_offset"] = self.v_offset.get()
        # No marcamos dirty aquí explícitamente: las tk.Vars v_div/v_offset
        # tienen trace que ya lo hace. Esta función solo vuelca a dict.


    def _hotkey_guard(self):
        """Devuelve True si el foco está en un widget que captura texto
        (Entry/Spinbox/Text). Los handlers de atajos globales llaman a esto
        al principio Y si devuelve True, abortan — así escribir en V/div,
        nombres de canal, expresiones math, etc. no dispara atajos."""
        try:
            w = self.root.focus_get()
        except Exception:
            return False
        return isinstance(
            w, (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox, tk.Text)
        )

    def _on_active_channel_hotkey(self, idx):
        """Atajo numérico (1-9): cambia el canal activo si hay canal en ese
        índice. Ignora la pulsación si el foco está en un widget de texto
        para no robar dígitos mientras se escribe."""
        if self._hotkey_guard():
            return
        if not (0 <= idx < len(self.channels)):
            return
        # Mismo canal: no-op silencioso (evita redraw + dirty espurio).
        if idx == self.active_channel_idx:
            return
        self.set_active_channel(idx)
        try:
            ch_name = self.channels[idx].get("name", f"CH{idx + 1}")
            self._show_toast(f"Active: {ch_name}", duration_ms=900)
        except Exception:
            pass

    # =====================
    # Atajos globales v1.30 — handlers
    # =====================
    def _hotkey_toggle_cursors(self):
        """C: toggle cursores horizontales+verticales del canal activo."""
        if self._hotkey_guard():
            return
        try:
            self.cursors_enabled.set(not bool(self.cursors_enabled.get()))
            self.on_cursors_toggle()
            self._show_toast(
                f"Cursors: {'on' if self.cursors_enabled.get() else 'off'}",
                duration_ms=800,
            )
        except Exception:
            pass

    def _hotkey_open_unit_picker(self):
        """U: abrir el picker de unidad del canal activo."""
        if self._hotkey_guard():
            return
        if not self.channels:
            return
        try:
            self._open_unit_picker_dialog(self.active_channel_idx)
        except Exception:
            pass

    def _hotkey_snap_gnd(self):
        """G: snap del 0V del canal activo a la división vertical más cercana."""
        if self._hotkey_guard():
            return
        if not self.channels:
            return
        try:
            self._snap_channel_gnd_to_grid(self.active_channel_idx)
        except Exception:
            pass

    def _hotkey_center_channel(self):
        """0: centrar el canal activo en el origen (v_offset = 0)."""
        if self._hotkey_guard():
            return
        if not self.channels:
            return
        try:
            ch = self.channels[self.active_channel_idx]
            ch["v_offset"] = 0.0
            self.v_offset.set(0.0)
            try:
                self.v_slider.set(0.0)
            except Exception:
                pass
            self.update_plot()
        except Exception:
            pass

    def _hotkey_toggle_channel_visibility(self, idx):
        """Ctrl+1..9: toggle visibilidad del canal en el índice dado."""
        if self._hotkey_guard():
            return
        if not (0 <= idx < len(self.channels)):
            return
        try:
            var = self.channels[idx].get("visible")
            if var is not None:
                var.set(not bool(var.get()))
                # var.trace dispara update_plot + dirty.
                self._show_toast(
                    f"{self.channels[idx].get('name', f'CH{idx+1}')}: "
                    f"{'on' if var.get() else 'off'}",
                    duration_ms=700,
                )
        except Exception:
            pass

    def _hotkey_pan_v(self, direction):
        """Flecha arr/abj: mueve el canal activo verticalmente 0.1 div.
        direction = +1 (arriba) o -1 (abajo).
        Convención: flecha arriba → traza sube → v_offset disminuye
        (porque y_screen = (v_real - v_offset)/v_div, subir v_offset
        baja la traza)."""
        if self._hotkey_guard():
            return
        if not self.channels:
            return
        try:
            ch = self.channels[self.active_channel_idx]
            step = ch["v_div"] * 0.1 * direction
            ch["v_offset"] -= step
            self.v_offset.set(ch["v_offset"])
            try:
                self.v_slider.set(-ch["v_offset"])
            except Exception:
                pass
            self.update_plot()
        except Exception:
            pass

    def _hotkey_pan_h(self, direction):
        """Flecha izq/der: mueve t_offset global 0.1 div de tiempo.
        direction = -1 (izq) o +1 (der)."""
        if self._hotkey_guard():
            return
        try:
            step = self.t_div.get() * 0.1 * direction
            self.t_offset.set(self.t_offset.get() + step)
            try:
                self.h_slider.set(self.t_offset.get())
            except Exception:
                pass
            self.update_plot()
        except Exception:
            pass

    def _hotkey_step_v_div(self, direction):
        """Shift+arr/abj: paso discreto de V/div del canal activo.
        direction = -1 (más zoom, V/div más pequeño) o +1 (menos zoom)."""
        if self._hotkey_guard():
            return
        if not self.channels:
            return
        try:
            current = self.v_div.get()
            if self.fine_mode.get():
                # En fine mode: paso de 0.1
                step = 0.1 * (-direction)
                new_val = max(0.1, round(current + step, 2))
            else:
                # Buscar el siguiente valor discreto en V_DIV_VALUES
                idx = min(range(len(V_DIV_VALUES)),
                          key=lambda i: abs(V_DIV_VALUES[i] - current))
                new_idx = max(0, min(len(V_DIV_VALUES) - 1, idx + direction))
                new_val = V_DIV_VALUES[new_idx]
            if abs(new_val - current) < 1e-15:
                return
            self.v_div.set(new_val)
            self._updating_display = True
            self.v_div_display.set(format_si(new_val, self._active_unit()))
            self._updating_display = False
            self.on_v_div_change()
        except Exception:
            pass

    def _hotkey_move_cursor(self, axis, idx, direction):
        """Mueve un cursor por teclado.
        axis = 'x' (tiempo) o 'y' (voltaje del canal activo).
        idx = 0 (cursor 1) o 1 (cursor 2).
        direction = -1 / +1.
        Step = 0.05 div (más fino que el pan general de 0.1).
        Si los cursores están deshabilitados, los activa antes de mover."""
        if self._hotkey_guard():
            return
        if not self.channels:
            return
        try:
            # Auto-activar cursores si están off (mejor UX: el usuario que
            # pulsa el atajo claramente quiere usarlos).
            if not bool(self.cursors_enabled.get()):
                self.cursors_enabled.set(True)
                self.on_cursors_toggle()
            if axis == "x":
                if idx >= len(self.cursor_x):
                    return
                step = self.t_div.get() * 0.05 * direction
                self.cursor_x[idx] = float(self.cursor_x[idx]) + step
            else:  # axis == "y"
                if idx >= len(self.cursor_y):
                    return
                # Step en voltaje del canal activo (el cursor Y es relativo
                # a la escala del activo, igual que el drag con el ratón).
                ch = self.channels[self.active_channel_idx]
                step = ch["v_div"] * 0.05 * direction
                self.cursor_y[idx] = float(self.cursor_y[idx]) + step
            self.update_plot()
            self._mark_dirty()
        except Exception:
            pass

    def _hotkey_step_t_div(self, direction):
        """Ctrl+arr/abj: paso discreto de T/div global.
        direction = -1 (más zoom, ms→µs) o +1 (menos zoom)."""
        if self._hotkey_guard():
            return
        try:
            current = self.t_div.get()
            # Buscar el vecino canónico en TIME_DIV_VALUES.
            if direction > 0:
                target = next((v for v in TIME_DIV_VALUES if v > current * 1.0001),
                              TIME_DIV_VALUES[-1])
            else:
                target = next((v for v in reversed(TIME_DIV_VALUES)
                               if v < current * 0.9999), TIME_DIV_VALUES[0])
            if abs(target - current) < 1e-15:
                return
            self._updating_display = True
            self.t_div_display.set(format_si(target, "s"))
            self._updating_display = False
            self.t_div.set(target)
            self.on_time_div_change()
        except Exception:
            pass

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


    # -----------------------------
    def auto_fit(self):
        """Ajusta T/div, y para cada canal su V/div y v_offset."""
        if self.time is None or not self.channels:
            return

        # Horizontal: mostrar todo el CSV
        self.auto_time_div()

        # Vertical: para CADA canal, ajustar su v_div y v_offset
        for ch in self.channels:
            v = ch["voltage"]
            if len(v) == 0:
                continue
            vmin = float(np.min(v))
            vmax = float(np.max(v))
            vpp = vmax - vmin

            # v_div que deje la señal ocupando ~8 divisiones
            if vpp > 0:
                target = vpp / 8.0
                chosen = V_DIV_VALUES[-1]
                for vd in V_DIV_VALUES:
                    if vd >= target:
                        chosen = vd
                        break
                ch["v_div"] = chosen
            else:
                ch["v_div"] = 1.0

            # Offset al punto medio
            # Offset al punto medio de la señal (para centrarla en pantalla)
            # Modelo: y_screen = (v - offset)/v_div * v_div_activo
            # Con offset = mean(v), la señal queda centrada en 0.
            ch["v_offset"] = (vmax + vmin) / 2.0

        # Volcar del canal activo a las vars globales
        self._sync_active_channel_vars()

        # Sincronizar sliders (auto_fit sí resetea offset a 0)
        self.t_offset.set(0.0)
        self.configure_slider()
        self.configure_v_slider()
        # El slider va invertido: derecha=+ = traza arriba = offset negativo
        self.v_slider.set(-self.v_offset.get())

        self._sync_scale_displays()
        self.update_plot()

    # -----------------------------
    def _sync_scale_displays(self):
        """Actualiza los StringVar de display a partir de los valores numéricos."""
        self._updating_display = True
        self.v_div_display.set(format_si(self.v_div.get(), self._active_unit()))
        self.t_div_display.set(format_si(self.t_div.get(), "s"))
        self._updating_display = False

    # -----------------------------
    def auto_v_div(self):
        """Auto-fit vertical del canal ACTIVO. Mantiene compatibilidad."""
        if not self.channels:
            return
        ch = self.channels[self.active_channel_idx]
        v = ch["voltage"]
        if len(v) == 0:
            return
        vmin = float(np.min(v))
        vmax = float(np.max(v))
        vpp = vmax - vmin
        if vpp <= 0:
            return
        target = vpp / 8.0
        chosen = V_DIV_VALUES[-1]
        for vd in V_DIV_VALUES:
            if vd >= target:
                chosen = vd
                break
        ch["v_div"] = chosen
        self.v_div.set(chosen)

    # -----------------------------
    def auto_time_div(self):
        """Usa el valor exacto t_total/H_DIVS para que la traza ocupe TODO
        el ancho del grid. Este valor no suele ser canónico; el spinbox lo
        acepta igualmente, y al pulsar una flecha se salta al canónico
        vecino."""
        if self.t_total <= 0:
            return
        exact = self.t_total / H_DIVS
        self.t_div.set(exact)
        self.t_offset.set(0.0)
        try:
            self._updating_display = True
            self.t_div_display.set(format_si(exact, "s"))
            self._updating_display = False
        except Exception:
            pass
        self._prev_t_div = exact

    # -----------------------------
    def configure_slider(self):
        window = self.t_div.get() * H_DIVS
        max_offset = max(0.0, self.t_total - window)

        # Anti-ruido de coma flotante: si el max_offset es ridículamente
        # pequeño comparado con la ventana visible, es porque la captura
        # cabe entera (o casi) en pantalla y el residuo viene de imprecisión
        # de float (t_total / H_DIVS no siempre da un t_div canónico,
        # y al combinar valores aparecen sobras de 1e-8 a 1e-7 que el slider
        # interpreta como rango utilizable). Eso provoca que tras un zoom
        # + auto fit el slider se quede mal alineado y los ticks del eje X
        # muestren "17.1 ns" o similar en vez de "0 s".
        #
        # Umbral: usamos t_div como referencia, no window. Cualquier offset
        # por debajo de t_div * 1e-4 (= 0.01 % de una división) es ruido
        # de float y lo tratamos como cero estricto. Con t/div = 1 ms eso
        # son 100 ns, suficiente para absorber cualquier error acumulado
        # por las operaciones t_total - canonical_t_div * H_DIVS.
        t_div = self.t_div.get()
        noise_threshold = t_div * 1e-4
        if max_offset < noise_threshold:
            max_offset = 0.0

        self.h_slider.config(from_=0, to=max_offset)
        # Mantener el offset actual (solo clamp si se sale)
        cur = self.t_offset.get()
        cur = max(0.0, min(cur, max_offset))
        # Mismo snap a 0 estricto si el cur queda en territorio "ruido".
        if abs(cur) < noise_threshold:
            cur = 0.0
        self.t_offset.set(cur)
        self.h_slider.set(cur)

    # -----------------------------
    def configure_v_slider(self):
        """Configura el rango del slider vertical en función de V/div.
        Se limita a ±4 divisiones."""
        v_div = self.v_div.get()
        max_v = v_div * 4   # máximo 4 divisiones arriba / abajo
        # Sentido normal: izquierda (-) → derecha (+)
        self.v_slider.config(from_=-max_v, to=max_v)

    # -----------------------------
    def on_slider_move(self, value):
        self.t_offset.set(float(value))
        self.update_plot()

    # -----------------------------
    def on_v_slider_move(self, value):
        # El slider va de -max (izquierda) a +max (derecha).
        # "derecha = traza arriba" → offset del canal = -value
        # (porque en el modelo nuevo, offset menor → traza arriba)
        if getattr(self, "_syncing_active", False):
            return
        v = float(value)
        self.v_offset.set(-v)
        if self.channels:
            self.channels[self.active_channel_idx]["v_offset"] = -v
        self.update_plot()

    # -----------------------------
    def on_time_div_change(self):
        if self.time is None:
            return
        # Zoom respecto al centro de la ventana visible.
        new_t_div = self.t_div.get()
        prev_t_div = getattr(self, "_prev_t_div", new_t_div)

        if prev_t_div != new_t_div and prev_t_div > 0:
            # Centro actual en segundos
            t_center = self.t_offset.get() + prev_t_div * H_DIVS / 2.0
            # Nuevo offset: el centro debe seguir siendo t_center
            new_offset = t_center - new_t_div * H_DIVS / 2.0
            # Clamp dentro del rango válido
            max_offset = max(0.0, self.t_total - new_t_div * H_DIVS)
            new_offset = max(0.0, min(new_offset, max_offset))
            # Anti-ruido: si tras todos los cálculos quedó un offset
            # ridículamente pequeño respecto al nuevo t/div, forzar a 0.
            # Esto evita arrastrar artefactos de coma flotante a través de
            # sucesivos cambios de t/div (zoom + auto-fit típico).
            if abs(new_offset) < new_t_div * 1e-4:
                new_offset = 0.0
            self.t_offset.set(new_offset)
        self._prev_t_div = new_t_div
        self.configure_slider()
        self.update_plot()

    def _snap_t_div_nearest(self, current):
        """Devuelve el valor de TIME_DIV_VALUES más cercano a 'current' en
        escala logarítmica."""
        import math
        lc = math.log10(max(current, 1e-18))
        return min(TIME_DIV_VALUES, key=lambda v: abs(math.log10(v) - lc))

    # -----------------------------
    def on_v_div_change(self):
        # Pivot desde el GND del canal activo, estilo scope real: el "0V
        # del canal" debe quedar en la misma posición RELATIVA AL GRID
        # (misma cantidad de divisiones desde el centro), no en el mismo
        # pixel absoluto. Esto requiere escalar v_offset proporcionalmente
        # a v_div: la posición del GND, en divisiones, es
        #     pos_GND_divs = -v_offset / v_div
        # y queremos que se conserve al cambiar v_div:
        #     v_offset_new = v_offset_old * (v_div_new / v_div_old)
        #
        # Sin esto, al subir V/div el centro del grid sigue mostrando el
        # mismo valor en voltios (label invariante) pero el GND del canal
        # se "acerca" al centro del grid en divisiones, lo que el usuario
        # percibe como un desplazamiento de la traza respecto a las líneas.
        new_v_div = self.v_div.get()
        if self.channels:
            # Leemos v_div ANTES de persistir el nuevo: ch["v_div"] del
            # canal activo aún tiene el valor antiguo en este punto
            # (_persist_active_channel_vars lo actualiza más abajo).
            old_v_div = self.channels[self.active_channel_idx].get("v_div")
            if (isinstance(old_v_div, (int, float)) and old_v_div > 0
                    and new_v_div > 0 and abs(new_v_div - old_v_div) > 1e-12):
                scaled = self.v_offset.get() * (new_v_div / old_v_div)
                self._syncing_active = True
                try:
                    self.v_offset.set(scaled)
                finally:
                    self._syncing_active = False

        # _syncing_active=True durante TODO el resto del método blinda
        # configure_v_slider() (al cambiar from_/to puede mover el thumb
        # y disparar on_v_slider_move espuriamente) y el set() final.
        self._syncing_active = True
        try:
            # Reconfigurar el slider vertical para adaptar el paso a la
            # nueva escala
            self.configure_v_slider()
            # Si el offset actual queda fuera del nuevo límite, recortarlo
            v_limit = new_v_div * 4
            current = self.v_offset.get()
            clamped = max(-v_limit, min(current, v_limit))
            if clamped != current:
                self.v_offset.set(clamped)
            # Sincronizar el slider con el offset definitivo (signo invertido).
            self.v_slider.set(-self.v_offset.get())
            # Persistir al canal activo
            self._persist_active_channel_vars()
        finally:
            self._syncing_active = False
        self.update_plot()

    # -----------------------------
    # Spinbox SCALE: flechas y edición manual
    # -----------------------------
    def _snap_to_list(self, value, values, direction=None):
        """Devuelve el valor de `values` más cercano a `value`.
        Si direction='up' busca el siguiente mayor; 'down', el anterior menor."""
        values = sorted(values)
        if direction == "up":
            for v in values:
                if v > value + 1e-15:
                    return v
            return values[-1]
        if direction == "down":
            for v in reversed(values):
                if v < value - 1e-15:
                    return v
            return values[0]
        # Más cercano (sin dirección)
        return min(values, key=lambda v: abs(v - value))

    def _spin_direction(self, spinbox, stringvar, last_text_attr):
        """Intenta inferir si el usuario pulsó ↑ o ↓ comparando el texto
        anterior con el actual. Si no se puede, devuelve None."""
        prev = getattr(self, last_text_attr, None)
        curr = stringvar.get()
        # Guardar para la próxima
        setattr(self, last_text_attr, curr)
        if prev is None:
            return None
        # Heurística simple: si el texto ha cambiado tras pulsar una flecha,
        # matplotlib/ttk nos daría el nuevo valor ya escrito. Aquí no tenemos
        # forma directa; usamos el valor numérico anterior guardado.
        return None

    # -----------------------------
    # Spinbox SCALE: valores pre-formateados, detección por trace
    # -----------------------------
    def _v_div_values_formatted(self):
        """Lista de textos válidos para el spinbox V/div.
        En modo normal usa V_DIV_VALUES; en Fine usa una rejilla de 0.1 V."""
        unit = self._active_unit()
        if self.fine_mode.get():
            # 0.1 a 50 (unit) en pasos de 0.1 (arriba de 50 sería exagerado)
            return [format_si(round(x * 0.1, 2), unit) for x in range(1, 501)]
        return [format_si(v, unit) for v in V_DIV_VALUES]

    def _on_fine_mode_toggle(self):
        """Al cambiar de modo Fine, re-acomoda el v_div actual a la nueva
        rejilla (Fine = pasos de 0.1V, Normal = V_DIV_VALUES canónicos)."""
        if self.fine_mode.get():
            snapped = round(self.v_div.get() * 10) / 10.0
            snapped = max(0.1, snapped)
        else:
            snapped = min(V_DIV_VALUES, key=lambda v: abs(v - self.v_div.get()))

        self.v_div.set(snapped)
        self._updating_display = True
        self.v_div_display.set(format_si(snapped, self._active_unit()))
        self._updating_display = False
        self.on_v_div_change()

    def _on_v_div_display_changed(self, *args):
        """Se invoca ante CUALQUIER cambio del StringVar (flechas o tipeo).
        Solo procesamos los cambios que provienen del usuario."""
        if getattr(self, "_updating_display", False):
            return
        text = self.v_div_display.get()
        val = parse_si(text, self._active_unit())
        if val is None or val <= 0:
            return  # texto incompleto o inválido: esperar a Enter/FocusOut
        if abs(val - self.v_div.get()) < 1e-15:
            return  # sin cambio real
        self.v_div.set(val)
        self.on_v_div_change()

    def _on_v_div_typed(self):
        """Enter o FocusOut: formatea el texto al canon."""
        text = self.v_div_display.get()
        val = parse_si(text, self._active_unit())
        if val is None or val <= 0:
            # Restaurar al valor anterior
            self._updating_display = True
            self.v_div_display.set(format_si(self.v_div.get(), self._active_unit()))
            self._updating_display = False
            return
        # Si el valor parseado es el mismo que ya tenemos, solo reformatear el
        # display — NO disparar on_v_div_change (eso tocaría el slider y movería
        # la traza sin motivo).
        if abs(val - self.v_div.get()) < 1e-15:
            self._updating_display = True
            self.v_div_display.set(format_si(self.v_div.get(), self._active_unit()))
            self._updating_display = False
            return
        self.v_div.set(val)
        self._updating_display = True
        self.v_div_display.set(format_si(val, self._active_unit()))
        self._updating_display = False
        self.on_v_div_change()

    def _step_v_div(self, direction):
        """Incrementa o decrementa v_div. En modo normal salta al V_DIV_VALUES
        vecino; en modo Fine usa pasos de 0.1 V."""
        current = self.v_div.get()
        if self.fine_mode.get():
            step = 0.1
            if direction == "up":
                # Sin techo: si el usuario está en una escala fuera del rango
                # cosmético del spinbox (p.ej. 100 V/div en buses de potencia),
                # un techo aplastaría el valor a la baja al primer click. Fine
                # solo cambia el TAMAÑO del paso, no el rango admisible.
                target = round((current + step) * 10) / 10.0
            else:
                target = round((current - step) * 10) / 10.0
                target = max(target, 0.1)
        else:
            if direction == "up":
                target = None
                for v in V_DIV_VALUES:
                    if v > current * 1.0001:
                        target = v
                        break
                if target is None:
                    target = V_DIV_VALUES[-1]
            else:
                target = None
                for v in reversed(V_DIV_VALUES):
                    if v < current * 0.9999:
                        target = v
                        break
                if target is None:
                    target = V_DIV_VALUES[0]

        self._updating_display = True
        self.v_div_display.set(format_si(target, self._active_unit()))
        self._updating_display = False
        self.v_div.set(target)
        self.on_v_div_change()

    def _step_t_div(self, direction):
        """Incrementa o decrementa t_div al TIME_DIV_VALUES vecino, partiendo
        SIEMPRE del valor actual de self.t_div (no del texto del display).
        El 'up' está capado: no permite alejarse más allá del t_div que hace
        que la traza completa ocupe H_DIVS divisiones."""
        current = self.t_div.get()

        # Cap superior: el t_div máximo útil es t_total/H_DIVS (más allá
        # queda pantalla vacía a la derecha de la traza).
        cap = (self.t_total / H_DIVS) if (self.t_total and self.t_total > 0) else None

        if direction == "up":
            if cap is not None and current >= cap * 0.9999:
                return  # ya estamos en el máximo útil
            target = None
            for v in TIME_DIV_VALUES:
                if v > current * 1.0001:
                    if cap is not None and v > cap:
                        # El siguiente canónico excede el cap: snapear al cap exacto
                        target = cap
                    else:
                        target = v
                    break
            if target is None:
                target = cap if cap is not None else TIME_DIV_VALUES[-1]
        else:
            target = None
            for v in reversed(TIME_DIV_VALUES):
                if v < current * 0.9999:
                    target = v
                    break
            if target is None:
                target = TIME_DIV_VALUES[0]

        self._updating_display = True
        self.t_div_display.set(format_si(target, "s"))
        self._updating_display = False
        self.t_div.set(target)
        self.on_time_div_change()

    def _on_t_div_spin_arrow(self):
        """Callback de las flechas del spinbox t_div. Ignora el valor que Tk
        haya puesto (incremento de 1 no tiene sentido aquí) y salta al
        TIME_DIV_VALUES vecino hacia arriba o hacia abajo según el delta
        aproximado."""
        try:
            new_text = self.t_div_display.get()
            new_val = parse_si(new_text, "s")
        except Exception:
            new_val = None
        current = self.t_div.get()
        # Dirección: si Tk subió, vamos al vecino mayor; si bajó, al menor
        direction = "up" if (new_val is not None and new_val > current) else "down"
        # Buscar el vecino canónico
        if direction == "up":
            target = None
            for v in TIME_DIV_VALUES:
                if v > current * 1.0001:
                    target = v
                    break
            if target is None:
                target = TIME_DIV_VALUES[-1]
        else:
            target = None
            for v in reversed(TIME_DIV_VALUES):
                if v < current * 0.9999:
                    target = v
                    break
            if target is None:
                target = TIME_DIV_VALUES[0]

        self._updating_display = True
        self.t_div_display.set(format_si(target, "s"))
        self._updating_display = False
        self.t_div.set(target)
        self.on_time_div_change()

    def _on_t_div_display_changed(self, *args):
        # El trace solo reacciona cuando el usuario escribe texto en directo.
        # NO aplica snap aquí (las flechas ya saltan por _on_t_div_spin_arrow,
        # y Enter/FocusOut los gestiona _on_t_div_typed).
        if getattr(self, "_updating_display", False):
            return
        text = self.t_div_display.get()
        val = parse_si(text, "s")
        if val is None or val <= 0:
            return
        if abs(val - self.t_div.get()) < 1e-18:
            return
        self.t_div.set(val)
        self.on_time_div_change()

    def _on_t_div_typed(self):
        text = self.t_div_display.get()
        val = parse_si(text, "s")
        if val is None or val <= 0:
            self._updating_display = True
            self.t_div_display.set(format_si(self.t_div.get(), "s"))
            self._updating_display = False
            return
        # Si el valor ya coincide con el actual (p.ej. tras auto_fit con valor
        # no-canónico), solo reformateamos el texto y NO hacemos snap.
        # Sin esto, un focus-out sobre un valor exacto se "saltaba" al
        # canónico más cercano.
        if abs(val - self.t_div.get()) < max(self.t_div.get() * 1e-6, 1e-18):
            self._updating_display = True
            self.t_div_display.set(format_si(self.t_div.get(), "s"))
            self._updating_display = False
            return
        # Valor distinto al actual → snap al canónico más cercano
        val = self._snap_t_div_nearest(val)
        self.t_div.set(val)
        self._updating_display = True
        self.t_div_display.set(format_si(val, "s"))
        self._updating_display = False
        self.on_time_div_change()

    # -----------------------------
    def center_trace(self):
        """Centra la traza del canal activo (offset = media de la señal)."""
        if not self.channels:
            return
        ch = self.channels[self.active_channel_idx]
        v = ch["voltage"]
        if len(v) == 0:
            return
        mid = float((np.max(v) + np.min(v)) / 2.0)
        ch["v_offset"] = mid
        self.v_offset.set(mid)
        self.v_slider.set(-mid)
        self.update_plot()

    def _snap_horizontal_to_zero(self):
        """Ajusta t_offset para que el instante 't = 0 absoluto del CSV'
        (i.e. -time_origin internamente) caiga exactamente sobre un tick
        del grid.

        Cómo funciona:
          - Solo aplica en modo Absolute (time_show_absolute=True).
          - El tiempo absoluto en la posición de pantalla x = k*t_div es:
                t_abs(k) = t_offset + k*t_div + time_origin
          - Queremos t_abs(k) = 0  →  t_offset = -k*t_div - time_origin
          - Elegimos k entero en [0, H_DIVS] que produce un t_offset
            válido (≥ 0 y ≤ max_offset) y más cercano al t_offset actual.
          - Si no hay ningún k válido (porque time_origin queda fuera
            del rango cubrible por t_offset), no hacemos nada y notificamos.
        """
        if self.time is None:
            self._show_toast("Load a CSV first")
            return
        if not getattr(self, "time_show_absolute", None) or \
                not self.time_show_absolute.get():
            self._show_toast("Switch Time axis to 'Absolute' in Appearance")
            return

        t_div = self.t_div.get()
        if t_div <= 0:
            return
        time_origin = self.time_origin
        window = t_div * H_DIVS
        max_offset = max(0.0, self.t_total - window)

        # Candidatos: t_offset = -k*t_div - time_origin, para k=0..H_DIVS.
        # Filtrar los que estén dentro del rango [0, max_offset].
        candidates = []
        for k in range(H_DIVS + 1):
            cand = -k * t_div - time_origin
            if 0.0 <= cand <= max_offset + t_div * 1e-6:
                candidates.append(cand)

        if not candidates:
            # No hay forma de alinear t=0 con un tick del grid actual.
            # Probablemente time_origin es positivo (no había pre-trigger)
            # o el zoom es demasiado grande/pequeño.
            if time_origin >= 0:
                self._show_toast("t=0 already at start (no pre-trigger)")
            else:
                self._show_toast("Cannot align: change T/div first")
            return

        # Elegir el candidato más cercano al t_offset actual (mínimo salto).
        cur = self.t_offset.get()
        best = min(candidates, key=lambda c: abs(c - cur))
        self.t_offset.set(best)
        try:
            self.h_slider.set(best)
        except Exception:
            pass
        self.update_plot()

    def _update_snap_t0_state(self):
        """Habilita/deshabilita el botón 'Snap horizontal to t=0' según
        si el modo Absolute está activo y hay un CSV cargado. Llamar desde:
          - construcción inicial de la UI
          - toggle del modo en Appearance
          - tras cargar/descargar un CSV
        """
        btn = getattr(self, "_snap_t0_btn", None)
        if btn is None:
            return
        try:
            absolute = (self.time_show_absolute.get()
                        if hasattr(self, "time_show_absolute") else False)
            has_csv = (self.time is not None and len(self.channels) > 0)
            btn.config(state=("normal" if (absolute and has_csv)
                              else "disabled"))
        except Exception:
            pass

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
        dt_safe = _np.where(dt == 0, 1e-30, dt)
        slope = _np.abs(dy / dt_safe)

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
                    base_color=ch["color"],
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
                lbl.set_color(active["color"])

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
                    base_color=ch["color"],
                    base_alpha=base_alpha,
                    linewidth=base_lw,
                    intensity=float(self.phosphor_intensity.get()),
                )
                self.ax.add_collection(line)
            else:
                (line,) = self.ax.plot(
                    t_plot - t_start,
                    y_plot,
                    color=ch["color"],
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
                color=ch["color"],
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

        # Etiqueta con el nombre del canal a la izquierda.
        # Usa un bbox semitransparente del color de fondo del grid para que
        # NUNCA se superponga con los ticks del eje Y.
        ann = self.ax.annotate(
            f"▶ {ch['name']}",
            xy=(0, y_gnd),
            xytext=(-t_range * 0.09, y_gnd),
            ha="right",
            va="center",
            color=ch["color"],
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
        

    def _on_plot_ctrl_wheel(self, event):
        """Ctrl + rueda sobre el plot: zoom horizontal (T/div).

        Mismo comportamiento que la rueda sobre el minimap pero accesible
        sin tener que mover el ratón fuera del grid.

        Convención: rueda ARRIBA → zoom in (T/div ↓, mostrar menos tiempo),
        rueda ABAJO → zoom out. Igual que _on_minimap_wheel para que el
        usuario no tenga que reaprender gestos.
        """
        if self.time is None:
            return
        if hasattr(event, "delta") and event.delta:
            direction = "down" if event.delta > 0 else "up"
        elif getattr(event, "num", None) == 4:
            direction = "down"     # Linux scroll-up → zoom in
        elif getattr(event, "num", None) == 5:
            direction = "up"       # Linux scroll-down → zoom out
        else:
            return
        self._step_t_div(direction)
        return "break"

    def _on_plot_shift_wheel(self, event):
        """Shift + rueda sobre el plot: pan horizontal.

        Detecta el sentido de la rueda según la plataforma:
          - Windows/Mac: event.delta (±120 múltiplos; signo = dirección).
          - Linux: usa Button-4 (up) y Button-5 (down), event.num.

        Mueve t_offset en pasos de ~1 división (=t_div). Es lo bastante
        rápido para navegar pero sin saltos enormes que despisten.
        """
        if self.time is None:
            return
        # Determinar dirección
        if hasattr(event, "delta") and event.delta:
            direction = 1 if event.delta < 0 else -1     # rueda abajo = pan→
        elif getattr(event, "num", None) == 5:
            direction = 1                                 # Linux scroll-down
        elif getattr(event, "num", None) == 4:
            direction = -1                                # Linux scroll-up
        else:
            return

        step = self.t_div.get() * direction              # 1 div por tick
        new_offset = self.t_offset.get() + step

        # Clamp al rango válido del CSV: 0 .. (t_total - t_window)
        t_window = self.t_div.get() * H_DIVS
        max_offset = max(0.0, self.t_total - t_window)
        new_offset = max(0.0, min(new_offset, max_offset))

        self.t_offset.set(new_offset)
        # Sincronizar el slider horizontal si existe
        try:
            self.h_slider.set(new_offset)
        except Exception:
            pass
        self.update_plot()
        # Devolver "break" para que el evento no se propague a contenedores
        return "break"

    def on_mouse_press(self, event):
        # Click derecho: menú contextual del plot
        if event.button == 3 and event.inaxes == self.ax:
            self._show_plot_context_menu(event)
            return

        # Annotation mode: intercepta y consume el evento
        if self._is_annotation_active():
            if self._on_annotation_press(event):
                return

        if event.button != 1 or event.inaxes != self.ax:
            return
        if self.time is None or event.xdata is None:
            return

        click_consumed = False


        # -----------------------------
        # CURSORES
        # -----------------------------
        # En modo combinado, probamos primero con el tipo cuyo click está
        # más cerca (minimal distance en coordenadas de su eje).

        v_on = self._cursors_vertical_active()
        h_on = self._cursors_horizontal_active()

        if v_on or h_on:
            # Probar vertical (tiempo)
            if v_on:
                click_time = event.xdata + self.t_offset.get()
                tol = self.t_div.get() * 0.2
                for i, x in enumerate(self.cursor_x):
                    if abs(click_time - x) < tol:
                        self.active_cursor = i
                        click_consumed = True
                        break

            # Si no se ha agarrado vertical, probar horizontal
            if not click_consumed and h_on:
                click_voltage = event.ydata
                tol = self.v_div.get() * 0.3
                for i, y in enumerate(self.cursor_y):
                    if abs(click_voltage - y) < tol:
                        self.active_cursor_y = i
                        click_consumed = True
                        break


        # -----------------------------
        # DRAG DE TRAZA (fallback) - siempre horizontal,
        # y también vertical SOLO si se pincha sobre la traza
        # -----------------------------
        if not click_consumed:
            self.dragging = True
            self.drag_start_x = event.xdata
            self.drag_start_y = event.ydata
            self.drag_start_offset = self.t_offset.get()

            # ¿El click ha caído sobre algún canal? Si sí, actívalo y
            # habilita drag vertical. Si no, solo drag horizontal.
            clicked_idx = self.find_channel_under_click(event)
            if clicked_idx is not None:
                if clicked_idx != self.active_channel_idx:
                    self.set_active_channel(clicked_idx)
                    self._rebuild_channels_panel()
                self.drag_vertical_enabled = True
                self.drag_start_v_offset = self.v_offset.get()
            else:
                self.drag_vertical_enabled = False
                self.drag_start_v_offset = self.v_offset.get()




    def on_mouse_release(self, event):
        # Annotation: termina stroke si estaba dibujando
        if self._current_stroke is not None:
            self._on_annotation_release(event)
            return
        self.dragging = False
        self.active_cursor = None
        self.active_cursor_y = None


    def on_mouse_move(self, event):
        if self.time is None:
            return

        # Annotation: prolonga stroke si se está dibujando
        if self._current_stroke is not None:
            self._on_annotation_move(event)
            return

        # -----------------------------
        # CURSORES ACTIVOS
        # -----------------------------
        # Cursor vertical (tiempo) - funciona en modos Vertical y Horizontal+Vertical
        if self._cursors_vertical_active() and self.active_cursor is not None:
            if event.xdata is None:
                return
            new_time = event.xdata + self.t_offset.get()
            new_time = max(0.0, min(new_time, self.t_total))
            self.cursor_x[self.active_cursor] = new_time
            self._schedule_update_plot()
            self._mark_dirty()
            return

        # Cursor horizontal (voltaje) - funciona en modos Horizontal y Horizontal+Vertical
        if self._cursors_horizontal_active() and self.active_cursor_y is not None:
            if event.ydata is None:
                return
            new_voltage = event.ydata
            v_half_range = (self.v_div.get() * V_DIVS) / 2
            new_voltage = max(-v_half_range, min(new_voltage, v_half_range))
            self.cursor_y[self.active_cursor_y] = new_voltage
            self._schedule_update_plot()
            self._mark_dirty()
            return

        # -----------------------------
        # MOVER TRAZA (fallback) - X e Y
        # -----------------------------
        if not self.dragging:
            return

        if event.xdata is None:
            return

        # --- Horizontal ---
        dx = event.xdata - self.drag_start_x
        window = self.t_div.get() * H_DIVS
        max_offset = max(0.0, self.t_total - window)

        new_offset = self.drag_start_offset - dx
        new_offset = max(0.0, min(new_offset, max_offset))

        self.t_offset.set(new_offset)
        self.h_slider.set(new_offset)

        # --- Vertical (solo si el click inicial fue sobre la traza) ---
        if getattr(self, "drag_vertical_enabled", False) and event.ydata is not None:
            dy = event.ydata - self.drag_start_y
            # Modelo nuevo: las trazas se dibujan como (v - offset)/v_div.
            # Arrastrar hacia arriba → la traza sube → offset debe disminuir.
            new_v_offset = self.drag_start_v_offset - dy

            # Limitar a ±4 divisiones (en V del canal activo)
            v_limit = self.v_div.get() * 4
            new_v_offset = max(-v_limit, min(new_v_offset, v_limit))

            self.v_offset.set(new_v_offset)
            # Slider INVERTIDO: derecha=+=traza arriba=offset negativo.
            # Bloqueamos el callback del slider para que no re-escriba el offset.
            self._syncing_active = True
            try:
                self.v_slider.set(-new_v_offset)
            finally:
                self._syncing_active = False
            # Persistir al canal activo
            if self.channels:
                self.channels[self.active_channel_idx]["v_offset"] = new_v_offset

        self._schedule_update_plot()


        
 
    
    def open_fft_window(self):
        # Singleton: si ya hay una ventana FFT abierta, traerla al frente
        # en lugar de abrir otra. Mismo patrón que open_xy_window.
        if getattr(self, "_fft_window", None) is not None \
                and self._fft_window.winfo_exists():
            self._fft_window.lift()
            self._fft_window.focus_force()
            return
        if self.time is None:
            return
        fft = FFTWindow(self)
        self._fft_window = fft.win
        # Guardar también la instancia para poder notificarle cambios de
        # tema en runtime (ver apply_theme_runtime).
        self._fft_instance = fft

    def open_eye_window(self):
        """Abre el diagrama de ojos sobre el canal activo."""
        # Singleton — ver open_fft_window
        if getattr(self, "_eye_window", None) is not None \
                and self._eye_window.winfo_exists():
            self._eye_window.lift()
            self._eye_window.focus_force()
            return
        if self.time is None or not self.channels:
            self._show_toast("Load a CSV first")
            return
        eye = EyeDiagramWindow(self)
        self._eye_window = eye.win
        self._eye_instance = eye

    def open_decoder_window(self):
        """Abre el decoder de protocolos."""
        # Singleton — ver open_fft_window
        if getattr(self, "_decoder_window", None) is not None \
                and self._decoder_window.winfo_exists():
            self._decoder_window.lift()
            self._decoder_window.focus_force()
            return
        if self.time is None or not self.channels:
            self._show_toast("Load a CSV first")
            return
        dec = DecoderWindow(self)
        self._decoder_window = dec.win

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

    def on_close(self):
        # Cierra limpiamente la aplicación. Antes de destruir la ventana,
        # persistimos su geometría y estado para restaurarlos en el próximo
        # arranque. Si _save_main_window_geom falla por cualquier motivo,
        # no bloqueamos el cierre.

        # Si hay cambios sin guardar pregunta al usuario. Cancel aborta
        # el cierre (no destruye la ventana).
        if not self._prompt_save_if_dirty(context="closing"):
            return

        try:
            _save_main_window_geom(self.root)
        except Exception:
            pass
        self.root.quit()      # Sale del mainloop
        self.root.destroy()   # Destruye la ventana y widgets


# -----------------------------
# Main
# -----------------------------
# Posicionamiento de la ventana principal
# -----------------------------
# La ventana principal sufre un comportamiento aleatorio del WM si se la
# deja sin posición: Windows decide dónde abrir, normalmente en la última
# posición conocida del sistema, lo que provoca que aparezca pegada a un
# borde, en un monitor secundario apagado, o solapando otras apps.
#
# Estrategia (sin dependencias extra):
#   1. Al cerrar la app, guardamos en user_config la geometría actual:
#        - "main_window_geom": "WxH+X+Y" del estado normal (no maximizada)
#        - "main_window_zoomed": True si estaba maximizada
#   2. Al abrir, si hay geometría guardada Y queda razonablemente visible
#      en la pantalla actual, se aplica tal cual. Si no (cambio de
#      monitor, resolución más pequeña, etc.) se descarta y se centra.
#   3. Si nunca se guardó nada (primer arranque): centrar.
#
# Definimos "razonablemente visible" como: al menos 80 píxeles de la
# ventana intersectan el área de la pantalla primaria. Esto tolera setups
# multi-monitor sin sacar la ventana fuera del área usable.

def _parse_geometry_string(s):
    """Parsea 'WxH+X+Y' o 'WxH-X-Y' a tupla (w, h, x, y) o None si falla."""
    if not isinstance(s, str):
        return None
    try:
        # Formatos posibles: '1536x960+100+50', '1536x960-50+50', etc.
        # Encontrar el separador después de WxH (puede ser + o -)
        i_x = s.find("x")
        if i_x <= 0:
            return None
        w = int(s[:i_x])
        rest = s[i_x + 1:]
        # rest = "960+100+50" o similar
        # Buscar primer + o - desde posición 1 (la altura puede ser 960
        # sin signo, así que evitamos confundir con un negativo)
        i_sign = -1
        for j in range(1, len(rest)):
            if rest[j] in "+-":
                i_sign = j
                break
        if i_sign < 0:
            # Solo "WxH" sin posición — no nos sirve
            return None
        h = int(rest[:i_sign])
        # rest[i_sign:] empieza con + o - y trae "X+Y" o "X-Y"
        tail = rest[i_sign:]
        # Buscar segundo + o - desde posición 1 de tail
        i_sign2 = -1
        for j in range(1, len(tail)):
            if tail[j] in "+-":
                i_sign2 = j
                break
        if i_sign2 < 0:
            return None
        x = int(tail[:i_sign2])
        y = int(tail[i_sign2:])
        return (w, h, x, y)
    except (ValueError, IndexError):
        return None


def _position_main_window(root, user_config):
    """Aplica geometry guardada si es válida; si no, centra en pantalla.

    Devuelve True si la ventana debería abrirse maximizada (zoomed),
    False en caso contrario. EL CALLER es responsable de aplicar
    root.state("zoomed") DESPUÉS de deiconify(), nunca antes — hacerlo
    mientras withdraw() está activo en Windows destruye el icono asignado
    con iconbitmap() y la taskbar acaba mostrando el icono genérico de
    python.exe.

    Llamar DESPUÉS de root.geometry() inicial (que solo fija tamaño) y
    ANTES de deiconify(). El estado zoomed se aplica más tarde.
    """
    # Pantalla primaria — Tk no expone múltiples monitores. Aceptamos la
    # limitación: el "razonablemente visible" se mide contra el primario.
    try:
        scr_w = root.winfo_screenwidth()
        scr_h = root.winfo_screenheight()
    except Exception:
        scr_w, scr_h = 1920, 1080

    # Intentar usar la geometría guardada
    saved = user_config.get("main_window_geom")
    # Primera apertura (sin geom guardada) → maximizada por defecto.
    # Si ya existe config previo, respetamos la elección del usuario.
    if saved is None:
        zoomed = True
    else:
        zoomed = bool(user_config.get("main_window_zoomed", False))
    parsed = _parse_geometry_string(saved)

    use_saved = False
    if parsed is not None:
        w, h, x, y = parsed
        # Validar que la ventana queda con al menos 80 px visibles
        # dentro del área de la pantalla primaria (tolerante a multi-mon).
        MIN_VISIBLE = 80
        visible_horiz = (x + w > MIN_VISIBLE) and (x < scr_w - MIN_VISIBLE)
        visible_vert  = (y + h > MIN_VISIBLE) and (y < scr_h - MIN_VISIBLE)
        # Tamaños sanos: ni minúscula ni gigantesca. El mínimo (400x300)
        # rechaza geometrías "default" minúsculas que Windows Tk asigna
        # a veces al des-maximizar sin tiempo de update.
        size_ok = (400 <= w <= scr_w * 3) and (300 <= h <= scr_h * 3)
        if visible_horiz and visible_vert and size_ok:
            use_saved = True
            root.geometry(f"{w}x{h}+{x}+{y}")

    if not use_saved:
        # Centrar en pantalla primaria. Tomar tamaño actual (ya fue
        # configurado con root.geometry("1536x960") antes de llamar).
        # update_idletasks fuerza a Tk a evaluar winfo_width/height.
        try:
            root.update_idletasks()
            w = root.winfo_width()
            h = root.winfo_height()
            # Si update_idletasks devuelve 1x1 (común antes del mainloop),
            # caer al tamaño nominal por defecto.
            if w <= 1 or h <= 1:
                w, h = 1536, 960
        except Exception:
            w, h = 1536, 960
        x = max(0, (scr_w - w) // 2)
        y = max(0, (scr_h - h) // 2)
        root.geometry(f"{w}x{h}+{x}+{y}")

    # NO aplicamos state aquí — eso es responsabilidad del caller, DESPUÉS
    # del deiconify(). Ver docstring para el porqué.
    return zoomed


def _register_windows_app_id():
    """Registra un AppUserModelID propio para la app en Windows.

    Sin esto, Windows agrupa la ventana bajo el AppUserModelID de
    python.exe (en modo desarrollo) o el del exe empaquetado (lo que
    suele funcionar bien), y la taskbar muestra el icono genérico de
    Python en vez del nuestro.

    Usa ctypes (stdlib, no dependencia nueva). Falla silenciosa en
    plataformas que no son Windows o si ctypes no encuentra shell32."""
    try:
        import ctypes
        # AppID propio — string único que identifica la app en Windows.
        # Cambiar este string solo si se cambia el "branding" de la app.
        app_id = "jotapeveloper.OscilloscopeCSVViewer"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        # No-Windows, ctypes faltante o shell32 sin la función: ignorar.
        pass


def _save_main_window_geom(root):
    """Guarda la geometría y el estado de maximización de la ventana
    principal. Llamada desde on_close ANTES de destruir.

    NO tocamos el state al leer geometry(): en Windows Tk root.geometry()
    devuelve la geometría "normal" subyacente incluso cuando la ventana
    está maximizada. Hacer state("normal") + update_idletasks() resulta
    poco fiable porque el WM no siempre devuelve la geometría real a
    tiempo (puede devolver un default minúsculo si update_idletasks no
    completa la des-maximización antes de leer).

    Validamos antes de escribir: si la geometría parsea a algo inválido
    (W < 400 o H < 300), no la guardamos — preservamos lo que hubiera
    antes en el config para no corromperlo."""
    try:
        st = root.state()
    except Exception:
        st = "normal"
    zoomed = (st == "zoomed")

    try:
        geom = root.geometry()   # "WxH+X+Y"
    except Exception:
        geom = None

    # Validar antes de guardar para no corromper el config con basura
    parsed = _parse_geometry_string(geom)
    geom_valid = False
    if parsed is not None:
        w, h, _x, _y = parsed
        geom_valid = (w >= 400 and h >= 300)

    updates = {"main_window_zoomed": zoomed}
    if geom_valid:
        updates["main_window_geom"] = geom
    # Si NO es válida, no escribimos main_window_geom — preservamos el
    # valor anterior del config. Así un cierre raro no rompe la próxima
    # apertura.

    try:
        save_user_config(updates)
    except Exception:
        pass


# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    # PRIMERO: registrar AppUserModelID antes de crear cualquier ventana.
    # Esto asocia la ventana a un grupo propio en la taskbar de Windows
    # en vez de agruparla bajo python.exe. Sin esto, la taskbar muestra
    # el icono genérico de Python aunque iconbitmap() funcione para el
    # icono del título de ventana. Inocuo fuera de Windows.
    _register_windows_app_id()

    root = tk.Tk()
    # Ocultar la ventana inmediatamente para evitar el "flash" de ventana
    # en blanco mientras se aplica icono, geometría, tema y se construye
    # la UI completa. Misma técnica que _apply_window_chrome usa para las
    # ventanas secundarias.
    root.withdraw()

    try:
        root.iconbitmap(_resource_path("assets/icon.ico"))
    except Exception as e:
        print(f"Icon load failed: {e}")

    root.geometry("1536x960")

    # Aplicar tema desde el config persistido (o Dark si no existe).
    # ANTES de construir la UI para que los widgets nazcan con el tema correcto.
    user_config = load_user_config()
    initial_theme = user_config.get("app_theme", "Dark")
    if initial_theme not in THEMES:
        initial_theme = "Dark"
    apply_theme(root, initial_theme)

    # Posicionar la ventana ANTES de construir la UI y ANTES de deiconify
    # para evitar el "flash" en posición incorrecta. Si hay geometría
    # guardada de una sesión anterior, la restauramos; si no, centramos
    # en pantalla. Detalles en _position_main_window().
    #
    # IMPORTANTE: _position_main_window NO aplica state("zoomed") —
    # devuelve el flag y nosotros lo aplicamos DESPUÉS de deiconify para
    # no destruir el icono asignado durante el withdraw.
    should_zoom = _position_main_window(root, user_config)

    app = OscilloscopeApp(root)

    # Una vez la UI está construida, mostrar la ventana ya con todo aplicado.
    # update_idletasks() fuerza a Tk a procesar pendientes (geometría, layout)
    # antes de hacerla visible, así nace en su tamaño correcto sin parpadeo.
    root.update_idletasks()
    root.deiconify()

    # AHORA aplicar el state maximizado, con la ventana ya visible y el
    # icono ya asignado. Hacer state("zoomed") mientras withdraw() rompe
    # la asociación del icono con la taskbar de Windows.
    if should_zoom:
        try:
            root.state("zoomed")
        except Exception:
            pass

    # update() (no idletasks) fuerza a Tk a procesar TODOS los eventos
    # pendientes — incluyendo el repintado completo de la mainwindow
    # tras el state("zoomed"). Necesario antes de cerrar el splash para
    # que la mainwindow ya esté pintada por encima cuando Windows
    # ejecute la animación shrink-to-close nativa del splash; si no, el
    # splash se ve encogiéndose y desplazándose justo antes de la app.
    try:
        root.update()
    except Exception:
        pass

    # Cerrar el splash de PyInstaller (--splash splash.png) con la
    # mainwindow ya pintada encima. La animación nativa de cierre del
    # splash ocurre tapada y es invisible. El módulo pyi_splash solo
    # existe en runtime del .exe empaquetado; en desarrollo no está,
    # ImportError → ignorar.
    try:
        import pyi_splash  # type: ignore
        if pyi_splash.is_alive():
            pyi_splash.close()
    except Exception:
        pass

    # Re-aplicar el icono como cinturón-y-tirantes: si state() o cualquier
    # otra operación de chrome anterior afectó al icono cacheado por el WM,
    # esta llamada lo deja correcto. Si no era necesario, es inocuo.
    try:
        root.iconbitmap(_resource_path("assets/icon.ico"))
    except Exception as e:
        print(f"Icon re-apply failed: {e}")

    # Si el directorio del .exe no es escribible, avisar al usuario UNA SOLA
    # vez al arrancar. Lo hacemos después de construir la UI para que el
    # diálogo herede el tema. Schedule con `after` para que el mainloop
    # arranque antes (la geometría de root está fija en ese momento).
    if not check_config_writable():
        def _warn_readonly():
            from tkinter import messagebox
            messagebox.showwarning(
                "Read-only install location",
                "The application folder is not writable, so preferences\n"
                "(theme, future settings...) will not be saved between\n"
                "sessions.\n\n"
                f"Folder: {config_dir()}\n\n"
                "To fix this, move the application to a writable location\n"
                "such as your Desktop or Documents folder.",
                parent=root,
            )
        root.after(300, _warn_readonly)

    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()