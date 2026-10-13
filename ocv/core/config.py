"""Persistent user preferences (config.json, portable mode)."""
import json
import os
import sys


# =====================================================
# User config (persistent across sessions)
# =====================================================
# Modo PORTABLE: el fichero de config vive JUNTO al ejecutable, no en el
# home del usuario. Una instalación queda autocontenida — la carpeta del
# programa se puede mover, copiar o borrar como una unidad sin dejar restos.
#
# Estrategia para encontrar la carpeta:
#   - Si la app está empaquetada con PyInstaller (sys.frozen=True):
#       carpeta del .exe vía sys.executable.
#   - En modo desarrollo (script .py):
#       carpeta del script principal vía __file__.
#
# Si la carpeta no es escribible (instalación en Program Files con UAC,
# USB read-only, etc.) NO caemos a home — mostramos diálogo al usuario
# pidiendo que mueva la app a otra ubicación. Esto se decide así
# DELIBERADAMENTE para que no haya sorpresas: o funciona donde está, o
# el usuario lo sabe.


def _app_base_dir():
    """Devuelve el directorio que aloja al ejecutable o al script.
    Estable entre arranques. NO depende del cwd."""
    if getattr(sys, "frozen", False):
        # PyInstaller: sys.executable apunta al .exe real (no a python.exe)
        return os.path.dirname(os.path.abspath(sys.executable))
    # Modo desarrollo: raíz del proyecto (ocv/core/config.py → 3 niveles)
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def config_dir():
    """Carpeta del config. OCV_CONFIG_DIR la redirige (tests, instalaciones
    especiales); si no, la carpeta de la app (modo portable)."""
    return os.environ.get("OCV_CONFIG_DIR") or _app_base_dir()


def _user_config_path():
    """Ruta del fichero de config (portable, junto al ejecutable)."""
    return os.path.join(config_dir(), "config.json")


def _is_writable(directory):
    """Comprueba REALMENTE si podemos escribir en el directorio creando y
    borrando un fichero temporal. os.access() no es fiable en Windows
    (UAC virtualization, ACLs complejas) por lo que la única forma robusta
    es probar a escribir."""
    try:
        test_path = os.path.join(directory, ".scope_write_test_tmp")
        with open(test_path, "w") as fh:
            fh.write("ok")
        os.remove(test_path)
        return True
    except Exception:
        return False


# Flag global: ¿hemos verificado ya escribibilidad? La verificación cuesta
# poco pero la hacemos una vez al inicio y propagamos el resultado.
# None = no comprobado, True/False = resultado del último chequeo.
_CONFIG_DIR_WRITABLE = None


def check_config_writable():
    """Verifica escribibilidad del directorio de config y devuelve el
    resultado. Cachea para no probar dos veces."""
    global _CONFIG_DIR_WRITABLE
    if _CONFIG_DIR_WRITABLE is None:
        _CONFIG_DIR_WRITABLE = _is_writable(config_dir())
    return _CONFIG_DIR_WRITABLE


def load_user_config():
    """Lee el config persistido. Devuelve dict (vacío si no existe o falla).

    Lectura SIEMPRE permitida (incluso si el dir no es escribible, podría
    haberse colocado un config.json read-only por un admin)."""
    path = _user_config_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_user_config(updates):
    """Mezcla `updates` (dict) con el config existente y lo escribe.

    Convención: si un valor en `updates` es None, se BORRA esa clave del
    config (en vez de almacenarla como null). Esto permite a los callers
    "limpiar" preferencias específicas sin tener que cargar+modificar+
    guardar manualmente.

    Devuelve True si se guardó OK, False si falló. Los callers pueden
    decidir si avisan al usuario; las llamadas habituales (al cambiar tema)
    son silenciosas para no molestar — el aviso se centraliza al inicio
    de la app."""
    path = _user_config_path()
    try:
        current = load_user_config()
        for k, v in updates.items():
            if v is None:
                current.pop(k, None)
            else:
                current[k] = v
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2)
        return True
    except Exception:
        return False
