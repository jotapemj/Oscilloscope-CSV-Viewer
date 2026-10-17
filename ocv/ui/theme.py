"""Themes: palette, Tk/ttk styling and live re-theming of widgets.

COLOR_* y CURRENT_THEME_NAME son estado de ESTE módulo: leerlos
siempre como theme.COLOR_BG, nunca con from-import (se quedaría
con el valor del tema anterior)."""
import tkinter as tk
from tkinter import ttk


# =============================
# Sistema de temas
# =============================
# La app soporta varios temas intercambiables en runtime. Cada tema define:
#   - Colores de la chrome (fondos, texto, acento, bordes)
#   - Colores recomendados del plot (bg, grid, text, time_label_bg)
#
# Diseño:
# Las variables COLOR_* son globales de ESTE módulo; el resto del código las
# lee como theme.COLOR_X. apply_theme(name) las reescribe y reaplica el
# style ttk. Para los widgets tk.* clásicos que
# ya están construidos (no responden a ttk.Style), _retheme_widgets() recorre
# el árbol y actualiza bg/fg.
#
# Para añadir un tema nuevo: añadir entrada a THEMES siguiendo el esquema.

THEMES = {
    "Dark": {
        "BG":         "#1e1e1e",   # fondo principal
        "BG_ALT":     "#2a2a2a",   # superficies (inputs, frames secundarios)
        "BG_HOVER":   "#353535",   # hover de controles
        "BG_ACTIVE":  "#404040",   # pressed / focus
        "BORDER":     "#3f3f3f",   # bordes sutiles
        "TEXT":       "#e0e0e0",   # texto principal
        "TEXT_DIM":   "#9a9a9a",   # texto secundario / labels
        "ACCENT":     "#4cc2ff",   # azul Sun Valley (cabeceras)
        "ACCENT_2":   "#3a7fb5",   # azul más oscuro para pressed
        "DANGER":     "#ff6b6b",
        # Plot
        "PLOT_BG":          "black",
        "PLOT_GRID":        "#555555",
        "PLOT_TEXT":        "white",
        "PLOT_TIME_LBL_BG": "#222222",
    },
    "Light": {
        # Tema claro tipo "blanco roto" — no blanco puro (cansa la vista),
        # acento azul más saturado para que destaque sobre fondo claro.
        "BG":         "#f5f5f5",
        "BG_ALT":     "#ffffff",
        "BG_HOVER":   "#e8e8e8",
        "BG_ACTIVE":  "#d8d8d8",
        "BORDER":     "#c8c8c8",
        "TEXT":       "#1a1a1a",
        "TEXT_DIM":   "#666666",
        "ACCENT":     "#0070c4",   # ≥4.5:1 sobre BG (antes #0078d4: 4.15)
        "ACCENT_2":   "#005a9e",
        "DANGER":     "#d13438",
        # Plot: fondo claro, grid gris, texto oscuro
        "PLOT_BG":          "#fafafa",
        "PLOT_GRID":        "#c0c0c0",
        "PLOT_TEXT":        "#222222",
        "PLOT_TIME_LBL_BG": "#e0e0e0",
    },
    "Blueprint": {
        # Estilo cianotipo / papel técnico azul oscuro con líneas claras.
        # Muy "ingeniería de los 80s", se ve fantástico con trazas amarillas.
        "BG":         "#0d2847",
        "BG_ALT":     "#143559",
        "BG_HOVER":   "#1d4271",
        "BG_ACTIVE":  "#265389",
        "BORDER":     "#2a5a8a",
        "TEXT":       "#cce4ff",
        "TEXT_DIM":   "#7fa9d4",
        "ACCENT":     "#ffb84d",   # ámbar para contraste con azul
        "ACCENT_2":   "#cc8a2e",
        "DANGER":     "#ff7070",   # ≥4.5:1 también sobre BG_ALT
        "PLOT_BG":          "#0a2240",
        "PLOT_GRID":        "#2d5a8c",
        "PLOT_TEXT":        "#d4e8ff",
        "PLOT_TIME_LBL_BG": "#143559",
    },
    "High Contrast": {
        # Accesibilidad: negro puro + amarillo + blanco. Sin grises medios.
        # Bordes gruesos, acentos brillantes. Cumple WCAG AAA aprox.
        "BG":         "#000000",
        "BG_ALT":     "#0a0a0a",
        "BG_HOVER":   "#1f1f1f",
        "BG_ACTIVE":  "#2f2f2f",
        "BORDER":     "#ffffff",
        "TEXT":       "#ffffff",
        "TEXT_DIM":   "#cccccc",
        "ACCENT":     "#ffff00",   # amarillo puro
        "ACCENT_2":   "#cccc00",
        "DANGER":     "#ff4040",
        "PLOT_BG":          "#000000",
        "PLOT_GRID":        "#808080",
        "PLOT_TEXT":        "#ffffff",
        "PLOT_TIME_LBL_BG": "#202020",
    },
}

