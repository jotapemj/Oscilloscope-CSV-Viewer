import tkinter as tk
import os
import sys
import json
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# Lógica pura extraída a ocv.core (fase 2 del refactor)
from ocv.core.si_format import format_si
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
from ocv.ui.app.panels import PanelsMixin
from ocv.ui.app.plot import PlotMixin


class OscilloscopeApp(ThemeRuntimeMixin, MinimapMixin, XYMixin, TriggerMixin, ExportMixin, AnnotationsMixin, MathChannelsMixin, SessionMixin, CsvLoadingMixin, DialogsMixin, MeasurementsMixin, ScalesMixin, InputMixin, PanelsMixin, PlotMixin):

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