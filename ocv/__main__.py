"""Application entry point: python main.py or python -m ocv."""
import os
import sys
import tkinter as tk
import traceback

from ocv.core.config import check_config_writable, config_dir, load_user_config
from ocv.resources import _resource_path
from ocv.ui.app.app import OscilloscopeApp
from ocv.ui.theme import apply_theme, THEMES
from ocv.ui.window_geom import _position_main_window, _register_windows_app_id


def main():
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
    smoke = _install_smoke_mode(root, app)
    root.mainloop()
    if smoke is not None:
        sys.exit(0 if smoke["ok"] else 1)


def _install_smoke_mode(root, app):
    """Prueba del .exe empaquetado (build.py --smoke, CI). Con
    OCV_SMOKE_EXIT=<ms> carga OCV_SMOKE_CSV (si hay), cierra a los <ms> y
    sale con código 1 si hubo excepción en un callback Tk o el CSV no
    cargó. Sin la variable no hace nada."""
    ms = os.environ.get("OCV_SMOKE_EXIT")
    if not ms:
        return None
    state = {"ok": True}

    def _on_error(exc, val, tb):
        traceback.print_exception(exc, val, tb)
        state["ok"] = False
    root.report_callback_exception = _on_error

    csv = os.environ.get("OCV_SMOKE_CSV")
    if csv:
        root.after(100, lambda: app._load_csv_from_path(csv))

    def _finish():
        if csv and not app.channels:
            print("smoke: CSV not loaded")
            state["ok"] = False
        app.session_dirty = False   # sin diálogo "¿guardar?" (modal)
        app.on_close()
    root.after(int(ms), _finish)
    return state


if __name__ == "__main__":
    main()