# Tema activo. apply_theme() lo modifica.
CURRENT_THEME_NAME = "Dark"

# Estas globales se rellenan con apply_theme() y son las que usa el resto
# del código. Inicializadas con valores de Dark para que importar el módulo
# sin haber llamado a apply_theme() siga funcionando.
COLOR_BG        = THEMES["Dark"]["BG"]
COLOR_BG_ALT    = THEMES["Dark"]["BG_ALT"]
COLOR_BG_HOVER  = THEMES["Dark"]["BG_HOVER"]
COLOR_BG_ACTIVE = THEMES["Dark"]["BG_ACTIVE"]
COLOR_BORDER    = THEMES["Dark"]["BORDER"]
COLOR_TEXT      = THEMES["Dark"]["TEXT"]
COLOR_TEXT_DIM  = THEMES["Dark"]["TEXT_DIM"]
COLOR_ACCENT    = THEMES["Dark"]["ACCENT"]
COLOR_ACCENT_2  = THEMES["Dark"]["ACCENT_2"]
COLOR_DANGER    = THEMES["Dark"]["DANGER"]


def _refresh_color_globals(theme_name):
    """Reescribe las constantes COLOR_* del módulo con los valores del tema
    indicado. Esto permite que el código existente (que usa COLOR_BG, etc.)
    siga funcionando sin tocar las ~350 referencias dispersas."""
    g = globals()
    t = THEMES[theme_name]
    g["COLOR_BG"]        = t["BG"]
    g["COLOR_BG_ALT"]    = t["BG_ALT"]
    g["COLOR_BG_HOVER"]  = t["BG_HOVER"]
    g["COLOR_BG_ACTIVE"] = t["BG_ACTIVE"]
    g["COLOR_BORDER"]    = t["BORDER"]
    g["COLOR_TEXT"]      = t["TEXT"]
    g["COLOR_TEXT_DIM"]  = t["TEXT_DIM"]
    g["COLOR_ACCENT"]    = t["ACCENT"]
    g["COLOR_ACCENT_2"]  = t["ACCENT_2"]
    g["COLOR_DANGER"]    = t["DANGER"]
    g["COLOR_ACCENT_HOVER"]         = t["ACCENT_HOVER"]
    g["COLOR_TEXT_ON_ACCENT"]       = t["TEXT_ON_ACCENT"]
    g["COLOR_TEXT_ON_ACCENT_HOVER"] = t["TEXT_ON_ACCENT_HOVER"]
    g["COLOR_TEXT_ON_ACCENT_2"]     = t["TEXT_ON_ACCENT_2"]
    g["CURRENT_THEME_NAME"] = theme_name


# =====================================================
# Interpolación de colores (para transiciones de tema)
# =====================================================
# Usadas por apply_theme_runtime() cuando fade=True. La animación
# interpola entre dos diccionarios THEME a lo largo de N frames con
# ~30-40 ms entre cada uno.
def _hex_to_rgb(c):
    """Convierte color hex '#RRGGBB' o '#RGB' a tupla (r, g, b) en
    enteros 0-255. Si no parsea, devuelve negro. Los colores en THEMES
    son siempre #hex, así que en la práctica siempre parsea."""
    if not isinstance(c, str) or not c.startswith("#"):
        return (0, 0, 0)
    s = c[1:]
    try:
        if len(s) == 3:
            return tuple(int(ch * 2, 16) for ch in s)
        if len(s) == 6:
            return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        pass
    return (0, 0, 0)


