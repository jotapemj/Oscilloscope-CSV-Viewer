"""Main window placement: restore saved geometry if still visible, else centre; Windows taskbar app id."""
import re

from ocv.core.config import save_user_config


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

_GEOM_RE = re.compile(r"(\d+)x(\d+)(\+-?\d+|-\d+)(\+-?\d+|-\d+)")


def _parse_geometry_string(s):
    """Parsea 'WxH+X+Y' a tupla (w, h, x, y) o None si falla.

    Offsets: '+N', '-N' y '+-N'. Tk en Windows da '+-1920' cuando la
    ventana está en un monitor a la izquierda/encima de la primaria."""
    if not isinstance(s, str):
        return None
    m = _GEOM_RE.fullmatch(s)
    if m is None:
        return None
    # int() no acepta '+-N': quitar el '+' delante
    return tuple(int(g[1:]) if g[0] == "+" else int(g) for g in m.groups())


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
