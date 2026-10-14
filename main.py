import tkinter as tk
from tkinter import ttk
import os
import sys
import json
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# Lógica pura extraída a ocv.core (fase 2 del refactor)
from ocv.core.dsp import (
    minmax_decimate,
)
from ocv.core.si_format import CHANNEL_UNITS, DEFAULT_UNIT, format_si
from ocv.ui import theme
from ocv.ui.theme import apply_theme, THEMES
from ocv.core.config import (
    config_dir,
    check_config_writable,
    load_user_config,
    save_user_config,
)
from ocv.resources import (
    _resource_path,
)
from ocv.core.scope import H_DIVS, V_DIVS
from ocv.ui.widgets import (
    Tooltip,
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
from ocv.ui.app.minimap import MinimapMixin
from ocv.ui.app.xy import XYMixin
from ocv.ui.app.trigger import TriggerMixin
from ocv.ui.app.export import ExportMixin
from ocv.ui.app.annotations import AnnotationsMixin
from ocv.ui.app.math_channels import MathChannelsMixin
from ocv.ui.app.session_ui import SessionMixin
from ocv.ui.app.csv_loading import CsvLoadingMixin
from ocv.ui.app.dialogs import DialogsMixin
from ocv.ui.app.measurements_ui import MeasurementsMixin
from ocv.ui.app.scales import ScalesMixin
from ocv.ui.app.input import InputMixin


class OscilloscopeApp(ThemeRuntimeMixin, MinimapMixin, XYMixin, TriggerMixin, ExportMixin, AnnotationsMixin, MathChannelsMixin, SessionMixin, CsvLoadingMixin, DialogsMixin, MeasurementsMixin, ScalesMixin, InputMixin):

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