def _rgb_to_hex(r, g, b):
    """Devuelve '#rrggbb' con clamping a [0, 255]."""
    r = max(0, min(255, int(round(r))))
    g = max(0, min(255, int(round(g))))
    b = max(0, min(255, int(round(b))))
    return f"#{r:02x}{g:02x}{b:02x}"


def lerp_color(c1, c2, t):
    """Interpola dos colores hex linealmente. t en [0, 1].
    t=0 → c1; t=1 → c2."""
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return _rgb_to_hex(
        r1 + (r2 - r1) * t,
        g1 + (g2 - g1) * t,
        b1 + (b2 - b1) * t,
    )


def lerp_theme(t1, t2, t):
    """Tema intermedio entre dos. Solo interpola valores hex; las claves
    no-color se toman de t1. t en [0, 1]."""
    out = {}
    for k, v1 in t1.items():
        v2 = t2.get(k, v1)
        if isinstance(v1, str) and v1.startswith("#") \
                and isinstance(v2, str) and v2.startswith("#"):
            out[k] = lerp_color(v1, v2, t)
        else:
            # No-color o tipos mixtos: mantenemos v1 hasta que el fade acabe
            out[k] = v1
    return out


def contrast_ratio(c1, c2):
    """Contraste WCAG 2.x entre dos colores hex (1.0 .. 21.0).
    Acepta también "black"/"white" (los usa Dark en PLOT_*)."""
    named = {"black": "#000000", "white": "#ffffff"}

    def lum(c):
        c = named.get(c, c)
        def ch(x):
            x /= 255
            return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
        r, g, b = (ch(x) for x in _hex_to_rgb(c))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    hi, lo = sorted((lum(c1), lum(c2)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def text_on(bg):
    """Negro o blanco, el que más contraste dé sobre `bg`."""
    black, white = contrast_ratio("#000000", bg), contrast_ratio("#ffffff", bg)
    return "#000000" if black >= white else "#ffffff"


# Mínimos WCAG: texto normal y elementos gráficos (trazas, iconos)
TEXT_MIN = 4.5
GRAPHIC_MIN = 3.0

_NAMED = {"black": "#000000", "white": "#ffffff"}


def readable_on(color, bg, min_ratio=TEXT_MIN):
    """Color de canal ajustado para leerse sobre `bg`: si no llega a
    min_ratio, lo mezcla hacia negro o blanco (el que dé más contraste)
    lo justo. Solo para mostrar; el color guardado del canal no cambia.
    Ej.: amarillo #ffeb3b sobre Light → mostaza oscuro."""
    color = _NAMED.get(color, color)
    bg = _NAMED.get(bg, bg)
    if not (isinstance(color, str) and color.startswith("#")
            and isinstance(bg, str) and bg.startswith("#")):
        return color
    if contrast_ratio(color, bg) >= min_ratio:
        return color
    target = text_on(bg)
    for k in range(1, 41):
        c = lerp_color(color, target, k / 40)
        if contrast_ratio(c, bg) >= min_ratio:
            return c
    return target


def _derive_roles(t):
    """Roles calculados: hover del acento (punto medio ACCENT→ACCENT_2) y
    texto legible sobre cada estado del acento."""
    t["ACCENT_HOVER"] = lerp_color(t["ACCENT"], t["ACCENT_2"], 0.5)
    t["TEXT_ON_ACCENT"] = text_on(t["ACCENT"])
    t["TEXT_ON_ACCENT_HOVER"] = text_on(t["ACCENT_HOVER"])
    t["TEXT_ON_ACCENT_2"] = text_on(t["ACCENT_2"])


for _t in THEMES.values():
    _derive_roles(_t)

COLOR_ACCENT_HOVER         = THEMES["Dark"]["ACCENT_HOVER"]
COLOR_TEXT_ON_ACCENT       = THEMES["Dark"]["TEXT_ON_ACCENT"]
COLOR_TEXT_ON_ACCENT_HOVER = THEMES["Dark"]["TEXT_ON_ACCENT_HOVER"]
COLOR_TEXT_ON_ACCENT_2     = THEMES["Dark"]["TEXT_ON_ACCENT_2"]


# =============================
# Canales — colores fijos por orden
# =============================
# CH1-CH8: paleta tipo Tek (yellow/green/orange/blue/magenta/cyan/red/white).
# CH9-CH12 (v1.26): paleta extendida — violeta, lima brillante, rosa fuerte y
# teal oscuro. Elegidos por contraste claro frente a los 8 originales y entre
# sí, evitando colisión visual sobre fondo oscuro.
CHANNEL_COLORS = [
    "#ffeb3b",   # CH1   amarillo
    "#4caf50",   # CH2   verde
    "#ff9800",   # CH3   naranja
    "#2196f3",   # CH4   azul
    "#e91e63",   # CH5   magenta
    "#00e5ff",   # CH6   cyan
    "#f44336",   # CH7   rojo
    "#ffffff",   # CH8   blanco
    "#b388ff",   # CH9   violeta
    "#76ff03",   # CH10  lima
    "#ff4081",   # CH11  rosa
    "#00bfa5",   # CH12  teal
]


def channel_color(idx):
    """Devuelve el color asignado al canal número idx (0-based).
    Si hay más canales que colores definidos, cicla."""
    return CHANNEL_COLORS[idx % len(CHANNEL_COLORS)]


def apply_theme(root, name="Dark"):
    """Aplica el tema indicado.

    Pasos:
      1) Reescribe las constantes COLOR_* del módulo.
      2) Configura ttk.Style con esos valores.
      3) Actualiza option_add para widgets futuros y Combobox listbox interno.

    Para re-tematizar widgets tk.* YA CREADOS (Frames, Labels, Buttons que
    no son ttk), llamar a _retheme_tk_widgets(root) después. El método de
    OscilloscopeApp .apply_theme_runtime() hace ambas cosas.
    """
    if name not in THEMES:
        name = "Dark"
    _refresh_color_globals(name)

    style = ttk.Style(root)
    # 'clam' es el único tema empotrado que permite personalización real
    # de colores en Windows/Linux/Mac.
    style.theme_use("clam")

    # Raíz
    root.configure(bg=COLOR_BG)

    # --- Frame / Label / Separator ---
    style.configure("TFrame", background=COLOR_BG)
    style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT)
    style.configure(
        "TSeparator",
        background=COLOR_BORDER,
    )

    # --- Button ---
    style.configure(
        "TButton",
        background=COLOR_BG_ALT,
        foreground=COLOR_TEXT,
        bordercolor=COLOR_BORDER,
        lightcolor=COLOR_BG_ALT,
        darkcolor=COLOR_BG_ALT,
        focuscolor=COLOR_BG_ALT,
        padding=(10, 6),
        relief="flat",
        borderwidth=1,
    )
    style.map(
        "TButton",
        background=[("active", COLOR_BG_HOVER), ("pressed", COLOR_BG_ACTIVE)],
        bordercolor=[("active", COLOR_ACCENT), ("pressed", COLOR_ACCENT)],
    )

    # Botón con énfasis (el típico azul)
    style.configure(
        "Accent.TButton",
        background=COLOR_ACCENT,
        foreground=COLOR_TEXT_ON_ACCENT,
        bordercolor=COLOR_ACCENT,
        lightcolor=COLOR_ACCENT,
        darkcolor=COLOR_ACCENT,
        padding=(10, 6),
        relief="flat",
        borderwidth=0,
    )
    style.map(
        "Accent.TButton",
        # pressed antes que active: al pulsar, ambos estados están activos
        background=[("pressed", COLOR_ACCENT_2), ("active", COLOR_ACCENT_HOVER)],
        foreground=[("pressed", COLOR_TEXT_ON_ACCENT_2),
                    ("active", COLOR_TEXT_ON_ACCENT_HOVER)],
    )

    # --- Checkbutton ---
    style.configure(
        "TCheckbutton",
        background=COLOR_BG,
        foreground=COLOR_TEXT,
        focuscolor=COLOR_BG,
        indicatorcolor=COLOR_BG_ALT,
        indicatorbackground=COLOR_BG_ALT,
        indicatorforeground=COLOR_TEXT,
    )
    style.map(
        "TCheckbutton",
        background=[("active", COLOR_BG)],
        foreground=[("active", COLOR_ACCENT)],
        # 'clam' pinta la casilla con indicatorbackground; indicatorcolor
        # solo lo usan otros temas ttk (se deja por si acaso)
        indicatorcolor=[
            ("selected", COLOR_ACCENT),
            ("!selected", COLOR_BG_ALT),
        ],
        indicatorbackground=[
            ("selected", COLOR_ACCENT),
            ("!selected", COLOR_BG_ALT),
        ],
        indicatorforeground=[("selected", COLOR_TEXT_ON_ACCENT)],
    )

    # --- Radiobutton ---
    style.configure(
        "TRadiobutton",
        background=COLOR_BG,
        foreground=COLOR_TEXT,
        focuscolor=COLOR_BG,
        indicatorcolor=COLOR_BG_ALT,
        indicatorbackground=COLOR_BG_ALT,
        indicatorforeground=COLOR_TEXT,
    )
    style.map(
        "TRadiobutton",
        background=[("active", COLOR_BG)],
        foreground=[("active", COLOR_ACCENT)],
        indicatorcolor=[
            ("selected", COLOR_ACCENT),
            ("!selected", COLOR_BG_ALT),
            ("active", COLOR_BG_HOVER),
        ],
        indicatorbackground=[
            ("selected", COLOR_ACCENT),
            ("!selected", COLOR_BG_ALT),
        ],
        indicatorforeground=[("selected", COLOR_TEXT_ON_ACCENT)],
    )

    # --- Entry / Spinbox / Combobox ---
    for name in ("TEntry", "TSpinbox", "TCombobox"):
        style.configure(
            name,
            fieldbackground=COLOR_BG_ALT,
            background=COLOR_BG_ALT,
            foreground=COLOR_TEXT,
            bordercolor=COLOR_BORDER,
            lightcolor=COLOR_BORDER,
            darkcolor=COLOR_BORDER,
            insertcolor=COLOR_TEXT,
            arrowcolor=COLOR_TEXT,
            padding=4,
        )
        style.map(
            name,
            bordercolor=[("focus", COLOR_ACCENT), ("hover", COLOR_ACCENT)],
            lightcolor=[("focus", COLOR_ACCENT)],
            darkcolor=[("focus", COLOR_ACCENT)],
            fieldbackground=[("readonly", COLOR_BG_ALT)],
            foreground=[("readonly", COLOR_TEXT)],
            # Antes: flecha negra en hover (invisible en temas oscuros) y
            # fondo gris claro de clam al pulsar.
            background=[("pressed", COLOR_BG_ACTIVE),
                        ("active", COLOR_BG_HOVER),
                        ("hover", COLOR_BG_HOVER)],
            arrowcolor=[("disabled", COLOR_TEXT_DIM),
                        ("!disabled", COLOR_TEXT)],
        )

    # Combobox tiene además un "dropdown" (Listbox interno) que no usa ttk.
    # Se configura por option_add:
    root.option_add("*TCombobox*Listbox.background", COLOR_BG_ALT)
    root.option_add("*TCombobox*Listbox.foreground", COLOR_TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", COLOR_ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", COLOR_TEXT_ON_ACCENT)

    # --- Scale (sliders) ---
    style.configure(
        "Horizontal.TScale",
        background=COLOR_BG,
        troughcolor=COLOR_BG_ALT,
        bordercolor=COLOR_BORDER,
        lightcolor=COLOR_BG_ALT,
        darkcolor=COLOR_BG_ALT,
    )
    style.map(
        "Horizontal.TScale",
        background=[("active", COLOR_BG)],
    )

    # ---- Treeview (tablas, e.g. DecoderWindow) ----
    # Por defecto ttk.Treeview ignora la mayoría de los colores configurados
    # globalmente. Hay que dárselos explícitamente.
    style.configure(
        "Treeview",
        background=COLOR_BG_ALT,
        foreground=COLOR_TEXT,
        fieldbackground=COLOR_BG_ALT,
        bordercolor=COLOR_BORDER,
        lightcolor=COLOR_BORDER,
        darkcolor=COLOR_BORDER,
        rowheight=22,
    )
    style.map(
        "Treeview",
        background=[("selected", COLOR_ACCENT)],
        foreground=[("selected", COLOR_TEXT_ON_ACCENT)],
    )
    style.configure(
        "Treeview.Heading",
        background=COLOR_BG,
        foreground=COLOR_ACCENT,
        bordercolor=COLOR_BORDER,
        relief="flat",
        font=("Segoe UI", 9, "bold"),
    )
    style.map(
        "Treeview.Heading",
        background=[("active", COLOR_BG_HOVER)],
    )

    # ---- LabelFrame (e.g. "Channel mapping", "Parameters") ----
    style.configure(
        "TLabelframe",
        background=COLOR_BG,
        bordercolor=COLOR_BORDER,
        relief="solid",
        borderwidth=1,
    )
    style.configure(
        "TLabelframe.Label",
        background=COLOR_BG,
        foreground=COLOR_ACCENT,
        font=("Segoe UI", 9, "bold"),
    )

    # ---- Scrollbar ----
    style.configure(
        "Vertical.TScrollbar",
        background=COLOR_BG_ALT,
        troughcolor=COLOR_BG,
        bordercolor=COLOR_BORDER,
        arrowcolor=COLOR_TEXT,
    )
    style.map(
        "Vertical.TScrollbar",
        background=[("active", COLOR_BG_HOVER)],
    )

    # Tk clásicos que todavía puedan aparecer (Toplevel, etc.)
    root.option_add("*Background", COLOR_BG)
    root.option_add("*Foreground", COLOR_TEXT)
    root.option_add("*selectBackground", COLOR_ACCENT)
    root.option_add("*selectForeground", COLOR_TEXT_ON_ACCENT)
    root.option_add("*Toplevel.background", COLOR_BG)
    root.option_add("*Menu.background", COLOR_BG_ALT)
    root.option_add("*Menu.foreground", COLOR_TEXT)
    # Nota: estos option_add solo afectan a widgets creados DESPUÉS de esta llamada


# Alias retrocompat: el código histórico llamaba a apply_dark_theme(root).
def apply_dark_theme(root):
    apply_theme(root, "Dark")


def _retheme_tk_widgets(widget, _depth=0, prev_theme=None):
    """Recorre el árbol de widgets a partir de `widget` y reescribe bg/fg de
    los tk.* clásicos (Frame, Label, Button, Canvas) usando las constantes
    COLOR_* actuales.

    Solo toca atributos que el widget soporta — los Canvas no tienen fg, los
    Frame no tienen fg, etc. Atrapa TclError silenciosamente porque algunos
    widgets nativos (Spinbox de ttk wrappeado, etc.) rechazan ciertas opciones.

    Las clases ttk.* NO se tocan aquí: ya se actualizan automáticamente al
    haber reconfigurado ttk.Style en apply_theme().

    Mapping de roles → colores:
      - tk.Frame con bg actual ~ BG     → nuevo BG
      - tk.Frame con bg actual ~ BG_ALT → nuevo BG_ALT (topbar, paneles)
      - tk.Label hereda según el bg del padre
      - tk.Canvas: igual que Frame, sin fg
      - tk.Button: como BG_ALT por defecto

    Widgets con atributo `_theme_protected = True` se respetan: no se
    cambia su color y no se desciende a sus hijos. Sirve para "muestras"
    de tema (botones del selector de tema en Appearance) que deben
    mantener su apariencia original siempre.

    `prev_theme`: tema del que vienen los widgets. Desempata colores que
    tienen varios roles según el tema (#ffffff es BG_ALT en Light y TEXT
    en High Contrast). Sin él, se usa la heurística por tipo de atributo.
    """
    # Skip widgets explícitamente marcados como protegidos contra retheme.
    # Tampoco descendemos: si todo un subárbol debe quedar fijo (poco
    # común, pero ocurre en swatches/previews), marca el contenedor raíz.
    if getattr(widget, "_theme_protected", False):
        return

    try:
        cls = widget.winfo_class()
    except Exception:
        return

    # Detectamos tipo "raw tk" por la ausencia de un punto en el nombre
    # de clase y por que ttk usa "TFrame", "TLabel", etc.
    is_ttk = cls.startswith("T") and cls not in ("Toplevel",)

    if not is_ttk:
        # Recoger bg actual y mapear
        try:
            current_bg = widget.cget("bg") if "bg" in widget.keys() else None
        except (tk.TclError, AttributeError):
            current_bg = None

        new_bg = _map_legacy_color(current_bg, "bg", prev_theme)
        if new_bg is not None:
            try:
                widget.configure(bg=new_bg)
            except tk.TclError:
                pass

        # Foreground si lo soporta
        try:
            if "fg" in widget.keys():
                current_fg = widget.cget("fg")
                new_fg = _map_legacy_color(current_fg, "fg", prev_theme)
                if new_fg is not None:
                    widget.configure(fg=new_fg)
        except (tk.TclError, AttributeError):
            pass

        # Para Toplevels y Canvas también highlight* si lo tienen
        for attr in ("highlightbackground",):
            try:
                if attr in widget.keys():
                    cur = widget.cget(attr)
                    mapped = _map_legacy_color(cur, "border", prev_theme)
                    if mapped is not None:
                        widget.configure(**{attr: mapped})
            except (tk.TclError, AttributeError):
                pass

    # Recursión sobre hijos
    try:
        children = widget.winfo_children()
    except Exception:
        children = []
    for child in children:
        _retheme_tk_widgets(child, _depth + 1, prev_theme)


# Mapa inverso: dado un color (de cualquier tema), devuelve la "etiqueta" que
# tenía (BG, BG_ALT, etc.) — útil para re-mapear al nuevo tema.
_LEGACY_COLOR_REVERSE = {}

def _rebuild_legacy_reverse():
    """Reconstruye la tabla inversa: hex_color → (theme_name, role).
    Llamado al inicio. Para retheme runtime, el caller debe pasar el tema
    ANTERIOR explícitamente."""
    global _LEGACY_COLOR_REVERSE
    _LEGACY_COLOR_REVERSE = {}
    for tname, td in THEMES.items():
        for role, hex_val in td.items():
            _LEGACY_COLOR_REVERSE.setdefault(hex_val.lower(), []).append((tname, role))

_rebuild_legacy_reverse()


def _map_legacy_color(hex_value, kind, prev_theme=None):
    """Dado un color hex que estaba en un widget, intentar identificar su
    'rol' y devolver el equivalente en el tema ACTUAL.

    `kind` es una hint ('bg', 'fg', 'border') para desempatar cuando el
    mismo hex se usaba para más de un rol.
    """
    if hex_value is None:
        return None
    try:
        h = str(hex_value).lower()
    except Exception:
        return None
    if h not in _LEGACY_COLOR_REVERSE:
        return None
    # Tomar el primer mapping encontrado, preferentemente del tema previo si
    # podemos identificarlo. Para simplicidad: si el rol contiene 'TEXT' y
    # kind=='fg', preferir TEXT. Si kind=='border', preferir BORDER.
    candidates = _LEGACY_COLOR_REVERSE[h]
    from_prev = [c for c in candidates if c[0] == prev_theme]
    if from_prev:
        candidates = from_prev
    elif any(c[0] == CURRENT_THEME_NAME for c in candidates):
        # Ya es un color del tema actual (segunda pasada): no tocar.
        # Re-mapearlo por hex es ambiguo (#ffffff = TEXT de High Contrast
        # y TEXT_ON_ACCENT de Light → acababa negro sobre negro).
        return None
    preferred_role = None
    if kind == "fg":
        for _, role in candidates:
            if "TEXT" in role:
                preferred_role = role; break
    elif kind == "border":
        for _, role in candidates:
            if role == "BORDER":
                preferred_role = role; break
    if preferred_role is None:
        # Bg por defecto: priorizar BG, luego BG_ALT, luego cualquiera
        for pref in ("BG", "BG_ALT", "BG_HOVER", "BG_ACTIVE"):
            for _, role in candidates:
                if role == pref:
                    preferred_role = role; break
            if preferred_role:
                break
    if preferred_role is None:
        preferred_role = candidates[0][1]
    return THEMES[CURRENT_THEME_NAME].get(preferred_role)
