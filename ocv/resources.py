"""Paths to bundled resources (icon, splash) in dev and PyInstaller builds."""
import os
import sys


# -----------------------------
# Resolución de rutas de recursos (icono, etc.)
# -----------------------------
def _resource_path(rel_path):
    """Resuelve rutas a recursos (icono, etc.) tanto en modo dev como
    empaquetado con PyInstaller. En modo --onedir los assets viven junto
    al .exe; en modo --onefile en sys._MEIPASS."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        # Raíz del proyecto (ocv/resources.py → 2 niveles)
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel_path)
